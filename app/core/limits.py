"""
app/core/limits.py
==================
Source de vérité des limites par plan d'abonnement.

Plans : free | membre | membre+ | prepa

Importé par les routers qcm, flash et files pour enforcer les limites
avant chaque opération sensible.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.db.models import User


# ============================================================
# LIMITES PAR PLAN
# ============================================================

# None = illimité
PLAN_LIMITS: dict[str, dict[str, Any]] = {
    "free": {
        "qcm_per_day":      1,      # sessions QCM max par jour calendaire
        "flashcards_total": 200,    # nombre total de flashcards stockées
        "files_total":      1,      # nombre de fichiers hébergés simultanément
        "file_ttl_hours":   24,     # durée de vie des fichiers en heures (None = infini)
    },
    "membre": {
        "qcm_per_day":      None,
        "flashcards_total": 500,
        "files_total":      7,
        "file_ttl_hours":   None,
    },
    "membre+": {
        "qcm_per_day":      None,
        "flashcards_total": 1000,
        "files_total":      24,
        "file_ttl_hours":   None,
    },
    "prepa": {
        # Programme d'été. L'élève a un accès large à NAVIRE pendant sa
        # période. Ces limites concernent UNIQUEMENT les features NAVIRE
        # (QCM, flashcards, host de fichiers perso) — le contenu PREPASSERELLE
        # (cours, sujets, dépôts) vit dans des tables dédiées et n'est PAS
        # décompté ici. L'accès au contenu prepa est gaté par le plan + l'année
        # (voir has_active_prepa_access / check_prepa_access plus bas).
        "qcm_per_day":      None,   # illimité (inclus dans l'offre)
        "flashcards_total": 1000,   # généreux, cohérent avec le tarif
        "files_total":      7,      # host perso de l'élève (PDF à lui)
        "file_ttl_hours":   None,   # pas d'expiration pendant la période
    },
    "beta": {
        "qcm_per_day":      None,
        "flashcards_total": 500,
        "files_total":      7,
        "file_ttl_hours":   None,
    },
}

# Fallback si un plan inconnu se retrouve en base (ne devrait pas arriver)
_DEFAULT_PLAN = "free"


# ============================================================
# ACCESSEURS
# ============================================================

def get_limits(plan: str) -> dict[str, Any]:
    """Retourne le dict de limites pour un plan donné."""
    return PLAN_LIMITS.get(plan, PLAN_LIMITS[_DEFAULT_PLAN])


def get_limit(plan: str, key: str) -> Any:
    """Retourne une limite précise pour un plan. Retourne None si illimité."""
    return get_limits(plan).get(key)


# ============================================================
# HELPERS DE VÉRIFICATION
# Lèvent une HTTPException 403 si la limite est dépassée.
# À injecter directement dans les endpoints des routers.
# ============================================================

def check_qcm_daily_limit(user: User, db: Session) -> None:
    limit = get_limit(user.plan, "qcm_per_day")
    if limit is None:
        return

    from app.db.models import QcmSessionHistory

    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    sessions_today = (
        db.query(QcmSessionHistory)
        .filter(
            QcmSessionHistory.user_id == user.id,
            QcmSessionHistory.started_at >= today_start,
            # ✅ Ne compter que les sessions où l'utilisateur a réellement joué
            (QcmSessionHistory.correct_answers + QcmSessionHistory.wrong_answers) > 0,
        )
        .count()
    )

    if sessions_today >= limit:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "QCM_DAILY_LIMIT_REACHED",
                "message": f"Limite de {limit} session(s) QCM par jour atteinte.",
                "plan": user.plan,
                "limit": limit,
                "used": sessions_today,
            },
        )


def check_flashcard_limit(user: User, db: Session) -> None:
    """
    Vérifie que l'utilisateur n'a pas atteint son quota total de flashcards.

    Utilisé dans : routers/flash.py → POST /flash/cards (création d'une carte)
    """
    limit = get_limit(user.plan, "flashcards_total")
    if limit is None:
        return

    from app.db.models import FlashCard, FlashDeck  # import local

    current_count = (
        db.query(FlashCard)
        .join(FlashDeck, FlashCard.deck_id == FlashDeck.id)
        .filter(FlashDeck.user_id == user.id)
        .count()
    )

    if current_count >= limit:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "FLASHCARD_LIMIT_REACHED",
                "message": f"Limite de {limit} flashcard(s) atteinte.",
                "plan": user.plan,
                "limit": limit,
                "used": current_count,
            },
        )


def check_file_limit(user: User, db: Session) -> None:
    """
    Vérifie que l'utilisateur n'a pas atteint son quota de fichiers hébergés.

    Utilisé dans : routers/files.py → POST /files/upload
    """
    limit = get_limit(user.plan, "files_total")
    if limit is None:
        return

    from app.db.models import File  # import local

    current_count = (
        db.query(File)
        .filter(File.user_id == user.id)
        .count()
    )

    if current_count >= limit:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "FILE_LIMIT_REACHED",
                "message": f"Limite de {limit} fichier(s) hébergé(s) atteinte.",
                "plan": user.plan,
                "limit": limit,
                "used": current_count,
            },
        )


def get_file_ttl(plan: str) -> int | None:
    """
    Retourne la durée de vie d'un fichier en heures selon le plan.
    Retourne None si le fichier ne doit pas expirer (membre / membre+).

    Utilisé dans : routers/files.py au moment du calcul de expires_at.
    """
    return get_limit(plan, "file_ttl_hours")

# ============================================================
# ACCÈS PREPASSERELLE
# ============================================================
# Le plan "prepa" est à durée limitée (paiement unique → prepa_expires_at).
# Ces deux helpers centralisent la règle d'accès pour que le router prepa
# ne réimplémente pas la logique d'expiration.
#
# NB : le filtrage par année (un L1 ne voit que le contenu L1) se fait au
# niveau du router, car il nécessite l'objet cours/exercice
# (course.annee == user.prepa_annee). Ici on ne juge que l'accès global.

def has_active_prepa_access(user: User) -> bool:
    """
    True si l'utilisateur a un accès PREPASSERELLE valide (plan prepa non expiré).
    Version booléenne — pour conditionner un affichage ou une réponse API.
    """
    if user.plan != "prepa":
        return False
    if user.prepa_expires_at is None:
        # Un plan prepa sans date de fin est anormal : on refuse par sécurité.
        return False
    return user.prepa_expires_at > datetime.now(timezone.utc)


def check_prepa_access(user: User) -> None:
    """
    Lève une HTTPException 403 si l'utilisateur n'a pas d'accès prepa actif.
    Version bloquante — à injecter en garde dans les endpoints du router prepa.
    """
    if has_active_prepa_access(user):
        return

    expired = user.plan == "prepa" and user.prepa_expires_at is not None
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "code": "PREPA_EXPIRED" if expired else "PREPA_ACCESS_REQUIRED",
            "message": (
                "Votre accès PREPASSERELLE a expiré."
                if expired
                else "Accès réservé aux inscrits PREPASSERELLE."
            ),
            "plan": user.plan,
        },
    )