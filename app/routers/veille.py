from __future__ import annotations

from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, and_
from sqlalchemy.orm import Session

from app.bot_discord.veille_publish import publish_veille_item_sync
from app.db.database import get_db
from app.db.models import (
    User,
    VeilleItem,
    VeilleEvent,
    VeilleUserProfile,
    VeilleDailyState,
)
from app.routers.auth import get_current_user

router = APIRouter(prefix="/veille", tags=["veille"])

# ============================================================
# Config
# ============================================================
VEILLE_RETENTION_DAYS = 14
VEILLE_GOAL_PER_DAY = 5

# Interactions acceptées. Une valeur hors liste est refusée en 400 : sans ce
# garde-fou, une faute de frappe côté client s'enregistre silencieusement et
# la lecture n'est jamais comptée.
VALID_EVENT_TYPES = {"impression", "read", "skip", "open"}


# ============================================================
# Helpers
# ============================================================
def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def today_start() -> datetime:
    """Retourne minuit UTC du jour courant."""
    now = utcnow()
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def get_or_create_profile(db: Session, user: User) -> VeilleUserProfile:
    """Récupère ou crée le profil veille d'un user."""
    profile = db.execute(
        select(VeilleUserProfile).where(VeilleUserProfile.user_id == user.id)
    ).scalar_one_or_none()

    if not profile:
        profile = VeilleUserProfile(user_id=user.id)
        db.add(profile)
        db.commit()
        db.refresh(profile)

    return profile


def get_or_create_daily_state(db: Session, user: User) -> VeilleDailyState:
    """Récupère ou crée l'état du jour pour un user."""
    today = today_start()

    state = db.execute(
        select(VeilleDailyState).where(
            and_(
                VeilleDailyState.user_id == user.id,
                VeilleDailyState.date == today,
            )
        )
    ).scalar_one_or_none()

    if not state:
        state = VeilleDailyState(
            user_id=user.id,
            date=today,
            goal=VEILLE_GOAL_PER_DAY,
        )
        db.add(state)
        db.commit()
        db.refresh(state)

    return state


def update_streak(db: Session, user: User, profile: VeilleUserProfile) -> None:
    """Met à jour le streak après avoir atteint l'objectif du jour."""
    today = today_start()
    yesterday = today - timedelta(days=1)

    if profile.last_goal_date is None:
        # Premier objectif atteint
        profile.streak = 1
        profile.last_goal_date = today
    elif profile.last_goal_date.date() == today.date():
        # Déjà atteint aujourd'hui, rien à faire
        pass
    elif profile.last_goal_date.date() == yesterday.date():
        # Hier atteint → streak continue
        profile.streak += 1
        profile.last_goal_date = today
    else:
        # Streak cassé → reset à 1
        profile.streak = 1
        profile.last_goal_date = today

    db.commit()


# ============================================================
# Schemas
# ============================================================
class VeilleItemOut(BaseModel):
    id: int
    title: str
    essentiel: str
    impact: str
    category: str
    source_url: str
    source_name: str
    published_at: datetime | None

    class Config:
        from_attributes = True


class VeilleStateOut(BaseModel):
    read_today: int
    goal: int
    goal_reached: bool
    streak: int


class VeilleEventIn(BaseModel):
    item_id: int
    event_type: str  # impression | read | skip | open


# ============================================================
# Endpoints
# ============================================================

@router.get("/feed")
def get_feed(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    limit: int = Query(default=50, le=100),
):
    """
    Retourne les actus des 14 derniers jours, triées par date décroissante.
    Exclut les items déjà lus par l'utilisateur.
    """
    cutoff = utcnow() - timedelta(days=VEILLE_RETENTION_DAYS)

    # IDs des items déjà lus
    read_ids_q = db.execute(
        select(VeilleEvent.item_id).where(
            and_(
                VeilleEvent.user_id == user.id,
                VeilleEvent.event_type == "read",
            )
        )
    ).scalars().all()
    read_ids = set(read_ids_q)

    # Fetch items récents
    stmt = (
        select(VeilleItem)
        .where(VeilleItem.created_at >= cutoff)
        .order_by(VeilleItem.published_at.desc().nullslast(), VeilleItem.created_at.desc())
        .limit(limit * 2)  # marge pour filtrer les lus
    )
    items = db.execute(stmt).scalars().all()

    # Filtre les déjà lus
    feed = [it for it in items if it.id not in read_ids][:limit]

    return {
        "items": [
            VeilleItemOut.model_validate(it).model_dump()
            for it in feed
        ]
    }


