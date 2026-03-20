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
from app.db.models import User, Subscription, PromoCode, PromoRedemption
from app.routers.auth import get_current_user
from app.core.limits import get_limits
from app.core.config import (
    STRIPE_SECRET_KEY,
    STRIPE_WEBHOOK_SECRET,
    STRIPE_PRICES,
    STRIPE_SUCCESS_URL,
    STRIPE_CANCEL_URL,
)

stripe.api_key = STRIPE_SECRET_KEY

router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])


# ============================================================
# Helpers internes
# ============================================================

def utcnow() -> datetime:
    return datetime.now(timezone.utc)


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
        # metadata transmise au webhook pour identifier l'user et le plan
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
        session = stripe.checkout.sessions.create(**checkout_params)
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

        if not user_id or not plan:
            return {"ok": True}

        user = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
        if not user:
            return {"ok": True}

        sub = get_or_create_subscription(db, user)

        period_start = period_end = None
        if stripe_sub_id:
            try:
                stripe_sub = stripe.Subscription.retrieve(stripe_sub_id)
                period_start = datetime.fromtimestamp(stripe_sub["current_period_start"], tz=timezone.utc)
                period_end = datetime.fromtimestamp(stripe_sub["current_period_end"], tz=timezone.utc)
            except Exception:
                pass

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

        # Appliquer le code promo
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
                # Pas de downgrade immédiat — Stripe retentera.
                # customer.subscription.deleted arrivera si tous les essais échouent.

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
    existing = db.execute(
        select(PromoCode).where(PromoCode.code == payload.code.upper())
    ).scalar_one_or_none()

    if existing:
        raise HTTPException(400, detail={
            "code": "PROMO_CODE_EXISTS",
            "message": f"Le code '{payload.code.upper()}' existe déjà.",
        })

    # Créer le coupon Stripe en même temps
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
                coupon_params["amount_off"] = int(payload.discount_value * 100)  # centimes

            if payload.max_uses:
                coupon_params["max_redemptions"] = payload.max_uses
            if payload.expires_at:
                coupon_params["redeem_by"] = int(payload.expires_at.timestamp())

            coupon = stripe.Coupon.create(**coupon_params)
            stripe_coupon_id = coupon.id
        except stripe.StripeError:
            pass  # le coupon Stripe est optionnel, on continue

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


@router.delete("/admin/promo/{promo_id}")
def admin_delete_promo(
    promo_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    promo = db.execute(
        select(PromoCode).where(PromoCode.id == promo_id)
    ).scalar_one_or_none()
    if not promo:
        raise HTTPException(404, detail="Code promo introuvable.")

    # Archiver le coupon Stripe si existant
    if promo.stripe_coupon_id and STRIPE_SECRET_KEY:
        try:
            stripe.Coupon.delete(promo.stripe_coupon_id)
        except stripe.StripeError:
            pass

    db.delete(promo)
    db.commit()
    return {"ok": True, "deleted_code": promo.code}