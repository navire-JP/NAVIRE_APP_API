"""
Panier Kitly — plusieurs articles, plusieurs vendeurs, un seul paiement.

Parcours :
  1. Le panier vit dans le navigateur (static/cart.js, localStorage du site Kitly).
  2. POST /kitly/cart/quote : le serveur revalide chaque ligne (offre achetable, option,
     stock, livraison) et renvoie le récapitulatif par vendeur, avec les prix du serveur.
  3. POST /kitly/checkout : crée le panier (`KitlyCheckout`), une commande par ligne chez
     son vendeur (`KitlyOrder`, en attente), la preuve d'acceptation des CGV de chaque
     vendeur, et un PaymentIntent sur le compte Stripe Kitly (`transfer_group` = référence).
     La page /checkout affiche le formulaire de carte Stripe (Payment Element) avec le
     `client_secret` renvoyé.
  4. Webhook `payment_intent.succeeded` (compte plateforme) : panier et commandes payés,
     stock décrémenté, puis un virement (Transfer) par vendeur vers son compte Connect,
     montant net de la commission marketplace, adossé au paiement (`source_transaction`).
     Emails : un récapitulatif par vendeur, un récapitulatif global à l'acheteur.

Comme pour l'achat d'une offre, rien n'est « payé » sur la foi du navigateur : seul le
webhook Stripe fait foi.

⚠️ Dans ce schéma (« separate charges and transfers »), l'argent transite par le compte
Kitly avant d'être reversé : rôle de Kitly à faire valider (agent de paiement /
place de marché) avant la mise en production.
"""

from __future__ import annotations

import logging
from collections import OrderedDict

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.kitly import emails
from app.kitly.config import KITLY_MARKETPLACE_COMMISSION_BPS, KITLY_STRIPE_PUBLISHABLE_KEY
from app.kitly.models import (
    KitlyCheckout,
    KitlyOffer,
    KitlyOrder,
    KitlyProfile,
    KitlySellerTermsVersion,
    KitlyTermsAcceptance,
)
from app.kitly.queries import public_profiles
from app.kitly.routers import shop
from app.kitly.schemas import CartCheckoutIn, CartItemIn, CartQuoteIn
from app.kitly.stripe_client import is_kitly_object, kitly_metadata, stripe_client
from app.kitly.utils import client_ip, now, rate_guard, rate_hit, today, visitor_hash

logger = logging.getLogger(__name__)
router = APIRouter(tags=["kitly · panier"])


# ============================================================
# Revalidation du panier
# ============================================================

def _live_profiles(db: Session, profile_ids: set[int]) -> dict[int, KitlyProfile]:
    if not profile_ids:
        return {}
    rows = db.execute(public_profiles().where(KitlyProfile.id.in_(profile_ids))).scalars().all()
    return {p.id: p for p in rows}


def _delivery_options(offer: KitlyOffer) -> list[dict]:
    d = offer.details or {}
    if offer.kind != "physical":
        return []
    opts = [{"value": str(i), "label": o.get("label") or "Livraison", "price_cents": int(o.get("price_cents") or 0),
             "delay": o.get("delay") or ""} for i, o in enumerate(d.get("shipping_options") or [])]
    if not opts:
        opts = [{"value": "0", "label": "Livraison", "price_cents": 0, "delay": d.get("delivery_delay") or ""}]
    if d.get("pickup_available"):
        opts.append({"value": "pickup", "label": "Retrait sur place", "price_cents": 0, "delay": d.get("pickup_location") or ""})
    return opts


