"""
app/services/discord_link.py
============================
Codes de liaison compte NAVIRE ↔ Discord.

Format unique : ``AJ`` + 6 chiffres (ex. ``AJ601405``).

Deux origines, même format, durées de vie différentes :

* ``profile``  — bouton « Connecter mes comptes » de la page profil.
  Valable quelques minutes (DISCORD_LINK_CODE_PROFILE_TTL, 300 s par défaut).
  Accessible à tout le monde, payant ou non : c'est le chemin normal.
* ``purchase`` — envoyé par email au moment d'un achat.
  Valable plusieurs jours (DISCORD_LINK_CODE_PURCHASE_TTL_DAYS, 7 par défaut),
  pour que l'élève puisse lier son compte sans repasser par le site.

La vérification exige **trois facteurs concordants** : identifiant du compte,
email, et code. Un code seul ne suffit pas, et un code est toujours rattaché à
un utilisateur précis — il ne peut pas servir à en authentifier un autre.

Six chiffres ne font qu'un million de combinaisons : le compteur ``attempts``
brûle le code après quelques essais ratés, ce qui rend le balayage inopérant
même sur la fenêtre longue des codes d'achat.
"""

from __future__ import annotations

import logging
import os
import re
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, desc, func, select
from sqlalchemy.orm import Session

from app.db.models import DiscordLinkCode, User

logger = logging.getLogger(__name__)

CODE_PREFIX = "AJ"
CODE_DIGITS = 6
CODE_RE = re.compile(rf"^{CODE_PREFIX}\d{{{CODE_DIGITS}}}$")

SOURCE_PROFILE = "profile"
SOURCE_PURCHASE = "purchase"

# Durées de vie
PROFILE_TTL_SECONDS = int(os.getenv("DISCORD_LINK_CODE_PROFILE_TTL", "300"))
PURCHASE_TTL_DAYS = int(os.getenv("DISCORD_LINK_CODE_PURCHASE_TTL_DAYS", "7"))

# Garde-fous
MAX_ATTEMPTS = int(os.getenv("DISCORD_LINK_CODE_MAX_ATTEMPTS", "5"))
MAX_CODES_PER_WINDOW = int(os.getenv("DISCORD_LINK_CODE_MAX_PER_WINDOW", "5"))
RATE_WINDOW_SECONDS = int(os.getenv("DISCORD_LINK_CODE_RATE_WINDOW", "900"))

_GENERATION_RETRIES = 12


