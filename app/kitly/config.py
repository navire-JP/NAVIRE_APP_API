"""
Configuration Kitly — uniquement des variables préfixées KITLY_.

Règle de séparation :
  - ce que voit l'utilisateur final (expéditeur des emails, libellé bancaire,
    marque Stripe) et ce qui touche à la sécurité (clé JWT, clés Stripe) n'a
    JAMAIS de repli sur la configuration NAVIRE ;
  - l'infrastructure pure (clé API Brevo, compte Cloudinary) peut retomber sur
    celle de NAVIRE tant qu'aucune clé Kitly n'est fournie : l'expéditeur et le
    dossier de stockage restent ceux de Kitly.
"""

from __future__ import annotations

import hashlib
import hmac
import os


def _csv(value: str) -> list[str]:
    return [x.strip() for x in value.split(",") if x.strip()]


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


# ============================================================
# Front (site Framer Kitly)
# ============================================================
KITLY_FRONTEND_URL = os.getenv("KITLY_FRONTEND_URL", "https://kitly.fr").rstrip("/")
# Domaine affiché dans les URLs de vitrine (kitly.fr/<slug>).
KITLY_PUBLIC_HOST = os.getenv("KITLY_PUBLIC_HOST", "kitly.fr")
# Politique de confidentialité Kitly (citée par le formulaire de contact des vitrines)
KITLY_PRIVACY_URL = os.getenv("KITLY_PRIVACY_URL", f"{KITLY_FRONTEND_URL}/confidentialite")

# ============================================================
# Auth
# ============================================================
_NAVIRE_JWT_SECRET = os.getenv("JWT_SECRET", "") or "DEV_ONLY_CHANGE_ME"


def _derive_kitly_secret() -> str:
    """
    Clé de signature des jetons Kitly.

    KITLY_JWT_SECRET doit être défini en production. À défaut, une clé est
    DÉRIVÉE de celle de NAVIRE (HMAC) : elle en est différente, si bien qu'un
    jeton NAVIRE ne peut jamais être accepté par Kitly, ni l'inverse — sans ça,
    le compte Kitly n°12 ouvrirait le compte NAVIRE n°12.
    """
    explicit = os.getenv("KITLY_JWT_SECRET", "")
    if explicit:
        return explicit
    return hmac.new(_NAVIRE_JWT_SECRET.encode(), b"kitly-jwt-v1", hashlib.sha256).hexdigest()


KITLY_JWT_SECRET = _derive_kitly_secret()
KITLY_JWT_ALGO = "HS256"
KITLY_JWT_AUDIENCE = "kitly"
KITLY_TOKEN_TTL_MINUTES = _int("KITLY_TOKEN_TTL_MINUTES", 60 * 24 * 14)

# Comptes promus administrateurs Kitly à l'inscription / connexion.
KITLY_ADMIN_EMAILS = {e.lower() for e in _csv(os.getenv("KITLY_ADMIN_EMAILS", ""))}

# Codes email (vérification d'adresse, mot de passe oublié)
KITLY_EMAIL_CODE_TTL_MINUTES = _int("KITLY_EMAIL_CODE_TTL_MINUTES", 15)
KITLY_EMAIL_CODE_MAX_ATTEMPTS = _int("KITLY_EMAIL_CODE_MAX_ATTEMPTS", 5)
KITLY_EMAIL_CODE_COOLDOWN_SECONDS = _int("KITLY_EMAIL_CODE_COOLDOWN_SECONDS", 60)

# Versions des documents acceptés à l'inscription (traçabilité du consentement)
KITLY_CGU_VERSION = os.getenv("KITLY_CGU_VERSION", "2026-11")
KITLY_PRIVACY_VERSION = os.getenv("KITLY_PRIVACY_VERSION", "2026-11")

# ============================================================
# Emails (Brevo)
# ============================================================
KITLY_BREVO_API_KEY = os.getenv("KITLY_BREVO_API_KEY", "") or os.getenv("BREVO_API_KEY", "")
KITLY_SENDER_EMAIL = os.getenv("KITLY_SENDER_EMAIL", "no-reply@kitly.fr")
KITLY_SENDER_NAME = os.getenv("KITLY_SENDER_NAME", "Kitly")

# ============================================================
# Médias (Cloudinary)
# ============================================================
# Format : cloudinary://<api_key>:<api_secret>@<cloud_name>
KITLY_CLOUDINARY_URL = os.getenv("KITLY_CLOUDINARY_URL", "") or os.getenv("CLOUDINARY_URL", "")
KITLY_CLOUDINARY_FOLDER = os.getenv("KITLY_CLOUDINARY_FOLDER", "kitly")
KITLY_MAX_IMAGE_BYTES = _int("KITLY_MAX_IMAGE_BYTES", 8 * 1024 * 1024)

