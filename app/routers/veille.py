from __future__ import annotations

from datetime import datetime, timezone, timedelta

import math
import re

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, and_, case, func, or_
from sqlalchemy.orm import Session

from app.bot_discord.veille_publish import publish_veille_item_sync
from app.db.database import get_db
from app.db.models import (
    User,
    VeilleBookmark,
    VeilleItem,
    VeilleEvent,
    VeilleUserProfile,
    VeilleDailyState,
)
from app.routers.auth import get_current_user, get_current_user_optional

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

    

# ============================================================
# Hub « Actualités » (embed du site)
# ============================================================
# Le feed ci-dessus ne montre que les 14 derniers jours, sans les actus déjà
# lues : c'est un exercice quotidien. Le hub, lui, est l'archive complète et
# consultable : toutes les actus publiées depuis le début, par rubrique, avec
# recherche. Il est lisible sans compte ; connecté, chaque actu porte en plus
# son état lu / à relire.

RUBRIQUES: dict[str, str] = {
    "civil":    "Droit civil",
    "penal":    "Droit pénal",
    "public":   "Droit public",
    "affaires": "Droit des affaires",
    "general":  "Général",
}

PERIODS = {"7d": 7, "30d": 30, "365d": 365}
HUB_PAGE_MAX = 50
WORDS_PER_MINUTE = 200

# Mots trop courants pour rapprocher deux actus (« À lire aussi »).
_STOPWORDS = set(
    "alors au aucun aussi autre avant avec avoir bon car ce cela ces ceux chaque ci comme comment dans "
    "des du dedans dehors depuis devrait doit donc dos droit début elle elles en encore est et etc eu "
    "fait faites fois font hors ici il ils je juste la le les leur là ma maintenant mais mes mine moins "
    "mon mot même ni nommés notre nous nouveau nouvelle nouvelles ou où par parce pas peut peu pièce "
    "plupart pour pourquoi quand que quel quelle quelles quels qui sa sans ses seulement si sien son "
    "sont sous soyez sujet sur ta tandis tellement tels tes ton tous tout toute toutes très tu un une "
    "voient vont votre vous vu ça étaient état étions été être leurs cette cet entre selon après lors "
    "article articles loi décision cour arrêt juge texte projet code"
    .split()
)


def _date_key():
    """Date de référence d'une actu : publication d'origine, sinon ajout en base."""
    return func.coalesce(VeilleItem.published_at, VeilleItem.created_at)


def _reading_minutes(it: VeilleItem) -> int:
    words = len(f"{it.title} {it.essentiel} {it.impact}".split())
    return max(1, math.ceil(words / WORDS_PER_MINUTE))


def _hub_item(it: VeilleItem, read_ids: set[int], saved_ids: set[int]) -> dict:
    when = it.published_at or it.created_at
    return {
        "id":              it.id,
        "title":           it.title,
        "essentiel":       it.essentiel,
        "impact":          it.impact,
        "category":        it.category,
        "category_label":  RUBRIQUES.get(it.category, it.category.capitalize()),
        "source_url":      it.source_url,
        "source_name":     it.source_name,
        "published_at":    when.isoformat() if when else None,
        "reading_minutes": _reading_minutes(it),
        "is_read":         it.id in read_ids,
        "is_saved":        it.id in saved_ids,
    }


def _read_ids(db: Session, user: User | None) -> set[int]:
    if not user:
        return set()
    return set(db.execute(
        select(VeilleEvent.item_id).where(
            VeilleEvent.user_id == user.id,
            VeilleEvent.event_type == "read",
        )
    ).scalars().all())


def _saved_ids(db: Session, user: User | None) -> set[int]:
    if not user:
        return set()
    return set(db.execute(
        select(VeilleBookmark.item_id).where(VeilleBookmark.user_id == user.id)
    ).scalars().all())


def _keywords(text: str) -> set[str]:
    words = re.findall(r"[a-zà-öø-ÿ0-9]{4,}", text.lower())
    return {w for w in words if w not in _STOPWORDS}


@router.get("/rubriques")
def get_rubriques(db: Session = Depends(get_db)):
    """
    Rubriques du hub avec, pour chacune, le nombre d'actus (total et 7 derniers
    jours) et la date de la plus récente. Public.
    """
    week_ago = utcnow() - timedelta(days=7)
    date_key = _date_key()
    rows = db.execute(
        select(
            VeilleItem.category,
            func.count(VeilleItem.id),
            func.sum(case((date_key >= week_ago, 1), else_=0)),
            func.max(date_key),
        ).group_by(VeilleItem.category)
    ).all()
    stats = {cat: (count, week or 0, last) for cat, count, week, last in rows}

    # Ordre fixe des rubriques connues, puis celles créées hors liste.
    keys = list(RUBRIQUES) + sorted(k for k in stats if k not in RUBRIQUES)
    rubriques = []
    for key in keys:
        count, week, last = stats.get(key, (0, 0, None))
        rubriques.append({
            "key":        key,
            "label":      RUBRIQUES.get(key, key.capitalize()),
            "count":      count,
            "week_count": int(week),
            "last_at":    last.isoformat() if last else None,
        })

    lasts = [r["last_at"] for r in rubriques if r["last_at"]]
    return {
        "total":      sum(r["count"] for r in rubriques),
        "week_total": sum(r["week_count"] for r in rubriques),
        "last_at":    max(lasts) if lasts else None,
        "rubriques":  rubriques,
    }


