# app/routers/discord_bot.py

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import BOT_SECRET
from app.core.limits import check_navire_daily_limit
from app.db.database import get_db
from app.db.models import User
from app.services import deepwork as deepwork_service
from app.services.discord_link import verify_and_link
from app.services.email import mail_discord_linked, send_mail
from app.services.navire_chat import send_message as navire_send_message

router = APIRouter(prefix="/discord", tags=["discord-bot"])

# Libellés d'abonnement pour les messages adressés aux utilisateurs.
PLAN_LABELS = {
    "free":    "Gratuit",
    "membre":  "NAVIRE AI",
    "membre+": "NAVIRE AI+",
    "prepa":   "PREPASSERELLE",
}


def _require_bot(x_bot_secret: str = Header(...)) -> None:
    if x_bot_secret != BOT_SECRET:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


class LinkDiscordIn(BaseModel):
    user_id: int
    discord_id: str


class VerifyLinkIn(BaseModel):
    """Triplet exigé par le Modal Discord : compte + email + code."""
    discord_id: str
    discord_name: str = ""
    user_id: int
    email: str
    code: str


class ParticipationIn(BaseModel):
    discord_id: str
    message_count: int = 1


class NavireAiChatIn(BaseModel):
    discord_id: str
    message: str
    conversation_id: Optional[int] = None


class DeepWorkStartIn(BaseModel):
    discord_id: str
    guild_id: str = ""
    channel_id: str = ""


class DeepWorkGoalIn(BaseModel):
    session_id: int
    discord_id: str
    # None = session libre, choix explicite de ne pas se fixer d'objectif.
    goal_minutes: Optional[int] = None


class DeepWorkSessionIn(BaseModel):
    session_id: int
    discord_id: str


class DeepWorkMessageIn(BaseModel):
    session_id: int
    discord_id: str
    dm_message_id: str


class DeepWorkResetIn(BaseModel):
    """IDs des sessions que le bot suit encore ; toutes les autres sont périmées."""
    keep_session_ids: list[int] = []


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


@router.post("/link-verify", dependencies=[Depends(_require_bot)])
def verify_link(body: VerifyLinkIn, db: Session = Depends(get_db)):
    """
    Liaison authentifiée : valide (identifiant, email, code) puis rattache
    discord_id au compte et confirme par email.

    Retourne toujours 200 — les échecs attendus (code expiré, triplet
    incohérent, Discord déjà lié ailleurs) remontent en {"ok": false,
    "message": ...} pour que le bot les affiche à l'utilisateur.
    """
    ok, message, user = verify_and_link(
        db,
        user_id=body.user_id,
        email=body.email,
        code=body.code,
        discord_id=body.discord_id,
    )

    if not ok:
        return {"ok": False, "message": message}

    plan = user.plan or "free"

    subject, html = mail_discord_linked(
        username=user.username,
        discord_name=body.discord_name or body.discord_id,
        plan_label=PLAN_LABELS.get(plan, plan),
    )
    send_mail(user.email, subject, html)

    return {"ok": True, "user_id": user.id, "username": user.username, "plan": plan}


@router.get("/sync-state", dependencies=[Depends(_require_bot)])
def discord_sync_state(db: Session = Depends(get_db)):
    """
    État d'abonnement de tous les comptes liés à un Discord.

    Lu périodiquement par le bot (cogs/sync_account.py) qui compare au snapshot
    précédent et ne touche aux rôles que des membres dont le plan a bougé.
    Une seule requête par cycle, quel que soit le nombre de membres.
    """
    rows = db.query(User).filter(User.discord_id.isnot(None)).all()
    return [
        {
            "discord_id": u.discord_id,
            "user_id":    u.id,
            "username":   u.username,
            "plan":       u.plan or "free",
        }
        for u in rows
    ]


@router.post("/navire/chat", dependencies=[Depends(_require_bot)])
def discord_navire_chat(body: NavireAiChatIn, db: Session = Depends(get_db)):
    """
    Relaie une question posée depuis Discord (/navire) à NAVIRE AI.

    Partage la même limite quotidienne que l'appli (check_navire_daily_limit) :
    une question posée sur Discord compte pour le quota du plan, tous canaux
    confondus. Retourne toujours 200 — {"ok": false, "message": ...} pour les
    cas attendus (compte non lié, limite atteinte) afin que le bot les affiche
    tels quels.
    """
    user = _by_discord(db, body.discord_id)
    if not user:
        return {
            "ok": False,
            "code": "NOT_LINKED",
            "message": "Compte NAVIRE non lié. Rendez-vous dans #🔹connecter-mon-compte-navire pour lier votre compte.",
        }

    message = (body.message or "").strip()
    if not message:
        return {"ok": False, "code": "EMPTY", "message": "Message vide."}
    message = message[:2000]

    try:
        check_navire_daily_limit(user, db)
    except HTTPException as e:
        detail = e.detail if isinstance(e.detail, dict) else {}
        return {
            "ok": False,
            "code": detail.get("code", "LIMIT_REACHED"),
            "message": detail.get("message", "Limite quotidienne NAVIRE atteinte."),
        }

    try:
        result = navire_send_message(
            db, user, conversation_id=body.conversation_id, user_message=message, mode="discord"
        )
    except Exception:
        return {"ok": False, "code": "ERROR", "message": "NAVIRE AI est momentanément indisponible."}

    return {
        "ok":              True,
        "reply":           result["reply"],
        "sources":         result.get("sources", []),
        "conversation_id": result.get("conversation_id"),
    }


