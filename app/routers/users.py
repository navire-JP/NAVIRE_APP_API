from __future__ import annotations

import re
from urllib.parse import urlsplit

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Query, UploadFile, File as FastAPIFile
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func, select, asc, desc

from app.db.database import get_db
from app.db.models import User, QcmSessionHistory, VeilleItem, File as FileModel
from app.schemas.auth import PasswordChangeIn, ProfileUpdateIn, UserOut, validate_password
from app.routers.auth import get_current_user, get_current_user_optional
from app.core.security import hash_password, verify_password, create_access_token
from app.core.cloudinary_client import (
    MAX_AVATAR_BYTES,
    is_allowed_image,
    is_allowed_proof,
    resolve_avatar_url,
    upload_avatar,
    upload_banner,
    upload_proof,
)
from app.core.config import DISCORD_INVITE_URL
from app.core.limits import get_user_limit
from app.services import deepwork as deepwork_service
from app.services.discord_link import (
    LinkCodeRateLimited,
    SOURCE_PROFILE,
    active_codes,
    issue_code,
    seconds_left,
)
from app.services.email import discord_sync_channel_url, mail_password_changed, send_mail
from app.services import user_categories


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
        **_profile_fields(u),
    }


def _article_fields(db: Session, u: User) -> dict:
    """
    Statut vérifié et articles : le profil d'un doctorant affiche le nombre
    d'articles publiés sous ses statistiques.
    """
    from app.services import articles as articles_svc

    badges = user_categories.user_badges(db, u)
    count = db.execute(
        select(func.count(VeilleItem.id)).where(
            VeilleItem.author_id == u.id, VeilleItem.kind == articles_svc.ARTICLE_KIND,
        )
    ).scalar_one()
    return {
        "badges": badges,
        "is_doctorant": "Doctorant" in badges,
        "articles_count": int(count or 0),
    }


def _profile_fields(u: User) -> dict:
    """Champs de la page profil façon X, communs à toutes les sérialisations."""
    return {
        "banner_url": u.banner_url,
        "bio": u.bio,
        "linkedin_url": u.linkedin_url,
        "stats_public": bool(u.stats_public),
        "created_at": u.created_at,
    }


# Adresse d'un profil LinkedIn personnel : linkedin.com/in/<identifiant>, avec
# ou sans www., sous-domaine de langue (fr.linkedin.com) accepté.
_LINKEDIN_HOST = re.compile(r"^([a-z]{2,3}\.|www\.)?linkedin\.com$")
_LINKEDIN_SLUG = re.compile(r"^/in/([A-Za-z0-9\-_%À-ÿ]{3,100})/?$")


def _linkedin_url(raw: str) -> str | None:
    """
    Vérifie et normalise un lien de profil LinkedIn. Renvoie None pour une
    chaîne vide (déliaison). L'URL stockée est toujours de la forme
    https://www.linkedin.com/in/<identifiant>, pour qu'aucun lien arbitraire
    ne s'affiche sur un profil.
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = "https://" + raw
    try:
        parts = urlsplit(raw)
    except ValueError:
        parts = None
    host = (parts.hostname or "").lower() if parts else ""
    m = _LINKEDIN_SLUG.match(parts.path) if parts and _LINKEDIN_HOST.match(host) else None
    if not m:
        raise HTTPException(
            status_code=400,
            detail="Lien LinkedIn invalide : colle l'adresse de ton profil (linkedin.com/in/…).",
        )
    return f"https://www.linkedin.com/in/{m.group(1)}"


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
        user_categories.apply_study_level(db, current_user, payload.study_level)
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

    # Un niveau à vérifier (ex. Doctorant) ouvre une demande pour la console :
    # le niveau actuel reste en place jusqu'à la validation.
    level_status = "unchanged"
    if payload.study_level is not None:
        level_status = user_categories.apply_study_level(db, current_user, payload.study_level)

    if payload.bio is not None:
        current_user.bio = " ".join(payload.bio.split()) or None

    if payload.linkedin_url is not None:
        current_user.linkedin_url = _linkedin_url(payload.linkedin_url)

    if payload.stats_public is not None:
        current_user.stats_public = payload.stats_public

    db.commit()
    db.refresh(current_user)
    return {**_user_dict(current_user), "study_level_status": level_status}


# ============================================================
# Statuts vérifiés (ex. badge Doctorant) — validés dans la console
# ============================================================

@router.get("/me/verifications")
def my_verifications(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Demandes de statut de l'élève, plus récentes d'abord, et badges acquis."""
    return {
        "badges": user_categories.user_badges(db, current_user),
        "items": [
            user_categories.verification_dict(v)
            for v in user_categories.user_verifications(db, current_user)
        ],
    }


