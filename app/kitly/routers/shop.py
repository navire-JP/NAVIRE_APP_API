"""
Achat sur une vitrine — Stripe Checkout sur le compte Stripe Kitly, argent versé
au freelance (Stripe Connect, « destination charge ») ; Kitly ne prélève sa
commission que sur les ventes issues de la marketplace.

Une offre est achetable en ligne (`buyable`) quand :
  - son action est « Commander » (cta = order), à prix fixe (≥ 0,50 €) ;
  - c'est un produit physique, un produit numérique ou une prestation ;
  - elle est disponible (stock) ;
  - le vendeur a activé ses paiements (Stripe Connect, charges_enabled) ;
  - le vendeur a publié ses CGV (acceptées par l'acheteur, preuve conservée).
Sinon, « Commander » reste une demande envoyée par email (comportement d'avant).

La commande n'est « payée » que sur la foi du webhook Stripe, jamais du retour
navigateur.
"""

from __future__ import annotations

import logging
import re
from datetime import timedelta

import stripe
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.kitly import emails
from app.kitly.config import KITLY_CORS_ORIGINS, KITLY_MARKETPLACE_COMMISSION_BPS
from app.kitly.models import (
    KitlyOffer,
    KitlyOrder,
    KitlyProfile,
    KitlySellerTermsVersion,
    KitlyTermsAcceptance,
    KitlyUser,
)
from app.kitly.queries import get_public_profile
from app.kitly.schemas import OrderCheckoutIn, OrderFulfillIn
from app.kitly.security import current_user
from app.kitly.serializers import public_url
from app.kitly.stripe_client import is_kitly_object, kitly_metadata, stripe_client
from app.kitly.utils import aware, client_ip, iso, now, rate_guard, rate_hit, today, visitor_hash

logger = logging.getLogger(__name__)
router = APIRouter(tags=["kitly · ventes"])

BUYABLE_KINDS = {"physical", "digital", "service"}
MIN_AMOUNT_CENTS = 50  # minimum Stripe en euros

_EU = ["AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE", "IT", "LV", "LT",
       "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE"]
ZONE_COUNTRIES = {
    "fr_metro": ["FR", "MC"],
    "fr_dom_tom": ["GP", "MQ", "GF", "RE", "YT", "PM", "BL", "MF", "NC", "PF", "WF"],
    "eu": _EU,
    "world": _EU + ["GB", "CH", "NO", "IS", "LI", "MC", "AD", "US", "CA", "AU", "NZ", "JP", "SG", "HK", "AE",
                    "IL", "MA", "TN", "SN", "CI", "BR", "MX"],
}


# ============================================================
# Disponibilité à la vente
# ============================================================

def shop_ready(profile: KitlyProfile) -> bool:
    """Le vendeur peut encaisser en ligne : paiements Stripe actifs et CGV publiées."""
    acc = profile.user.connected_account
    has_terms = bool(profile.terms and profile.terms.current_version_id)
    return bool(acc and acc.charges_enabled and has_terms and profile.user.is_active)


def _in_stock(offer: KitlyOffer) -> bool:
    d = offer.details or {}
    if offer.kind != "physical":
        return True
    variants = d.get("variants") or []
    if variants:
        return any(v.get("stock") is None or v["stock"] > 0 for v in variants)
    return d.get("stock") is None or d["stock"] > 0


def offer_buyable(offer: KitlyOffer, ready: bool) -> bool:
    return bool(
        ready and offer.status == "published" and offer.cta == "order" and offer.kind in BUYABLE_KINDS
        and offer.price_mode == "fixed" and (offer.price_cents or 0) >= MIN_AMOUNT_CENTS and _in_stock(offer)
    )