def _resolve(db: Session, items: list[CartItemIn]) -> list[dict]:
    """
    Chaque ligne du panier, revalidée : {item, offer, profile, line | None, error}.
    `line` vient de shop.price_line (prix, stock, livraison) ; `error` explique une ligne
    qui ne peut plus être payée (offre retirée, épuisée, vendeur non prêt…).
    """
    offers = {o.id: o for o in db.execute(
        select(KitlyOffer).where(KitlyOffer.id.in_({i.offer_id for i in items}))).scalars()} if items else {}
    profiles = _live_profiles(db, {o.profile_id for o in offers.values()})
    out = []
    for item in items:
        offer = offers.get(item.offer_id)
        profile = profiles.get(offer.profile_id) if offer else None
        row = {"item": item, "offer": offer, "profile": profile, "line": None, "error": ""}
        if not offer or not profile or offer.status != "published":
            row["error"] = "Cette offre n'est plus disponible."
        elif not shop.offer_buyable(offer, shop.shop_ready(profile)):
            row["error"] = "Cette offre ne peut plus être payée en ligne : contacte le vendeur."
        else:
            try:
                row["line"] = shop.price_line(offer, item.quantity, item.variant_id, item.delivery)
            except HTTPException as exc:
                row["error"] = str(exc.detail)
        out.append(row)
    _group_shipping(out)
    return out


def _group_shipping(rows: list[dict]) -> None:
    """
    Une seule livraison par vendeur : les articles expédiés d'un même vendeur partent
    ensemble ; seul le mode de livraison le plus cher est facturé, les autres sont offerts.
    """
    by_profile: dict[int, list[dict]] = {}
    for r in rows:
        if r["line"] and r["line"]["shipping_cents"] > 0:
            by_profile.setdefault(r["profile"].id, []).append(r)
    for group in by_profile.values():
        if len(group) < 2:
            continue
        keep = max(group, key=lambda r: r["line"]["shipping_cents"])
        for r in group:
            if r is keep:
                continue
            line = r["line"]
            line["total_cents"] -= line["shipping_cents"]
            line["shipping_cents"] = 0
            line["snap"] = dict(line["snap"], shipping_cents=0, shipping_grouped=True)


def _line_out(index: int, row: dict) -> dict:
    offer, item, line = row["offer"], row["item"], row["line"]
    d = (offer.details or {}) if offer else {}
    variants = [{"id": v.get("id"), "label": v.get("label", ""), "price_cents": v.get("price_cents"),
                 "in_stock": v.get("stock") is None or v["stock"] > 0}
                for v in (d.get("variants") or [])] if offer and offer.kind == "physical" else []
    stock = None
    if offer and offer.kind == "physical":
        v = next((x for x in d.get("variants") or [] if x.get("id") == item.variant_id), None)
        stock = (v or d).get("stock")
    return {
        "index": index, "offer_id": item.offer_id, "variant_id": item.variant_id, "delivery": item.delivery,
        "source": item.source, "title": offer.title if offer else "", "kind": offer.kind if offer else "",
        "image": ((offer.images or [None])[0]) if offer else None, "variants": variants,
        "quantity": line["quantity"] if line else item.quantity,
        "max_quantity": 1 if not offer or offer.kind != "physical" else max(1, min(10, stock if stock is not None else 10)),
        "unit_cents": line["unit_cents"] if line else (offer.price_cents if offer else 0),
        "shipping_cents": line["shipping_cents"] if line else 0, "total_cents": line["total_cents"] if line else 0,
        "variant": (line or {}).get("snap", {}).get("variant", ""),
        "delivery_options": _delivery_options(offer) if offer else [], "needs_address": bool(line and line["needs_address"]),
        "error": row["error"],
    }


