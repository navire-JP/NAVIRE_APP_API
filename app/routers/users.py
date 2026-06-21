from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File as FastAPIFile
from sqlalchemy.orm import Session
from sqlalchemy import func, select, asc

from app.db.database import get_db
from app.db.models import User, QcmSessionHistory, File as FileModel
from app.schemas.auth import ProfileUpdateIn, UserOut
from app.routers.auth import get_current_user
from app.core.cloudinary_client import upload_avatar, is_allowed_image, MAX_AVATAR_BYTES, resolve_avatar_url
from app.core.limits import get_limit


router = APIRouter(prefix="/users", tags=["users"])


def _user_dict(u: User) -> dict:
    """
    Sérialisation manuelle (pas de response_model) pour ne pas dépendre du
    schéma UserOut existant, qu'on ne modifie pas pour rester compatible avec
    le reste du code (auth.py, etc. qui l'utilisent déjà ailleurs).
    """
    return {
        "id": u.id,
        "username": u.username,
        "email": u.email,
        "university": u.university,
        "study_level": u.study_level,
        "avatar_url": resolve_avatar_url(u.avatar_url),
        "plan": u.plan,
        "elo": int(u.elo or 0),
    }


# ============================================================
# Profil — update (université, niveau d'étude, username)
# ============================================================

@router.post("/profile", response_model=UserOut)
def update_profile(
    payload: ProfileUpdateIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Conservé pour compatibilité avec l'existant (université / niveau d'étude).
    Préférer PATCH /users/me pour les nouveaux usages (update partiel incluant
    le username).
    """
    if payload.university is not None:
        current_user.university = payload.university
    if payload.study_level is not None:
        current_user.study_level = payload.study_level
    if payload.username is not None:
        current_user.username = payload.username.strip()

    db.commit()
    db.refresh(current_user)
    return current_user


@router.patch("/me")
def update_me(
    payload: ProfileUpdateIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Update partiel du profil courant. Seuls les champs fournis (non None)
    sont modifiés. Pas de contrainte d'unicité sur username (l'identité de
    référence reste l'id / l'email).
    """
    if payload.username is not None:
        username = payload.username.strip()
        if not username:
            raise HTTPException(status_code=400, detail="Le nom ne peut pas être vide.")
        current_user.username = username

    if payload.university is not None:
        current_user.university = payload.university

    if payload.study_level is not None:
        current_user.study_level = payload.study_level

    db.commit()
    db.refresh(current_user)
    return _user_dict(current_user)


# ============================================================
# Avatar — upload Cloudinary
# ============================================================