def seller_blockers(profile: KitlyProfile) -> list[str]:
    """Ce qui empêche le vendeur d'encaisser en ligne (vide = prêt)."""
    out = []
    acc = profile.user.connected_account
    if not acc:
        out.append("Paiements non activés (bouton « Activer » du bloc Vente en ligne).")
    elif not acc.charges_enabled:
        out.append("Paiements en cours de validation par Stripe (informations à compléter ou vérification en cours).")
    if not (profile.terms and profile.terms.current_version_id):
        out.append("CGV non publiées (bouton « Rédiger » du bloc Vente en ligne).")
    if not profile.user.is_active:
        out.append("Compte désactivé.")
    return out


def offer_blockers(offer: KitlyOffer) -> list[str]:
    """Ce qui empêche CETTE offre d'être payée en ligne, côté offre (vide = OK)."""
    out = []
    if offer.status != "published":
        out.append("Offre non publiée.")
    if offer.kind not in BUYABLE_KINDS:
        out.append("Les rendez-vous se réservent sur demande.")
    if offer.cta != "order":
        out.append("Bouton de l'offre différent de « Commander ».")
    if offer.price_mode != "fixed":
        out.append("Prix non fixe (sur devis, fourchette ou gratuit).")
    elif (offer.price_cents or 0) < MIN_AMOUNT_CENTS:
        out.append("Prix inférieur à 0,50 €.")
    if not _in_stock(offer):
        out.append("Épuisé.")
    return out


def mark_buyable(payload: dict, profile: KitlyProfile, offers: list[KitlyOffer]) -> dict:
    """Ajoute `buyable` aux offres d'une réponse publique (vitrine ou fiche offre)."""
    ready = shop_ready(profile)
    by_id = {o.id: o for o in offers}
    for item in payload.get("offers", []):
        o = by_id.get(item["id"])
        item["buyable"] = bool(o and offer_buyable(o, ready))
    for b in payload.get("blocks", []):
        if b.get("type") == "offer" and b.get("offer"):
            o = by_id.get(b["offer"]["id"])
            b["offer"]["buyable"] = bool(o and offer_buyable(o, ready))
    return payload


# ============================================================
# Paiement
# ============================================================

_FRAGMENT_SLUG = re.compile(r"[a-z0-9-]{3,40}")


def _safe_return(request: Request, url: str, fallback: str) -> str:
    """Page de retour : celle d'où vient l'acheteur, si elle est sur un domaine Kitly connu."""
    try:
        parts = urlsplit((url or "").strip())
        origin = f"{parts.scheme}://{parts.netloc}".lower()
        api_origin = str(request.base_url).rstrip("/").lower()
        if parts.scheme in ("http", "https") and (origin in KITLY_CORS_ORIGINS or origin == api_origin):
            query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in ("commande", "ref")]
            # Le hash garde l'adresse de la vitrine (/profile#lea-martin), rien d'autre.
            fragment = parts.fragment if _FRAGMENT_SLUG.fullmatch(parts.fragment or "") else ""
            return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), fragment))
    except Exception:
        pass
    return fallback