@router.get("/actus")
def list_actus(
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user_optional),
    category: str | None = Query(default=None),
    q: str | None = Query(default=None, max_length=120),
    status: str = Query(default="all"),     # all | unread | saved
    period: str = Query(default="all"),     # all | 7d | 30d | 365d
    limit: int = Query(default=20, ge=1, le=HUB_PAGE_MAX),
    offset: int = Query(default=0, ge=0),
):
    """
    Archive complète des actus, de la plus récente à la plus ancienne.

    - `category` : une rubrique (civil, penal, public, affaires, general)
    - `q` : recherche dans le titre, l'essentiel, l'impact et la source ;
      tous les mots doivent apparaître
    - `status` : `unread` / `saved` (connexion requise)
    - `period` : `7d`, `30d` ou `365d`
    """
    if status not in {"all", "unread", "saved"}:
        raise HTTPException(400, detail=f"status invalide : {status}")
    if period != "all" and period not in PERIODS:
        raise HTTPException(400, detail=f"period invalide : {period}")
    if status != "all" and not user:
        raise HTTPException(401, detail="Connecte-toi pour filtrer tes actus.")

    conds = []
    if category:
        conds.append(VeilleItem.category == category)
    if period != "all":
        conds.append(_date_key() >= utcnow() - timedelta(days=PERIODS[period]))
    for term in (q or "").split():
        conds.append(or_(
            VeilleItem.title.icontains(term, autoescape=True),
            VeilleItem.essentiel.icontains(term, autoescape=True),
            VeilleItem.impact.icontains(term, autoescape=True),
            VeilleItem.source_name.icontains(term, autoescape=True),
        ))
    if status == "unread":
        conds.append(VeilleItem.id.not_in(
            select(VeilleEvent.item_id).where(
                VeilleEvent.user_id == user.id,
                VeilleEvent.event_type == "read",
            )
        ))
    elif status == "saved":
        conds.append(VeilleItem.id.in_(
            select(VeilleBookmark.item_id).where(VeilleBookmark.user_id == user.id)
        ))

    total = db.execute(select(func.count(VeilleItem.id)).where(*conds)).scalar_one()
    items = db.execute(
        select(VeilleItem)
        .where(*conds)
        .order_by(_date_key().desc(), VeilleItem.id.desc())
        .offset(offset)
        .limit(limit)
    ).scalars().all()

    read_ids, saved_ids = _read_ids(db, user), _saved_ids(db, user)
    return {
        "total":    total,
        "offset":   offset,
        "has_more": offset + len(items) < total,
        "items":    [_hub_item(it, read_ids, saved_ids) for it in items],
    }


@router.get("/actus/{item_id}")
def get_actu(
    item_id: int,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user_optional),
):
    """
    Une actu et jusqu'à 4 actus « À lire aussi » : celles qui partagent le plus
    de mots-clés avec elle, la même rubrique départageant les égalités.
    """
    item = db.execute(select(VeilleItem).where(VeilleItem.id == item_id)).scalar_one_or_none()
    if not item:
        raise HTTPException(404, detail="Actu introuvable.")

    ref = _keywords(f"{item.title} {item.essentiel} {item.impact}")
    candidates = db.execute(
        select(VeilleItem)
        .where(VeilleItem.id != item.id)
        .order_by(_date_key().desc())
        .limit(300)
    ).scalars().all()

    scored = []
    for rank, c in enumerate(candidates):
        common = len(ref & _keywords(f"{c.title} {c.essentiel}"))
        same_cat = c.category == item.category
        if common == 0 and not same_cat:
            continue
        # rank : à score égal, la plus récente passe devant
        scored.append((common * 2 + (1 if same_cat else 0), -rank, c))
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    related = [c for _, _, c in scored[:4]]

    read_ids, saved_ids = _read_ids(db, user), _saved_ids(db, user)
    return {
        "item":    _hub_item(item, read_ids, saved_ids),
        "related": [_hub_item(c, read_ids, saved_ids) for c in related],
    }


@router.put("/actus/{item_id}/save")
def save_actu(
    item_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Met une actu de côté (« À relire »). Idempotent."""
    if not db.get(VeilleItem, item_id):
        raise HTTPException(404, detail="Actu introuvable.")
    exists = db.execute(
        select(VeilleBookmark.id).where(
            VeilleBookmark.user_id == user.id,
            VeilleBookmark.item_id == item_id,
        )
    ).first()
    if not exists:
        db.add(VeilleBookmark(user_id=user.id, item_id=item_id))
        db.commit()
    return {"ok": True, "is_saved": True}


@router.delete("/actus/{item_id}/save")
def unsave_actu(
    item_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Retire une actu de « À relire ». Idempotent."""
    bm = db.execute(
        select(VeilleBookmark).where(
            VeilleBookmark.user_id == user.id,
            VeilleBookmark.item_id == item_id,
        )
    ).scalar_one_or_none()
    if bm:
        db.delete(bm)
        db.commit()
    return {"ok": True, "is_saved": False}
