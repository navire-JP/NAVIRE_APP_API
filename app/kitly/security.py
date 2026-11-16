"""
Authentification Kitly — totalement indépendante de celle de NAVIRE.

Les jetons Kitly portent une audience « kitly » et sont signés avec une clé
propre (cf. config.KITLY_JWT_SECRET) : un jeton NAVIRE est refusé ici, et un
jeton Kitly est refusé par NAVIRE (signature différente).
"""

from __future__ import annotations

from datetime import timedelta

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.kitly.config import (
    KITLY_JWT_ALGO,
    KITLY_JWT_AUDIENCE,
    KITLY_JWT_SECRET,
    KITLY_TOKEN_TTL_MINUTES,
)
from app.kitly.models import KitlyUser
from app.kitly.utils import now

_pwd = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")
_bearer = HTTPBearer(auto_error=False)


def hash_secret(value: str) -> str:
    return _pwd.hash(value)


def verify_secret(value: str, hashed: str) -> bool:
    try:
        return _pwd.verify(value, hashed)
    except Exception:
        return False


def create_token(user: KitlyUser, ttl_minutes: int | None = None, impersonator: KitlyUser | None = None) -> str:
    """
    Jeton de session. `impersonator` : jeton court émis pour un admin qui agit
    à la place du compte (support) — marqué « imp », il ne permet ni paiement,
    ni changement de mot de passe, ni suppression du compte.
    """
    issued = now()
    payload = {
        "sub": str(user.id),
        "aud": KITLY_JWT_AUDIENCE,
        "iss": "kitly",
        "tv": user.token_version,
        "iat": int(issued.timestamp()),
        "exp": int((issued + timedelta(minutes=ttl_minutes or KITLY_TOKEN_TTL_MINUTES)).timestamp()),
    }
    if impersonator is not None:
        payload["imp"] = impersonator.id
        payload["imp_email"] = impersonator.email
    return jwt.encode(payload, KITLY_JWT_SECRET, algorithm=KITLY_JWT_ALGO)


def decode_token(token: str) -> dict:
    return jwt.decode(
        token,
        KITLY_JWT_SECRET,
        algorithms=[KITLY_JWT_ALGO],
        audience=KITLY_JWT_AUDIENCE,
        issuer="kitly",
        options={"require": ["sub", "aud", "exp", "iss"]},
    )


def _user_from_token(db: Session, token: str) -> KitlyUser | None:
    try:
        payload = decode_token(token)
        user_id = int(payload["sub"])
    except Exception:
        return None
    user = db.get(KitlyUser, user_id)
    if not user or not user.is_active:
        return None
    if int(payload.get("tv", -1)) != user.token_version:
        return None  # session révoquée (déconnexion partout, mot de passe changé)
    # Attribut transitoire (non stocké) : session ouverte par un admin à la place du compte.
    user._kitly_impersonator = payload.get("imp_email") if payload.get("imp") else None
    return user


def forbid_impersonation(user: KitlyUser) -> None:
    """Actions réservées au titulaire du compte, interdites en mode admin « à sa place »."""
    if getattr(user, "_kitly_impersonator", None):
        raise HTTPException(403, detail="Action réservée au titulaire du compte (session ouverte par l'équipe Kitly).")


def current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> KitlyUser:
    if not creds:
        raise HTTPException(status_code=401, detail="Connexion requise.")
    user = _user_from_token(db, creds.credentials)
    if not user:
        raise HTTPException(status_code=401, detail="Session expirée, reconnecte-toi.")
    return user


def optional_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> KitlyUser | None:
    if not creds:
        return None
    return _user_from_token(db, creds.credentials)


def admin_user(user: KitlyUser = Depends(current_user)) -> KitlyUser:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Réservé à l'équipe Kitly.")
    return user