def _with_params(url: str, **params) -> str:
    parts = urlsplit(url)
    query = parse_qsl(parts.query, keep_blank_values=True) + list(params.items())
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def price_line(offer: KitlyOffer, quantity: int, variant_id: str | None, delivery: str | None) -> dict:
    """
    Prix d'une ligne d'achat (offre déjà vérifiée achetable) : option, quantité, stock,
    livraison. Partagé par l'achat d'une offre et par le panier. Lève 409 / 422.
    → {snap, unit_cents, quantity, shipping_cents, needs_address, countries, total_cents}
    """
    d = offer.details or {}
    snap: dict = {"title": offer.title, "kind": offer.kind, "offer_id": offer.id, "quantity": 1}
    unit = offer.price_cents or 0
    quantity = quantity if offer.kind == "physical" else 1

    # Variante (taille, couleur…)
    variants = (d.get("variants") or []) if offer.kind == "physical" else []
    variant = None
    if variants:
        variant = next((v for v in variants if v.get("id") == variant_id), None)
        if variant is None:
            raise HTTPException(422, detail=f"« {offer.title} » : choisis une option (taille, couleur…).")
        if variant.get("price_cents") is not None:
            unit = variant["price_cents"]
        snap["variant"], snap["variant_id"] = variant.get("label", ""), variant.get("id")
    stock = (variant or d).get("stock") if offer.kind == "physical" else None
    if stock is not None and stock < quantity:
        raise HTTPException(409, detail=(f"« {offer.title} » : stock insuffisant ({stock} disponible{'s' if stock > 1 else ''})."
                                         if stock else f"« {offer.title} » est épuisé."))
    snap["quantity"] = quantity
    snap["unit_cents"] = unit

    # Livraison
    shipping_cents, needs_address, countries = 0, False, []
    if offer.kind == "physical":
        options = d.get("shipping_options") or []
        if delivery == "pickup":
            if not d.get("pickup_available"):
                raise HTTPException(422, detail=f"« {offer.title} » : le retrait sur place n'est pas proposé.")
            snap["pickup"], snap["pickup_location"] = True, d.get("pickup_location", "")
        else:
            needs_address = True
            countries = sorted({c for z in (d.get("delivery_zones") or ["fr_metro"]) for c in ZONE_COUNTRIES.get(z, [])}) or ["FR"]
            if options:
                try:
                    opt = options[int(delivery or "0")]
                except (ValueError, IndexError):
                    raise HTTPException(422, detail=f"« {offer.title} » : choisis un mode de livraison.")
                shipping_cents = int(opt.get("price_cents") or 0)
                snap["shipping_label"], snap["shipping_cents"] = opt.get("label", "Livraison"), shipping_cents
    return {"snap": snap, "unit_cents": unit, "quantity": quantity, "shipping_cents": shipping_cents,
            "needs_address": needs_address, "countries": countries, "total_cents": unit * quantity + shipping_cents}


def _absolute_image(url: str) -> list[str]:
    return [url] if url and url.startswith("https://") else []


