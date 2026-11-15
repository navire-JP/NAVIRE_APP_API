"""
Upload d'images Kitly (Cloudinary).

Les identifiants sont passés à chaque appel (jamais via la config globale du
SDK, utilisée par NAVIRE) : KITLY_CLOUDINARY_URL permet un compte distinct,
sinon le compte NAVIRE est utilisé, toujours dans le dossier « kitly/ ».
"""

from __future__ import annotations

import secrets
from urllib.parse import urlparse

from fastapi import HTTPException

from app.kitly.config import KITLY_CLOUDINARY_FOLDER, KITLY_CLOUDINARY_URL, KITLY_MAX_IMAGE_BYTES

ALLOWED_TYPES = {"image/jpeg", "image/jpg", "image/png", "image/webp", "image/gif"}
MEDIA_PURPOSES = {"avatar", "banner", "logo", "offer", "portfolio", "block", "background"}


def _credentials() -> dict:
    url = urlparse(KITLY_CLOUDINARY_URL)
    if url.scheme != "cloudinary" or not url.hostname or not url.username:
        raise HTTPException(503, detail="Stockage d'images non configuré (KITLY_CLOUDINARY_URL).")
    return {"cloud_name": url.hostname, "api_key": url.username, "api_secret": url.password or ""}


def check_image(content_type: str | None, size: int) -> None:
    if (content_type or "").lower() not in ALLOWED_TYPES:
        raise HTTPException(415, detail="Format accepté : JPG, PNG, WEBP ou GIF.")
    if size > KITLY_MAX_IMAGE_BYTES:
        raise HTTPException(413, detail=f"Image trop lourde (max {KITLY_MAX_IMAGE_BYTES // (1024 * 1024)} Mo).")
    if size == 0:
        raise HTTPException(400, detail="Fichier vide.")


def upload_image(data: bytes, user_id: int, purpose: str) -> str:
    """Envoie l'image et rend son URL https (Cloudinary recompresse et sert en CDN)."""
    import cloudinary.uploader  # import local : le SDK n'est utile qu'ici

    creds = _credentials()
    try:
        result = cloudinary.uploader.upload(
            data,
            folder=f"{KITLY_CLOUDINARY_FOLDER}/u{user_id}/{purpose}",
            public_id=secrets.token_hex(8),
            resource_type="image",
            overwrite=False,
            transformation=[{"width": 2000, "height": 2000, "crop": "limit"}, {"quality": "auto", "fetch_format": "auto"}],
            **creds,
        )
    except Exception as exc:
        raise HTTPException(502, detail=f"Envoi de l'image impossible : {exc}")
    return result["secure_url"]
