"""
app/routers/subscriptions.py
=============================
Router abonnements — Phase 2 (Stripe).

Endpoints utilisateur :
  GET  /subscriptions/me               → plan actuel + limites
  POST /subscriptions/checkout         → crée une Stripe Checkout Session
  POST /subscriptions/cancel           → résilie via Stripe
  POST /subscriptions/portal           → ouvre le Stripe Customer Portal
  GET  /subscriptions/promo/{code}     → valide un code promo

Endpoints Stripe :
  POST /subscriptions/webhook          → reçoit les events Stripe

Endpoints admin :
  GET    /subscriptions/admin/list
  POST   /subscriptions/admin/set-plan/{user_id}
  GET    /subscriptions/admin/promo
  POST   /subscriptions/admin/promo
  PATCH  /subscriptions/admin/promo/{promo_id}
  DELETE /subscriptions/admin/promo/{promo_id}
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import select, desc

from app.db.database import get_db
from app.db.models import User, Subscription, PromoCode, PromoRedemption, PendingSubscription
from app.routers.auth import get_current_user
from app.core.limits import get_limits
from app.core.config import (
    STRIPE_SECRET_KEY,
    STRIPE_WEBHOOK_SECRET,
    STRIPE_PRICES,
    STRIPE_SUCCESS_URL,
    STRIPE_CANCEL_URL,
    FRONTEND_URL,
)
from app.services.email import send_mail, mail_pending_subscription

# NE PAS setter stripe.api_key globalement ici — MEOLES l'écrase au démarrage.
# La clé est forcée dans chaque fonction qui appelle Stripe.

router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])


# ============================================================
# Helpers internes
# ============================================================

def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _stripe() -> None:
    """Force la clé NAVIRE avant tout appel Stripe."""
    stripe.api_key = STRIPE_SECRET_KEY


def require_admin(user: User = Depends(get_current_user)) -> User:
    if not getattr(user, "is_admin", False):
        raise HTTPException(status_code=403, detail="Admin only")
    return user


def get_or_create_subscription(db: Session, user: User) -> Subscription:
    """Retourne la subscription de l'user, ou en crée une free si absente."""
    sub = db.execute(
        select(Subscription).where(Subscription.user_id == user.id)
    ).scalar_one_or_none()

    if not sub:
        sub = Subscription(
            user_id=user.id,
            plan="free",
            billing_cycle=None,
            status="active",
        )
        db.add(sub)
        db.commit()
        db.refresh(sub)

    return sub


def _sync_user_plan(db: Session, user: User, plan: str) -> None:
    """Cache User.plan — toujours appeler après un changement de plan."""
    user.plan = plan
    db.commit()


def _get_or_create_stripe_customer(user: User, sub: Subscription, db: Session) -> str:
    """
    Retourne le stripe_customer_id existant ou en crée un nouveau.
    Stocke l'id en base pour les prochains appels.
    """
    if sub.stripe_customer_id:
        return sub.stripe_customer_id

    _stripe()
    customer = stripe.Customer.create(
        email=user.email,
        name=user.username,
        metadata={"user_id": str(user.id)},
    )
    sub.stripe_customer_id = customer.id
    db.commit()
    return customer.id


def _downgrade_to_free(db: Session, user: User, sub: Subscription, status: str = "cancelled") -> None:
    """Repasse l'user en free. Utilisé par le webhook et le endpoint cancel."""
    sub.plan = "free"
    sub.billing_cycle = None
    sub.status = status
    sub.cancelled_at = utcnow()
    sub.current_period_end = None
    sub.stripe_subscription_id = None
    db.commit()
    _sync_user_plan(db, user, "free")


def _validate_promo(db: Session, user: User, code: str, billing_cycle: str) -> PromoCode:
    """Vérifie qu'un code promo est utilisable. Lève 400 sinon."""
    promo = db.execute(
        select(PromoCode).where(PromoCode.code == code.upper())
    ).scalar_one_or_none()

    if not promo:
        raise HTTPException(400, detail={"code": "PROMO_NOT_FOUND", "message": "Code promo introuvable."})
    if not promo.is_active:
        raise HTTPException(400, detail={"code": "PROMO_INACTIVE", "message": "Code promo inactif."})
    if promo.expires_at and promo.expires_at < utcnow():
        raise HTTPException(400, detail={"code": "PROMO_EXPIRED", "message": "Code promo expiré."})
    if promo.max_uses is not None and promo.uses_count >= promo.max_uses:
        raise HTTPException(400, detail={"code": "PROMO_EXHAUSTED", "message": "Code promo épuisé."})
    if promo.applies_to and promo.applies_to != "both" and promo.applies_to != billing_cycle:
        raise HTTPException(400, detail={
            "code": "PROMO_WRONG_CYCLE",
            "message": f"Ce code est valable uniquement pour l'abonnement '{promo.applies_to}'.",
        })

    already_used = db.execute(
        select(PromoRedemption).where(
            PromoRedemption.user_id == user.id,
            PromoRedemption.promo_code_id == promo.id,
        )
    ).scalar_one_or_none()

    if already_used:
        raise HTTPException(400, detail={"code": "PROMO_ALREADY_USED", "message": "Vous avez déjà utilisé ce code."})

    return promo


