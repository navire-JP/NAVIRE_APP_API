"""
Leaderboard router — Classements utilisateurs et universités.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import func, desc
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.db.database import get_db
from app.db.models import User
from app.routers.auth import get_current_user_optional

router = APIRouter(prefix="/leaderboard", tags=["leaderboard"])


# ============================================================
# SCHEMAS
# ============================================================

class UserRank(BaseModel):
    rank: int
    username: str
    elo: int
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
        is_current_uni = user_university and uni["university"] == user_university
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