@router.post("/me/avatar")
async def update_avatar(
    file: UploadFile = FastAPIFile(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not file or not file.filename:
        raise HTTPException(status_code=400, detail="Fichier manquant.")

    if not is_allowed_image(file.content_type):
        raise HTTPException(
            status_code=400,
            detail="Format non supporté. Utilise JPG, PNG ou WEBP.",
        )

    content = await file.read()
    if len(content) > MAX_AVATAR_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Image trop volumineuse (max {MAX_AVATAR_BYTES // (1024*1024)} Mo).",
        )

    try:
        secure_url = upload_avatar(content, user_id=current_user.id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Échec upload Cloudinary : {e}")

    current_user.avatar_url = secure_url
    db.commit()
    db.refresh(current_user)
    return _user_dict(current_user)


@router.patch("/me/avatar-url")
def update_avatar_url(
    payload: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Enregistre une URL d'avatar déjà uploadée côté client (ex: via le
    Cloudinary Upload Widget, qui upload directement depuis le navigateur
    avec un preset non signé — l'upload ne passe pas par ce backend, on ne
    fait qu'enregistrer l'URL résultante ici).

    Validation minimale : on exige que l'URL pointe bien vers Cloudinary,
    pour éviter d'enregistrer n'importe quelle URL externe arbitraire sur
    le profil d'un utilisateur.
    """
    url = (payload or {}).get("avatar_url", "")
    url = (url or "").strip()

    if not url:
        raise HTTPException(status_code=400, detail="avatar_url manquant.")

    if not url.startswith("https://res.cloudinary.com/"):
        raise HTTPException(
            status_code=400,
            detail="URL invalide — doit provenir de Cloudinary.",
        )

    current_user.avatar_url = url
    db.commit()
    db.refresh(current_user)
    return _user_dict(current_user)


# ============================================================
# Profil — résumé agrégé (page profil complète en un seul appel)
# ============================================================

@router.get("/me/profile-summary")
def get_profile_summary(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Agrège tout ce dont la page profil a besoin :
      - infos user (nom, université, avatar, email, elo, rang)
      - stats QCM (sessions, taux de réussite)
      - documents (nombre + quota)
      - les N users juste au-dessus en ELO (voisins immédiats au classement)
    """
    u = current_user

    # --- Stats QCM (même logique que /users/{username}/public) ---
    qcm_stats = (
        db.query(
            func.count(QcmSessionHistory.id).label("total_sessions"),
            func.sum(QcmSessionHistory.correct_answers).label("total_correct"),
            func.sum(QcmSessionHistory.total_questions).label("total_questions"),
        )
        .filter(QcmSessionHistory.user_id == u.id)
        .one()
    )
    total_sessions = qcm_stats.total_sessions or 0
    total_q = qcm_stats.total_questions or 0
    total_c = qcm_stats.total_correct or 0
    success_rate = round((total_c / total_q * 100) if total_q > 0 else 0.0, 1)

    # --- Rang ELO global ---
    rank = db.execute(
        select(func.count()).select_from(User).where(User.elo > u.elo)
    ).scalar_one()
    rank = int(rank or 0) + 1

    total_ranked_users = db.execute(
        select(func.count()).select_from(User).where(User.elo > 0)
    ).scalar_one()

    # --- Fichiers : nombre actif + quota ---
    files_count = db.execute(
        select(func.count()).select_from(FileModel).where(FileModel.user_id == u.id)
    ).scalar_one()
    files_limit = get_limit(u.plan, "files_total")

    # --- Tous les utilisateurs au-dessus en ELO (classement complet, sans
    # limite) — nécessaire pour le pattern UX "ma ligne reste collée en bas
    # jusqu'à ce que le scroll atteigne ma vraie position dans le classement"
    # côté frontend. Pas de LIMIT ici par choix : voir discussion produit.
    rows_above = db.execute(
        select(User.id, User.username, User.elo, User.avatar_url)
        .where(User.elo > u.elo)
        .order_by(asc(User.elo))
    ).all()

    # rows_above est trié du plus proche (juste au-dessus) au plus loin ;
    # on l'inverse pour un affichage classement classique (meilleur en haut)
    above_list = [
        {
            "rank": rank - 1 - i,
            "user_id": r.id,
            "username": r.username,
            "elo": int(r.elo or 0),
            "avatar_url": resolve_avatar_url(r.avatar_url),
        }
        for i, r in enumerate(reversed(rows_above))
    ]

    return {
        "user": {
            "id": u.id,
            "username": u.username,
            "email": u.email,
            "university": u.university,
            "study_level": u.study_level,
            "avatar_url": resolve_avatar_url(u.avatar_url),
            "plan": u.plan,
            "created_at": u.created_at,
        },
        "elo": {
            "value": int(u.elo or 0),
            "rank": rank,
            "total_ranked_users": int(total_ranked_users or 0),
        },
        "qcm_stats": {
            "total_sessions": total_sessions,
            "total_questions": total_q,
            "total_correct": total_c,
            "success_rate": success_rate,
        },
        "files": {
            "count": int(files_count or 0),
            "limit": files_limit,
        },
        "ranking_above": above_list,
    }


# ============================================================
# Profil public (inchangé)
# ============================================================

@router.get("/{username}/public")
def get_user_public(username: str, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == username).first()
    if not user:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable")

    result = (
        db.query(
            func.count(QcmSessionHistory.id).label("total_sessions"),
            func.sum(QcmSessionHistory.correct_answers).label("total_correct"),
            func.sum(QcmSessionHistory.total_questions).label("total_questions"),
        )
        .filter(QcmSessionHistory.user_id == user.id)
        .one()
    )

    total_sessions = result.total_sessions or 0
    total_q = result.total_questions or 0
    total_c = result.total_correct or 0
    success_rate = round((total_c / total_q * 100) if total_q > 0 else 0.0, 1)

    return {
        "username": user.username,
        "university": user.university or "Non renseignée",
        "avatar_url": resolve_avatar_url(user.avatar_url),
        "elo": user.elo or 0,
        "total_sessions": total_sessions,
        "success_rate": success_rate,
    }