def _summary(rows: list[dict]) -> dict:
    """Récapitulatif groupé par vendeur, pays de livraison communs, consentements requis."""
    sellers: "OrderedDict[int, dict]" = OrderedDict()
    orphans = []
    countries: set[str] | None = None
    for i, row in enumerate(rows):
        out = _line_out(i, row)
        p = row["profile"]
        if p is None:
            orphans.append(out)
            continue
        g = sellers.setdefault(p.id, {"slug": p.slug, "display_name": p.display_name, "avatar_url": p.avatar_url,
                                      "lines": [], "subtotal_cents": 0})
        g["lines"].append(out)
        g["subtotal_cents"] += out["total_cents"]
        if row["line"] and row["line"]["needs_address"]:
            c = set(row["line"]["countries"])
            countries = c if countries is None else countries & c
    valid = [r for r in rows if r["line"]]
    return {
        "sellers": list(sellers.values()), "unavailable": orphans,
        "total_cents": sum(r["line"]["total_cents"] for r in valid),
        "count": sum(r["line"]["quantity"] for r in valid),
        "has_errors": any(r["error"] for r in rows),
        "needs_address": countries is not None, "countries": sorted(countries or []),
        "has_digital": any(r["offer"].kind == "digital" for r in valid),
        "has_service": any(r["offer"].kind == "service" for r in valid),
        "payments_ready": bool(KITLY_STRIPE_PUBLISHABLE_KEY),
    }


@router.post("/cart/quote")
def quote(payload: CartQuoteIn, db: Session = Depends(get_db)):
    """Récapitulatif du panier, prix et disponibilités recalculés par le serveur."""
    return _summary(_resolve(db, payload.items))


# ============================================================
# Paiement
# ============================================================

def _cancel(db: Session, checkout: KitlyCheckout, remote: bool = True) -> None:
    """Annule un panier non payé (et son PaymentIntent chez Stripe si `remote`)."""
    if checkout.status != "pending":
        return
    if remote and checkout.stripe_payment_intent_id:
        try:
            stripe_client().payment_intents.cancel(checkout.stripe_payment_intent_id)
        except Exception as exc:  # déjà annulé, déjà payé… : le webhook tranchera
            logger.info("[kitly] Panier %s : annulation Stripe ignorée (%s)", checkout.public_ref, exc)
    checkout.status = "canceled"
    for o in _orders_of(db, checkout):
        if o.status == "pending":
            o.status = "canceled"


def _orders_of(db: Session, checkout: KitlyCheckout) -> list[KitlyOrder]:
    if not checkout.stripe_payment_intent_id:
        return []
    rows = db.execute(select(KitlyOrder).where(KitlyOrder.stripe_payment_intent_id == checkout.stripe_payment_intent_id)
                      .order_by(KitlyOrder.id)).scalars().all()
    return [o for o in rows if (o.offer_snapshot or {}).get("checkout_ref") == checkout.public_ref]


