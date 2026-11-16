"""
Embed 5 — Abonnements Kitly (Stripe Billing) et encaissement des ventes du
freelance (Stripe Connect).

Deux systèmes distincts, sur le compte Stripe KITLY (jamais celui de NAVIRE) :
  - Billing : Kitly facture ses formules Custom / Premium+ au freelance ;
  - Connect : le client paie le freelance, Kitly prélève éventuellement sa
    commission (ventes issues de la marketplace) et Stripe reverse le reste.

Règle d'or : l'état d'un abonnement ou d'un compte de paiement ne change QUE
sur la foi de Stripe (webhook ou lecture API), jamais du retour navigateur.
"""

from __future__ import annotations

import functools
import logging
from datetime import datetime, timezone

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.kitly.config import (
    KITLY_CONNECT_COUNTRY,
    KITLY_CORS_ORIGINS,
    KITLY_FRONTEND_URL,
    KITLY_RETURN_URL_OVERRIDES,
    KITLY_STRIPE_CONNECT_WEBHOOK_SECRET,
    KITLY_STRIPE_SECRET_KEY,
    KITLY_STRIPE_WEBHOOK_SECRET,
)
from app.kitly.models import KitlyConnectedAccount, KitlyStripeEvent, KitlySubscription, KitlyUser
from app.kitly.plans import PAID_PLANS, PLAN_RANK, get_plan, plan_for_price, plan_out, stripe_price_for
from app.kitly.schemas import CheckoutIn
from app.kitly.security import current_user, forbid_impersonation
from app.kitly.serializers import public_url
from app.kitly.stripe_client import is_kitly_object, kitly_metadata, stripe_client, stripe_reason
from app.kitly.utils import iso

logger = logging.getLogger(__name__)
router = APIRouter(tags=["kitly · paiements"])

# Chemins de retour par défaut (page /subscribe du site Framer Kitly).
RETURN_PATHS = {
    "KITLY_BILLING_SUCCESS_URL": "/subscribe?subscription=success",
    "KITLY_BILLING_CANCEL_URL": "/subscribe?subscription=cancelled",
    "KITLY_BILLING_RETURN_URL": "/subscribe",
    "KITLY_CONNECT_RETURN_URL": "/subscribe?payments=return",
    "KITLY_CONNECT_REFRESH_URL": "/subscribe?payments=refresh",
}


def return_url(request: Request, key: str) -> str:
    """
    Adresse de retour après Stripe : celle fixée par variable d'environnement si
    elle existe, sinon le domaine sur lequel se trouve le visiteur (kitly.fr ou
    kitly.framer.website), à condition qu'il fasse partie de KITLY_CORS_ORIGINS ;
    à défaut KITLY_FRONTEND_URL.
    """
    if KITLY_RETURN_URL_OVERRIDES.get(key):
        return KITLY_RETURN_URL_OVERRIDES[key]
    origin = (request.headers.get("origin") or "").strip().lower().rstrip("/")
    base = origin if origin in KITLY_CORS_ORIGINS else KITLY_FRONTEND_URL
    return base + RETURN_PATHS[key]


def stripe_guard(message: str):
    """
    Transforme une erreur Stripe en réponse 502 lisible par le front, au lieu
    d'une erreur 500 opaque (ex. portail client non configuré dans Stripe).
    Les webhooks n'en bénéficient pas : ils doivent répondre 500 pour être relivrés.
    """
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except stripe.StripeError as exc:
                reason = stripe_reason(exc)
                # print : seul canal visible dans les journaux Render.
                print(f"[kitly] ⚠️  Stripe a refusé ({fn.__name__}) : {reason}", flush=True)
                raise HTTPException(502, detail={"message": message, "stripe": reason, "code": "stripe_error"})
        return wrapper
    return deco


LIVE_STATUSES = ("active", "trialing", "past_due")  # la formule reste ouverte (relances Stripe en cours)
OPEN_STATUSES = LIVE_STATUSES + ("unpaid", "incomplete")


