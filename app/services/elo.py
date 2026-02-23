from __future__ import annotations

from typing import Optional, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.db.models import User, EloEvent

QCM_DELTA = {
    "easy":   {"correct": +1, "wrong": -1},
    "medium": {"correct": +3, "wrong": -1},
    "hard":   {"correct": +5, "wrong": -2},
}

def compute_qcm_delta(difficulty: str, is_correct: bool) -> int:
    diff = (difficulty or "medium").strip()
    if diff not in QCM_DELTA:
        diff = "medium"
    key = "correct" if is_correct else "wrong"
    return int(QCM_DELTA[diff][key])

def apply_elo_delta(
    db: Session,
    *,
    user_id: int,
    delta: int,
    source: str,
    session_id: Optional[str] = None,
    question_index: Optional[int] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> int:
    """
    Applique delta à User.elo + log EloEvent.
    Idempotence simple: si un EloEvent existe déjà pour (user_id, source, session_id, question_index),
    on NE ré-applique pas.
    """
    meta = meta or {}

    # Idempotence guard (QCM: 1 event par question)
    if session_id is not None and question_index is not None:
        exists = db.execute(
            select(EloEvent).where(
                EloEvent.user_id == user_id,
                EloEvent.source == source,
                EloEvent.session_id == session_id,
                EloEvent.question_index == question_index,
            )
        ).scalar_one_or_none()
        if exists:
            # déjà appliqué -> on renvoie le score actuel
            u = db.execute(select(User).where(User.id == user_id)).scalar_one()
            return int(u.elo)

    u = db.execute(select(User).where(User.id == user_id)).scalar_one()

    u.elo = int(u.elo or 0) + int(delta)

    ev = EloEvent(
        user_id=user_id,
        source=source,
        delta=int(delta),
        session_id=session_id,
        question_index=question_index,
        meta=meta,
    )
    db.add(ev)
    db.commit()
    db.refresh(u)
    return int(u.elo)