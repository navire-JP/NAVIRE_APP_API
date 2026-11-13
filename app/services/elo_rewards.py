"""
app/services/elo_rewards.py
===========================
Elo gagné par l'activité sur le site (hors QCM, Lib et deep work).

  Photo de profil (1re fois) ............................ +10
  Bannière (1re fois) ................................... +10
  Premier post .......................................... +10  (en plus du post)
  Chaque post ........................................... +5, +7 avec un devoir/fichier joint
  Connexion jours d'affilée ............................. J2 +5, J3 +5, J4 +3, J5 +4, J6 +4, J7 +10
                                                          (puis le cycle de 7 jours recommence)
  Actu lue (plus de 30 secondes sur l'actu) ............. +2   (une fois par actu)
  5 actus lues dans la journée .......................... +10  (une fois par jour)
  Devoir prépa rendu .................................... +25

Chaque gain est un EloEvent identifié par (source, session_id = clé) : la
même action ne rapporte qu'une fois (`award_once`). Un post supprimé ou une
copie retirée reprend les points gagnés (`revoke`), pour qu'on ne puisse pas
publier / supprimer en boucle.

Les jours sont comptés à l'heure de Paris.
"""

from __future__ import annotations

import logging
import threading
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import EloEvent, User
from app.services.elo import apply_elo_delta

log = logging.getLogger(__name__)

PARIS = ZoneInfo("Europe/Paris")

# --- Barème -------------------------------------------------------------
AVATAR_FIRST = 10
BANNER_FIRST = 10
FIRST_POST = 10
POST = 5
POST_WITH_FILE = 7
ACTU_READ = 2
ACTU_READ_SECONDS = 30
ACTUS_DAILY_GOAL = 5
ACTUS_DAILY_BONUS = 10
PREPA_DEVOIR = 25

# Gain du jour N d'une série de connexions (le jour 1 ne rapporte rien).
LOGIN_STREAK_REWARDS = {1: 0, 2: 5, 3: 5, 4: 3, 5: 4, 6: 4, 7: 10}
LOGIN_STREAK_CYCLE = 7

# --- Sources (EloEvent.source) -----------------------------------------
SRC_PROFIL = "profil"
SRC_POST = "post"
SRC_CONNEXION = "connexion"
SRC_ACTU = "actu"
SRC_ACTU_BONUS = "actu_bonus"
SRC_PREPA = "prepa_devoir"

# question_index : 0 = le gain, 1 = sa reprise (voir `revoke`)
_GAIN, _REPRISE = 0, 1


def paris_today(now: datetime | None = None) -> date:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(PARIS).date()


def _day_start_utc_naive(day: date) -> datetime:
    """Minuit (Paris) du jour, en UTC sans fuseau — le format d'EloEvent.created_at."""
    return datetime.combine(day, time.min, tzinfo=PARIS).astimezone(timezone.utc).replace(tzinfo=None)


def _event(db: Session, user_id: int, source: str, key: str, idx: int = _GAIN) -> EloEvent | None:
    return db.execute(
        select(EloEvent).where(
            EloEvent.user_id == user_id,
            EloEvent.source == source,
            EloEvent.session_id == key,
            EloEvent.question_index == idx,
        )
    ).scalars().first()


def award_once(db: Session, user_id: int, source: str, key: str, delta: int, meta: dict | None = None) -> int:
    """Crédite `delta` une seule fois pour (user, source, clé). Renvoie le gain (0 si déjà reçu)."""
    if _event(db, user_id, source, key) is not None:
        return 0
    apply_elo_delta(db, user_id=user_id, delta=delta, source=source,
                    session_id=key, question_index=_GAIN, meta=meta or {})
    return int(delta)


def revoke(db: Session, user_id: int, source: str, key: str) -> int:
    """Reprend un gain (post supprimé, copie retirée). Renvoie les points repris."""
    gain = _event(db, user_id, source, key)
    if gain is None or not gain.delta or _event(db, user_id, source, key, _REPRISE) is not None:
        return 0
    apply_elo_delta(db, user_id=user_id, delta=-int(gain.delta), source=source,
                    session_id=key, question_index=_REPRISE, meta={"reprise": True})
    return int(gain.delta)


def _safe(fn):
    """Un gain d'elo ne doit jamais faire échouer l'action de l'utilisateur."""
    def wrapper(db: Session, *args, **kwargs):
        try:
            return fn(db, *args, **kwargs)
        except Exception:  # noqa: BLE001
            log.exception("elo_rewards.%s a échoué", fn.__name__)
            db.rollback()
            return 0
    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