# ============================================================
# Formules
# ============================================================

@router.get("/plans")
def list_plans(db: Session = Depends(get_db)):
    from app.kitly.models import KitlyPlan

    plans = db.execute(select(KitlyPlan).where(KitlyPlan.is_active.is_(True)).order_by(KitlyPlan.position)).scalars()
    return {"items": [plan_out(p) for p in plans], "tax_note": "Prix TTC."}


# ============================================================
# Synchronisation abonnement (webhook et lectures API)
# ============================================================

def _ts(value) -> datetime | None:
    return datetime.fromtimestamp(int(value), tz=timezone.utc) if value else None


def _first_item(sub) -> dict:
    items = (sub.get("items") or {}).get("data") or []
    return items[0] if items else {}


def _user_for(db: Session, obj) -> KitlyUser | None:
    meta = obj.get("metadata") or {}
    if meta.get("kitly_user_id"):
        user = db.get(KitlyUser, int(meta["kitly_user_id"]))
        if user:
            return user
    customer = obj.get("customer")
    if isinstance(customer, dict):
        customer = customer.get("id")
    if customer:
        return db.execute(select(KitlyUser).where(KitlyUser.stripe_customer_id == customer)).scalar_one_or_none()
    return None


def _recompute_plan(db: Session, user: KitlyUser) -> None:
    """
    Formule effective = la plus haute parmi les abonnements Stripe encore ouverts
    et les formules offertes par un admin (non révoquées, non expirées).
    """
    from app.kitly.models import KitlyPlanGrant
    from app.kitly.utils import aware, now

    live = db.execute(
        select(KitlySubscription).where(KitlySubscription.user_id == user.id,
                                        KitlySubscription.status.in_(LIVE_STATUSES))
    ).scalars().all()
    grants = db.execute(
        select(KitlyPlanGrant).where(KitlyPlanGrant.user_id == user.id, KitlyPlanGrant.revoked_at.is_(None))
    ).scalars().all()
    codes = [s.plan_code for s in live] + [g.plan_code for g in grants if g.expires_at is None or aware(g.expires_at) > now()]
    user.plan_code = max(codes, key=lambda c: PLAN_RANK.get(c, 0), default="free")


def recompute_plan(db: Session, user: KitlyUser) -> None:
    """Point d'entrée public (back-office, tâche planifiée)."""
    _recompute_plan(db, user)


def sync_subscription(db: Session, sub) -> KitlySubscription | None:
    user = _user_for(db, sub)
    if user is None:
        logger.warning("[kitly] abonnement %s sans compte Kitly associé", sub.get("id"))
        return None
    item = _first_item(sub)
    price_id = (item.get("price") or {}).get("id", "")
    plan_code = plan_for_price(db, price_id) or (sub.get("metadata") or {}).get("plan")
    if plan_code not in PAID_PLANS:
        logger.warning("[kitly] prix Stripe %s inconnu (abonnement %s)", price_id, sub.get("id"))
        return None

    row = db.execute(
        select(KitlySubscription).where(KitlySubscription.stripe_subscription_id == sub["id"])
    ).scalar_one_or_none()
    if row is None:
        row = KitlySubscription(user_id=user.id, stripe_subscription_id=sub["id"], plan_code=plan_code, status="")
        db.add(row)
    row.plan_code = plan_code
    row.status = sub.get("status") or "incomplete"
    # Selon la version d'API, la fin de période est sur l'abonnement ou sur l'item.
    row.current_period_end = _ts(item.get("current_period_end") or sub.get("current_period_end"))
    row.cancel_at_period_end = bool(sub.get("cancel_at_period_end"))
    if sub.get("customer") and not user.stripe_customer_id:
        user.stripe_customer_id = sub["customer"] if isinstance(sub["customer"], str) else sub["customer"].get("id")
    db.flush()
    _recompute_plan(db, user)
    return row