def _apply_promo(db: Session, user: User, promo: PromoCode) -> None:
    """Enregistre l'utilisation du code et incrémente uses_count."""
    db.add(PromoRedemption(user_id=user.id, promo_code_id=promo.id))
    promo.uses_count += 1
    db.commit()


# ============================================================
# Schemas
# ============================================================

class CheckoutIn(BaseModel):
    plan: Literal["membre", "membre+"]
    billing_cycle: Literal["monthly", "annual"]
    promo_code: str | None = None


class SetPlanIn(BaseModel):
    plan: Literal["free", "membre", "membre+"]
    billing_cycle: Literal["monthly", "annual"] | None = None
    note: str | None = None


class PromoCreateIn(BaseModel):
    code: str = Field(..., min_length=3, max_length=50)
    discount_type: Literal["percent", "fixed"]
    discount_value: float = Field(..., gt=0)
    applies_to: Literal["monthly", "annual", "both"] | None = None
    max_uses: int | None = Field(None, gt=0)
    expires_at: datetime | None = None


class PromoUpdateIn(BaseModel):
    is_active: bool | None = None
    max_uses: int | None = None
    expires_at: datetime | None = None
    discount_value: float | None = Field(None, gt=0)


# ============================================================
# Routes utilisateur
# ============================================================

