# app/routers/discord_bot.py

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import BOT_SECRET
from app.db.database import get_db
from app.db.models import User

router = APIRouter(prefix="/discord", tags=["discord-bot"])


def _require_bot(x_bot_secret: str = Header(...)) -> None:
    if x_bot_secret != BOT_SECRET:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


class LinkDiscordIn(BaseModel):
    user_id: int
    discord_id: str


class ParticipationIn(BaseModel):
    discord_id: str
    message_count: int = 1


MESSAGES_PER_ELO = 3
ELO_PER_BATCH    = 1
STREAK_BONUS_ELO = 2


def _by_discord(db: Session, discord_id: str) -> Optional[User]:
    return db.query(User).filter(User.discord_id == discord_id).first()

def _today() -> date:
    return datetime.now(timezone.utc).date()

def _is_consecutive(last: date, today: date) -> bool:
    return (today - last).days == 1

def _elo_gain(pending: int) -> tuple[int, int]:
    batches   = pending // MESSAGES_PER_ELO
    remainder = pending % MESSAGES_PER_ELO
    return batches * ELO_PER_BATCH, remainder


@router.post("/link", dependencies=[Depends(_require_bot)])
def link_discord(body: LinkDiscordIn, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == body.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    conflict = _by_discord(db, body.discord_id)
    if conflict and conflict.id != body.user_id:
        raise HTTPException(status_code=409, detail="Discord ID already linked to another account")
    user.discord_id = body.discord_id
    db.commit()
    return {"ok": True, "user_id": user.id, "discord_id": body.discord_id}


@router.get("/user/{discord_id}", dependencies=[Depends(_require_bot)])
def get_discord_user(discord_id: str, db: Session = Depends(get_db)):
    user = _by_discord(db, discord_id)
    if not user:
        raise HTTPException(status_code=404, detail="No NAVIRE account linked")
    return {
        "user_id":                  user.id,
        "username":                 user.username,
        "plan":                     user.plan,
        "elo":                      user.elo,
        "discord_streak":           user.discord_streak or 0,
        "discord_messages_pending": user.discord_messages_pending or 0,
    }


@router.post("/participation", dependencies=[Depends(_require_bot)])
def record_participation(body: ParticipationIn, db: Session = Depends(get_db)):
    user = _by_discord(db, body.discord_id)
    if not user:
        return {"ok": False, "reason": "not_linked"}

    today = _today()
    last  = user.discord_last_active

    if last is None:
        user.discord_streak = 1
    elif last == today:
        pass
    elif _is_consecutive(last, today):
        user.discord_streak = (user.discord_streak or 0) + 1
    else:
        user.discord_streak = 1

    user.discord_last_active = today

    pending           = (user.discord_messages_pending or 0) + body.message_count
    gained, remaining = _elo_gain(pending)
    user.discord_messages_pending = remaining

    streak_bonus = 0
    if gained > 0 and (user.discord_streak or 0) >= 3:
        streak_bonus = STREAK_BONUS_ELO
        gained      += streak_bonus

    if gained > 0:
        user.elo = (user.elo or 0) + gained

    db.commit()
    db.refresh(user)

    return {
        "ok":           True,
        "elo_gained":   gained,
        "streak_bonus": streak_bonus,
        "new_elo":      user.elo,
        "streak":       user.discord_streak,
        "pending":      user.discord_messages_pending,
    }


@router.get("/leaderboard", dependencies=[Depends(_require_bot)])
def discord_leaderboard(limit: int = 10, db: Session = Depends(get_db)):
    limit = max(1, min(50, limit))
    rows  = (
        db.query(User)
        .filter(User.discord_id.isnot(None))
        .order_by(User.elo.desc())
        .limit(limit)
        .all()
    )
    return [
        {"rank": i + 1, "discord_id": u.discord_id, "username": u.username,
         "elo": u.elo or 0, "plan": u.plan}
        for i, u in enumerate(rows)
    ]