def _current_sub(db: Session, user: KitlyUser) -> KitlySubscription | None:
    return db.execute(
        select(KitlySubscription).where(KitlySubscription.user_id == user.id,
                                        KitlySubscription.status.in_(OPEN_STATUSES))
        .order_by(KitlySubscription.updated_at.desc())
    ).scalars().first()


def _sub_out(db: Session, user: KitlyUser) -> dict:
    sub = _current_sub(db, user)
    return {
        "plan": plan_out(get_plan(db, user.plan_code)),
        "subscription": None if sub is None else {
            "plan": sub.plan_code,
            "status": sub.status,
            "renews_at": iso(sub.current_period_end) if not sub.cancel_at_period_end else None,
            "ends_at": iso(sub.current_period_end) if sub.cancel_at_period_end else None,
            "cancel_at_period_end": sub.cancel_at_period_end,
            "payment_issue": sub.status in ("past_due", "unpaid", "incomplete"),
        },
    }


# ============================================================
# Abonnement — routes
# ============================================================

def _ensure_customer(db: Session, user: KitlyUser) -> str:
    if user.stripe_customer_id:
        return user.stripe_customer_id
    customer = stripe_client().customers.create(params={
        "email": user.email,
        "name": f"{user.first_name} {user.last_name}".strip(),
        "preferred_locales": ["fr"],
        "metadata": kitly_metadata(kitly_user_id=user.id),
    })
    user.stripe_customer_id = customer["id"]
    db.commit()
    return customer["id"]


@router.get("/billing/subscription")
def get_subscription(user: KitlyUser = Depends(current_user), db: Session = Depends(get_db)):
    return _sub_out(db, user)


@router.post("/billing/checkout")
@stripe_guard("Le paiement n'a pas pu être préparé. Réessaie dans un instant.")
def checkout(payload: CheckoutIn, request: Request, user: KitlyUser = Depends(current_user), db: Session = Depends(get_db)):
    """
    Pas encore abonné : rend l'URL de la page de paiement Stripe.
    Déjà abonné : change de formule sur place (prorata), sans nouveau paiement à saisir.
    """
    forbid_impersonation(user)
    plan = get_plan(db, payload.plan)
    price = stripe_price_for(plan)
    if not price:
        raise HTTPException(503, detail=f"Le prix Stripe de la formule {plan.name} n'est pas configuré.")
    client = stripe_client()
    current = _current_sub(db, user)

    if current and current.status in LIVE_STATUSES:
        if current.plan_code == plan.code and not current.cancel_at_period_end:
            raise HTTPException(409, detail=f"Tu es déjà abonné(e) à {plan.name}.")
        remote = client.subscriptions.retrieve(current.stripe_subscription_id)
        item_id = _first_item(remote).get("id")
        updated = client.subscriptions.update(current.stripe_subscription_id, params={
            "items": [{"id": item_id, "price": price}],
            "proration_behavior": "always_invoice" if PLAN_RANK[plan.code] > PLAN_RANK[current.plan_code] else "create_prorations",
            "cancel_at_period_end": False,
            "metadata": kitly_metadata(kitly_user_id=user.id, plan=plan.code),
        })
        sync_subscription(db, updated)
        db.commit()
        return {"changed": True, **_sub_out(db, user)}

    customer = _ensure_customer(db, user)
    meta = kitly_metadata(kitly_user_id=user.id, plan=plan.code)
    success = return_url(request, "KITLY_BILLING_SUCCESS_URL")
    session = client.checkout.sessions.create(params={
        "mode": "subscription",
        "customer": customer,
        "client_reference_id": str(user.id),
        "line_items": [{"price": price, "quantity": 1}],
        "success_url": success + ("&" if "?" in success else "?") + "session_id={CHECKOUT_SESSION_ID}",
        "cancel_url": return_url(request, "KITLY_BILLING_CANCEL_URL"),
        "allow_promotion_codes": True,
        "billing_address_collection": "auto",
        "locale": "fr",
        "metadata": meta,
        "subscription_data": {"metadata": meta},
    })
    return {"changed": False, "checkout_url": session["url"]}