@router.post("/me/verifications")
async def request_my_verification(
    label: str = Form(..., max_length=120),
    message: str = Form(default="", max_length=4000),
    proof: UploadFile | None = FastAPIFile(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Demande (ou complète) un statut vérifié, ex. label="Doctorant".
    multipart : label, message (justificatif), proof (image ou PDF, facultatif).
    """
    option = user_categories.find_option(db, "study_level", label.strip())
    if option is None or not option.active or not option.requires_verification:
        raise HTTPException(status_code=400, detail="Ce statut ne demande pas de vérification.")

    content = None
    if proof is not None and proof.filename:
        if not is_allowed_proof(proof.content_type):
            raise HTTPException(status_code=400, detail="Justificatif : JPG, PNG, WEBP ou PDF.")
        content = await proof.read()
        if len(content) > MAX_AVATAR_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"Justificatif trop volumineux (max {MAX_AVATAR_BYTES // (1024*1024)} Mo).",
            )

    v = user_categories.request_verification(db, current_user, option, message=message)
    if v.status == "approved":
        db.commit()
        return user_categories.verification_dict(v)
    if not (v.message or "").strip() and content is None:
        db.rollback()
        raise HTTPException(status_code=400, detail="Ajoute quelques mots ou un justificatif.")

    if content is not None:
        try:
            v.proof_url = upload_proof(content, user_id=current_user.id, request_id=v.id)
        except Exception as e:
            db.rollback()
            raise HTTPException(status_code=502, detail=f"Échec de l'envoi du justificatif : {e}")

    db.commit()
    db.refresh(v)
    return user_categories.verification_dict(v)


# ============================================================
# Parcours d'accueil (après l'inscription)
# ============================================================

class OnboardingIn(BaseModel):
    done: bool = True


@router.post("/me/onboarding")
def set_my_onboarding(
    payload: OnboardingIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Marque le parcours d'accueil comme terminé (ou à refaire avec done=false)."""
    current_user.onboarding_completed_at = datetime.now(timezone.utc) if payload.done else None
    db.commit()
    return {"onboarding_done": current_user.onboarding_completed_at is not None}


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
# Bannière — upload Cloudinary (page profil façon X)
# ============================================================

@router.post("/me/banner")
async def update_banner(
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
        secure_url = upload_banner(content, user_id=current_user.id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Échec upload Cloudinary : {e}")

    current_user.banner_url = secure_url
    db.commit()
    db.refresh(current_user)
    return _user_dict(current_user)


@router.delete("/me/banner")
def delete_banner(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Retire la bannière : la page profil revient au dégradé par défaut."""
    current_user.banner_url = None
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
    ranking: bool = Query(default=True),
):
    """
    Agrège tout ce dont la page profil a besoin :
      - infos user (nom, université, avatar, email, elo, rang)
      - stats QCM (sessions, taux de réussite)
      - documents (nombre + quota)
      - les N users juste au-dessus en ELO (voisins immédiats au classement)
      - le code de liaison Discord encore en cours, s'il en reste un
      - les stats deep work (encart Discord)
      - l'état de l'abonnement, lu en base seulement (aucun appel Stripe) :
        le détail (carte, factures, prochain prélèvement) est servi par
        GET /subscriptions/billing, à l'ouverture des paramètres
      - la première page de ses posts de veille (`posts`, voir GET
        /users/{id}/posts pour la suite)

    `ranking=false` saute le classement complet (ranking_above/below vides) :
    la page profil façon X ne l'affiche plus, et c'est la requête la plus
    lourde de la route.
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
    files_limit = get_user_limit(u, "files_total")

    # --- Tous les utilisateurs au-dessus en ELO (classement complet, sans
    # limite) — nécessaire pour le pattern UX "ma ligne reste collée en bas
    # jusqu'à ce que le scroll atteigne ma vraie position dans le classement"
    # côté frontend. Pas de LIMIT ici par choix : voir discussion produit.
    rows_above = db.execute(
        select(User.id, User.username, User.elo, User.avatar_url)
        .where(User.elo > u.elo)
        .order_by(asc(User.elo))
    ).all() if ranking else []

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
    ).all() if ranking else []

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

    # --- Code de liaison Discord encore valable ---
    # Sans ça, un rechargement de la page profil faisait disparaître le code
    # affiché et son décompte : l'élève devait en demander un autre, alors que
    # l'ancien courait toujours et que la génération est limitée à quelques
    # codes par quart d'heure. On renvoie donc le code en cours et le temps
    # qu'il lui reste, pour que la page reprenne le décompte là où il en est.
    code_actif = next(
        (row for row in active_codes(db, u.id) if row.source == SOURCE_PROFILE),
        None,
    )
    discord_link_code = None
    if code_actif:
        restant = seconds_left(code_actif)
        if restant > 0:
            discord_link_code = {
                "code": code_actif.code,
                "expires_at": code_actif.expires_at,
                "expires_in": restant,
                "user_id": u.id,
                "email": u.email,
                "discord_url": discord_sync_channel_url() or DISCORD_INVITE_URL or None,
            }

    # --- Deep work (sessions vocales Discord) — alimente l'encart Discord du
    # profil. Toujours présent, même sans compte Discord lié : le front affiche
    # alors une invitation plutôt qu'un encart qui change de forme.
    deepwork_stats = deepwork_service.compute_stats(db, u.id)

    # --- Abonnement : de quoi afficher l'état et l'alerte d'impayé ---
    from app.routers.subscriptions import billing_status
    subscription = billing_status(db, u)

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
            **_profile_fields(u),
            **_article_fields(db, u),
        },
        "posts": _posts_page(db, u.id, viewer=u, offset=0, limit=POSTS_PAGE),
        "deepwork": deepwork_stats,
        "subscription": subscription,
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
        "discord_link_code": discord_link_code,
    }


# ============================================================
# Recherche d'élèves (profil : « Rechercher un élève »)
# ============================================================

@router.get("/search")
def search_users(
    q: str = Query("", max_length=80),
    limit: int = Query(8, ge=1, le=20),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Élèves dont le pseudo (ou la fac) contient q, les mieux classés d'abord.
    Sert à ouvrir le profil d'un autre élève depuis la page profil."""
    q = " ".join(q.split())
    if len(q) < 2:
        return {"items": []}
    like = "%" + q.replace("%", "").replace("_", "") + "%"
    rows = db.execute(
        select(User)
        .where(User.username.is_not(None), (User.username.ilike(like)) | (User.university.ilike(like)))
        .order_by(desc(User.elo), asc(User.id))
        .limit(limit)
    ).scalars().all()
    return {"items": [
        {
            "id": u.id,
            "username": u.username,
            "avatar_url": resolve_avatar_url(u.avatar_url),
            "university": u.university,
            "elo": int(u.elo or 0),
            "is_me": u.id == current_user.id,
        }
        for u in rows
    ]}


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


# ============================================================
# Page profil façon X : profil d'un autre élève, et posts de veille
# ============================================================

POSTS_PAGE = 10
POSTS_PAGE_MAX = 30


def _posts_page(db: Session, author_id: int, viewer: User | None, offset: int, limit: int) -> dict:
    """
    Actus de veille postées par `author_id`, de la plus récente à la plus
    ancienne, au même format que le hub Actualités (GET /veille/actus) pour
    que les deux pages rendent une actu de la même façon.
    """
    # Import local : veille.py importe déjà beaucoup, et rien ici ne doit
    # charger le routeur veille avant que l'app ne l'ait monté.
    from app.routers.veille import _date_key, _hub_item, _read_ids, _saved_ids

    total = db.execute(
        select(func.count(VeilleItem.id)).where(VeilleItem.author_id == author_id)
    ).scalar_one()
    items = db.execute(
        select(VeilleItem)
        .where(VeilleItem.author_id == author_id)
        .order_by(_date_key().desc(), VeilleItem.id.desc())
        .offset(offset)
        .limit(limit)
    ).scalars().all()
    read_ids, saved_ids = _read_ids(db, viewer), _saved_ids(db, viewer)
    return {
        "total": int(total or 0),
        "offset": offset,
        "has_more": offset + len(items) < (total or 0),
        "items": [_hub_item(it, read_ids, saved_ids) for it in items],
    }


@router.get("/{user_id}/posts")
def get_user_posts(
    user_id: int,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=POSTS_PAGE, ge=1, le=POSTS_PAGE_MAX),
    db: Session = Depends(get_db),
    viewer: User | None = Depends(get_current_user_optional),
):
    """Posts de veille d'un élève, page par page (« Voir plus » du profil)."""
    if not db.get(User, user_id):
        raise HTTPException(status_code=404, detail="Utilisateur introuvable")
    return _posts_page(db, user_id, viewer=viewer, offset=offset, limit=limit)


@router.get("/{user_id}/profile")
def get_user_profile(
    user_id: int,
    db: Session = Depends(get_db),
    viewer: User | None = Depends(get_current_user_optional),
):
    """
    Profil vu par un autre élève (page profil ouverte avec ?u=<id>).

    Toujours visibles : nom, photo, bannière, bio, LinkedIn, date d'arrivée,
    Elo et posts de veille. Le reste (rang, QCM, documents, deep work) n'est
    servi que si le profil est public (`stats_public`) ou que c'est le sien :
    sinon `stats` vaut None et la page affiche « profil privé ».
    Jamais d'email, d'abonnement ni de code Discord ici.
    """
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable")

    is_me = viewer is not None and viewer.id == u.id
    stats = None
    if u.stats_public or is_me:
        rank = db.execute(
            select(func.count()).select_from(User).where(User.elo > u.elo)
        ).scalar_one()
        total_ranked = db.execute(
            select(func.count()).select_from(User).where(User.elo > 0)
        ).scalar_one()
        qcm = (
            db.query(
                func.count(QcmSessionHistory.id).label("total_sessions"),
                func.sum(QcmSessionHistory.correct_answers).label("total_correct"),
                func.sum(QcmSessionHistory.total_questions).label("total_questions"),
            )
            .filter(QcmSessionHistory.user_id == u.id)
            .one()
        )
        total_q = qcm.total_questions or 0
        total_c = qcm.total_correct or 0
        files_count = db.execute(
            select(func.count()).select_from(FileModel).where(FileModel.user_id == u.id)
        ).scalar_one()
        stats = {
            "rank": int(rank or 0) + 1,
            "total_ranked_users": int(total_ranked or 0),
            "qcm_stats": {
                "total_sessions": qcm.total_sessions or 0,
                "success_rate": round((total_c / total_q * 100) if total_q > 0 else 0.0, 1),
            },
            "files": {"count": int(files_count or 0)},
            "deepwork": deepwork_service.compute_stats(db, u.id),
        }

    return {
        "is_me": is_me,
        "user": {
            "id": u.id,
            "username": u.username,
            "university": u.university,
            "avatar_url": resolve_avatar_url(u.avatar_url),
            "plan": u.plan,
            "discord_linked": bool(u.discord_id),
            **_article_fields(db, u),
            **_profile_fields(u),
        },
        "elo": {"value": int(u.elo or 0)},
        "stats": stats,
        "posts": _posts_page(db, u.id, viewer=viewer, offset=0, limit=POSTS_PAGE),
    }