@router.get("/leaderboard", dependencies=[Depends(_require_bot)])
def discord_leaderboard(limit: int = 20, db: Session = Depends(get_db)):
    """Top N users par ELO — inclut discord_streak pour l'affichage."""
    limit = max(1, min(50, limit))
    rows  = (
        db.query(User)
        .filter(User.discord_id.isnot(None))
        .order_by(User.elo.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "rank":           i + 1,
            "discord_id":     u.discord_id,
            "username":       u.username,
            "elo":            u.elo or 0,
            "plan":           u.plan,
            "discord_streak": u.discord_streak or 0,
        }
        for i, u in enumerate(rows)
    ]

# ============================================================
# DEEP WORK — salon vocal de travail
# ============================================================
# Le bot signale seulement « untel entre / untel sort » : la mesure du temps
# est faite ici, à partir de started_at posé en base. Comme partout dans ce
# routeur, les cas attendus (compte non lié, session inconnue) répondent 200
# avec {"ok": false, ...} pour que le bot les affiche tels quels.

def _deepwork_elo(db: Session, session, user: User) -> dict:
    """
    Bloc de classement joint à chaque réponse deep work.

      credited — Elo déjà crédités pour cette session (0 tant qu'elle tourne)
      pending  — Elo qu'elle vaut à l'instant, crédités à la clôture
      total    — Elo gagnés en deep work depuis toujours
      elo      — classement global du membre, toutes sources confondues
    """
    credited = deepwork_service.awarded_elo(db, session.id)
    return {
        "per_hour":  deepwork_service.ELO_PER_HOUR,
        "credited":  credited,
        "pending":   0 if credited else deepwork_service.session_elo(session),
        "total":     deepwork_service.total_elo_earned(db, user.id),
        "elo":       int(user.elo or 0),
        "goal_rewards": deepwork_service.goal_rewards(),
    }


def _deepwork_payload(db: Session, session, user: User) -> dict:
    return {
        "ok":      True,
        "session": deepwork_service.serialize_session(session),
        "user":    {"user_id": user.id, "username": user.username, "plan": user.plan or "free"},
        "stats":   deepwork_service.compute_stats(db, user.id),
        "elo":     _deepwork_elo(db, session, user),
    }


def _load_session(db: Session, session_id: int, discord_id: str):
    """
    Charge la session et vérifie qu'elle appartient bien au Discord qui la
    manipule : un session_id deviné ne permet pas de toucher à autrui.
    """
    session = deepwork_service.get_session(db, session_id)
    if not session:
        return None, None
    user = _by_discord(db, discord_id)
    if not user or session.user_id != user.id:
        return None, None
    return session, user


@router.post("/deepwork/start", dependencies=[Depends(_require_bot)])
def deepwork_start(body: DeepWorkStartIn, db: Session = Depends(get_db)):
    """Entrée dans le vocal deep work : ouvre une session et renvoie les stats."""
    user = _by_discord(db, body.discord_id)
    if not user:
        return {
            "ok": False,
            "code": "NOT_LINKED",
            "message": (
                "Ton compte NAVIRE n'est pas encore lié à Discord : tes sessions "
                "deep work ne peuvent pas être enregistrées. Rends-toi dans "
                "#🔹connecter-mon-compte-navire pour le lier."
            ),
        }

    session = deepwork_service.start_session(
        db,
        user,
        discord_id=body.discord_id,
        guild_id=body.guild_id,
        channel_id=body.channel_id,
    )
    payload = _deepwork_payload(db, session, user)
    payload["goal_choices"] = deepwork_service.GOAL_CHOICES
    # Ce que rapporte chaque objectif, pour que le MP l'affiche sur les boutons.
    payload["goal_rewards"] = deepwork_service.goal_rewards()
    return payload


