from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File as FastAPIFile
from sqlalchemy.orm import Session
from sqlalchemy import func, select, asc, desc

from app.db.database import get_db
from app.db.models import User, QcmSessionHistory, File as FileModel
from app.schemas.auth import PasswordChangeIn, ProfileUpdateIn, UserOut, validate_password
from app.routers.auth import get_current_user
from app.core.security import hash_password, verify_password, create_access_token
from app.core.cloudinary_client import upload_avatar, is_allowed_image, MAX_AVATAR_BYTES, resolve_avatar_url
from app.core.config import DISCORD_INVITE_URL
from app.core.limits import get_limit
from app.services import deepwork as deepwork_service
from app.services.discord_link import (
    LinkCodeRateLimited,
    SOURCE_PROFILE,
    issue_code,
    seconds_left,
)
from app.services.email import discord_sync_channel_url, mail_password_changed, send_mail


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
        "discord_linked": bool(u.discord_id),
        # Le front en a besoin pour choisir entre « Modifier mon mot de passe »
        # (compte email) et « Définir un mot de passe » (compte Google/Facebook,
        # qui n'en a aucun tant qu'il n'en crée pas un ici).
        "has_password": bool(u.password_hash),
        "oauth_provider": u.oauth_provider,
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
# Mot de passe — changement depuis la page profil
# ============================================================

