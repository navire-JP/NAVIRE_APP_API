"""
Leaderboard router — Classements utilisateurs et universités.

Page classement (embeds/classement.html) :
  GET   /leaderboard/classement?periode=general|30j|7j&fac=…  → joueurs classés
  GET   /leaderboard/joueurs/{id}                             → profil et stats d'un joueur
  PATCH /leaderboard/me/visibilite                            → afficher / masquer mes stats
  GET   /leaderboard/embed                                    → page d'embed servie par l'API

Le nom, l'Elo, le rang et la fac sont toujours visibles (c'est le
classement). Les stats détaillées (QCM, séries, courbe d'Elo, Lib) ne le sont
que si le joueur les laisse visibles (User.stats_public, oui par défaut).
"""

from datetime import datetime, timedelta, timezone
from typing import Literal

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from sqlalchemy import func, desc, select
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.db.database import get_db
from app.db.models import EloEvent, LibDocument, LibProgress, User
from app.routers.auth import get_current_user, get_current_user_optional
from app.core.cloudinary_client import resolve_avatar_url
from app.services.elo import tier_from_elo

router = APIRouter(prefix="/leaderboard", tags=["leaderboard"])


# ============================================================
# SCHEMAS
# ============================================================

class UserRank(BaseModel):
    rank: int
    username: str
    elo: int
    avatar_url: str | None = None
    is_current_user: bool = False


class UniversityRank(BaseModel):
    rank: int
    university: str
    total_elo: int
    member_count: int
    is_current_user_university: bool = False


class LeaderboardUsersResponse(BaseModel):
    top: list[UserRank]
    current_user: UserRank | None = None


class LeaderboardUniversitiesResponse(BaseModel):
    top: list[UniversityRank]
    current_user_university: UniversityRank | None = None


# ============================================================
# ENDPOINTS
# ============================================================

@router.get("/users", response_model=LeaderboardUsersResponse)
def get_users_leaderboard(
    limit: int = 10,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user_optional),
):
    """
    Classement des utilisateurs par ELO.
    Retourne le top N + la position du joueur connecté s'il n'est pas dans le top.
    """
    # Top N users par ELO décroissant
    top_users = (
        db.query(User)
        .filter(User.elo > 0)  # Exclure les users sans activité
        .order_by(desc(User.elo), User.id)  # ELO desc, puis id pour départager
        .limit(limit)
        .all()
    )

    top_list: list[UserRank] = []
    current_user_in_top = False

    for idx, user in enumerate(top_users, start=1):
        is_current = current_user is not None and user.id == current_user.id
        if is_current:
            current_user_in_top = True
        top_list.append(
            UserRank(
                rank=idx,
                username=user.username,
                elo=user.elo,
                avatar_url=resolve_avatar_url(user.avatar_url),
                is_current_user=is_current,
            )
        )

    # Position du joueur connecté s'il n'est pas dans le top
    current_user_rank: UserRank | None = None
    if current_user and not current_user_in_top and current_user.elo > 0:
        # Compter combien d'users ont un ELO supérieur
        rank = (
            db.query(func.count(User.id))
            .filter(User.elo > current_user.elo)
            .scalar()
        ) + 1  # +1 car rank = nombre de gens devant + 1

        current_user_rank = UserRank(
            rank=rank,
            username=current_user.username,
            elo=current_user.elo,
            avatar_url=resolve_avatar_url(current_user.avatar_url),
            is_current_user=True,
        )

    return LeaderboardUsersResponse(top=top_list, current_user=current_user_rank)