@router.post("/billing/portal")
@stripe_guard("Le portail de gestion de l'abonnement est indisponible pour le moment.")
def billing_portal(request: Request, user: KitlyUser = Depends(current_user), db: Session = Depends(get_db)):
    """Portail Stripe : carte bancaire, factures, résiliation."""
    forbid_impersonation(user)
    if not user.stripe_customer_id:
        raise HTTPException(404, detail="Aucun abonnement à gérer.")
    session = stripe_client().billing_portal.sessions.create(params={
        "customer": user.stripe_customer_id, "return_url": return_url(request, "KITLY_BILLING_RETURN_URL"), "locale": "fr",
    })
    return {"url": session["url"]}


def _set_cancel(db: Session, user: KitlyUser, cancel: bool) -> dict:
    forbid_impersonation(user)
    sub = _current_sub(db, user)
    if sub is None:
        raise HTTPException(404, detail="Aucun abonnement en cours.")
    updated = stripe_client().subscriptions.update(sub.stripe_subscription_id, params={"cancel_at_period_end": cancel})
    sync_subscription(db, updated)
    db.commit()
    return _sub_out(db, user)


@router.post("/billing/cancel")
@stripe_guard("La résiliation n'a pas pu être enregistrée. Réessaie dans un instant.")
def cancel_subscription(user: KitlyUser = Depends(current_user), db: Session = Depends(get_db)):
    """Résiliation à la fin de la période payée (la formule reste active jusque-là)."""
    return _set_cancel(db, user, True)


@router.post("/billing/resume")
@stripe_guard("La reprise de l'abonnement n'a pas pu être enregistrée. Réessaie dans un instant.")
def resume_subscription(user: KitlyUser = Depends(current_user), db: Session = Depends(get_db)):
    return _set_cancel(db, user, False)


@router.get("/billing/invoices")
@stripe_guard('Les factures sont indisponibles pour le moment.')
def invoices(user: KitlyUser = Depends(current_user)):
    if not user.stripe_customer_id:
        return {"items": []}
    res = stripe_client().invoices.list(params={"customer": user.stripe_customer_id, "limit": 24})
    return {"items": [
        {
            "id": inv["id"],
            "number": inv.get("number"),
            "date": iso(_ts(inv.get("created"))),
            "amount_cents": inv.get("amount_paid") or inv.get("amount_due") or 0,
            "currency": inv.get("currency"),
            "status": inv.get("status"),
            "pdf_url": inv.get("invoice_pdf"),
            "url": inv.get("hosted_invoice_url"),
        }
        for inv in res["data"]
    ]}


# ============================================================
# Stripe Connect — encaissement des ventes
# ============================================================

STATUS_MESSAGES = {
    "not_started": "Active les paiements pour encaisser tes ventes directement sur Kitly.",
    "onboarding_incomplete": "Termine la saisie de tes informations pour activer les paiements.",
    "action_required": "Stripe a besoin d'informations complémentaires pour activer ou maintenir ton compte.",
    "pending_verification": "Tes informations sont en cours de vérification par Stripe.",
    "restricted": "Ton compte de paiement est restreint. Consulte les informations demandées par Stripe.",
    "active": "Paiements activés : tu peux encaisser tes ventes.",
}


def connect_status(acc: KitlyConnectedAccount | None) -> str:
    if acc is None:
        return "not_started"
    if acc.charges_enabled and acc.payouts_enabled:
        return "action_required" if acc.requirements_due else "active"
    if not acc.details_submitted:
        return "onboarding_incomplete"
    if acc.requirements_due:
        return "action_required"
    if acc.disabled_reason and "pending" not in acc.disabled_reason:
        return "restricted"
    return "pending_verification"