@router.get("/me")
def get_my_subscription(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Plan actuel + statut + limites complètes."""
    sub = get_or_create_subscription(db, user)
    limits = get_limits(user.plan)

    return {
        "plan": user.plan,
        "status": sub.status,
        "billing_cycle": sub.billing_cycle,
        "current_period_end": sub.current_period_end,
        "cancelled_at": sub.cancelled_at,
        "limits": {
            "qcm_per_day": limits["qcm_per_day"],
            "flashcards_total": limits["flashcards_total"],
            "files_total": limits["files_total"],
            "file_ttl_hours": limits["file_ttl_hours"],
        },
    }


@router.post("/checkout")
def create_checkout_session(
    payload: CheckoutIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Crée une Stripe Checkout Session et retourne la checkout_url.
    Le front redirige l'user vers cette URL pour le paiement.
    Après paiement, Stripe envoie checkout.session.completed au webhook.
    """
    _stripe()  # force la clé NAVIRE

    sub = get_or_create_subscription(db, user)

    # Bloquer si déjà abonné
    if sub.status == "active" and user.plan != "free":
        raise HTTPException(400, detail={
            "code": "ALREADY_SUBSCRIBED",
            "message": f"Abonnement actif ({user.plan}). Résiliez d'abord.",
        })

    # Récupérer le price_id depuis config
    price_id = STRIPE_PRICES.get(payload.plan, {}).get(payload.billing_cycle)
    if not price_id:
        raise HTTPException(500, detail="Configuration Stripe manquante pour ce plan.")

    # Créer ou récupérer le customer Stripe
    customer_id = _get_or_create_stripe_customer(user, sub, db)

    # Validation code promo
    promo = None
    stripe_coupon_id = None
    if payload.promo_code:
        promo = _validate_promo(db, user, payload.promo_code, payload.billing_cycle)
        if promo.stripe_coupon_id:
            stripe_coupon_id = promo.stripe_coupon_id

    checkout_params: dict = {
        "customer": customer_id,
        "mode": "subscription",
        "line_items": [{"price": price_id, "quantity": 1}],
        "success_url": STRIPE_SUCCESS_URL,
        "cancel_url": STRIPE_CANCEL_URL,
        "metadata": {
            "user_id": str(user.id),
            "plan": payload.plan,
            "billing_cycle": payload.billing_cycle,
            "promo_code": payload.promo_code or "",
        },
        "subscription_data": {
            "metadata": {
                "user_id": str(user.id),
                "plan": payload.plan,
                "billing_cycle": payload.billing_cycle,
            }
        },
    }

    if stripe_coupon_id:
        checkout_params["discounts"] = [{"coupon": stripe_coupon_id}]

    try:
        session = stripe.checkout.Session.create(**checkout_params)
    except stripe.StripeError as e:
        raise HTTPException(502, detail=f"Erreur Stripe : {str(e)}")

    return {
        "checkout_url": session.url,
        "session_id": session.id,
    }


@router.post("/cancel")
def cancel_subscription(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Résilie en fin de période (cancel_at_period_end=True).
    L'user garde son accès jusqu'à current_period_end.
    Le downgrade effectif arrive via webhook customer.subscription.deleted.
    """
    _stripe()  # force la clé NAVIRE

    sub = get_or_create_subscription(db, user)

    if user.plan == "free":
        raise HTTPException(400, detail={
            "code": "ALREADY_FREE",
            "message": "Vous êtes déjà sur le plan gratuit.",
        })

    if not sub.stripe_subscription_id:
        # Plan forcé par admin → downgrade immédiat sans Stripe
        _downgrade_to_free(db, user, sub)
        return {"ok": True, "plan": "free", "message": "Abonnement résilié."}

    try:
        stripe.Subscription.modify(
            sub.stripe_subscription_id,
            cancel_at_period_end=True,
        )
    except stripe.StripeError as e:
        raise HTTPException(502, detail=f"Erreur Stripe : {str(e)}")

    sub.status = "cancelled"
    sub.cancelled_at = utcnow()
    db.commit()

    return {
        "ok": True,
        "message": "Résiliation programmée. Votre accès reste actif jusqu'à la fin de la période.",
        "current_period_end": sub.current_period_end,
    }


@router.post("/portal")
def create_portal_session(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Stripe Customer Portal : l'user gère lui-même sa CB, ses factures, son abonnement.
    """
    _stripe()  # force la clé NAVIRE

    sub = get_or_create_subscription(db, user)

    if not sub.stripe_customer_id:
        raise HTTPException(400, detail={
            "code": "NO_STRIPE_CUSTOMER",
            "message": "Aucun compte de facturation trouvé.",
        })

    try:
        session = stripe.billing_portal.Session.create(
            customer=sub.stripe_customer_id,
            return_url=STRIPE_SUCCESS_URL,
        )
    except stripe.StripeError as e:
        raise HTTPException(502, detail=f"Erreur Stripe : {str(e)}")

    return {"portal_url": session.url}


@router.get("/promo/{code}")
def validate_promo_code(
    code: str,
    billing_cycle: Literal["monthly", "annual"] = "monthly",
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Valide un code promo sans l'appliquer (affichage temps réel Framer)."""
    promo = _validate_promo(db, user, code, billing_cycle)
    return {
        "valid": True,
        "code": promo.code,
        "discount_type": promo.discount_type,
        "discount_value": promo.discount_value,
        "applies_to": promo.applies_to,
        "expires_at": promo.expires_at,
    }


# ============================================================
# Webhook Stripe
# ============================================================

@router.post("/webhook")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    """
    Reçoit et traite les events Stripe.

    Events gérés :
      checkout.session.completed      → activer le plan après paiement
      invoice.payment_succeeded       → renouvellement → prolonger la période
      invoice.payment_failed          → passer en past_due
      customer.subscription.deleted   → résiliation effective → downgrade free
      customer.subscription.updated   → mise à jour dates/statut
    """
    _stripe()  # force la clé NAVIRE

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except stripe.errors.SignatureVerificationError:
        raise HTTPException(400, detail="Signature Stripe invalide.")
    except Exception:
        raise HTTPException(400, detail="Payload webhook invalide.")

    event_type = event["type"]
    data = event["data"]["object"]

    # ── checkout.session.completed ─────────────────────────
    if event_type == "checkout.session.completed":
        meta = data.get("metadata") or {}
        user_id = int(meta.get("user_id", 0))
        plan = meta.get("plan", "")
        billing_cycle = meta.get("billing_cycle", "")
        promo_code_str = meta.get("promo_code", "")
        stripe_sub_id = data.get("subscription")
        customer_email = data.get("customer_details", {}).get("email") or data.get("customer_email", "")

        if not plan:
            return {"ok": True}

        # Récupérer les dates de période depuis Stripe
        period_start = period_end = None
        if stripe_sub_id:
            try:
                stripe_sub = stripe.Subscription.retrieve(stripe_sub_id)
                period_start = datetime.fromtimestamp(stripe_sub["current_period_start"], tz=timezone.utc)
                period_end = datetime.fromtimestamp(stripe_sub["current_period_end"], tz=timezone.utc)
            except Exception:
                pass

        user = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none() if user_id else None

        # ── Cas : email inconnu (pas de compte NAVIRE) ──────
        if not user and customer_email:
            pending = db.execute(
                select(PendingSubscription).where(PendingSubscription.email == customer_email.lower())
            ).scalar_one_or_none()

            if pending:
                pending.plan = plan
                pending.billing_cycle = billing_cycle
                pending.stripe_subscription_id = stripe_sub_id
                pending.stripe_customer_id = data.get("customer") or pending.stripe_customer_id
                pending.current_period_start = period_start
                pending.current_period_end = period_end
            else:
                pending = PendingSubscription(
                    email=customer_email.lower(),
                    plan=plan,
                    billing_cycle=billing_cycle,
                    stripe_subscription_id=stripe_sub_id,
                    stripe_customer_id=data.get("customer"),
                    current_period_start=period_start,
                    current_period_end=period_end,
                )
                db.add(pending)

            db.flush()

            subject, html = mail_pending_subscription(customer_email, plan, FRONTEND_URL)
            mail_ok = send_mail(customer_email, subject, html)
            pending.mail_sent = mail_ok
            db.commit()

            return {"ok": True}

        if not user:
            return {"ok": True}

        sub = get_or_create_subscription(db, user)

        sub.plan = plan
        sub.billing_cycle = billing_cycle
        sub.status = "active"
        sub.stripe_subscription_id = stripe_sub_id
        sub.stripe_customer_id = data.get("customer") or sub.stripe_customer_id
        sub.current_period_start = period_start
        sub.current_period_end = period_end
        sub.cancelled_at = None
        db.commit()
        _sync_user_plan(db, user, plan)

        if promo_code_str:
            try:
                promo = db.execute(
                    select(PromoCode).where(PromoCode.code == promo_code_str.upper())
                ).scalar_one_or_none()
                if promo:
                    already = db.execute(
                        select(PromoRedemption).where(
                            PromoRedemption.user_id == user_id,
                            PromoRedemption.promo_code_id == promo.id,
                        )
                    ).scalar_one_or_none()
                    if not already:
                        _apply_promo(db, user, promo)
            except Exception:
                pass

    # ── invoice.payment_succeeded ──────────────────────────
    elif event_type == "invoice.payment_succeeded":
        stripe_sub_id = data.get("subscription")
        if stripe_sub_id:
            sub = db.execute(
                select(Subscription).where(Subscription.stripe_subscription_id == stripe_sub_id)
            ).scalar_one_or_none()

            if sub:
                try:
                    stripe_sub = stripe.Subscription.retrieve(stripe_sub_id)
                    sub.current_period_start = datetime.fromtimestamp(stripe_sub["current_period_start"], tz=timezone.utc)
                    sub.current_period_end = datetime.fromtimestamp(stripe_sub["current_period_end"], tz=timezone.utc)
                    sub.status = "active"
                    db.commit()
                    user = db.execute(select(User).where(User.id == sub.user_id)).scalar_one_or_none()
                    if user:
                        _sync_user_plan(db, user, sub.plan)
                except Exception:
                    pass

    # ── invoice.payment_failed ─────────────────────────────
    elif event_type == "invoice.payment_failed":
        stripe_sub_id = data.get("subscription")
        if stripe_sub_id:
            sub = db.execute(
                select(Subscription).where(Subscription.stripe_subscription_id == stripe_sub_id)
            ).scalar_one_or_none()
            if sub:
                sub.status = "past_due"
                db.commit()

    # ── customer.subscription.deleted ─────────────────────
    elif event_type == "customer.subscription.deleted":
        stripe_sub_id = data.get("id")
        sub = db.execute(
            select(Subscription).where(Subscription.stripe_subscription_id == stripe_sub_id)
        ).scalar_one_or_none()

        if sub:
            user = db.execute(select(User).where(User.id == sub.user_id)).scalar_one_or_none()
            if user:
                _downgrade_to_free(db, user, sub, status="expired")

    # ── customer.subscription.updated ─────────────────────
    elif event_type == "customer.subscription.updated":
        stripe_sub_id = data.get("id")
        stripe_status = data.get("status")

        sub = db.execute(
            select(Subscription).where(Subscription.stripe_subscription_id == stripe_sub_id)
        ).scalar_one_or_none()

        if sub:
            try:
                sub.current_period_start = datetime.fromtimestamp(data["current_period_start"], tz=timezone.utc)
                sub.current_period_end = datetime.fromtimestamp(data["current_period_end"], tz=timezone.utc)
            except Exception:
                pass

            if stripe_status == "active":
                sub.status = "active"
            elif stripe_status in ("past_due", "unpaid"):
                sub.status = "past_due"

            db.commit()

    return {"ok": True}


# ============================================================
# Routes admin — abonnements
# ============================================================

@router.get("/admin/list")
def admin_list_subscriptions(
    plan: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    limit = max(1, min(200, int(limit)))
    offset = max(0, int(offset))

    stmt = (
        select(Subscription, User.email, User.username)
        .join(User, Subscription.user_id == User.id)
        .order_by(desc(Subscription.updated_at))
        .limit(limit)
        .offset(offset)
    )
    if plan:
        stmt = stmt.where(Subscription.plan == plan)
    if status:
        stmt = stmt.where(Subscription.status == status)

    rows = db.execute(stmt).all()

    return {
        "items": [
            {
                "user_id": sub.user_id,
                "email": email,
                "username": username,
                "plan": sub.plan,
                "billing_cycle": sub.billing_cycle,
                "status": sub.status,
                "current_period_end": sub.current_period_end,
                "cancelled_at": sub.cancelled_at,
                "stripe_subscription_id": sub.stripe_subscription_id,
                "created_at": sub.created_at,
            }
            for sub, email, username in rows
        ],
        "limit": limit,
        "offset": offset,
    }


@router.post("/admin/set-plan/{user_id}")
def admin_set_plan(
    user_id: int,
    payload: SetPlanIn,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Forcer un plan sur un user (tests, offres commerciales, corrections)."""
    target = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
    if not target:
        raise HTTPException(404, detail="Utilisateur introuvable.")

    sub = get_or_create_subscription(db, target)
    sub.plan = payload.plan
    sub.billing_cycle = payload.billing_cycle
    sub.status = "active"
    sub.cancelled_at = None if payload.plan != "free" else sub.cancelled_at
    db.commit()
    _sync_user_plan(db, target, payload.plan)

    return {
        "ok": True,
        "user_id": user_id,
        "plan": payload.plan,
        "billing_cycle": payload.billing_cycle,
        "set_by_admin": admin.id,
    }


# ============================================================
# Routes admin — codes promo
# ============================================================

@router.get("/admin/promo")
def admin_list_promos(
    active_only: bool = False,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    stmt = select(PromoCode).order_by(desc(PromoCode.created_at))
    if active_only:
        stmt = stmt.where(PromoCode.is_active == True)

    promos = db.execute(stmt).scalars().all()
    return {
        "items": [
            {
                "id": p.id,
                "code": p.code,
                "discount_type": p.discount_type,
                "discount_value": p.discount_value,
                "applies_to": p.applies_to,
                "max_uses": p.max_uses,
                "uses_count": p.uses_count,
                "expires_at": p.expires_at,
                "is_active": p.is_active,
                "stripe_coupon_id": p.stripe_coupon_id,
                "created_at": p.created_at,
            }
            for p in promos
        ]
    }


@router.post("/admin/promo")
def admin_create_promo(
    payload: PromoCreateIn,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    _stripe()  # force la clé NAVIRE

    existing = db.execute(
        select(PromoCode).where(PromoCode.code == payload.code.upper())
    ).scalar_one_or_none()

    if existing:
        raise HTTPException(400, detail={
            "code": "PROMO_CODE_EXISTS",
            "message": f"Le code '{payload.code.upper()}' existe déjà.",
        })

    stripe_coupon_id = None
    if STRIPE_SECRET_KEY:
        try:
            coupon_params: dict = {
                "id": payload.code.upper(),
                "currency": "eur",
                "duration": "once",
            }
            if payload.discount_type == "percent":
                coupon_params["percent_off"] = payload.discount_value
            else:
                coupon_params["amount_off"] = int(payload.discount_value * 100)

            if payload.max_uses:
                coupon_params["max_redemptions"] = payload.max_uses
            if payload.expires_at:
                coupon_params["redeem_by"] = int(payload.expires_at.timestamp())

            coupon = stripe.Coupon.create(**coupon_params)
            stripe_coupon_id = coupon.id
        except stripe.StripeError:
            pass

    promo = PromoCode(
        code=payload.code.upper(),
        discount_type=payload.discount_type,
        discount_value=payload.discount_value,
        applies_to=payload.applies_to,
        max_uses=payload.max_uses,
        expires_at=payload.expires_at,
        is_active=True,
        created_by=admin.id,
        stripe_coupon_id=stripe_coupon_id,
    )
    db.add(promo)
    db.commit()
    db.refresh(promo)

    return {
        "ok": True,
        "id": promo.id,
        "code": promo.code,
        "discount_type": promo.discount_type,
        "discount_value": promo.discount_value,
        "applies_to": promo.applies_to,
        "max_uses": promo.max_uses,
        "expires_at": promo.expires_at,
        "stripe_coupon_id": stripe_coupon_id,
    }


@router.patch("/admin/promo/{promo_id}")
def admin_update_promo(
    promo_id: int,
    payload: PromoUpdateIn,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    promo = db.execute(
        select(PromoCode).where(PromoCode.id == promo_id)
    ).scalar_one_or_none()
    if not promo:
        raise HTTPException(404, detail="Code promo introuvable.")

    if payload.is_active is not None:
        promo.is_active = payload.is_active
    if payload.max_uses is not None:
        promo.max_uses = payload.max_uses
    if payload.expires_at is not None:
        promo.expires_at = payload.expires_at
    if payload.discount_value is not None:
        promo.discount_value = payload.discount_value

    db.commit()
    return {"ok": True, "id": promo.id, "code": promo.code}


# ============================================================
# Job APScheduler — vérification périodique des abonnements
# ============================================================

def check_expired_subscriptions(db_factory) -> None:
    """
    Job horaire : repasse en free les abonnements dont current_period_end
    est dépassé et dont le statut Stripe est expiré/annulé.
    """
    import logging
    logger = logging.getLogger(__name__)

    _stripe()  # force la clé NAVIRE

    db: Session = db_factory()
    try:
        now = datetime.now(timezone.utc)

        expired_subs = db.execute(
            select(Subscription).where(
                Subscription.plan != "free",
                Subscription.current_period_end < now,
            )
        ).scalars().all()

        for sub in expired_subs:
            if sub.stripe_subscription_id:
                try:
                    stripe_sub = stripe.Subscription.retrieve(sub.stripe_subscription_id)
                    stripe_status = stripe_sub.get("status", "")

                    if stripe_status == "active":
                        new_end = datetime.fromtimestamp(
                            stripe_sub["current_period_end"], tz=timezone.utc
                        )
                        sub.current_period_end = new_end
                        sub.status = "active"
                        db.commit()
                        continue

                    if stripe_status in ("canceled", "unpaid", "incomplete_expired"):
                        user = db.execute(
                            select(User).where(User.id == sub.user_id)
                        ).scalar_one_or_none()
                        if user:
                            _downgrade_to_free(db, user, sub, status="expired")
                            logger.info("Downgraded user %s to free (stripe: %s)", user.id, stripe_status)

                except stripe.StripeError as e:
                    logger.warning("Stripe retrieve failed for sub %s: %s", sub.stripe_subscription_id, e)

            else:
                user = db.execute(
                    select(User).where(User.id == sub.user_id)
                ).scalar_one_or_none()
                if user:
                    _downgrade_to_free(db, user, sub, status="expired")

        from datetime import timedelta
        cutoff = now - timedelta(days=30)
        old_pending = db.execute(
            select(PendingSubscription).where(PendingSubscription.created_at < cutoff)
        ).scalars().all()
        for p in old_pending:
            db.delete(p)
        if old_pending:
            db.commit()
            logger.info("Purged %d expired PendingSubscriptions", len(old_pending))

    except Exception as exc:
        logger.error("check_expired_subscriptions error: %s", exc)
        db.rollback()
    finally:
        db.close()


@router.delete("/admin/promo/{promo_id}")
def admin_delete_promo(
    promo_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    _stripe()  # force la clé NAVIRE

    promo = db.execute(
        select(PromoCode).where(PromoCode.id == promo_id)
    ).scalar_one_or_none()
    if not promo:
        raise HTTPException(404, detail="Code promo introuvable.")

    if promo.stripe_coupon_id and STRIPE_SECRET_KEY:
        try:
            stripe.Coupon.delete(promo.stripe_coupon_id)
        except stripe.StripeError:
            pass

    db.delete(promo)
    db.commit()
    return {"ok": True, "deleted_code": promo.code}