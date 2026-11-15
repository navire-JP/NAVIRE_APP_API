"""Codes à 6 chiffres envoyés par email (vérification d'adresse, mot de passe oublié)."""

from __future__ import annotations

import secrets
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.kitly import emails
from app.kitly.config import (
    KITLY_EMAIL_CODE_COOLDOWN_SECONDS,
    KITLY_EMAIL_CODE_MAX_ATTEMPTS,
    KITLY_EMAIL_CODE_TTL_MINUTES,
)
from app.kitly.models import KitlyEmailCode
from app.kitly.security import hash_secret, verify_secret
from app.kitly.utils import aware, now

PURPOSES = ("verify", "reset")


def _get(db: Session, email: str, purpose: str) -> KitlyEmailCode | None:
    return db.execute(
        select(KitlyEmailCode).where(KitlyEmailCode.email == email, KitlyEmailCode.purpose == purpose)
    ).scalar_one_or_none()


def send_code(db: Session, email: str, purpose: str, first_name: str = "", enforce_cooldown: bool = True) -> bool:
    """
    Crée (ou remplace) le code et l'envoie. Lève 429 si une demande vient
    d'être faite pour la même adresse. Renvoie le succès de l'envoi Brevo.
    """
    assert purpose in PURPOSES
    row = _get(db, email, purpose)
    if row and enforce_cooldown:
        elapsed = (now() - aware(row.sent_at)).total_seconds()
        if elapsed < KITLY_EMAIL_CODE_COOLDOWN_SECONDS:
            wait = int(KITLY_EMAIL_CODE_COOLDOWN_SECONDS - elapsed) + 1
            raise HTTPException(429, detail=f"Patiente {wait} s avant de redemander un code.",
                                headers={"Retry-After": str(wait)})

    code = f"{secrets.randbelow(1_000_000):06d}"
    expires = now() + timedelta(minutes=KITLY_EMAIL_CODE_TTL_MINUTES)
    if row is None:
        row = KitlyEmailCode(email=email, purpose=purpose, code_hash=hash_secret(code), expires_at=expires)
        db.add(row)
    else:
        row.code_hash = hash_secret(code)
        row.attempts = 0
        row.sent_at = now()
        row.expires_at = expires
    db.commit()

    if purpose == "verify":
        subject, html = emails.mail_verify_code(first_name, code, KITLY_EMAIL_CODE_TTL_MINUTES)
    else:
        subject, html = emails.mail_reset_code(first_name, code, KITLY_EMAIL_CODE_TTL_MINUTES)
    return emails.send(email, subject, html)


def consume_code(db: Session, email: str, purpose: str, code: str) -> None:
    """Vérifie le code et le supprime. Lève 400 avec un message affichable sinon."""
    row = _get(db, email, purpose)
    bad = HTTPException(400, detail="Code invalide ou expiré.")
    if row is None:
        raise bad
    if aware(row.expires_at) < now() or row.attempts >= KITLY_EMAIL_CODE_MAX_ATTEMPTS:
        db.delete(row)
        db.commit()
        raise HTTPException(400, detail="Code expiré : demandes-en un nouveau.")
    if not verify_secret((code or "").strip(), row.code_hash):
        row.attempts += 1
        db.commit()
        raise bad
    db.delete(row)
    db.commit()
