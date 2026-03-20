"""
app/routers/subscriptions.py
=============================
Router abonnements — Phase 1 (logique grades, sans Stripe).

Endpoints utilisateur :
  GET  /subscriptions/me              → plan actuel + limites
  POST /subscriptions/upgrade         → souscrire à un plan
  POST /subscriptions/cancel          → résilier → retour free
  GET  /subscriptions/promo/{code}    → valider un code promo

Endpoints admin :
  GET    /subscriptions/admin/list                   → tous les abonnements actifs
  POST   /subscriptions/admin/set-plan/{user_id}     → forcer un plan sur un user
  GET    /subscriptions/admin/promo                  → lister les codes promo
  POST   /subscriptions/admin/promo                  → créer un code promo
  PATCH  /subscriptions/admin/promo/{promo_id}       → modifier un code promo
  DELETE /subscriptions/admin/promo/{promo_id}       → supprimer un code promo

Phase 2 (Stripe) : les champs stripe_* des modèles sont déjà prêts.
Les webhooks Stripe viendront mettre à jour subscription.status et user.plan.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import select, desc

from app.db.database import get_db
from app.db.models import User, Subscription, PromoCode, PromoRedemption
from app.routers.auth import get_current_user
from app.core.limits import get_limits, PLAN_LIMITS

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
    """
    Retourne la subscription de l'user, ou en crée une free si elle n'existe pas.
    Utilisé pour garantir que chaque user a toujours une ligne en base.
    """
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
    """
    Met à jour le cache User.plan pour qu'il soit en sync
    avec Subscription.plan. Toujours appeler après un changement de plan.
    """
    user.plan = plan
    db.commit()


def _validate_promo(
    db: Session,
    user: User,
    code: str,
    billing_cycle: str,
) -> PromoCode:
    """
    Vérifie qu'un code promo est utilisable par cet user pour ce cycle.
    Lève une HTTPException 400 descriptive sinon.
    Retourne le PromoCode si valide.
    """
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

    # Vérifier que l'user ne l'a pas déjà utilisé
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
    redemption = PromoRedemption(
        user_id=user.id,
        promo_code_id=promo.id,
    )
    db.add(redemption)
    promo.uses_count += 1
    db.commit()


# ============================================================
# Schemas
# ============================================================

class UpgradeIn(BaseModel):
    plan: Literal["membre", "membre+"] = Field(..., description="Plan cible")
    billing_cycle: Literal["monthly", "annual"] = Field(..., description="Cycle de facturation")
    promo_code: str | None = Field(None, description="Code promo optionnel")


class SetPlanIn(BaseModel):
    plan: Literal["free", "membre", "membre+"]
    billing_cycle: Literal["monthly", "annual"] | None = None
    note: str | None = Field(None, description="Raison admin (non exposée à l'user)")


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
    """
    Retourne le plan actuel de l'utilisateur, son statut d'abonnement
    et toutes ses limites (QCM, flashcards, fichiers).
    """
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


@router.post("/upgrade")
def upgrade_plan(
    payload: UpgradeIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Souscrit à un plan payant.

    Phase 1 : logique manuelle (pas de Stripe).
    Phase 2 : ce endpoint créera une Stripe Checkout Session et retournera
              une checkout_url pour rediriger l'user.

    Règles :
    - Impossible de payer si on a déjà un plan actif payant (évite les doublons).
    - Le code promo est validé et enregistré si fourni.
    - User.plan et Subscription sont mis à jour immédiatement.
    """
    sub = get_or_create_subscription(db, user)

    # Bloquer si déjà abonné (même plan ou plan supérieur)
    if sub.status == "active" and user.plan != "free":
        raise HTTPException(400, detail={
            "code": "ALREADY_SUBSCRIBED",
            "message": f"Vous avez déjà un abonnement actif ({user.plan}). Résiliez-le avant d'en souscrire un nouveau.",
        })

    # Validation et application du code promo si fourni
    promo = None
    if payload.promo_code:
        promo = _validate_promo(db, user, payload.promo_code, payload.billing_cycle)

    # ── Phase 1 : mise à jour directe (sans paiement réel) ──
    # Phase 2 : ici on appellera stripe.checkout.sessions.create(...)
    sub.plan = payload.plan
    sub.billing_cycle = payload.billing_cycle
    sub.status = "active"
    sub.cancelled_at = None

    # Phase 2 remplira ces champs via webhook Stripe
    # sub.stripe_subscription_id = ...
    # sub.current_period_start = ...
    # sub.current_period_end = ...

    db.commit()

    # Sync cache User.plan
    _sync_user_plan(db, user, payload.plan)

    # Enregistrer le promo si valide
    if promo:
        _apply_promo(db, user, promo)

    limits = get_limits(payload.plan)

    return {
        "ok": True,
        "plan": payload.plan,
        "billing_cycle": payload.billing_cycle,
        "promo_applied": promo.code if promo else None,
        "limits": {
            "qcm_per_day": limits["qcm_per_day"],
            "flashcards_total": limits["flashcards_total"],
            "files_total": limits["files_total"],
            "file_ttl_hours": limits["file_ttl_hours"],
        },
        # Phase 2 : "checkout_url": "https://checkout.stripe.com/..."
    }