@router.get("/universities", response_model=LeaderboardUniversitiesResponse)
def get_universities_leaderboard(
    limit: int = 10,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user_optional),
):
    """
    Classement des universités par somme des ELO de leurs membres.
    Retourne le top N + la position de la fac du joueur connecté.
    """
    # Agrégation : somme des ELO par université
    total_elo_col = func.sum(User.elo).label("total_elo")
    member_count_col = func.count(User.id).label("member_count")
    
    uni_stats = (
        db.query(
            User.university,
            total_elo_col,
            member_count_col,
        )
        .filter(User.university.isnot(None), User.university != "")
        .group_by(User.university)
        .order_by(desc(total_elo_col))
        .all()
    )

    # Construire le classement complet pour trouver la position de la fac du user
    all_unis = [
        {
            "university": row.university,
            "total_elo": row.total_elo or 0,
            "member_count": row.member_count,
        }
        for row in uni_stats
    ]

    user_university = current_user.university if current_user else None

    top_list: list[UniversityRank] = []
    current_user_uni_in_top = False

    for idx, uni in enumerate(all_unis[:limit], start=1):
        is_current_uni = bool(user_university and uni["university"] == user_university)
        if is_current_uni:
            current_user_uni_in_top = True
        top_list.append(
            UniversityRank(
                rank=idx,
                university=uni["university"],
                total_elo=uni["total_elo"],
                member_count=uni["member_count"],
                is_current_user_university=is_current_uni,
            )
        )

    # Position de la fac du joueur si pas dans le top
    current_user_uni_rank: UniversityRank | None = None
    if user_university and not current_user_uni_in_top:
        for idx, uni in enumerate(all_unis, start=1):
            if uni["university"] == user_university:
                current_user_uni_rank = UniversityRank(
                    rank=idx,
                    university=uni["university"],
                    total_elo=uni["total_elo"],
                    member_count=uni["member_count"],
                    is_current_user_university=True,
                )
                break

    return LeaderboardUniversitiesResponse(
        top=top_list,
        current_user_university=current_user_uni_rank,
    )

# ============================================================
# PAGE CLASSEMENT
# ============================================================

PERIODES = {"general": None, "30j": 30, "7j": 7}


def _depuis(jours: int) -> datetime:
    # EloEvent.created_at est enregistré sans fuseau (UTC, server_default now()).
    return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=jours)


def _gains(db: Session, jours: int, user_ids: list[int] | None = None) -> dict[int, int]:
    """Elo gagné (net) par joueur sur les `jours` derniers jours."""
    q = (
        select(EloEvent.user_id, func.sum(EloEvent.delta))
        .where(EloEvent.created_at >= _depuis(jours))
        .group_by(EloEvent.user_id)
    )
    if user_ids is not None:
        if not user_ids:
            return {}
        q = q.where(EloEvent.user_id.in_(user_ids))
    return {uid: int(total or 0) for uid, total in db.execute(q).all()}


def _carte(u: User, rang: int, score: int, gain_7j: int, me: User | None) -> dict:
    t = tier_from_elo(u.elo)
    return {
        "rank": rang,
        "id": u.id,
        "username": u.username,
        "avatar_url": resolve_avatar_url(u.avatar_url),
        "elo": int(u.elo or 0),
        "score": score,
        "tier": t["tier"],
        "university": u.university or "",
        "study_level": u.study_level or "",
        "gain_7j": gain_7j,
        "stats_public": bool(u.stats_public),
        "is_current_user": bool(me and me.id == u.id),
    }


@router.get("/classement")
def classement(
    periode: Literal["general", "30j", "7j"] = "general",
    fac: str | None = None,
    limit: int = Query(100, ge=1, le=200),
    db: Session = Depends(get_db),
    me: User | None = Depends(get_current_user_optional),
):
    """
    Joueurs classés : à l'Elo (general) ou à l'Elo gagné sur 30 / 7 jours.
    `fac` restreint à une université. `me` donne la place du joueur connecté,
    même hors du top.
    """
    jours = PERIODES[periode]
    base = select(User)
    if fac:
        base = base.where(User.university == fac)

    if jours is None:
        users = db.execute(
            base.where(User.elo > 0).order_by(desc(User.elo), User.id)
        ).scalars().all()
        scores = {u.id: int(u.elo or 0) for u in users}
    else:
        gains = {uid: g for uid, g in _gains(db, jours).items() if g > 0}
        users = db.execute(base.where(User.id.in_(list(gains) or [-1]))).scalars().all()
        users.sort(key=lambda u: (-gains[u.id], -int(u.elo or 0), u.id))
        scores = {u.id: gains[u.id] for u in users}

    top = users[:limit]
    ids = [u.id for u in top] + ([me.id] if me else [])
    g7 = _gains(db, 7, ids) if jours != 7 else {u.id: scores[u.id] for u in users}

    items, moi = [], None
    rang = 0
    for i, u in enumerate(users, start=1):
        # Ex aequo : même score, même rang.
        if i == 1 or scores[u.id] != scores[users[i - 2].id]:
            rang = i
        if i <= limit:
            items.append(_carte(u, rang, scores[u.id], g7.get(u.id, 0), me))
        if me and u.id == me.id:
            moi = _carte(u, rang, scores[u.id], g7.get(u.id, 0), me)
        if i > limit and (moi or not me):
            break

    return {
        "periode": periode,
        "fac": fac or None,
        "total_joueurs": len(users),
        "items": items,
        "me": moi,
        "moi": (
            {"id": me.id, "username": me.username, "stats_public": bool(me.stats_public),
             "university": me.university or "", "elo": int(me.elo or 0)}
            if me else None
        ),
    }


