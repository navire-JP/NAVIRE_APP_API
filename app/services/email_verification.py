"""
app/services/email_verification.py
==================================
Preuve qu'une adresse email appartient bien à la personne qui s'inscrit.

Principe : un code à 6 chiffres est envoyé à l'adresse. Une fois le code
rendu, l'API délivre un jeton court (30 minutes) qui atteste de la vérification
et que /auth/register exige pour créer le compte. Sans ce jeton, impossible de
s'inscrire avec l'adresse de quelqu'un d'autre : le code ne part que dans la
boîte du propriétaire.

Le code n'est jamais stocké en clair, il est limité en essais et en durée, et
les demandes successives sont espacées par un délai anti-spam.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import (
    JWT_SECRET,
    JWT_ALGO,
    EMAIL_CODE_TTL_MINUTES,
    EMAIL_CODE_MAX_ATTEMPTS,
    EMAIL_CODE_RESEND_COOLDOWN,
)
from app.core.security import hash_password, verify_password
from app.db.models import EmailVerification
from app.services.email import send_mail, mail_verification_code

EMAIL_TOKEN_SCOPE = "email_verify"
EMAIL_TOKEN_TTL_MINUTES = 30


class VerificationError(Exception):
    """Message directement affichable par le formulaire."""

    def __init__(self, message: str, retry_after: int = 0):
        super().__init__(message)
        self.message = message
        self.retry_after = retry_after


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    """
    Certains pilotes rendent des datetimes sans fuseau selon la colonne.
    Les comparaisons ci-dessous se font toutes en UTC.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def generate_code() -> str:
    """Code à 6 chiffres, tiré par le générateur cryptographique."""
    return f"{secrets.randbelow(1_000_000):06d}"


# ============================================================
# Envoi
# ============================================================

def request_code(db: Session, email: str, username: str = "") -> int:
    """
    Crée (ou remplace) le code de l'adresse et l'envoie par email.
    Retourne le nombre de secondes avant qu'un nouvel envoi soit possible.
    Lève VerificationError si l'envoi est demandé trop tôt.
    """
    email = email.strip().lower()

    row = db.execute(
        select(EmailVerification).where(EmailVerification.email == email)
    ).scalar_one_or_none()

    if row is not None:
        elapsed = (_now() - _aware(row.last_sent_at)).total_seconds()
        if elapsed < EMAIL_CODE_RESEND_COOLDOWN:
            wait = int(EMAIL_CODE_RESEND_COOLDOWN - elapsed)
            raise VerificationError(
                f"Un code vient d'être envoyé. Réessaie dans {wait} secondes.",
                retry_after=wait,
            )

    code = generate_code()

    if row is None:
        row = EmailVerification(email=email, code_hash=hash_password(code), expires_at=_now())
        db.add(row)

    row.code_hash = hash_password(code)
    row.expires_at = _now() + timedelta(minutes=EMAIL_CODE_TTL_MINUTES)
    row.attempts = 0
    row.last_sent_at = _now()
    row.verified_at = None
    db.commit()

    subject, html = mail_verification_code(code, username, EMAIL_CODE_TTL_MINUTES)
    sent = send_mail(to=email, subject=subject, html=html)

    if not sent:
        # Sans email parti, l'utilisateur attendrait un code fantôme.
        raise VerificationError(
            "Impossible d'envoyer l'email pour le moment. Réessaie dans un instant."
        )

    return EMAIL_CODE_RESEND_COOLDOWN


# ============================================================
# Vérification
# ============================================================

def check_code(db: Session, email: str, code: str) -> str:
    """
    Valide le code et retourne le jeton de vérification à passer ensuite à
    /auth/register. Lève VerificationError sinon.
    """
    email = email.strip().lower()
    code = (code or "").strip()

    row = db.execute(
        select(EmailVerification).where(EmailVerification.email == email)
    ).scalar_one_or_none()

    if row is None:
        raise VerificationError("Aucun code en cours pour cette adresse. Demande un nouvel envoi.")

    if _now() > _aware(row.expires_at):
        raise VerificationError("Ce code a expiré. Demande un nouvel envoi.")

    if row.attempts >= EMAIL_CODE_MAX_ATTEMPTS:
        raise VerificationError("Trop d'essais. Demande un nouveau code.")

    if not verify_password(code, row.code_hash):
        row.attempts += 1
        db.commit()
        restant = max(0, EMAIL_CODE_MAX_ATTEMPTS - row.attempts)
        if restant == 0:
            raise VerificationError("Code incorrect. Trop d'essais, demande un nouveau code.")
        raise VerificationError(f"Code incorrect. Il te reste {restant} essai(s).")

    row.verified_at = _now()
    db.commit()

    return issue_email_token(email)


# ============================================================
# Jeton de vérification
# ============================================================

def issue_email_token(email: str) -> str:
    now = _now()
    payload = {
        "sub": email.strip().lower(),
        "scope": EMAIL_TOKEN_SCOPE,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=EMAIL_TOKEN_TTL_MINUTES)).timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)


def email_token_is_valid(token: str, email: str) -> bool:
    """Le jeton prouve-t-il la vérification de CETTE adresse ?"""
    if not token:
        return False
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
    except Exception:
        return False

    if payload.get("scope") != EMAIL_TOKEN_SCOPE:
        return False

    return (payload.get("sub") or "").strip().lower() == email.strip().lower()


def purge_verification(db: Session, email: str) -> None:
    """Supprime la ligne une fois le compte créé."""
    row = db.execute(
        select(EmailVerification).where(EmailVerification.email == email.strip().lower())
    ).scalar_one_or_none()
    if row is not None:
        db.delete(row)
        db.commit()