@router.post("/cancel")
def cancel_plan(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Résilie l'abonnement de l'utilisateur et le repasse en free.

    Phase 1 : downgrade immédiat.
    Phase 2 : appellera stripe.subscriptions.cancel(...) et le downgrade
              se fera via webhook customer.subscription.deleted.
    """
    sub = get_or_create_subscription(db, user)

    if user.plan == "free":
        raise HTTPException(400, detail={
            "code": "ALREADY_FREE",
            "message": "Vous êtes déjà sur le plan gratuit.",
        })

    sub.plan = "free"
    sub.billing_cycle = None
    sub.status = "cancelled"
    sub.cancelled_at = utcnow()
    sub.current_period_end = None
    db.commit()

    _sync_user_plan(db, user, "free")

    return {
        "ok": True,
        "plan": "free",
        "message": "Abonnement résilié. Vous repassez sur le plan gratuit.",
    }


@router.get("/promo/{code}")
def validate_promo_code(
    code: str,
    billing_cycle: Literal["monthly", "annual"] = "monthly",
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Valide un code promo sans l'appliquer.
    Utilisé pour afficher la remise en temps réel dans le formulaire Framer.
    """
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
    """Liste tous les abonnements avec filtres optionnels."""
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
    """
    Forcer un plan sur n'importe quel utilisateur.
    Utile pour : comptes de test, offres commerciales, corrections manuelles.
    """
    target = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
    if not target:
        raise HTTPException(404, detail="Utilisateur introuvable.")

    sub = get_or_create_subscription(db, target)

    sub.plan = payload.plan
    sub.billing_cycle = payload.billing_cycle
    sub.status = "active" if payload.plan != "free" else "active"
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
    """Liste tous les codes promo."""
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
    """Crée un nouveau code promo."""
    # Vérifier unicité du code
    existing = db.execute(
        select(PromoCode).where(PromoCode.code == payload.code.upper())
    ).scalar_one_or_none()

    if existing:
        raise HTTPException(400, detail={
            "code": "PROMO_CODE_EXISTS",
            "message": f"Le code '{payload.code.upper()}' existe déjà.",
        })

    promo = PromoCode(
        code=payload.code.upper(),
        discount_type=payload.discount_type,
        discount_value=payload.discount_value,
        applies_to=payload.applies_to,
        max_uses=payload.max_uses,
        expires_at=payload.expires_at,
        is_active=True,
        created_by=admin.id,
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
    }


@router.patch("/admin/promo/{promo_id}")
def admin_update_promo(
    promo_id: int,
    payload: PromoUpdateIn,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Modifie un code promo existant (activer/désactiver, changer les uses, etc.)."""
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
    """Supprime un code promo (et ses redemptions en cascade)."""
    promo = db.execute(
        select(PromoCode).where(PromoCode.id == promo_id)
    ).scalar_one_or_none()

    if not promo:
        raise HTTPException(404, detail="Code promo introuvable.")

    db.delete(promo)
    db.commit()

    return {"ok": True, "deleted_code": promo.code}