# ============================================================
# Stripe — compte Kitly, jamais celui de NAVIRE
# ============================================================
KITLY_STRIPE_SECRET_KEY = os.getenv("KITLY_STRIPE_SECRET_KEY", "")
# Webhook « compte plateforme » (abonnements Billing)
KITLY_STRIPE_WEBHOOK_SECRET = os.getenv("KITLY_STRIPE_WEBHOOK_SECRET", "")
# Webhook « comptes connectés » (Stripe Connect : account.updated, …)
KITLY_STRIPE_CONNECT_WEBHOOK_SECRET = os.getenv("KITLY_STRIPE_CONNECT_WEBHOOK_SECRET", "")
# Price IDs des abonnements (prix récurrents mensuels du compte Kitly)
KITLY_STRIPE_PRICES = {
    "custom": os.getenv("KITLY_STRIPE_PRICE_CUSTOM", ""),
    "premium": os.getenv("KITLY_STRIPE_PRICE_PREMIUM", ""),
}
# Suffixe de libellé bancaire ajouté aux paiements ponctuels (le libellé de
# base est celui du compte Stripe Kitly : « KITLY »).
KITLY_STATEMENT_DESCRIPTOR_SUFFIX = os.getenv("KITLY_STATEMENT_DESCRIPTOR_SUFFIX", "KITLY")
KITLY_CONNECT_COUNTRY = os.getenv("KITLY_CONNECT_COUNTRY", "FR")
# Commission marketplace en points de base (1000 = 10 %)
KITLY_MARKETPLACE_COMMISSION_BPS = _int("KITLY_MARKETPLACE_COMMISSION_BPS", 1000)

# Retours Stripe (pages du site Framer)
KITLY_BILLING_SUCCESS_URL = os.getenv(
    "KITLY_BILLING_SUCCESS_URL", f"{KITLY_FRONTEND_URL}/dashboard?subscription=success"
)
KITLY_BILLING_CANCEL_URL = os.getenv(
    "KITLY_BILLING_CANCEL_URL", f"{KITLY_FRONTEND_URL}/tarifs?subscription=cancelled"
)
KITLY_BILLING_RETURN_URL = os.getenv("KITLY_BILLING_RETURN_URL", f"{KITLY_FRONTEND_URL}/dashboard")
KITLY_CONNECT_RETURN_URL = os.getenv(
    "KITLY_CONNECT_RETURN_URL", f"{KITLY_FRONTEND_URL}/dashboard?payments=return"
)
KITLY_CONNECT_REFRESH_URL = os.getenv(
    "KITLY_CONNECT_REFRESH_URL", f"{KITLY_FRONTEND_URL}/dashboard?payments=refresh"
)

# ============================================================
# Marketplace & statistiques
# ============================================================
# Fenêtre « Les nouveaux sur Kitly » (jours depuis la première publication)
KITLY_NEW_PROFILES_DAYS = _int("KITLY_NEW_PROFILES_DAYS", 7)
# Période de référence du classement « Les profils populaires »
KITLY_POPULAR_WINDOW_DAYS = _int("KITLY_POPULAR_WINDOW_DAYS", 30)
# Durée de mise en cache du classement populaire (secondes)
KITLY_POPULAR_CACHE_SECONDS = _int("KITLY_POPULAR_CACHE_SECONDS", 600)
# Sel du hachage des visiteurs : aucune IP n'est stockée en clair.
KITLY_VISITOR_SALT = os.getenv("KITLY_VISITOR_SALT", "") or hmac.new(
    _NAVIRE_JWT_SECRET.encode(), b"kitly-visitor-v1", hashlib.sha256
).hexdigest()


def missing_env() -> list[str]:
    """Variables Kitly absentes, signalées au démarrage (sans bloquer)."""
    wanted = {
        "KITLY_JWT_SECRET": os.getenv("KITLY_JWT_SECRET", ""),
        "KITLY_STRIPE_SECRET_KEY": KITLY_STRIPE_SECRET_KEY,
        "KITLY_STRIPE_WEBHOOK_SECRET": KITLY_STRIPE_WEBHOOK_SECRET,
        "KITLY_STRIPE_CONNECT_WEBHOOK_SECRET": KITLY_STRIPE_CONNECT_WEBHOOK_SECRET,
        "KITLY_STRIPE_PRICE_CUSTOM": KITLY_STRIPE_PRICES["custom"],
        "KITLY_STRIPE_PRICE_PREMIUM": KITLY_STRIPE_PRICES["premium"],
        "KITLY_BREVO_API_KEY (ou BREVO_API_KEY)": KITLY_BREVO_API_KEY,
    }
    return [k for k, v in wanted.items() if not v]