@router.post("/deepwork/goal", dependencies=[Depends(_require_bot)])
def deepwork_goal(body: DeepWorkGoalIn, db: Session = Depends(get_db)):
    """Objectif choisi dans le MP. goal_minutes absent = session libre."""
    session, user = _load_session(db, body.session_id, body.discord_id)
    if not session:
        return {"ok": False, "code": "NOT_FOUND", "message": "Session introuvable."}

    try:
        session = deepwork_service.set_goal(db, session, body.goal_minutes)
    except ValueError:
        return {"ok": False, "code": "BAD_GOAL", "message": "Objectif non proposé."}

    payload = _deepwork_payload(db, session, user)
    payload["elapsed_seconds"] = deepwork_service.elapsed_seconds(session)
    return payload


@router.post("/deepwork/tick", dependencies=[Depends(_require_bot)])
def deepwork_tick(body: DeepWorkSessionIn, db: Session = Depends(get_db)):
    """
    Rafraîchissement du chronomètre affiché dans le MP : renvoie le temps
    écoulé faisant foi et note le franchissement de l'objectif au moment où il
    se produit (plutôt qu'à la clôture, qui pourrait ne jamais arriver).
    """
    session, user = _load_session(db, body.session_id, body.discord_id)
    if not session:
        return {"ok": False, "code": "NOT_FOUND", "message": "Session introuvable."}

    was_reached = bool(session.goal_reached)
    session = deepwork_service.mark_goal_reached(db, session)

    return {
        "ok":              True,
        "session":         deepwork_service.serialize_session(session),
        "elapsed_seconds": deepwork_service.elapsed_seconds(session),
        "goal_just_reached": bool(session.goal_reached) and not was_reached,
        "still_active":    session.status == deepwork_service.STATUS_ACTIVE,
        "elo":             _deepwork_elo(db, session, user),
    }


@router.post("/deepwork/message", dependencies=[Depends(_require_bot)])
def deepwork_message(body: DeepWorkMessageIn, db: Session = Depends(get_db)):
    """Mémorise l'ID du MP porteur du chronomètre."""
    session, _user = _load_session(db, body.session_id, body.discord_id)
    if not session:
        return {"ok": False, "code": "NOT_FOUND", "message": "Session introuvable."}
    session.dm_message_id = (body.dm_message_id or "")[:32]
    db.commit()
    return {"ok": True}


@router.post("/deepwork/stop", dependencies=[Depends(_require_bot)])
def deepwork_stop(body: DeepWorkSessionIn, db: Session = Depends(get_db)):
    """Sortie du vocal : clôture la session et renvoie le bilan + stats à jour."""
    session, user = _load_session(db, body.session_id, body.discord_id)
    if not session:
        return {"ok": False, "code": "NOT_FOUND", "message": "Session introuvable."}

    session = deepwork_service.stop_session(db, session)
    db.refresh(user)   # l'Elo du membre vient d'être crédité
    payload = _deepwork_payload(db, session, user)
    payload["too_short"] = session.status == deepwork_service.STATUS_DISCARDED
    payload["min_seconds"] = deepwork_service.MIN_SESSION_SECONDS
    # Raccourci de lecture pour le bilan affiché en MP.
    payload["elo_gained"] = payload["elo"]["credited"]
    payload["new_elo"] = payload["elo"]["elo"]
    return payload


@router.post("/deepwork/reset-stale", dependencies=[Depends(_require_bot)])
def deepwork_reset_stale(body: DeepWorkResetIn, db: Session = Depends(get_db)):
    """
    Démarrage du bot : les sessions restées « active » n'ont plus de
    chronomètre attaché, leur durée réelle est inconnue. Elles passent en
    `stale` et sortent des statistiques, sauf celles que le bot vient de
    rouvrir pour les membres déjà présents dans le vocal.
    """
    closed = deepwork_service.close_stale_sessions(db, keep_session_ids=body.keep_session_ids)
    return {"ok": True, "closed": closed}


@router.get("/deepwork/stats/{discord_id}", dependencies=[Depends(_require_bot)])
def deepwork_stats(discord_id: str, db: Session = Depends(get_db)):
    """Stats deep work d'un membre — utilisé par la commande /deepwork."""
    user = _by_discord(db, discord_id)
    if not user:
        return {"ok": False, "code": "NOT_LINKED", "message": "Compte NAVIRE non lié."}

    active = deepwork_service.get_active_session(db, user.id)
    return {
        "ok":       True,
        "username": user.username,
        "stats":    deepwork_service.compute_stats(db, user.id),
        "recent":   deepwork_service.recent_sessions(db, user.id, limit=5),
        "active":   deepwork_service.serialize_session(active) if active else None,
        "active_elapsed_seconds": deepwork_service.elapsed_seconds(active) if active else 0,
        "elo": {
            "per_hour":     deepwork_service.ELO_PER_HOUR,
            "total":        deepwork_service.total_elo_earned(db, user.id),
            "elo":          int(user.elo or 0),
            "pending":      deepwork_service.session_elo(active) if active else 0,
            "goal_rewards": deepwork_service.goal_rewards(),
        },
    }
