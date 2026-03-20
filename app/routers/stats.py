# app/routers/stats.py
from __future__ import annotations

from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import User, QcmSessionHistory
from app.routers.auth import get_current_user

router = APIRouter(prefix="/stats", tags=["stats"])


# =========================================================
# Time helpers
# =========================================================

def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _to_local_date(dt: datetime, tz_offset_minutes: int) -> str:
    """
    Convertit un datetime UTC en date locale (YYYY-MM-DD)
    selon l'offset en minutes fourni par le client (ex: -120 pour UTC+2).
    On utilise un offset simple car pytz n'est pas requis.
    """
    local_dt = dt + timedelta(minutes=-tz_offset_minutes)
    return local_dt.strftime("%Y-%m-%d")


# =========================================================
# Helpers communs
# =========================================================

def _get_user_history(db: Session, user_id: int) -> list[QcmSessionHistory]:
    """Retourne toutes les entrées d'historique QCM de l'utilisateur."""
    return db.execute(
        select(QcmSessionHistory)
        .where(QcmSessionHistory.user_id == user_id)
        .order_by(QcmSessionHistory.started_at.asc())
    ).scalars().all()


def _completed_only(history: list[QcmSessionHistory]) -> list[QcmSessionHistory]:
    """Filtre : uniquement les sessions complètes (5/5 questions)."""
    return [h for h in history if h.is_complete]


# =========================================================
# Calcul des streaks
# =========================================================

def _compute_streaks(
    history: list[QcmSessionHistory],
    tz_offset_minutes: int,
) -> dict:
    """
    Calcule à partir des sessions complètes :
    - current_streak  : nombre de jours consécutifs jusqu'à aujourd'hui (ou hier)
    - longest_streak  : record historique
    - total_days      : nombre de jours distincts avec au moins 1 session complète
    - last_activity   : date ISO de la dernière session complète (timezone locale)

    Règle streak : un jour compte si au moins 1 session complète ce jour-là.
    Le streak est maintenu si l'utilisateur a joué aujourd'hui OU hier
    (pour ne pas pénaliser les fuseaux horaires et les sessions de fin de journée).
    """
    completed = _completed_only(history)
    if not completed:
        return {
            "current_streak": 0,
            "longest_streak": 0,
            "total_days": 0,
            "last_activity": None,
        }

    # Ensemble des dates locales avec au moins une session complète
    active_dates: set[str] = set()
    for h in completed:
        if h.completed_at:
            active_dates.add(_to_local_date(h.completed_at, tz_offset_minutes))

    sorted_dates = sorted(active_dates)  # ["2026-01-01", "2026-01-02", ...]
    total_days = len(sorted_dates)
    last_activity = sorted_dates[-1]

    # Calcul du streak courant
    today_str = _to_local_date(utcnow(), tz_offset_minutes)
    yesterday_str = _to_local_date(utcnow() - timedelta(days=1), tz_offset_minutes)

    # Si la dernière activité n'est ni aujourd'hui ni hier → streak rompu
    if last_activity not in (today_str, yesterday_str):
        current_streak = 0
    else:
        # Remonter en arrière depuis last_activity
        current_streak = 1
        check = datetime.strptime(last_activity, "%Y-%m-%d") - timedelta(days=1)
        while check.strftime("%Y-%m-%d") in active_dates:
            current_streak += 1
            check -= timedelta(days=1)

    # Calcul du longest streak (parcours séquentiel)
    longest_streak = 1
    run = 1
    for i in range(1, len(sorted_dates)):
        prev = datetime.strptime(sorted_dates[i - 1], "%Y-%m-%d")
        curr = datetime.strptime(sorted_dates[i], "%Y-%m-%d")
        if (curr - prev).days == 1:
            run += 1
            longest_streak = max(longest_streak, run)
        else:
            run = 1

    return {
        "current_streak": current_streak,
        "longest_streak": longest_streak,
        "total_days": total_days,
        "last_activity": last_activity,
    }


# =========================================================
# Calcul du taux de réussite global
# =========================================================

def _compute_success_rate(history: list[QcmSessionHistory]) -> dict:
    """
    Taux de réussite global sur toutes les sessions (complètes ou non).
    On n'utilise que les sessions ayant au moins 1 réponse.
    """
    answered_sessions = [h for h in history if (h.correct_answers + h.wrong_answers) > 0]
    if not answered_sessions:
        return {"success_rate_pct": 0, "total_correct": 0, "total_wrong": 0, "total_answered": 0}

    total_correct = sum(h.correct_answers for h in answered_sessions)
    total_wrong = sum(h.wrong_answers for h in answered_sessions)
    total_answered = total_correct + total_wrong
    rate_pct = round((total_correct / total_answered) * 100) if total_answered > 0 else 0

    return {
        "success_rate_pct": rate_pct,        # ex: 73
        "total_correct": total_correct,
        "total_wrong": total_wrong,
        "total_answered": total_answered,
    }


