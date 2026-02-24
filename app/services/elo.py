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



def tier_from_elo(elo: int) -> dict:
    elo = int(elo or 0)

    tiers = [
        (0, 19,   "starter"),
        (20, 49,  "junior bronze"),
        (50, 79,  "junior argent"),
        (80, 119, "junior or"),
        (120, 179,"auditor bronze"),
        (180, 259,"auditor argent"),
        (260, 349,"auditor or"),
        (350, 449,"consultant bronze"),
        (450, 559,"consultant argent"),
        (560, 679,"consultant or"),
        (680, 700,"senior"),
        (701, 899,"senior+"),
        (900, 1199,"partner"),
        (1200, 10**9, "polaris"),
    ]

    for lo, hi, name in tiers:
        if lo <= elo <= hi:
            next_lo = None
            next_name = None
            for lo2, hi2, name2 in tiers:
                if lo2 > lo:
                    next_lo = lo2
                    next_name = name2
                    break

            # progress intra-tier (0..1) utile pour une barre
            span = max(1, hi - lo + 1)
            progress = (elo - lo) / span

            return {
                "tier": name,
                "tier_min": lo,
                "tier_max": hi if hi < 10**9 else None,
                "next_tier_min": next_lo,
                "next_tier": next_name,
                "progress": float(max(0.0, min(1.0, progress))),
            }

    # fallback (ne devrait jamais arriver)
    return {"tier": "starter", "tier_min": 0, "tier_max": 19, "next_tier_min": 20, "next_tier": "junior bronze", "progress": 0.0}

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