class LinkCodeRateLimited(Exception):
    """Trop de codes demandés sur la fenêtre glissante."""

    def __init__(self, retry_after_seconds: int):
        self.retry_after_seconds = retry_after_seconds
        super().__init__("Trop de demandes de code.")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    """Postgres peut renvoyer un datetime naïf selon le driver — on normalise."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def normalize_code(raw: str) -> str:
    """« aj 601-405 » → « AJ601405 ». Tolère espaces, tirets et minuscules."""
    return re.sub(r"[\s\-_.]", "", (raw or "")).upper()


def is_valid_format(code: str) -> bool:
    return bool(CODE_RE.match(code))


def _random_code() -> str:
    digits = "".join(str(secrets.randbelow(10)) for _ in range(CODE_DIGITS))
    return f"{CODE_PREFIX}{digits}"


def _purge_stale(db: Session, user_id: int) -> None:
    """
    Retire les vieux codes consommés ou expirés de cet utilisateur.

    Les lignes de la fenêtre de limitation sont épargnées : c'est sur elles que
    repose le comptage anti-abus, les effacer reviendrait à remettre le compteur
    à zéro à chaque demande.

    synchronize_session="fetch" : le critère doit être évalué en SQL. Évalué en
    Python, il casse dès qu'un datetime revient naïf du driver, et laisse en
    prime des objets périmés dans la session.
    """
    cutoff = _utcnow() - timedelta(seconds=RATE_WINDOW_SECONDS)
    db.execute(
        delete(DiscordLinkCode).where(
            DiscordLinkCode.user_id == user_id,
            DiscordLinkCode.created_at < cutoff,
            (DiscordLinkCode.used_at.isnot(None))
            | (DiscordLinkCode.expires_at < _utcnow()),
        ),
        execution_options={"synchronize_session": "fetch"},
    )


def active_codes(db: Session, user_id: int) -> list[DiscordLinkCode]:
    """Codes encore valables de cet utilisateur, du plus récent au plus ancien."""
    return list(
        db.execute(
            select(DiscordLinkCode)
            .where(
                DiscordLinkCode.user_id == user_id,
                DiscordLinkCode.used_at.is_(None),
                DiscordLinkCode.expires_at > _utcnow(),
            )
            .order_by(desc(DiscordLinkCode.created_at))
        )
        .scalars()
        .all()
    )


def issue_code(
    db: Session,
    user: User,
    source: str = SOURCE_PROFILE,
    *,
    enforce_rate_limit: bool = True,
) -> DiscordLinkCode:
    """
    Émet un code de liaison pour `user`.

    Un seul code `profile` vit à la fois : demander un nouveau code invalide le
    précédent, pour qu'un code affiché puis abandonné ne traîne pas. Les codes
    `purchase` coexistent (plusieurs achats possibles avant la liaison).

    Lève LinkCodeRateLimited si l'utilisateur en demande trop vite.
    """
    now = _utcnow()

    if enforce_rate_limit:
        window_start = now - timedelta(seconds=RATE_WINDOW_SECONDS)
        recent = db.execute(
            select(func.count())
            .select_from(DiscordLinkCode)
            .where(
                DiscordLinkCode.user_id == user.id,
                DiscordLinkCode.created_at >= window_start,
            )
        ).scalar_one()
        if recent >= MAX_CODES_PER_WINDOW:
            raise LinkCodeRateLimited(RATE_WINDOW_SECONDS)

    _purge_stale(db, user.id)

    if source == SOURCE_PROFILE:
        for row in active_codes(db, user.id):
            if row.source == SOURCE_PROFILE:
                row.used_at = now

    ttl = (
        timedelta(seconds=PROFILE_TTL_SECONDS)
        if source == SOURCE_PROFILE
        else timedelta(days=PURCHASE_TTL_DAYS)
    )

    # Le format ne laisse qu'un million de valeurs : on retente en cas de
    # collision avec un code encore vivant.
    for _ in range(_GENERATION_RETRIES):
        candidate = _random_code()
        clash = db.execute(
            select(DiscordLinkCode).where(
                DiscordLinkCode.code == candidate,
                DiscordLinkCode.used_at.is_(None),
                DiscordLinkCode.expires_at > now,
            )
        ).scalars().first()
        if clash:
            continue

        row = DiscordLinkCode(
            user_id=user.id,
            code=candidate,
            source=source,
            expires_at=now + ttl,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    raise RuntimeError("Impossible de générer un code de liaison disponible.")


def seconds_left(row: DiscordLinkCode) -> int:
    return max(0, int((_as_utc(row.expires_at) - _utcnow()).total_seconds()))


# ── Vérification ─────────────────────────────────────────────────────────────


def _burn_attempt(db: Session, user_id: int) -> None:
    """
    Comptabilise un essai raté sur le code actif le plus récent. Au-delà de
    MAX_ATTEMPTS le code est consommé : l'utilisateur doit en redemander un,
    ce qui borne toute tentative de balayage à quelques essais.
    """
    rows = active_codes(db, user_id)
    if not rows:
        return
    row = rows[0]
    row.attempts = (row.attempts or 0) + 1
    if row.attempts >= MAX_ATTEMPTS:
        row.used_at = _utcnow()
    db.commit()


def verify_and_link(
    db: Session,
    *,
    user_id: int,
    email: str,
    code: str,
    discord_id: str,
) -> tuple[bool, str, User | None]:
    """
    Valide le triplet (identifiant, email, code) et lie `discord_id` au compte.

    Retourne (ok, message, user). Les échecs attendus ne lèvent pas : ils
    remontent en (False, message) pour que le bot les affiche tels quels.

    Les messages restent volontairement peu bavards sur ce qui a échoué
    exactement : ils ne doivent pas permettre de savoir si un identifiant ou un
    email existe.
    """
    code = normalize_code(code)
    email = (email or "").strip().lower()

    if not is_valid_format(code):
        return False, f"Format de code invalide (attendu : {CODE_PREFIX}123456).", None

    user = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
    if not user or (user.email or "").lower() != email:
        return False, "Identifiant, email ou code incorrect.", None

    now = _utcnow()
    row = db.execute(
        select(DiscordLinkCode).where(
            DiscordLinkCode.user_id == user.id,
            DiscordLinkCode.code == code,
        )
    ).scalars().first()

    if row is None:
        _burn_attempt(db, user.id)
        return False, "Identifiant, email ou code incorrect.", None

    if (row.attempts or 0) >= MAX_ATTEMPTS:
        return False, "Trop d'essais sur ce code. Demande-en un nouveau.", None

    if row.used_at is not None:
        return False, "Ce code a déjà été utilisé. Demande-en un nouveau.", None

    if _as_utc(row.expires_at) < now:
        return False, "Ce code a expiré. Demande-en un nouveau depuis ton profil.", None

    conflict = db.execute(
        select(User).where(User.discord_id == discord_id)
    ).scalars().first()
    if conflict and conflict.id != user.id:
        return (
            False,
            "Ce compte Discord est déjà lié à un autre compte NAVIRE.",
            None,
        )

    user.discord_id = discord_id
    row.used_at = now
    db.commit()
    db.refresh(user)

    return True, "Comptes liés.", user