# =========================================================
# Calcul du nombre de sessions
# =========================================================

def _compute_session_counts(history: list[QcmSessionHistory]) -> dict:
    total = len(history)
    complete = len(_completed_only(history))
    abandoned = total - complete
    return {
        "total_sessions": total,
        "complete_sessions": complete,
        "abandoned_sessions": abandoned,
    }


# =========================================================
# Calcul des stats par fichier
# =========================================================

def _compute_file_stats(history: list[QcmSessionHistory]) -> list[dict]:
    """
    Pour chaque fichier distinct :
    - file_id, file_name
    - sessions_count        : nombre de sessions sur ce fichier
    - usage_pct             : % d'utilisation sur le total des sessions
    - success_rate_pct      : taux de réussite moyen sur ce fichier
    Trié par sessions_count DESC.
    """
    if not history:
        return []

    # Agrégation par file_id
    file_map: dict[int, dict] = {}
    for h in history:
        fid = h.file_id or 0
        if fid not in file_map:
            file_map[fid] = {
                "file_id": fid,
                "file_name": h.file_name or "Fichier inconnu",
                "sessions_count": 0,
                "total_correct": 0,
                "total_answered": 0,
            }
        file_map[fid]["sessions_count"] += 1
        file_map[fid]["total_correct"] += h.correct_answers
        file_map[fid]["total_answered"] += h.correct_answers + h.wrong_answers

    total_sessions = len(history)

    result = []
    for fid, data in file_map.items():
        answered = data["total_answered"]
        correct = data["total_correct"]
        result.append({
            "file_id": data["file_id"],
            "file_name": data["file_name"],
            "sessions_count": data["sessions_count"],
            "usage_pct": round((data["sessions_count"] / total_sessions) * 100) if total_sessions > 0 else 0,
            "success_rate_pct": round((correct / answered) * 100) if answered > 0 else 0,
        })

    return sorted(result, key=lambda x: x["sessions_count"], reverse=True)


# =========================================================
# ROUTES — une métrique = un endpoint = un override Framer
# =========================================================

@router.get("/streaks")
def get_streaks(
    tz_offset: int = Query(default=0, description="Offset UTC en minutes (ex: -120 pour UTC+2). Fourni par le front via Intl.DateTimeFormat().resolvedOptions().timeZone ou getTimezoneOffset()."),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Retourne les données de streak pour l'utilisateur courant.
    Le front envoie ?tz_offset=<getTimezoneOffset()> (valeur JS native).

    Réponse :
    {
        "current_streak": 5,
        "longest_streak": 12,
        "total_days": 34,
        "last_activity": "2026-03-20"
    }
    """
    history = _get_user_history(db, user.id)
    return _compute_streaks(history, tz_offset)


@router.get("/success-rate")
def get_success_rate(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Taux de réussite global (toutes sessions confondues).

    Réponse :
    {
        "success_rate_pct": 73,
        "total_correct": 109,
        "total_wrong": 41,
        "total_answered": 150
    }
    """
    history = _get_user_history(db, user.id)
    return _compute_success_rate(history)


@router.get("/sessions")
def get_session_counts(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Nombre de sessions QCM effectuées.

    Réponse :
    {
        "total_sessions": 32,
        "complete_sessions": 28,
        "abandoned_sessions": 4
    }
    """
    history = _get_user_history(db, user.id)
    return _compute_session_counts(history)


@router.get("/files")
def get_file_stats(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Stats par fichier : utilisation + taux de réussite, triés par fréquence d'usage.

    Réponse :
    [
        {
            "file_id": 12,
            "file_name": "Droit des contrats.pdf",
            "sessions_count": 14,
            "usage_pct": 44,
            "success_rate_pct": 68
        },
        ...
    ]
    """
    history = _get_user_history(db, user.id)
    return _compute_file_stats(history)


@router.get("/summary")
def get_summary(
    tz_offset: int = Query(default=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Endpoint agrégé : toutes les métriques en un seul appel.
    Utile si tu veux charger toutes les stats d'une page en une requête.

    Réponse : fusion de /streaks + /success-rate + /sessions + /files
    """
    history = _get_user_history(db, user.id)
    return {
        **_compute_streaks(history, tz_offset),
        **_compute_success_rate(history),
        **_compute_session_counts(history),
        "files": _compute_file_stats(history),
    }