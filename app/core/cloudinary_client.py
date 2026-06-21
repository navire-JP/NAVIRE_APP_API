"""
app/core/cloudinary_client.py

Helper centralisé pour l'upload d'images sur Cloudinary.
Lit CLOUDINARY_URL depuis l'environnement (format :
cloudinary://<api_key>:<api_secret>@<cloud_name>), configuré automatiquement
par le SDK Cloudinary au premier import — aucune config manuelle nécessaire
si la variable d'env est bien définie sur Render.

Utilisation :
    from app.core.cloudinary_client import upload_avatar, resolve_avatar_url
    url = upload_avatar(file_bytes, public_id=f"user_{user_id}")
    display_url = resolve_avatar_url(user.avatar_url)  # jamais None
"""
from __future__ import annotations

import cloudinary
import cloudinary.uploader

# Le SDK lit automatiquement CLOUDINARY_URL depuis os.environ au moment de
# l'import. Pas besoin d'appeler cloudinary.config() manuellement tant que
# la variable d'env existe (c'est le cas sur Render désormais).

ALLOWED_CONTENT_TYPES = {
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp",
}

MAX_AVATAR_BYTES = 8 * 1024 * 1024  # 8 MB — large car Cloudinary recompresse

AVATAR_FOLDER = "navire/avatars"

# ============================================================
# AVATAR PAR DÉFAUT
# ============================================================
# Image affichée pour tout utilisateur n'ayant jamais uploadé de photo.
# Centralisé ici : toute route qui renvoie avatar_url doit passer par
# resolve_avatar_url() plutôt que de renvoyer user.avatar_url brut, pour que
# le fallback soit cohérent sur tout le backend (auth, users, leaderboard…).
DEFAULT_AVATAR_URL = "https://image.noelshack.com/fichiers/2026/25/7/1782078362-294f2b4fc8c430f7ff19c7afec00de5a.jpg"


def resolve_avatar_url(avatar_url: str | None) -> str:
    """
    Retourne l'URL d'avatar à afficher : celle de l'utilisateur si définie,
    sinon l'avatar par défaut. Ne retourne jamais None — toute route qui
    expose un avatar à un client doit utiliser cette fonction.
    """
    return avatar_url if avatar_url else DEFAULT_AVATAR_URL


def is_allowed_image(content_type: str | None) -> bool:
    return (content_type or "").lower() in ALLOWED_CONTENT_TYPES


def upload_avatar(file_bytes: bytes, user_id: int) -> str:
    """
    Upload une image vers Cloudinary avec crop/resize automatique en carré
    400x400, recadrage intelligent sur le visage si détecté (gravity=face),
    sinon centré. Retourne l'URL https sécurisée de l'image transformée.

    public_id fixe par user (avatar_user_<id>) => un nouvel upload remplace
    l'ancien avatar automatiquement (overwrite=True), pas d'accumulation de
    fichiers orphelins sur Cloudinary.
    """
    result = cloudinary.uploader.upload(
        file_bytes,
        folder=AVATAR_FOLDER,
        public_id=f"avatar_user_{user_id}",
        overwrite=True,
        invalidate=True,  # purge le cache CDN pour voir le nouvel avatar immédiatement
        resource_type="image",
        transformation=[
            {
                "width": 400,
                "height": 400,
                "crop": "fill",
                "gravity": "face",
            },
            {"quality": "auto", "fetch_format": "auto"},
        ],
    )
    return result["secure_url"]