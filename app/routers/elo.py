from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import select, desc, func

from app.db.database import get_db
from app.db.models import User
from app.routers.auth import get_current_user

router = APIRouter(prefix="/elo", tags=["elo"])

@router.get("/me")
def me(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    u = db.execute(select(User).where(User.id == user.id)).scalar_one()
    # rank: nb d'utilisateurs avec elo strictement supérieur + 1
    rank = db.execute(select(func.count()).select_from(User).where(User.elo > u.elo)).scalar_one()
    return {
        "user_id": u.id,
        "username": u.username,
        "elo": int(u.elo or 0),
        "rank": int(rank or 0) + 1,
    }

@router.get("/leaderboard")
def leaderboard(
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    limit = max(1, min(200, int(limit)))
    offset = max(0, int(offset))

    rows = db.execute(
        select(User.id, User.username, User.elo)
        .order_by(desc(User.elo), User.id.asc())
        .limit(limit)
        .offset(offset)
    ).all()

    out = []
    for i, r in enumerate(rows):
        out.append({
            "rank": offset + i + 1,
            "user_id": r.id,
            "username": r.username,
            "elo": int(r.elo or 0),
        })
    return {"items": out, "limit": limit, "offset": offset}