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

from app.core.default_avatars import DEFAULT_AVATAR_URLS

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
# Chaque compte reçoit à sa création un avatar prédéfini tiré au hasard
# (app/core/default_avatars.py), enregistré dans users.avatar_url. Ce fallback
# ne sert plus qu'en filet de sécurité, si une ligne n'a pas d'avatar.
# Toute route qui renvoie avatar_url doit passer par resolve_avatar_url().
DEFAULT_AVATAR_URL = DEFAULT_AVATAR_URLS[0]


def resolve_avatar_url(avatar_url: str | None) -> str:
    """
    Retourne l'URL d'avatar à afficher : celle de l'utilisateur si définie,
    sinon un avatar prédéfini. Ne retourne jamais None.
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

BANNER_FOLDER = "navire/banners"


def upload_banner(file_bytes: bytes, user_id: int) -> str:
    """
    Bannière de la page profil : recadrée en 1500x500 (format 3:1 de X),
    centrée. public_id fixe par user, comme l'avatar : un nouvel upload
    remplace l'ancienne bannière.
    """
    result = cloudinary.uploader.upload(
        file_bytes,
        folder=BANNER_FOLDER,
        public_id=f"banner_user_{user_id}",
        overwrite=True,
        invalidate=True,
        resource_type="image",
        transformation=[
            {"width": 1500, "height": 500, "crop": "fill", "gravity": "auto"},
            {"quality": "auto", "fetch_format": "auto"},
        ],
    )
    return result["secure_url"]


VERIFICATION_FOLDER = "navire/verifications"
PROOF_CONTENT_TYPES = ALLOWED_CONTENT_TYPES | {"application/pdf"}


def is_allowed_proof(content_type: str | None) -> bool:
    return (content_type or "").lower() in PROOF_CONTENT_TYPES


def upload_proof(file_bytes: bytes, user_id: int, request_id: int) -> str:
    """
    Justificatif d'une demande de statut (attestation de doctorat, carte…),
    image ou PDF. Lu uniquement depuis la console admin.
    """
    result = cloudinary.uploader.upload(
        file_bytes,
        folder=VERIFICATION_FOLDER,
        public_id=f"proof_user_{user_id}_{request_id}",
        overwrite=True,
        resource_type="auto",
    )
    return result["secure_url"]


# ============================================================
# POSTS DES ÉLÈVES : image et document joints
# ============================================================
POST_FOLDER = "navire/posts"
MAX_POST_DOC_BYTES = 10 * 1024 * 1024  # 10 Mo
POST_DOC_TYPES = {
    "application/pdf": "pdf",
    "application/msword": "doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
}
POST_DOC_EXTS = {"pdf", "doc", "docx"}


def post_doc_ext(filename: str | None, content_type: str | None) -> str | None:
    """Extension d'un document de post accepté (pdf, doc, docx), sinon None."""
    ext = (filename or "").rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""
    if ext in POST_DOC_EXTS:
        return ext
    return POST_DOC_TYPES.get((content_type or "").lower())


def upload_post_image(file_bytes: bytes, user_id: int, key: str) -> str:
    """Image d'un post : 1600 px de large au plus, proportions gardées."""
    result = cloudinary.uploader.upload(
        file_bytes,
        folder=POST_FOLDER,
        public_id=f"img_user_{user_id}_{key}",
        resource_type="image",
        transformation=[
            {"width": 1600, "height": 1600, "crop": "limit"},
            {"quality": "auto", "fetch_format": "auto"},
        ],
    )
    return result["secure_url"]


def upload_post_doc(file_bytes: bytes, user_id: int, key: str, ext: str) -> str:
    """
    Document d'un post (devoir partagé : PDF ou Word), stocké tel quel
    (resource_type raw : Cloudinary ne le transforme pas, l'extension reste
    dans l'adresse pour que le navigateur l'ouvre correctement).
    """
    result = cloudinary.uploader.upload(
        file_bytes,
        folder=POST_FOLDER,
        public_id=f"doc_user_{user_id}_{key}.{ext}",
        resource_type="raw",
    )
    return result["secure_url"]