def _connect_out(acc: KitlyConnectedAccount | None) -> dict:
    status = connect_status(acc)
    return {
        "status": status,
        "message": STATUS_MESSAGES[status],
        "charges_enabled": bool(acc and acc.charges_enabled),
        "payouts_enabled": bool(acc and acc.payouts_enabled),
        "requirements_due": acc.requirements_due if acc else [],
        "updated_at": iso(acc.updated_at) if acc else None,
    }


def sync_account(db: Session, account) -> KitlyConnectedAccount | None:
    acc = db.execute(
        select(KitlyConnectedAccount).where(KitlyConnectedAccount.stripe_account_id == account["id"])
    ).scalar_one_or_none()
    if acc is None:
        return None
    req = account.get("requirements") or {}
    acc.details_submitted = bool(account.get("details_submitted"))
    acc.charges_enabled = bool(account.get("charges_enabled"))
    acc.payouts_enabled = bool(account.get("payouts_enabled"))
    acc.requirements_due = sorted(set((req.get("currently_due") or []) + (req.get("past_due") or [])))
    acc.disabled_reason = req.get("disabled_reason") or ""
    db.flush()
    return acc


@router.get("/connect/status")
@stripe_guard('Le statut de ton compte de paiement est indisponible pour le moment.')
def get_connect_status(refresh: bool = False, user: KitlyUser = Depends(current_user), db: Session = Depends(get_db)):
    """`refresh=true` relit Stripe (au retour de l'onboarding) ; sinon l'état reçu par webhook."""
    acc = user.connected_account
    if acc and refresh:
        sync_account(db, stripe_client().accounts.retrieve(acc.stripe_account_id))
        db.commit()
    return _connect_out(acc)


@router.post("/connect/onboarding")
@stripe_guard("L'activation des paiements est indisponible pour le moment.")
def connect_onboarding(request: Request, user: KitlyUser = Depends(current_user), db: Session = Depends(get_db)):
    """
    « Activer les paiements » : crée le compte connecté au premier appel puis
    rend un lien d'onboarding hébergé par Stripe (identité, adresse, statut,
    IBAN, justificatifs selon le cas). À rappeler si le lien a expiré.

    ⚠️ Les responsabilités ci-dessous (frais et pertes portés par Kitly, tableau
    de bord Express) sont à valider selon le montage juridique retenu.
    """
    forbid_impersonation(user)
    if not user.email_verified_at:
        raise HTTPException(403, detail="Confirme ton adresse email avant d'activer les paiements.")
    client = stripe_client()
    acc = user.connected_account
    if acc is None:
        profile = user.profile
        business_profile = {"product_description": (profile.headline if profile else "") or "Freelance Kitly"}
        if profile and profile.is_published:
            business_profile["url"] = public_url(profile)
        account = client.accounts.create(params={
            "country": KITLY_CONNECT_COUNTRY,
            "email": user.email,
            "controller": {
                "fees": {"payer": "application"},
                "losses": {"payments": "application"},
                "stripe_dashboard": {"type": "express"},
            },
            "capabilities": {"card_payments": {"requested": True}, "transfers": {"requested": True}},
            "business_profile": business_profile,
            "metadata": kitly_metadata(kitly_user_id=user.id),
        })
        acc = KitlyConnectedAccount(user_id=user.id, stripe_account_id=account["id"], country=KITLY_CONNECT_COUNTRY)
        db.add(acc)
        db.commit()
    link = client.account_links.create(params={
        "account": acc.stripe_account_id,
        "refresh_url": return_url(request, "KITLY_CONNECT_REFRESH_URL"),
        "return_url": return_url(request, "KITLY_CONNECT_RETURN_URL"),
        "type": "account_onboarding",
        "collection_options": {"fields": "eventually_due"},
    })
    return {"url": link["url"], **_connect_out(acc)}