# ============================================================
# Profil
# ============================================================

@_safe
def reward_avatar(db: Session, user: User) -> int:
    return award_once(db, user.id, SRC_PROFIL, "avatar", AVATAR_FIRST)


@_safe
def reward_banner(db: Session, user: User) -> int:
    return award_once(db, user.id, SRC_PROFIL, "banniere", BANNER_FIRST)


# ============================================================
# Posts
# ============================================================

@_safe
def reward_post(db: Session, user: User, post_id: int, has_file: bool) -> int:
    gained = award_once(db, user.id, SRC_POST, "premier_post", FIRST_POST, {"post_id": post_id})
    delta = POST_WITH_FILE if has_file else POST
    gained += award_once(db, user.id, SRC_POST, f"post:{post_id}", delta, {"fichier": bool(has_file)})
    return gained


@_safe
def revoke_post(db: Session, author_id: int, post_id: int) -> int:
    return revoke(db, author_id, SRC_POST, f"post:{post_id}")


# ============================================================
# Connexion jours d'affilée
# ============================================================

def login_streak_reward(streak: int) -> int:
    if streak <= 0:
        return 0
    return LOGIN_STREAK_REWARDS[(streak - 1) % LOGIN_STREAK_CYCLE + 1]


# Jour déjà traité par user, dans ce processus : évite une requête par appel
# d'API, et le verrou évite que les requêtes parallèles d'un chargement de
# page créditent deux fois.
_presence_seen: dict[int, date] = {}
_presence_lock = threading.Lock()


@_safe
def register_daily_presence(db: Session, user: User, now: datetime | None = None) -> int:
    """
    Appelé à chaque requête authentifiée (get_current_user). Le premier passage
    du jour enregistre un EloEvent « connexion » qui porte la série en cours
    (meta.streak) et le gain du jour de série.
    """
    today = paris_today(now)
    if _presence_seen.get(user.id) == today:
        return 0
    with _presence_lock:
        if _presence_seen.get(user.id) == today:
            return 0
        key = f"connexion:{today.isoformat()}"
        if _event(db, user.id, SRC_CONNEXION, key) is not None:
            _presence_seen[user.id] = today
            return 0
        hier = _event(db, user.id, SRC_CONNEXION, f"connexion:{(today - timedelta(days=1)).isoformat()}")
        streak = int((hier.meta or {}).get("streak", 1)) + 1 if hier is not None else 1
        delta = login_streak_reward(streak)
        gained = award_once(db, user.id, SRC_CONNEXION, key, delta, {"streak": streak})
        _presence_seen[user.id] = today
        return gained


def login_streak(db: Session, user_id: int, now: datetime | None = None) -> int:
    """Série de connexions en cours (0 si ni aujourd'hui ni hier)."""
    today = paris_today(now)
    for day in (today, today - timedelta(days=1)):
        ev = _event(db, user_id, SRC_CONNEXION, f"connexion:{day.isoformat()}")
        if ev is not None:
            return int((ev.meta or {}).get("streak", 1))
    return 0


# ============================================================
# Actus
# ============================================================

@_safe
def reward_actu_read(db: Session, user: User, item_id: int, now: datetime | None = None) -> int:
    """+2 la première fois que l'actu est lue 30 s ; +10 à la 5e actu du jour."""
    gained = award_once(db, user.id, SRC_ACTU, f"actu:{item_id}", ACTU_READ)
    if not gained:
        return 0
    today = paris_today(now)
    lues = db.execute(
        select(func.count(EloEvent.id)).where(
            EloEvent.user_id == user.id,
            EloEvent.source == SRC_ACTU,
            EloEvent.question_index == _GAIN,
            EloEvent.created_at >= _day_start_utc_naive(today),
        )
    ).scalar_one()
    if (lues or 0) >= ACTUS_DAILY_GOAL:
        gained += award_once(db, user.id, SRC_ACTU_BONUS, f"actus:{today.isoformat()}", ACTUS_DAILY_BONUS)
    return gained


# ============================================================
# Prépa
# ============================================================

@_safe
def reward_prepa_devoir(db: Session, user: User, key: str) -> int:
    return award_once(db, user.id, SRC_PREPA, key, PREPA_DEVOIR)


@_safe
def revoke_prepa_devoir(db: Session, user_id: int, key: str) -> int:
    return revoke(db, user_id, SRC_PREPA, key)