@router.post("/checkout")
def create_checkout(payload: CartCheckoutIn, request: Request, db: Session = Depends(get_db)):
    if not KITLY_STRIPE_PUBLISHABLE_KEY:
        raise HTTPException(503, detail="Le paiement du panier n'est pas encore activé (KITLY_STRIPE_PUBLISHABLE_KEY).")
    ip = client_ip(request)
    rate_guard(db, f"checkout:ip:{ip}", 15, 3600, "Trop de tentatives de paiement, réessaie plus tard.")
    rate_hit(db, f"checkout:ip:{ip}")

    rows = _resolve(db, payload.items)
    bad = [r for r in rows if r["error"]]
    if bad:
        raise HTTPException(409, detail=bad[0]["error"] if len(bad) == 1 else
                            "Certains articles de ton panier ne sont plus disponibles : vérifie ton panier.")
    summary = _summary(rows)
    address = payload.address.model_dump() if payload.address else None
    if summary["needs_address"]:
        if not address:
            raise HTTPException(422, detail="Indique ton adresse de livraison.")
        address["country"] = address["country"].upper()
        if address["country"] not in summary["countries"]:
            raise HTTPException(422, detail="Un des vendeurs ne livre pas dans ce pays : choisis le retrait sur place "
                                            "ou retire l'article du panier.")
    if summary["has_digital"] and not payload.waive_withdrawal:
        raise HTTPException(422, detail="Pour un contenu numérique livré immédiatement, coche la case de renonciation "
                                        "au droit de rétractation.")

    # Panier précédent non payé (l'acheteur a modifié son panier) : annulé.
    if payload.replaces:
        prev = db.execute(select(KitlyCheckout).where(KitlyCheckout.public_ref == payload.replaces)).scalar_one_or_none()
        if prev and prev.buyer_email == str(payload.email).lower():
            _cancel(db, prev)

    checkout = KitlyCheckout(buyer_email=str(payload.email).lower(), buyer_name=payload.name, buyer_phone=payload.phone,
                             shipping_address=address or {}, amount_cents=summary["total_cents"], currency="eur")
    db.add(checkout)
    db.flush()

    orders: list[KitlyOrder] = []
    terms_cache: dict[int, KitlySellerTermsVersion] = {}
    for row in rows:
        offer, profile, line, item = row["offer"], row["profile"], row["line"], row["item"]
        snap = dict(line["snap"], checkout_ref=checkout.public_ref)
        if line["needs_address"]:
            snap["shipping_address"] = dict(address)
        if payload.phone:
            snap["phone"] = payload.phone
        tid = profile.terms.current_version_id
        terms = terms_cache.setdefault(tid, db.get(KitlySellerTermsVersion, tid))
        bps = KITLY_MARKETPLACE_COMMISSION_BPS if item.source == "marketplace" else 0
        order = KitlyOrder(
            profile_id=profile.id, offer_id=offer.id, offer_snapshot=snap, buyer_email=checkout.buyer_email,
            buyer_name=payload.name, amount_cents=line["total_cents"], currency="eur", channel=item.source,
            commission_bps=bps, commission_cents=round(line["total_cents"] * bps / 10000), status="pending",
            terms_version_id=terms.id,
        )
        db.add(order)
        db.flush()
        db.add(KitlyTermsAcceptance(
            terms_version_id=terms.id, order_id=order.id, buyer_email=checkout.buyer_email, content_hash=terms.content_hash,
            consents={"cgv": True, "checkout_ref": checkout.public_ref,
                      "waive_withdrawal": bool(payload.waive_withdrawal and offer.kind == "digital"),
                      "early_start": bool(payload.early_start and offer.kind == "service")},
            visitor_hash=visitor_hash(request, today()), user_agent=(request.headers.get("user-agent") or "")[:300],
        ))
        orders.append(order)

    sellers = ", ".join(g["display_name"] for g in summary["sellers"])
    params = {
        "amount": checkout.amount_cents, "currency": "eur",
        "automatic_payment_methods": {"enabled": True},
        "receipt_email": checkout.buyer_email,
        "description": f"Kitly — panier {checkout.public_ref} ({sellers})"[:250],
        "transfer_group": checkout.public_ref,
        "metadata": kitly_metadata(kitly_checkout_id=checkout.id, kitly_checkout_ref=checkout.public_ref),
    }
    if address:
        params["shipping"] = {"name": address["name"], "phone": payload.phone or None, "address": {
            "line1": address["line1"], "line2": address["line2"] or None, "postal_code": address["postal_code"],
            "city": address["city"], "country": address["country"]}}
    try:
        intent = stripe_client().payment_intents.create(params=params,
                                                        options={"idempotency_key": f"kitly-checkout-{checkout.public_ref}"})
    except stripe.StripeError as exc:
        db.rollback()
        logger.warning("[kitly] Stripe (panier) : %s", exc)
        raise HTTPException(502, detail={"message": "Le paiement n'a pas pu être préparé. Réessaie dans un instant.",
                                         "stripe": exc.user_message or "", "code": "stripe_error"})
    checkout.stripe_payment_intent_id = intent["id"]
    for o in orders:
        o.stripe_payment_intent_id = intent["id"]
    db.commit()
    return {"ref": checkout.public_ref, "client_secret": intent["client_secret"],
            "publishable_key": KITLY_STRIPE_PUBLISHABLE_KEY, "amount_cents": checkout.amount_cents}


