"""Petits utilitaires Kitly : dates, empreinte visiteur, limitation de débit."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone

from fastapi import HTTPException, Request
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.kitly.config import KITLY_VISITOR_SALT
from app.kitly.models import KitlyRateEvent


def now() -> datetime:
    return datetime.now(timezone.utc)


def today() -> date:
    return now().date()


def aware(value: datetime | None) -> datetime | None:
    """SQLite rend des datetimes sans fuseau : tout est comparé en UTC."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def iso(value: datetime | None) -> str | None:
    value = aware(value)
    return value.isoformat() if value else None


# ============================================================
# Visiteur
# ============================================================

def client_ip(request: Request) -> str:
    """IP du client derrière le proxy Render (premier X-Forwarded-For)."""
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "0.0.0.0"


def visitor_hash(request: Request, day: date | None = None) -> str:
    """
    Empreinte d'un visiteur pour une journée : sha256(sel + IP + navigateur +
    jour). Non réversible, et différente d'un jour à l'autre.
    """
    day = day or today()
    ua = request.headers.get("user-agent", "")[:300]
    raw = f"{KITLY_VISITOR_SALT}|{client_ip(request)}|{ua}|{day.isoformat()}"
    return hashlib.sha256(raw.encode()).hexdigest()


_BOT_RE = re.compile(r"bot|crawl|spider|slurp|preview|facebookexternalhit|headless|python-requests|curl", re.I)


def looks_like_bot(request: Request) -> bool:
    ua = request.headers.get("user-agent", "")
    return not ua or bool(_BOT_RE.search(ua))


# ============================================================
# Limitation de débit (stockée en base : survit aux redémarrages)
# ============================================================

def _rate_key(key: str) -> str:
    """
    Les clés contiennent une IP ou une adresse email : seule leur empreinte
    salée est stockée (stable, donc utilisable pour compter, mais jamais en clair).
    """
    prefix = key.split(":", 1)[0]
    digest = hashlib.sha256(f"{KITLY_VISITOR_SALT}|rate|{key}".encode()).hexdigest()
    return f"{prefix}:{digest}"


def rate_count(db: Session, key: str, window_seconds: int) -> int:
    since = now() - timedelta(seconds=window_seconds)
    return db.execute(
        select(func.count()).select_from(KitlyRateEvent).where(
            KitlyRateEvent.key == _rate_key(key), KitlyRateEvent.created_at >= since
        )
    ).scalar_one()


def rate_hit(db: Session, key: str) -> None:
    db.add(KitlyRateEvent(key=_rate_key(key)))
    db.commit()


def rate_clear(db: Session, key: str) -> None:
    db.execute(delete(KitlyRateEvent).where(KitlyRateEvent.key == _rate_key(key)))
    db.commit()


def rate_guard(db: Session, key: str, limit: int, window_seconds: int, message: str) -> None:
    """Refuse (429) si `key` a déjà atteint `limit` événements dans la fenêtre."""
    if rate_count(db, key, window_seconds) >= limit:
        raise HTTPException(status_code=429, detail=message, headers={"Retry-After": str(window_seconds)})


def purge_rate_events(db: Session, older_than_hours: int = 48) -> int:
    cutoff = now() - timedelta(hours=older_than_hours)
    res = db.execute(delete(KitlyRateEvent).where(KitlyRateEvent.created_at < cutoff))
    db.commit()
    return res.rowcount or 0


# ============================================================
# Slugs
# ============================================================

SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,38}[a-z0-9])$")

# Chemins du site Kitly qui ne peuvent pas devenir une vitrine.
RESERVED_SLUGS = {
    "admin", "api", "app", "aide", "help", "about", "a-propos", "auth", "login", "connexion",
    "register", "inscription", "logout", "signup", "dashboard", "tableau-de-bord", "compte",
    "account", "settings", "parametres", "marketplace", "freelances", "explorer", "decouvrir",
    "tarifs", "pricing", "subscribe", "abonnement", "cgu", "cgv", "mentions-legales", "legal",
    "confidentialite", "privacy", "cookies", "contact", "blog", "support", "kitly", "navire",
    "static", "assets", "media", "www", "mail", "faq", "fonctionnalites", "features", "checkout",
    "paiement", "paiements", "commande", "commandes", "vitrine", "vitrines", "profil", "profile",
}


def normalize_text(text: str) -> str:
    """Minuscules sans accents, pour une recherche tolérante (« developpeur » trouve « Développeur »)."""
    text = unicodedata.normalize("NFKD", text or "")
    return " ".join("".join(c for c in text if not unicodedata.combining(c)).lower().split())


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return re.sub(r"-{2,}", "-", text)[:40].strip("-")


def slug_problem(slug: str) -> str | None:
    """Message d'erreur si le slug est invalide, None sinon (ne vérifie pas l'unicité)."""
    if not SLUG_RE.match(slug or ""):
        return "3 à 40 caractères : lettres minuscules, chiffres et tirets (pas en début ni en fin)."
    if "--" in slug:
        return "Pas de double tiret."
    if slug in RESERVED_SLUGS:
        return "Cette adresse est réservée."
    return None