def _courbe_elo(db: Session, u: User, jours: int = 30) -> list[dict]:
    """Elo en fin de journée sur les `jours` derniers jours (le dernier point = aujourd'hui)."""
    debut = _depuis(jours)
    events = db.execute(
        select(EloEvent.created_at, EloEvent.delta)
        .where(EloEvent.user_id == u.id, EloEvent.created_at >= debut)
    ).all()
    aujourd_hui = datetime.now(timezone.utc).date()
    par_jour: dict = {}
    for at, delta in events:
        par_jour[at.date()] = par_jour.get(at.date(), 0) + int(delta or 0)
    elo = int(u.elo or 0)
    points = []
    for k in range(jours):
        jour = aujourd_hui - timedelta(days=k)
        points.append({"date": jour.isoformat(), "elo": elo, "gain": par_jour.get(jour, 0)})
        elo -= par_jour.get(jour, 0)
    return list(reversed(points))


@router.get("/joueurs/{user_id}")
def joueur(
    user_id: int,
    db: Session = Depends(get_db),
    me: User | None = Depends(get_current_user_optional),
):
    """Profil d'un joueur ; stats détaillées s'il les laisse visibles (ou si c'est moi)."""
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(404, detail="Joueur introuvable.")
    elo = int(u.elo or 0)
    rang = (db.query(func.count(User.id)).filter(User.elo > elo).scalar() or 0) + 1 if elo > 0 else None
    t = tier_from_elo(elo)
    soi = bool(me and me.id == u.id)
    out = {
        "id": u.id,
        "username": u.username,
        "avatar_url": resolve_avatar_url(u.avatar_url),
        "university": u.university or "",
        "study_level": u.study_level or "",
        "membre_depuis": u.created_at.date().isoformat() if u.created_at else None,
        "elo": elo,
        "rank": rang,
        "total_joueurs": db.query(func.count(User.id)).filter(User.elo > 0).scalar() or 0,
        **t,
        "stats_public": bool(u.stats_public),
        "is_current_user": soi,
        "stats": None,
    }
    if not (u.stats_public or soi):
        return out

    from app.routers.stats import (  # import local : mêmes calculs que la page stats
        _compute_session_counts, _compute_streaks, _compute_success_rate, _get_user_history,
    )
    history = _get_user_history(db, u.id)
    sources = db.execute(
        select(EloEvent.source, func.sum(EloEvent.delta)).where(EloEvent.user_id == u.id).group_by(EloEvent.source)
    ).all()
    lib = db.execute(
        select(func.count(LibProgress.id), func.count(func.distinct(LibProgress.document_id)))
        .join(LibDocument, LibDocument.id == LibProgress.document_id)
        .where(LibProgress.user_id == u.id, LibProgress.passed.is_(True), LibDocument.owner_id.is_(None))
    ).one()
    out["stats"] = {
        **_compute_success_rate(history),
        **_compute_session_counts(history),
        **_compute_streaks(history, 0),
        "gain_7j": _gains(db, 7, [u.id]).get(u.id, 0),
        "gain_30j": _gains(db, 30, [u.id]).get(u.id, 0),
        "elo_30j": _courbe_elo(db, u, 30),
        "elo_par_source": sorted(
            ({"source": src or "autre", "elo": int(total or 0)} for src, total in sources if total),
            key=lambda x: -x["elo"],
        ),
        "lib_points_passage": int(lib[0] or 0),
        "lib_documents": int(lib[1] or 0),
    }
    return out


class VisibiliteIn(BaseModel):
    stats_public: bool


@router.patch("/me/visibilite")
def visibilite(payload: VisibiliteIn, db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    """Afficher ou masquer mes stats détaillées aux autres joueurs."""
    me.stats_public = payload.stats_public
    db.commit()
    return {"stats_public": bool(me.stats_public)}


_EMBED = Path(__file__).resolve().parents[2] / "embeds" / "classement.html"


@router.get("/embed", response_class=HTMLResponse, include_in_schema=False)
def embed():
    contenu = _EMBED.read_text(encoding="utf-8")
    contenu = contenu.replace('API: "https://navire-app-api.onrender.com"', 'API: ""')
    return HTMLResponse(
        '<!DOCTYPE html><html lang="fr"><head><meta charset="UTF-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<title>Classement NAVIRE</title></head>'
        f'<body style="margin:0;background:transparent">{contenu}</body></html>'
    )
