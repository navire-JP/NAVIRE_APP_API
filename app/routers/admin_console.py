from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import select, desc

from app.db.database import get_db
from app.db.models import User
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
        # SQLite: ilike peut ne pas exister selon config -> on utilise like + lower
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