@router.get("/state", response_model=VeilleStateOut)
def get_state(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Retourne la progression du jour + streak."""
    profile = get_or_create_profile(db, user)
    daily = get_or_create_daily_state(db, user)

    return VeilleStateOut(
        read_today=daily.read_count,
        goal=daily.goal,
        goal_reached=daily.goal_reached,
        streak=profile.streak,
    )


def record_event(db: Session, user: User, item_id: int, event_type: str) -> dict:
    """
    Enregistre une interaction et, pour un "read", fait avancer la progression
    du jour puis le streak.

    Le compteur du jour ne bouge qu'à la PREMIÈRE lecture d'un item : relire la
    même actu, ou double-cliquer sur « Lu », ne fait pas avancer l'objectif.

    Retourne la progression à jour pour que le client se resynchronise sur la
    réponse, sans second appel à /state — c'est ce qui évite qu'un compteur
    tenu en mémoire côté navigateur diverge de la base.
    """
    if event_type not in VALID_EVENT_TYPES:
        raise HTTPException(400, detail=f"event_type invalide : {event_type}")

    item = db.execute(
        select(VeilleItem).where(VeilleItem.id == item_id)
    ).scalar_one_or_none()

    if not item:
        raise HTTPException(404, detail="Item introuvable.")

    profile = get_or_create_profile(db, user)
    daily = get_or_create_daily_state(db, user)

    # Le doublon se juge AVANT d'ajouter la nouvelle ligne, sinon celle-ci
    # compterait comme une lecture antérieure et aucune lecture ne serait
    # jamais décomptée. first() plutôt que scalar_one_or_none() : d'anciens
    # doublons en base ne doivent pas faire tomber la requête en 500.
    already_read = False
    if event_type == "read":
        already_read = db.execute(
            select(VeilleEvent.id).where(
                and_(
                    VeilleEvent.user_id == user.id,
                    VeilleEvent.item_id == item_id,
                    VeilleEvent.event_type == "read",
                )
            )
        ).first() is not None

    db.add(VeilleEvent(user_id=user.id, item_id=item_id, event_type=event_type))

    if event_type == "read" and not already_read:
        daily.read_count += 1
        profile.total_read += 1

        if daily.read_count >= daily.goal and not daily.goal_reached:
            daily.goal_reached = True
            update_streak(db, user, profile)

    db.commit()
    db.refresh(daily)
    db.refresh(profile)

    return {
        "ok":           True,
        "already_read": already_read,
        "read_today":   daily.read_count,
        "goal":         daily.goal,
        "goal_reached": daily.goal_reached,
        "streak":       profile.streak,
    }


@router.post("/read/{item_id}")
def mark_read(
    item_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Marque une actu comme lue — la route qu'appelle le bouton « Lu » du feed.

    Elle double POST /event volontairement : le client n'a pas de corps de
    requête à composer, et un chemin dédié rend une erreur visible (404 sur un
    item supprimé) au lieu de la noyer dans un event générique.
    """
    return record_event(db, user, item_id, "read")


@router.post("/event")
def log_event(
    payload: VeilleEventIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Enregistre une interaction (impression, read, skip, open).
    Si event_type == "read", met à jour le compteur du jour et potentiellement le streak.
    """
    return record_event(db, user, payload.item_id, payload.event_type)


# ============================================================
# Admin: ajout manuel d'items (pour tests / V1)
# ============================================================

class VeilleItemCreateIn(BaseModel):
    title: str
    essentiel: str
    impact: str
    category: str = "general"
    source_url: str
    source_name: str = ""
    published_at: datetime | None = None


@router.post("/admin/items")
def admin_create_item(
    payload: VeilleItemCreateIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Endpoint admin pour créer des items manuellement (tests V1)."""
    if not user.is_admin:
        raise HTTPException(403, detail="Admin only.")

    # Génère un external_id unique basé sur l'URL
    external_id = f"manual:{payload.source_url}"

    # Vérifie doublon
    existing = db.execute(
        select(VeilleItem).where(VeilleItem.external_id == external_id)
    ).scalar_one_or_none()

    if existing:
        raise HTTPException(409, detail="Item déjà existant.")

    item = VeilleItem(
        external_id=external_id,
        title=payload.title,
        essentiel=payload.essentiel,
        impact=payload.impact,
        category=payload.category,
        source_url=payload.source_url,
        source_name=payload.source_name,
        published_at=payload.published_at or utcnow(),
    )
    db.add(item)
    db.commit()
    db.refresh(item)

    # Diffusion Discord (#veille + trace dans #logsnewsletter). C'est la route
    # qu'utilise la console du site : elle doit publier au même titre que
    # /admin/actus/add, sinon une actu créée depuis la console n'atteint
    # jamais Discord. Best-effort — ne doit jamais faire échouer la création.
    publish_veille_item_sync(
        item_id=item.id,
        title=item.title,
        essentiel=item.essentiel,
        impact=item.impact,
        category=item.category,
        source_url=item.source_url,
        source_name=item.source_name,
        published_at=item.published_at,
    )

    return {"ok": True, "id": item.id}

@router.get("/history")
def get_history(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    limit: int = Query(default=50, le=100),
):
    """
    Retourne les actus déjà lues par l'utilisateur (14 derniers jours).
    """
    cutoff = utcnow() - timedelta(days=VEILLE_RETENTION_DAYS)

    # IDs des items lus
    read_ids_q = db.execute(
        select(VeilleEvent.item_id).where(
            and_(
                VeilleEvent.user_id == user.id,
                VeilleEvent.event_type == "read",
                VeilleEvent.created_at >= cutoff,
            )
        )
    ).scalars().all()
    read_ids = list(set(read_ids_q))

    if not read_ids:
        return {"items": []}

    # Fetch ces items
    stmt = (
        select(VeilleItem)
        .where(VeilleItem.id.in_(read_ids))
        .order_by(VeilleItem.published_at.desc().nullslast(), VeilleItem.created_at.desc())
        .limit(limit)
    )
    items = db.execute(stmt).scalars().all()

    return {
        "items": [
            VeilleItemOut.model_validate(it).model_dump()
            for it in items
        ]
    }

    