@router.post("/me/password")
def change_my_password(
    payload: PasswordChangeIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Change le mot de passe du compte connecté (roue « paramètres » de la page
    profil).

    Deux cas :
      - compte email : l'ancien mot de passe est obligatoire et vérifié, pour
        qu'un jeton volé ne suffise pas à verrouiller le compte de son
        propriétaire ;
      - compte Google/Facebook (password_hash NULL) : il n'y a pas d'ancien
        mot de passe à fournir, la route sert alors à en *définir* un. Le
        compte garde sa connexion par fournisseur et gagne la connexion par
        email + mot de passe.

    La nouvelle valeur passe par la même politique qu'à l'inscription
    (validate_password) : 8 à 72 caractères, 1 majuscule, 1 chiffre ou symbole.

    Un nouveau jeton est renvoyé pour que le front remplace celui qu'il garde
    en local sans forcer une reconnexion.
    """
    has_password = bool(current_user.password_hash)

    # 1) Vérification de l'identité pour les comptes qui ont déjà un mot de passe
    if has_password:
        if not payload.current_password:
            raise HTTPException(
                status_code=400,
                detail="Mot de passe actuel requis.",
            )
        if not verify_password(payload.current_password, current_user.password_hash):
            raise HTTPException(
                status_code=400,
                detail="Mot de passe actuel incorrect.",
            )

    # 2) Politique de mot de passe (identique à l'inscription)
    try:
        validate_password(payload.new_password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # 3) Refus d'un « changement » qui n'en est pas un
    if has_password and verify_password(payload.new_password, current_user.password_hash):
        raise HTTPException(
            status_code=400,
            detail="Le nouveau mot de passe doit être différent de l'actuel.",
        )

    current_user.password_hash = hash_password(payload.new_password)
    db.commit()
    db.refresh(current_user)

    # 4) Alerte de sécurité par email — best effort, ne bloque jamais la
    # réponse : c'est par ce message que le titulaire du compte apprend qu'un
    # mot de passe a été changé sans lui.
    try:
        subject, html = mail_password_changed(current_user.username, was_set=not has_password)
        send_mail(current_user.email, subject, html)
    except Exception:
        pass

    return {
        "ok": True,
        "access_token": create_access_token(str(current_user.id)),
        "token_type": "bearer",
        "user": _user_dict(current_user),
    }


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
# Profil — bouton « Connecter mes comptes » (liaison Discord)
# ============================================================

@router.post("/me/discord-code")
def create_discord_link_code(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Émet un code de liaison Discord à durée courte pour l'utilisateur connecté.

    C'est le chemin ouvert à tous : aucun achat n'est nécessaire, n'importe
    quel titulaire d'un compte NAVIRE peut lier son Discord. Le front affiche
    le code renvoyé, le décompte, et le lien vers le salon de liaison.

    Le code seul ne suffit pas à s'authentifier : le bot exige aussi
    l'identifiant du compte et l'email, tous deux rappelés dans la réponse
    pour que la page profil puisse les afficher à recopier.
    """
    try:
        row = issue_code(db, current_user, SOURCE_PROFILE)
    except LinkCodeRateLimited as e:
        raise HTTPException(
            status_code=429,
            detail="Trop de codes demandés. Réessaie dans quelques minutes.",
            headers={"Retry-After": str(e.retry_after_seconds)},
        )
    except RuntimeError:
        raise HTTPException(
            status_code=503,
            detail="Génération de code indisponible. Réessaie dans un instant.",
        )

    return {
        "code":          row.code,
        "expires_at":    row.expires_at,
        "expires_in":    seconds_left(row),
        "user_id":       current_user.id,
        "email":         current_user.email,
        "already_linked": bool(current_user.discord_id),
        "discord_url":   discord_sync_channel_url() or DISCORD_INVITE_URL or None,
    }


@router.post("/me/discord-unlink")
def unlink_discord(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Délie le compte Discord courant du compte NAVIRE (ex: mauvais compte lié
    par erreur, changement de compte Discord). L'utilisateur peut ensuite
    relier un autre compte via /me/discord-code.

    Retire aussi les rôles Discord gérés par NAVIRE (abonnement + matières
    Prép'AdJuris) sur le membre — best-effort, ne bloque jamais la réponse :
    une fois discord_id vidé, plus rien ne resynchronisera ce membre, le rôle
    resterait sinon indéfiniment même après un désabonnement.
    """
    if not current_user.discord_id:
        raise HTTPException(status_code=400, detail="Aucun compte Discord lié.")

    discord_id = current_user.discord_id
    current_user.discord_id = None
    db.commit()
    db.refresh(current_user)

    try:
        from app.bot_discord.role_sync import remove_all_navire_roles_sync
        remove_all_navire_roles_sync(discord_id)
    except Exception:
        pass

    return _user_dict(current_user)


# ============================================================
# Deep work — statistiques détaillées
# ============================================================

@router.get("/me/deepwork")
def get_my_deepwork(
    limit: int = 10,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Détail des sessions deep work (salon vocal Discord) : agrégats + dernières
    sessions. `profile-summary` renvoie déjà les agrégats pour l'encart du
    profil ; cette route sert au détail, sans recharger tout le profil.
    """
    return {
        "discord_linked": bool(current_user.discord_id),
        "stats":          deepwork_service.compute_stats(db, current_user.id),
        "sessions":       deepwork_service.recent_sessions(db, current_user.id, limit=limit),
        "goal_choices":   deepwork_service.GOAL_CHOICES,
    }


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

    # --- Tous les utilisateurs EN DESSOUS en ELO (classement complet, sans
    # limite). Triés par ELO décroissant : le premier en dessous a le rang
    # rank+1, le suivant rank+2, etc. Inclut explicitement User.elo == 0 ou
    # négatif si jamais présents — pas de filtre arbitraire ici, seulement
    # "strictement inférieur à mon ELO".
    rows_below = db.execute(
        select(User.id, User.username, User.elo, User.avatar_url)
        .where(User.elo < u.elo)
        .order_by(desc(User.elo))
    ).all()

    below_list = [
        {
            "rank": rank + 1 + i,
            "user_id": r.id,
            "username": r.username,
            "elo": int(r.elo or 0),
            "avatar_url": resolve_avatar_url(r.avatar_url),
        }
        for i, r in enumerate(rows_below)
    ]

    # --- Deep work (sessions vocales Discord) — alimente l'encart Discord du
    # profil. Toujours présent, même sans compte Discord lié : le front affiche
    # alors des compteurs à zéro plutôt qu'un encart qui change de forme.
    deepwork_stats = deepwork_service.compute_stats(db, u.id)

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
            "discord_linked": bool(u.discord_id),
            "has_password": bool(u.password_hash),
            "oauth_provider": u.oauth_provider,
        },
        "deepwork": deepwork_stats,
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
        "ranking_below": below_list,
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