@router.get("/checkout/{ref}")
def checkout_status(ref: str, db: Session = Depends(get_db)):
    """État d'un panier (page de confirmation, retour de 3D Secure). Aucune donnée personnelle."""
    checkout = db.execute(select(KitlyCheckout).where(KitlyCheckout.public_ref == ref.upper())).scalar_one_or_none()
    if not checkout:
        raise HTTPException(404, detail="Commande introuvable.")
    orders = _orders_of(db, checkout)
    names = {p.id: p.display_name for p in db.execute(
        select(KitlyProfile).where(KitlyProfile.id.in_({o.profile_id for o in orders if o.profile_id}))).scalars()} if orders else {}
    return {
        "ref": checkout.public_ref, "status": checkout.status, "amount_cents": checkout.amount_cents,
        "items": [{"title": (o.offer_snapshot or {}).get("title", ""), "variant": (o.offer_snapshot or {}).get("variant", ""),
                   "quantity": (o.offer_snapshot or {}).get("quantity", 1), "seller": names.get(o.profile_id, ""),
                   "order_ref": o.public_ref} for o in orders],
    }


# ============================================================
# Webhook (compte plateforme) : paiement confirmé, échec, remboursement, litige
# ============================================================

def _checkout_for_intent(db: Session, obj) -> KitlyCheckout | None:
    meta = obj.get("metadata") or {}
    cid = meta.get("kitly_checkout_id")
    if cid and str(cid).isdigit() and is_kitly_object(obj):
        c = db.get(KitlyCheckout, int(cid))
        if c:
            return c
    pi = obj.get("id") if str(obj.get("object", "")) == "payment_intent" else obj.get("payment_intent")
    if not pi:
        return None
    return db.execute(select(KitlyCheckout).where(KitlyCheckout.stripe_payment_intent_id == pi)).scalar_one_or_none()


def _mark_paid(db: Session, checkout: KitlyCheckout, intent) -> None:
    if checkout.status != "pending":
        return  # déjà traité (événement relivré)
    checkout.status = "paid"
    checkout.paid_at = now()
    checkout.stripe_charge_id = intent.get("latest_charge") if isinstance(intent.get("latest_charge"), str) else \
        (intent.get("latest_charge") or {}).get("id")
    orders = _orders_of(db, checkout)
    for o in orders:
        if o.status == "pending":
            o.status = "paid"
            o.paid_at = checkout.paid_at
            shop._decrement_stock(db, o)
    db.flush()

    # Un virement par vendeur, net de la commission, adossé au paiement du client.
    by_profile: "OrderedDict[int, list[KitlyOrder]]" = OrderedDict()
    for o in orders:
        if o.status == "paid" and o.profile_id:
            by_profile.setdefault(o.profile_id, []).append(o)
    transfers = []
    for pid, group in by_profile.items():
        profile = db.get(KitlyProfile, pid)
        acc = profile.user.connected_account if profile else None
        net = sum(o.amount_cents - o.commission_cents for o in group)
        entry = {"profile_id": pid, "account": acc.stripe_account_id if acc else None, "amount_cents": net,
                 "orders": [o.public_ref for o in group], "transfer_id": None, "error": ""}
        if not acc:
            entry["error"] = "Compte de paiement du vendeur introuvable."
        elif net > 0:
            tparams = {"amount": net, "currency": checkout.currency, "destination": acc.stripe_account_id,
                       "transfer_group": checkout.public_ref,
                       "description": f"Kitly — panier {checkout.public_ref}"[:250],
                       "metadata": kitly_metadata(kitly_checkout_ref=checkout.public_ref, kitly_profile_id=pid,
                                                  kitly_orders=",".join(entry["orders"])[:450])}
            if checkout.stripe_charge_id:
                tparams["source_transaction"] = checkout.stripe_charge_id
            try:
                t = stripe_client().transfers.create(params=tparams,
                                                     options={"idempotency_key": f"kitly-transfer-{checkout.public_ref}-{pid}"})
                entry["transfer_id"] = t["id"]
            except stripe.StripeError as exc:
                entry["error"] = str(exc.user_message or exc)[:300]
                logger.error("[kitly] ⚠️ Virement non effectué (panier %s, vendeur %s) : %s", checkout.public_ref, pid, exc)
        transfers.append(entry)
        for o in group:
            o.offer_snapshot = dict(o.offer_snapshot or {}, transfer_id=entry["transfer_id"])
    checkout.transfers = transfers
    db.flush()

    # Emails : un récapitulatif par vendeur, un récapitulatif global à l'acheteur.
    buyer_groups = []
    for pid, group in by_profile.items():
        profile = db.get(KitlyProfile, pid)
        if not profile:
            continue
        seller_email = profile.contact_email or profile.user.email
        subject, html = emails.mail_cart_seller(profile.user.first_name, checkout.public_ref,
                                                [(o.public_ref, o.offer_snapshot or {}, o.amount_cents, o.commission_cents) for o in group],
                                                checkout.buyer_name, checkout.buyer_email)
        emails.send(seller_email, subject, html, checkout.buyer_email)
        lines = []
        for o in group:
            offer = db.get(KitlyOffer, o.offer_id) if o.offer_id else None
            url = ((offer.details or {}).get("delivery_url") or "") if offer and offer.kind == "digital" else ""
            lines.append((o.public_ref, o.offer_snapshot or {}, o.amount_cents, url))
        buyer_groups.append({"seller": profile.display_name, "seller_email": seller_email, "lines": lines})
    subject, html = emails.mail_cart_buyer(checkout.public_ref, buyer_groups, checkout.amount_cents)
    emails.send(checkout.buyer_email, subject, html)