@router.post("/public/profiles/{slug}/offers/{offer_id}/checkout")
def checkout_offer(slug: str, offer_id: int, payload: OrderCheckoutIn, request: Request, db: Session = Depends(get_db)):
    profile = get_public_profile(db, slug)
    if not profile:
        raise HTTPException(404, detail="Cette vitrine n'existe pas ou n'est pas publiée.")
    offer = db.get(KitlyOffer, offer_id)
    if not offer or offer.profile_id != profile.id or offer.status != "published":
        raise HTTPException(404, detail="Offre introuvable.")
    if not offer_buyable(offer, shop_ready(profile)):
        raise HTTPException(409, detail="Cette offre ne peut pas être payée en ligne : contacte le vendeur.")

    ip = client_ip(request)
    rate_guard(db, f"checkout:ip:{ip}", 15, 3600, "Trop de tentatives de paiement, réessaie plus tard.")
    rate_hit(db, f"checkout:ip:{ip}")

    line = price_line(offer, payload.quantity, payload.variant_id, payload.delivery)
    d, snap, unit, quantity = offer.details or {}, line["snap"], line["unit_cents"], line["quantity"]
    shipping_cents, collect_address = line["shipping_cents"], line["needs_address"]

    # Consentements propres au type d'offre (Code de la consommation)
    if offer.kind == "digital" and not payload.waive_withdrawal:
        raise HTTPException(422, detail="Pour un contenu numérique livré immédiatement, coche la case de renonciation "
                                        "au droit de rétractation.")

    terms = db.get(KitlySellerTermsVersion, profile.terms.current_version_id)
    amount = unit * quantity + shipping_cents
    channel = "marketplace" if payload.source == "marketplace" else "direct"
    bps = KITLY_MARKETPLACE_COMMISSION_BPS if channel == "marketplace" else 0
    commission = round(amount * bps / 10000)

    order = KitlyOrder(
        profile_id=profile.id, offer_id=offer.id, offer_snapshot=snap, buyer_email=str(payload.email).lower(),
        buyer_name=payload.name, amount_cents=amount, currency=offer.currency or "eur", channel=channel,
        commission_bps=bps, commission_cents=commission, status="pending", terms_version_id=terms.id,
    )
    db.add(order)
    db.flush()
    db.add(KitlyTermsAcceptance(
        terms_version_id=terms.id, order_id=order.id, buyer_email=order.buyer_email, content_hash=terms.content_hash,
        consents={"cgv": True, "waive_withdrawal": bool(payload.waive_withdrawal and offer.kind == "digital"),
                  "early_start": bool(payload.early_start and offer.kind == "service")},
        visitor_hash=visitor_hash(request, today()), user_agent=(request.headers.get("user-agent") or "")[:300],
    ))

    back = _safe_return(request, payload.return_url, public_url(profile))
    name = offer.title + (f" — {snap['variant']}" if snap.get("variant") else "")
    line_items = [{
        "quantity": quantity,
        "price_data": {"currency": order.currency, "unit_amount": unit,
                       "product_data": {"name": name[:250], "images": _absolute_image((offer.images or [""])[0])}},
    }]
    if shipping_cents:
        line_items.append({"quantity": 1, "price_data": {"currency": order.currency, "unit_amount": shipping_cents,
                                                          "product_data": {"name": f"Livraison — {snap['shipping_label']}"[:250]}}})
    meta = kitly_metadata(kitly_order_id=order.id, kitly_order_ref=order.public_ref, kitly_profile_id=profile.id)
    intent = {"transfer_data": {"destination": profile.user.connected_account.stripe_account_id},
              "description": f"{profile.display_name} — {name}"[:250], "metadata": meta}
    if commission:
        intent["application_fee_amount"] = commission
    params = {
        "mode": "payment",
        "line_items": line_items,
        "customer_email": order.buyer_email,
        "client_reference_id": order.public_ref,
        "locale": "fr",
        "success_url": _with_params(back, commande="ok", ref=order.public_ref),
        "cancel_url": _with_params(back, commande="annulee"),
        "payment_intent_data": intent,
        "metadata": meta,
    }
    if collect_address:
        params["shipping_address_collection"] = {"allowed_countries": line["countries"]}
        params["phone_number_collection"] = {"enabled": True}
    try:
        session = stripe_client().checkout.sessions.create(params=params)
    except stripe.StripeError as exc:
        db.rollback()
        logger.warning("[kitly] Stripe (achat %s) : %s", offer.id, exc)
        raise HTTPException(502, detail={"message": "Le paiement n'a pas pu être préparé. Réessaie dans un instant.",
                                         "stripe": exc.user_message or "", "code": "stripe_error"})
    order.stripe_checkout_session_id = session["id"]
    db.commit()
    return {"url": session["url"], "ref": order.public_ref}


# ============================================================
# Confirmation par webhook (compte plateforme Kitly)
# ============================================================

def _order_for_session(db: Session, obj) -> KitlyOrder | None:
    if not is_kitly_object(obj):
        return None
    oid = (obj.get("metadata") or {}).get("kitly_order_id")
    order = db.get(KitlyOrder, int(oid)) if oid and str(oid).isdigit() else None
    if order is None and obj.get("id"):
        order = db.execute(select(KitlyOrder).where(KitlyOrder.stripe_checkout_session_id == obj["id"])).scalar_one_or_none()
    return order


