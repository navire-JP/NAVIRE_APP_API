# app/services/deepwork.py
"""
Deep work — sessions de travail dans le salon vocal Discord dédié.

Le bot ne fait que signaler les entrées/sorties du vocal : toute la mesure du
temps est faite ici, à partir de `started_at` posé en base au démarrage. Une
horloge côté bot pourrait dériver ou être rejouée, celle-ci non.

Vocabulaire :
  session   — un passage dans le vocal deep work (une ligne DeepWorkSession)
  objectif  — durée visée choisie dans le MP (20 min, 1 h, 1 h 30, 2 h)
  atteint   — la session a duré au moins l'objectif choisi
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import DeepWorkSession, User

# Objectifs proposés dans le MP, en minutes. L'ordre est celui des boutons.
GOAL_CHOICES: list[int] = [20, 60, 90, 120]

# En dessous de ce seuil, un passage dans le vocal n'est pas une session de
# travail (aller-retour, erreur de salon) : la ligne est marquée `discarded` et
# n'entre dans aucune statistique.
MIN_SESSION_SECONDS = 60

# Une session ouverte depuis plus longtemps que ça n'a pas été fermée
# proprement (crash du bot, coupure réseau) : sa durée réelle est inconnue.
MAX_SESSION_HOURS = 14

STATUS_ACTIVE    = "active"
STATUS_COMPLETED = "completed"
STATUS_DISCARDED = "discarded"
STATUS_STALE     = "stale"


# ============================================================
# Helpers
# ============================================================

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    """
    Postgres renvoie bien des datetimes aware, SQLite non. On normalise pour
    que les soustractions ne lèvent pas « can't subtract offset-naive and
    offset-aware datetimes » selon la base utilisée.
    """
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def format_duration(seconds: int) -> str:
    """3 725 → « 1 h 02 », 900 → « 15 min »."""
    seconds = max(0, int(seconds))
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60} h {minutes % 60:02d}"


def goal_label(goal_minutes: int | None) -> str:
    if not goal_minutes:
        return "Session libre"
    return format_duration(goal_minutes * 60)


def user_by_discord(db: Session, discord_id: str) -> Optional[User]:
    return db.query(User).filter(User.discord_id == discord_id).first()


# ============================================================
# Cycle de vie d'une session
# ============================================================

def close_stale_sessions(db: Session, keep_session_ids: list[int] | None = None) -> int:
    """
    Marque `stale` toute session encore `active` — sauf celles explicitement
    conservées. Appelé au démarrage du bot : les sessions actives d'avant le
    redémarrage n'ont plus de chronomètre attaché, leur durée est inconnue.

    Aussi utilisé en garde-fou pour les sessions ouvertes depuis plus de
    MAX_SESSION_HOURS (membre resté connecté toute la nuit, micro coupé).
    """
    keep = set(keep_session_ids or [])
    rows = db.query(DeepWorkSession).filter(DeepWorkSession.status == STATUS_ACTIVE).all()

    closed = 0
    for row in rows:
        if row.id in keep:
            continue
        row.status = STATUS_STALE
        row.ended_at = _now()
        started = _aware(row.started_at)
        row.duration_seconds = int((row.ended_at - started).total_seconds()) if started else 0
        closed += 1

    if closed:
        db.commit()
    return closed


def expire_long_sessions(db: Session) -> int:
    """Clôture en `stale` les sessions actives ouvertes depuis trop longtemps."""
    cutoff = _now() - timedelta(hours=MAX_SESSION_HOURS)
    rows = (
        db.query(DeepWorkSession)
        .filter(DeepWorkSession.status == STATUS_ACTIVE, DeepWorkSession.started_at < cutoff)
        .all()
    )
    for row in rows:
        row.status = STATUS_STALE
        row.ended_at = _now()
        started = _aware(row.started_at)
        row.duration_seconds = int((row.ended_at - started).total_seconds()) if started else 0
    if rows:
        db.commit()
    return len(rows)


def start_session(
    db: Session,
    user: User,
    *,
    discord_id: str,
    guild_id: str = "",
    channel_id: str = "",
) -> DeepWorkSession:
    """
    Ouvre une session. Une éventuelle session déjà active pour ce membre est
    clôturée d'abord : un utilisateur n'a jamais deux chronomètres en cours
    (reconnexion rapide, salon rejoint depuis deux appareils).
    """
    previous = (
        db.query(DeepWorkSession)
        .filter(
            DeepWorkSession.user_id == user.id,
            DeepWorkSession.status == STATUS_ACTIVE,
        )
        .all()
    )
    for row in previous:
        _finalize(row)

    session = DeepWorkSession(
        user_id=user.id,
        discord_id=discord_id,
        guild_id=guild_id or "",
        channel_id=channel_id or "",
        started_at=_now(),
        status=STATUS_ACTIVE,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def set_goal(db: Session, session: DeepWorkSession, goal_minutes: int | None) -> DeepWorkSession:
    """
    Enregistre l'objectif choisi. `None` = session libre (choix explicite de
    ne pas se fixer d'objectif) — c'est une réponse valide, pas une absence.
    """
    if goal_minutes is not None and goal_minutes not in GOAL_CHOICES:
        raise ValueError("Objectif non proposé")

    session.goal_minutes = goal_minutes
    session.goal_set_at = _now()

    # L'objectif est modifiable en cours de session : on recalcule dans les
    # deux sens. Objectif raccourci alors que le temps est déjà fait = atteint
    # d'emblée ; objectif rallongé = de nouveau à atteindre.
    reached = bool(goal_minutes) and elapsed_seconds(session) >= goal_minutes * 60
    session.goal_reached = reached
    session.goal_reached_at = _now() if reached else None

    db.commit()
    db.refresh(session)
    return session


def elapsed_seconds(session: DeepWorkSession) -> int:
    """Temps écoulé pour une session active, durée figée sinon."""
    if session.status != STATUS_ACTIVE and session.duration_seconds:
        return int(session.duration_seconds)
    started = _aware(session.started_at)
    if not started:
        return 0
    return max(0, int((_now() - started).total_seconds()))


def mark_goal_reached(db: Session, session: DeepWorkSession) -> DeepWorkSession:
    """
    Note le franchissement de l'objectif au moment où il se produit, sans
    attendre la fin de session : le MP l'annonce en direct, et l'information
    survit à un arrêt du bot juste après.
    """
    if session.goal_reached or not session.goal_minutes:
        return session
    if elapsed_seconds(session) < session.goal_minutes * 60:
        return session

    session.goal_reached = True
    session.goal_reached_at = _now()
    db.commit()
    db.refresh(session)
    return session


def _finalize(session: DeepWorkSession) -> None:
    """Pose ended_at / duration_seconds / status. Ne commit pas."""
    session.ended_at = _now()
    started = _aware(session.started_at)
    duration = int((session.ended_at - started).total_seconds()) if started else 0
    session.duration_seconds = max(0, duration)

    if session.duration_seconds < MIN_SESSION_SECONDS:
        session.status = STATUS_DISCARDED
        return

    session.status = STATUS_COMPLETED
    if session.goal_minutes and session.duration_seconds >= session.goal_minutes * 60:
        session.goal_reached = True
        if not session.goal_reached_at:
            session.goal_reached_at = session.ended_at


def stop_session(db: Session, session: DeepWorkSession) -> DeepWorkSession:
    """Clôture la session (quitte le vocal). Idempotent."""
    if session.status == STATUS_ACTIVE:
        _finalize(session)
        db.commit()
        db.refresh(session)
    return session


def get_active_session(db: Session, user_id: int) -> Optional[DeepWorkSession]:
    return (
        db.query(DeepWorkSession)
        .filter(DeepWorkSession.user_id == user_id, DeepWorkSession.status == STATUS_ACTIVE)
        .order_by(DeepWorkSession.started_at.desc())
        .first()
    )


def get_session(db: Session, session_id: int) -> Optional[DeepWorkSession]:
    return db.query(DeepWorkSession).filter(DeepWorkSession.id == session_id).first()


# ============================================================
# Statistiques
# ============================================================

def _streak_days(days: list[date]) -> tuple[int, int]:
    """
    (streak en cours, meilleur streak) à partir des jours distincts travaillés.

    Le streak en cours tolère un jour non terminé : une série qui va jusqu'à
    hier reste vivante tant qu'aujourd'hui n'est pas fini.
    """
    if not days:
        return 0, 0

    ordered = sorted(set(days))

    best = run = 1
    for prev, cur in zip(ordered, ordered[1:]):
        run = run + 1 if (cur - prev).days == 1 else 1
        best = max(best, run)

    today = datetime.now(timezone.utc).date()
    last = ordered[-1]
    if (today - last).days > 1:
        return 0, best

    current = 1
    for prev, cur in zip(reversed(ordered[:-1]), reversed(ordered)):
        if (cur - prev).days == 1:
            current += 1
        else:
            break
    return current, best


def compute_stats(db: Session, user_id: int) -> dict:
    """
    Agrégats affichés dans le profil et dans le récapitulatif de fin de
    session. Seules les sessions `completed` sont comptées.
    """
    base = [
        DeepWorkSession.user_id == user_id,
        DeepWorkSession.status == STATUS_COMPLETED,
    ]

    totals = db.execute(
        select(
            func.count(DeepWorkSession.id),
            func.coalesce(func.sum(DeepWorkSession.duration_seconds), 0),
            func.coalesce(func.max(DeepWorkSession.duration_seconds), 0),
        ).where(*base)
    ).one()
    total_sessions, total_seconds, longest_seconds = int(totals[0]), int(totals[1]), int(totals[2])

    goals_set = db.execute(
        select(func.count(DeepWorkSession.id)).where(*base, DeepWorkSession.goal_minutes.isnot(None))
    ).scalar_one()
    goals_reached = db.execute(
        select(func.count(DeepWorkSession.id)).where(*base, DeepWorkSession.goal_reached.is_(True))
    ).scalar_one()
    goals_set, goals_reached = int(goals_set or 0), int(goals_reached or 0)

    now = _now()
    week_seconds = db.execute(
        select(func.coalesce(func.sum(DeepWorkSession.duration_seconds), 0)).where(
            *base, DeepWorkSession.started_at >= now - timedelta(days=7)
        )
    ).scalar_one()

    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_seconds = db.execute(
        select(func.coalesce(func.sum(DeepWorkSession.duration_seconds), 0)).where(
            *base, DeepWorkSession.started_at >= today_start
        )
    ).scalar_one()

    # Streaks : jours distincts avec au moins une session comptée. Le calcul se
    # fait en Python plutôt qu'en SQL pour rester portable Postgres/SQLite.
    started_rows = db.execute(
        select(DeepWorkSession.started_at).where(*base).order_by(DeepWorkSession.started_at)
    ).scalars().all()
    days = [_aware(d).date() for d in started_rows if d]
    current_streak, best_streak = _streak_days(days)

    return {
        "total_sessions":     total_sessions,
        "total_seconds":      total_seconds,
        "total_minutes":      total_seconds // 60,
        "total_label":        format_duration(total_seconds),
        "today_seconds":      int(today_seconds or 0),
        "today_label":        format_duration(int(today_seconds or 0)),
        "week_seconds":       int(week_seconds or 0),
        "week_label":         format_duration(int(week_seconds or 0)),
        "longest_seconds":    longest_seconds,
        "longest_label":      format_duration(longest_seconds),
        "average_seconds":    total_seconds // total_sessions if total_sessions else 0,
        "average_label":      format_duration(total_seconds // total_sessions) if total_sessions else "0 min",
        "goals_set":          goals_set,
        "goals_reached":      goals_reached,
        "goal_success_rate":  round(goals_reached / goals_set * 100, 1) if goals_set else 0.0,
        "current_streak":     current_streak,
        "best_streak":        best_streak,
    }


def serialize_session(session: DeepWorkSession) -> dict:
    started = _aware(session.started_at)
    ended = _aware(session.ended_at)
    return {
        "id":               session.id,
        "started_at":       started.isoformat() if started else None,
        "ended_at":         ended.isoformat() if ended else None,
        "duration_seconds": int(session.duration_seconds or 0),
        "duration_label":   format_duration(session.duration_seconds or 0),
        "goal_minutes":     session.goal_minutes,
        "goal_label":       goal_label(session.goal_minutes),
        "goal_reached":     bool(session.goal_reached),
        "status":           session.status,
    }


def recent_sessions(db: Session, user_id: int, limit: int = 10) -> list[dict]:
    rows = (
        db.query(DeepWorkSession)
        .filter(
            DeepWorkSession.user_id == user_id,
            DeepWorkSession.status == STATUS_COMPLETED,
        )
        .order_by(DeepWorkSession.started_at.desc())
        .limit(max(1, min(50, limit)))
        .all()
    )
    return [serialize_session(r) for r in rows]