@router.post("/connect/dashboard-link")
@stripe_guard('Le tableau de bord des paiements est indisponible pour le moment.')
def connect_dashboard(user: KitlyUser = Depends(current_user)):
    """Accès au tableau de bord Stripe Express (paiements, reversements)."""
    forbid_impersonation(user)
    acc = user.connected_account
    if not acc or not acc.details_submitted:
        raise HTTPException(404, detail="Active d'abord les paiements.")
    link = stripe_client().accounts.login_links.create(acc.stripe_account_id)
    return {"url": link["url"]}


# ============================================================
# Webhooks
# ============================================================

def _verify(payload: bytes, signature: str, secret: str):
    if not secret:
        raise HTTPException(503, detail="Secret de webhook Kitly non configuré.")
    try:
        # Vérification locale de la signature : ne dépend que du secret de webhook Kitly.
        return stripe.Webhook.construct_event(payload, signature, secret, api_key=KITLY_STRIPE_SECRET_KEY or None)
    except Exception:
        raise HTTPException(400, detail="Signature de webhook invalide.")


def _already_processed(db: Session, event_id: str) -> bool:
    return db.get(KitlyStripeEvent, event_id) is not None


def handle_billing_event(db: Session, event) -> None:
    from app.kitly.routers.shop import handle_order_event  # import local : shop dépend de ce module

    if handle_order_event(db, event):
        return  # vente sur une vitrine (Checkout en mode paiement, remboursement, litige)
    etype = event["type"]
    obj = event["data"]["object"]
    if etype == "checkout.session.completed":
        if obj.get("mode") != "subscription" or not is_kitly_object(obj):
            return
        user = _user_for(db, obj) or (db.get(KitlyUser, int(obj["client_reference_id"]))
                                      if obj.get("client_reference_id") else None)
        if user and obj.get("customer") and not user.stripe_customer_id:
            user.stripe_customer_id = obj["customer"]
        if obj.get("subscription"):
            sync_subscription(db, stripe_client().subscriptions.retrieve(obj["subscription"]))
    elif etype in ("customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"):
        sync_subscription(db, obj)
    elif etype == "invoice.payment_failed":
        sub_id = obj.get("subscription") or ((obj.get("parent") or {}).get("subscription_details") or {}).get("subscription")
        if sub_id:
            sync_subscription(db, stripe_client().subscriptions.retrieve(sub_id))


def handle_connect_event(db: Session, event) -> None:
    if event["type"] == "account.updated":
        sync_account(db, event["data"]["object"])
    elif event["type"] == "account.application.deauthorized" and event.get("account"):
        acc = db.execute(
            select(KitlyConnectedAccount).where(KitlyConnectedAccount.stripe_account_id == event["account"])
        ).scalar_one_or_none()
        if acc:
            acc.charges_enabled = acc.payouts_enabled = False
            acc.disabled_reason = "deauthorized"


async def _process(request: Request, db: Session, secret: str, handler) -> dict:
    event = _verify(await request.body(), request.headers.get("stripe-signature", ""), secret)
    if _already_processed(db, event["id"]):
        return {"received": True, "duplicate": True}
    try:
        handler(db, event)
        db.add(KitlyStripeEvent(id=event["id"], type=event["type"]))
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("[kitly] échec du traitement de l'événement %s", event.get("id"))
        raise HTTPException(500, detail="Traitement impossible, Stripe relivrera l'événement.")
    return {"received": True}


@router.post("/stripe/webhook", include_in_schema=False)
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    """Webhook du compte plateforme Kitly (abonnements)."""
    return await _process(request, db, KITLY_STRIPE_WEBHOOK_SECRET, handle_billing_event)


@router.post("/stripe/connect-webhook", include_in_schema=False)
async def stripe_connect_webhook(request: Request, db: Session = Depends(get_db)):
    """Webhook des comptes connectés (Stripe Connect)."""
    return await _process(request, db, KITLY_STRIPE_CONNECT_WEBHOOK_SECRET, handle_connect_event)