def _decrement_stock(db: Session, order: KitlyOrder) -> None:
    offer = db.get(KitlyOffer, order.offer_id) if order.offer_id else None
    if not offer or offer.kind != "physical":
        return
    snap, details = order.offer_snapshot or {}, dict(offer.details or {})
    qty = int(snap.get("quantity") or 1)
    if snap.get("variant_id"):
        variants = [dict(v) for v in details.get("variants") or []]
        for v in variants:
            if v.get("id") == snap["variant_id"] and v.get("stock") is not None:
                v["stock"] = max(0, v["stock"] - qty)
        details["variants"] = variants
    elif details.get("stock") is not None:
        details["stock"] = max(0, details["stock"] - qty)
    offer.details = details  # nouvel objet : SQLAlchemy détecte la modification du JSON


def mark_paid(db: Session, order: KitlyOrder, obj) -> None:
    if order.status != "pending":
        return  # déjà traitée (événement relivré)
    cd = obj.get("customer_details") or {}
    snap = dict(order.offer_snapshot or {})
    ship = (obj.get("collected_information") or {}).get("shipping_details") or obj.get("shipping_details") or {}
    if ship.get("address"):
        snap["shipping_address"] = {"name": ship.get("name") or cd.get("name") or "", **{
            k: ship["address"].get(k) or "" for k in ("line1", "line2", "postal_code", "city", "state", "country")}}
    if cd.get("phone"):
        snap["phone"] = cd["phone"]
    order.offer_snapshot = snap
    order.status = "paid"
    order.paid_at = now()
    order.stripe_payment_intent_id = obj.get("payment_intent")
    order.buyer_name = order.buyer_name or cd.get("name") or ""
    if cd.get("email"):
        order.buyer_email = cd["email"].lower()
    _decrement_stock(db, order)
    db.flush()

    profile = db.get(KitlyProfile, order.profile_id) if order.profile_id else None
    if profile is None:
        return
    seller = profile.user
    offer = db.get(KitlyOffer, order.offer_id) if order.offer_id else None
    delivery_url = ((offer.details or {}).get("delivery_url") or "") if offer and offer.kind == "digital" else ""
    seller_email = profile.contact_email or seller.email
    subject, html = emails.mail_order_seller(seller.first_name, order.public_ref, snap, order.amount_cents,
                                             order.commission_cents, order.buyer_name, order.buyer_email)
    emails.send(seller_email, subject, html, order.buyer_email)
    subject, html = emails.mail_order_buyer(profile.display_name, order.public_ref, snap, order.amount_cents,
                                            delivery_url, seller_email)
    emails.send(order.buyer_email, subject, html, seller_email)


def handle_order_event(db: Session, event) -> bool:
    """Événements de commande ; rend True si l'événement concernait une vente."""
    from app.kitly.routers.cart import handle_cart_event  # import local : cart dépend de ce module

    if handle_cart_event(db, event):
        return True  # panier (plusieurs commandes pour un même paiement)
    etype, obj = event["type"], event["data"]["object"]
    if etype in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
        if obj.get("mode") != "payment":
            return False
        order = _order_for_session(db, obj)
        if order and obj.get("payment_status") in ("paid", "no_payment_required"):
            mark_paid(db, order, obj)
        return order is not None
    if etype in ("checkout.session.expired", "checkout.session.async_payment_failed"):
        order = _order_for_session(db, obj)
        if order and order.status == "pending":
            order.status = "canceled"
        return order is not None
    if etype in ("charge.refunded", "charge.dispute.created"):
        pi = obj.get("payment_intent")
        order = db.execute(select(KitlyOrder).where(KitlyOrder.stripe_payment_intent_id == pi)).scalar_one_or_none() if pi else None
        if order:
            if etype == "charge.dispute.created":
                order.status = "disputed"
            elif obj.get("refunded"):  # remboursement total
                order.status = "refunded"
        return order is not None
    return False


# ============================================================
# Ventes du freelance
# ============================================================

