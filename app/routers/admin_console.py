from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Literal

import stripe
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import select, desc

from app.db.database import get_db
from app.db.models import User, Subscription
from app.routers.auth import get_current_user
from app.services.elo import apply_elo_delta

# =========================================================
# Guard admin
# =========================================================
def require_admin(user: User = Depends(get_current_user)) -> User:
    if not getattr(user, "is_admin", False):
        raise HTTPException(status_code=403, detail="Admin only")
    return user


router = APIRouter(prefix="/admin", tags=["admin-console"])


# =========================================================
# Schemas
# =========================================================
class EloAddIn(BaseModel):
    delta: int = Field(..., description="Ex: +15 ou -3")

class EloSetIn(BaseModel):
    value: int = Field(..., description="Valeur absolue (ex: 200)")

class SetAdminIn(BaseModel):
    is_admin: bool = Field(..., description="True pour promouvoir, False pour révoquer")

class PlanAddIn(BaseModel):
    plan: Literal["free", "membre", "membre+"]


# =========================================================
# Helpers
# =========================================================
def get_user_or_404(db: Session, user_id: int) -> User:
    u = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
    if not u:
        raise HTTPException(404, detail="User not found")
    return u


# =========================================================
# USERS
# =========================================================
@router.get("/users")
def admin_users(
    q: str = "",
    limit: int = 25,
    offset: int = 0,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    limit = max(1, min(100, int(limit)))
    offset = max(0, int(offset))

    stmt = select(User).order_by(desc(User.created_at), User.id.asc()).limit(limit).offset(offset)

    if q.strip():
        qq = f"%{q.strip()}%"
        stmt = (
            select(User)
            .where(
                (User.email.like(qq)) | (User.username.like(qq))
            )
            .order_by(desc(User.created_at), User.id.asc())
            .limit(limit)
            .offset(offset)
        )

    rows = db.execute(stmt).scalars().all()

    return {
        "items": [
            {
                "id": u.id,
                "email": u.email,
                "username": u.username,
                "elo": int(u.elo or 0),
                "plan": u.plan,
                "is_admin": bool(u.is_admin),
                "created_at": (u.created_at.isoformat() if u.created_at else None),
            }
            for u in rows
        ],
        "limit": limit,
        "offset": offset,
    }


@router.get("/users/{user_id}")
def admin_user_get(
    user_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    u = get_user_or_404(db, user_id)
    return {
        "id": u.id,
        "email": u.email,
        "username": u.username,
        "university": u.university,
        "study_level": u.study_level,
        "elo": int(u.elo or 0),
        "plan": u.plan,
        "is_admin": bool(u.is_admin),
        "created_at": (u.created_at.isoformat() if u.created_at else None),
        "last_login_at": (u.last_login_at.isoformat() if u.last_login_at else None),
    }


# =========================================================
# SET ADMIN
# =========================================================
@router.post("/users/{user_id}/set-admin")
def admin_user_set_admin(
    user_id: int,
    body: SetAdminIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Deux cas d'usage :
    - Premier admin : le user cible SE promeut lui-même via le code THORKISHERE
      (vérifié côté front, pas de garde admin ici pour permettre l'auto-promotion)
    - Admin existant : peut promouvoir / révoquer n'importe quel user
      (la vérification is_admin est faite si le user n'est pas lui-même la cible)
    """
    u = get_user_or_404(db, user_id)

    # Autorisation : soit l'utilisateur se promeut lui-même, soit c'est un admin
    if current_user.id != user_id and not getattr(current_user, "is_admin", False):
        raise HTTPException(status_code=403, detail="Admin only")

    u.is_admin = body.is_admin
    db.commit()

    return {
        "user_id": u.id,
        "username": u.username,
        "is_admin": bool(u.is_admin),
    }


# =========================================================
# ELO COMMANDS
# =========================================================
@router.post("/users/{user_id}/elo/add")
def admin_user_elo_add(
    user_id: int,
    body: EloAddIn,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    _ = get_user_or_404(db, user_id)

    new_elo = apply_elo_delta(
        db,
        user_id=user_id,
        delta=int(body.delta),
        source="admin",
        session_id=None,
        question_index=None,
        meta={"by_admin_id": admin.id, "cmd": "elo_add"},
    )
    return {"user_id": user_id, "delta": int(body.delta), "new_elo": int(new_elo)}


@router.post("/users/{user_id}/elo/set")
def admin_user_elo_set(
    user_id: int,
    body: EloSetIn,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    u = get_user_or_404(db, user_id)

    target = int(body.value)
    current = int(u.elo or 0)
    delta = target - current

    new_elo = apply_elo_delta(
        db,
        user_id=user_id,
        delta=int(delta),
        source="admin",
        session_id=None,
        question_index=None,
        meta={"by_admin_id": admin.id, "cmd": "elo_set", "set_to": target},
    )
    return {"user_id": user_id, "set_to": target, "delta": int(delta), "new_elo": int(new_elo)}


@router.post("/users/{user_id}/elo/reset")
def admin_user_elo_reset(
    user_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    u = get_user_or_404(db, user_id)

    current = int(u.elo or 0)
    new_elo = apply_elo_delta(
        db,
        user_id=user_id,
        delta=-current,
        source="admin",
        session_id=None,
        question_index=None,
        meta={"by_admin_id": admin.id, "cmd": "elo_reset"},
    )
    return {"user_id": user_id, "new_elo": int(new_elo)}


# =========================================================
# PLAN COMMANDS
# =========================================================

def _get_or_create_sub(db: Session, user: User) -> Subscription:
    sub = db.execute(
        select(Subscription).where(Subscription.user_id == user.id)
    ).scalar_one_or_none()
    if not sub:
        sub = Subscription(user_id=user.id, plan="free", status="active")
        db.add(sub)
        db.commit()
        db.refresh(sub)
    return sub


@router.get("/users/{user_id}/plan")
def admin_user_plan_get(
    user_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """
    Affiche le plan actuel de l'user + détails Stripe si disponibles.
    Commande console : /plan <id>
    """
    u = get_user_or_404(db, user_id)
    sub = _get_or_create_sub(db, u)

    stripe_info = None
    if sub.stripe_subscription_id:
        try:
            from app.core.config import STRIPE_SECRET_KEY
            stripe.api_key = STRIPE_SECRET_KEY
            s = stripe.Subscription.retrieve(sub.stripe_subscription_id)
            stripe_info = {
                "status": s.get("status"),
                "current_period_end": datetime.fromtimestamp(
                    s["current_period_end"], tz=timezone.utc
                ).isoformat() if s.get("current_period_end") else None,
                "cancel_at_period_end": s.get("cancel_at_period_end"),
            }
        except Exception as e:
            stripe_info = {"error": str(e)}

    return {
        "user_id": u.id,
        "username": u.username,
        "email": u.email,
        "plan": u.plan,
        "billing_cycle": sub.billing_cycle,
        "status": sub.status,
        "is_manual": sub.stripe_subscription_id is None and u.plan != "free",
        "current_period_end": sub.current_period_end.isoformat() if sub.current_period_end else None,
        "stripe_subscription_id": sub.stripe_subscription_id,
        "stripe_customer_id": sub.stripe_customer_id,
        "stripe": stripe_info,
    }


@router.post("/users/{user_id}/plan/add")
def admin_user_plan_add(
    user_id: int,
    body: PlanAddIn,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """
    Attribue un plan manuel pour 1 mois (sans Stripe).
    Règles :
    - Impossible si l'user a déjà un abonnement Stripe actif
    - Impossible si l'user a déjà un plan manuel actif (utiliser /plan clear d'abord)
    Commande console : /plan <id> add <free|M|M+>
    """
    u = get_user_or_404(db, user_id)
    sub = _get_or_create_sub(db, u)

    # Bloquer si abonnement Stripe actif
    if sub.stripe_subscription_id and sub.status == "active":
        raise HTTPException(400, detail={
            "code": "STRIPE_ACTIVE",
            "message": f"Cet user a un abonnement Stripe actif ({sub.plan}). Impossible d'attribuer un plan manuel.",
        })

    # Bloquer si plan manuel encore actif
    if sub.stripe_subscription_id is None and u.plan != "free" and sub.current_period_end:
        now = datetime.now(timezone.utc)
        if sub.current_period_end > now:
            raise HTTPException(400, detail={
                "code": "MANUAL_PLAN_ACTIVE",
                "message": f"Plan manuel {u.plan} actif jusqu'au {sub.current_period_end.strftime('%d/%m/%Y')}. Fais /plan {user_id} clear d'abord.",
            })

    plan = body.plan
    now = datetime.now(timezone.utc)

    sub.plan = plan
    sub.billing_cycle = "monthly"
    sub.status = "active"
    sub.stripe_subscription_id = None  # pas géré par Stripe
    sub.current_period_start = now
    sub.current_period_end = now + timedelta(days=30)
    sub.cancelled_at = None
    db.commit()

    u.plan = plan
    db.commit()

    return {
        "ok": True,
        "user_id": u.id,
        "plan": plan,
        "valid_until": sub.current_period_end.isoformat(),
        "managed_by": "admin_manual",
    }


@router.post("/users/{user_id}/plan/clear")
def admin_user_plan_clear(
    user_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """
    Retire un plan manuel et repasse l'user en free.
    Ne touche pas aux abonnements Stripe.
    Commande console : /plan <id> clear
    """
    u = get_user_or_404(db, user_id)
    sub = _get_or_create_sub(db, u)

    if sub.stripe_subscription_id:
        raise HTTPException(400, detail={
            "code": "STRIPE_MANAGED",
            "message": "Cet abonnement est géré par Stripe. Utilise le portail Stripe ou /subscriptions/cancel.",
        })

    sub.plan = "free"
    sub.billing_cycle = None
    sub.status = "cancelled"
    sub.current_period_start = None
    sub.current_period_end = None
    sub.cancelled_at = datetime.now(timezone.utc)
    db.commit()

    u.plan = "free"
    db.commit()

    return {"ok": True, "user_id": u.id, "plan": "free"}