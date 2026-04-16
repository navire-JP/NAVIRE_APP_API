import os
from fastapi import APIRouter, Header, HTTPException, Depends
from sqlalchemy.orm import Session
from sqlalchemy import select, desc

from app.db.database import get_db
from app.db import models

router = APIRouter(prefix="/admin", tags=["admin"])

ADMIN_CODE = "THORKISHERE"


def verify_admin_code(x_admin_code: str | None = Header(default=None)):
    """Vérifie le code admin passé en header."""
    if x_admin_code != ADMIN_CODE:
        raise HTTPException(status_code=401, detail="Invalid admin code")


@router.post("/make-admin")
def make_admin(
    email: str,
    x_api_key: str | None = Header(default=None),
):
    expected = os.getenv("API_KEY")
    if not expected or x_api_key != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")

    from app.db.database import SessionLocal

    db = SessionLocal()
    try:
        u = db.query(models.User).filter(models.User.email == email).first()
        if not u:
            raise HTTPException(status_code=404, detail="User not found")
        u.is_admin = True
        db.commit()
        return {"ok": True, "email": email, "is_admin": True}
    finally:
        db.close()


@router.get("/users")
def get_all_users(
    db: Session = Depends(get_db),
    _: None = Depends(verify_admin_code),
):
    """
    Liste tous les utilisateurs avec leurs infos d'abonnement.
    Requiert le header X-Admin-Code: THORKISHERE
    """
    users = (
        db.execute(
            select(models.User).order_by(desc(models.User.created_at))
        )
        .scalars()
        .all()
    )

    result = []
    for u in users:
        # Récupérer l'abonnement s'il existe
        sub = (
            db.execute(
                select(models.Subscription).where(models.Subscription.user_id == u.id)
            )
            .scalar_one_or_none()
        )

        # Détecter si manuel : pas de stripe_subscription_id mais plan payant
        plan = u.plan or "free"
        is_manual = False
        sub_status = None

        if sub:
            sub_status = sub.status
            # Manuel = plan payant sans ID Stripe
            if plan != "free" and not sub.stripe_subscription_id:
                is_manual = True

        result.append({
            "id": u.id,
            "username": u.username,
            "email": u.email,
            "elo": u.elo or 0,
            "universite": getattr(u, "university", None) or getattr(u, "universite", None),
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "subscription_tier": plan,
            "subscription_status": sub_status,
            "is_manual_subscription": is_manual,
        })

    return {
        "users": result,
        "total": len(result),
    }


@router.post("/set-subscription")
def set_manual_subscription(
    email: str,
    plan: str,
    x_admin_code: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """
    Attribue manuellement un abonnement à un utilisateur.
    Plans valides: free, membre, membre+
    """
    if x_admin_code != ADMIN_CODE:
        raise HTTPException(status_code=401, detail="Invalid admin code")

    if plan not in ["free", "membre", "membre+"]:
        raise HTTPException(status_code=400, detail="Invalid plan. Use: free, membre, membre+")

    user = db.execute(
        select(models.User).where(models.User.email == email)
    ).scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Chercher ou créer l'abonnement
    sub = db.execute(
        select(models.Subscription).where(models.Subscription.user_id == user.id)
    ).scalar_one_or_none()

    if plan == "free":
        # Supprimer l'abonnement ou le passer en free
        if sub:
            sub.plan = "free"
            sub.status = "cancelled"
            sub.billing_cycle = None
            sub.stripe_subscription_id = None
        user.plan = "free"
        db.commit()
        return {"ok": True, "email": email, "plan": "free", "is_manual": False}

    # Plan payant manuel
    if sub:
        sub.plan = plan
        sub.status = "active"
        sub.billing_cycle = None  # Manuel = pas de cycle
        # On ne touche PAS à stripe_subscription_id (reste None = manuel)
    else:
        sub = models.Subscription(
            user_id=user.id,
            plan=plan,
            status="active",
            billing_cycle=None,
            stripe_subscription_id=None,  # Manuel
        )
        db.add(sub)

    user.plan = plan
    db.commit()

    return {"ok": True, "email": email, "plan": plan, "is_manual": True}