def _refund(db: Session, checkout: KitlyCheckout, charge) -> None:
    if not charge.get("refunded"):
        if checkout.status == "paid":
            checkout.status = "partially_refunded"
        logger.warning("[kitly] Panier %s : remboursement partiel — virements vendeurs à régulariser à la main.",
                       checkout.public_ref)
        return
    if checkout.status == "refunded":
        return
    checkout.status = "refunded"
    for o in _orders_of(db, checkout):
        if o.status in ("paid", "disputed"):
            o.status = "refunded"
    # Remboursement total : chaque vendeur rend ce qui lui avait été viré.
    out = []
    for t in checkout.transfers or []:
        t = dict(t)
        if t.get("transfer_id") and not t.get("reversal_id"):
            try:
                r = stripe_client().transfers.create_reversal(t["transfer_id"], params={"metadata": kitly_metadata(
                    kitly_checkout_ref=checkout.public_ref)}, options={"idempotency_key": f"kitly-reversal-{t['transfer_id']}"})
                t["reversal_id"] = r["id"]
            except stripe.StripeError as exc:
                t["error"] = f"Reprise du virement impossible : {exc.user_message or exc}"[:300]
                logger.error("[kitly] ⚠️ Panier %s : reprise du virement %s impossible : %s",
                             checkout.public_ref, t["transfer_id"], exc)
        out.append(t)
    checkout.transfers = out


def handle_cart_event(db: Session, event) -> bool:
    """Événements d'un panier ; rend True si l'événement concernait un panier Kitly."""
    etype, obj = event["type"], event["data"]["object"]
    if etype.startswith("payment_intent."):
        checkout = _checkout_for_intent(db, obj)
        if not checkout:
            return False
        if etype == "payment_intent.succeeded":
            _mark_paid(db, checkout, obj)
        elif etype == "payment_intent.canceled":
            _cancel(db, checkout, remote=False)
        # payment_intent.payment_failed : rien à faire, l'acheteur peut réessayer avec le même panier.
        return True
    if etype in ("charge.refunded", "charge.dispute.created"):
        checkout = _checkout_for_intent(db, obj)
        if not checkout:
            return False
        if etype == "charge.refunded":
            _refund(db, checkout, obj)
        else:
            checkout.status = "disputed"
            for o in _orders_of(db, checkout):
                if o.status == "paid":
                    o.status = "disputed"
        return True
    return False