def order_out(o: KitlyOrder) -> dict:
    snap = o.offer_snapshot or {}
    return {
        "ref": o.public_ref, "status": o.status, "channel": o.channel, "amount_cents": o.amount_cents,
        "commission_cents": o.commission_cents, "net_cents": o.amount_cents - o.commission_cents, "currency": o.currency,
        "item": snap, "fulfilled_at": snap.get("fulfilled_at"), "tracking": snap.get("tracking", ""),
        "buyer": {"name": o.buyer_name, "email": o.buyer_email}, "created_at": iso(o.created_at), "paid_at": iso(o.paid_at),
    }


def _is_todo(o: KitlyOrder) -> bool:
    return o.status == "paid" and not (o.offer_snapshot or {}).get("fulfilled_at")


@router.get("/me/orders")
def my_orders(status: str = "", page: int = Query(1, ge=1), user: KitlyUser = Depends(current_user),
              db: Session = Depends(get_db)):
    """
    Ventes de la vitrine. `status` : vide (toutes, sauf les paiements non aboutis),
    `todo` (payées, pas encore marquées expédiées / traitées) ou un statut précis.
    `summary` : chiffres affichés en tête de l'onglet « Mes ventes ».
    """
    empty = {"to_fulfill": 0, "orders_30d": 0, "net_30d_cents": 0, "net_total_cents": 0, "orders_total": 0}
    if not user.profile:
        return {"items": [], "summary": empty, "page": page, "has_more": False}
    rows = db.execute(select(KitlyOrder).where(KitlyOrder.profile_id == user.profile.id,
                                               KitlyOrder.status.not_in(("pending", "canceled")))
                      .order_by(KitlyOrder.created_at.desc())).scalars().all()
    since = now() - timedelta(days=30)
    paid = [o for o in rows if o.status == "paid"]
    recent = [o for o in paid if o.paid_at and aware(o.paid_at) >= since]
    summary = {
        "to_fulfill": sum(1 for o in rows if _is_todo(o)),
        "orders_30d": len(recent),
        "net_30d_cents": sum(o.amount_cents - o.commission_cents for o in recent),
        "orders_total": len(paid),
        "net_total_cents": sum(o.amount_cents - o.commission_cents for o in paid),
    }
    if status == "todo":
        rows = [o for o in rows if _is_todo(o)]
    elif status:
        rows = [o for o in rows if o.status == status]
    start = (page - 1) * 50
    return {"items": [order_out(o) for o in rows[start:start + 50]], "summary": summary, "page": page,
            "has_more": len(rows) > start + 50}


@router.patch("/me/orders/{ref}")
def update_order(ref: str, payload: OrderFulfillIn, user: KitlyUser = Depends(current_user), db: Session = Depends(get_db)):
    """
    Marque une vente comme expédiée / traitée (ou annule ce marquage), avec un
    numéro de suivi facultatif ; l'acheteur est prévenu par email si `notify`.
    """
    order = db.execute(select(KitlyOrder).where(KitlyOrder.public_ref == ref)).scalar_one_or_none()
    if not order or not user.profile or order.profile_id != user.profile.id:
        raise HTTPException(404, detail="Commande introuvable.")
    if order.status != "paid":
        raise HTTPException(409, detail="Seule une commande payée peut être marquée comme traitée.")
    snap = dict(order.offer_snapshot or {})
    first_time = payload.fulfilled and not snap.get("fulfilled_at")
    if payload.fulfilled:
        snap["fulfilled_at"] = snap.get("fulfilled_at") or iso(now())
        snap["tracking"] = payload.tracking
    else:
        snap.pop("fulfilled_at", None)
        snap.pop("tracking", None)
    order.offer_snapshot = snap  # nouvel objet : modification du JSON détectée
    db.commit()
    notified = False
    if first_time and payload.notify:
        profile = user.profile
        seller_email = profile.contact_email or user.email
        subject, html = emails.mail_order_fulfilled(profile.display_name, order.public_ref, snap, payload.tracking, seller_email)
        notified = emails.send(order.buyer_email, subject, html, seller_email)
    return {"order": order_out(order), "buyer_notified": bool(notified)}
