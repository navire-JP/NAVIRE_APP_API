"""
Crédits NAVIRE AI : solde mensuel pour les modèles Avancé et Expert.

Solde du mois = crédits du plan (limits.py → navire_credits_month)
              + crédits offerts ce mois-ci par un admin
              − crédits dépensés ce mois-ci.
Rien n'est reporté d'un mois sur l'autre. Une question n'est débitée
qu'une fois la réponse obtenue : une erreur ne coûte rien.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.limits import get_user_limit
from app.db.models import NavireCreditEvent, User


def period_key(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return f"{now.year:04d}-{now.month:02d}"


def next_reset(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    year, month = (now.year + 1, 1) if now.month == 12 else (now.year, now.month + 1)
    return datetime(year, month, 1, tzinfo=timezone.utc)


def monthly_allowance(user: User) -> int:
    return int(get_user_limit(user, "navire_credits_month") or 0)


def summary(db: Session, user: User) -> dict:
    period = period_key()
    rows = db.execute(
        select(NavireCreditEvent.kind, func.coalesce(func.sum(NavireCreditEvent.delta), 0))
        .where(NavireCreditEvent.user_id == user.id, NavireCreditEvent.period == period)
        .group_by(NavireCreditEvent.kind)
    ).all()
    sums = {k: int(v or 0) for k, v in rows}
    allowance = monthly_allowance(user)
    bonus = sums.get("bonus", 0)
    used = -sums.get("usage", 0)
    return {
        "allowance": allowance,
        "bonus": bonus,
        "used": used,
        "balance": max(0, allowance + bonus - used),
        "period": period,
        "resets_at": next_reset().isoformat(),
    }


def balance(db: Session, user: User) -> int:
    return summary(db, user)["balance"]


def debit(db: Session, user: User, cost: int, tier: str, model: str, conversation_id: int | None) -> None:
    if cost <= 0:
        return
    db.add(NavireCreditEvent(
        user_id=user.id, period=period_key(), delta=-int(cost), kind="usage",
        tier=tier, model=model[:64] if model else None, conversation_id=conversation_id,
    ))
    db.commit()


def grant(db: Session, user: User, amount: int, admin: User, reason: str = "") -> None:
    """Crédits offerts par un admin, valables jusqu'à la fin du mois."""
    db.add(NavireCreditEvent(
        user_id=user.id, period=period_key(), delta=int(amount), kind="bonus",
        reason=(reason or "").strip()[:200] or None, granted_by_id=admin.id,
    ))
    db.commit()
