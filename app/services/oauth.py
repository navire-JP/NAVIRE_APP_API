"""
app/services/oauth.py
=====================
Vérification, côté serveur, des jetons Google et Facebook envoyés par le
formulaire d'inscription.

Pourquoi côté serveur : le navigateur peut envoyer n'importe quoi. Si l'API
faisait confiance à l'email transmis par la page, n'importe qui pourrait
s'inscrire avec l'adresse d'un autre. Ici, l'email n'est jamais lu depuis la
requête : il est récupéré directement auprès de Google ou de Facebook à partir
du jeton, après avoir vérifié que ce jeton a bien été émis pour NAVIRE.

Ce contrôle d'audience est le point critique. Sans lui, un jeton obtenu par une
application tierce quelconque (celle de l'attaquant) serait accepté ici : c'est
l'attaque dite de substitution de jeton.

Aucune dépendance supplémentaire : tout passe par httpx, déjà utilisé par le
projet, et par les points d'entrée publics de Google et Facebook.
"""

from __future__ import annotations

import logging
import httpx

from app.core.config import (
    GOOGLE_CLIENT_ID,
    FACEBOOK_APP_ID,
    FACEBOOK_APP_SECRET,
)

logger = logging.getLogger(__name__)

GOOGLE_TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
FACEBOOK_DEBUG_URL = "https://graph.facebook.com/debug_token"
FACEBOOK_ME_URL = "https://graph.facebook.com/v19.0/me"

TIMEOUT = 10.0


class OAuthError(Exception):
    """Jeton invalide, expiré, ou émis pour une autre application."""


class OAuthNotConfigured(Exception):
    """Identifiants du fournisseur absents de la configuration serveur."""


def _get(url: str, params: dict) -> dict:
    try:
        response = httpx.get(url, params=params, timeout=TIMEOUT)
    except Exception as exc:
        logger.error("OAuth: appel %s impossible : %s", url, exc)
        raise OAuthError("Fournisseur injoignable.")

    if response.status_code != 200:
        logger.warning("OAuth: %s a répondu %s : %s", url, response.status_code, response.text[:200])
        raise OAuthError("Jeton refusé par le fournisseur.")

    try:
        return response.json()
    except Exception:
        raise OAuthError("Réponse illisible du fournisseur.")


# ============================================================
# Google
# ============================================================

def verify_google(access_token: str) -> dict:
    """
    Valide un access_token Google et retourne le profil.

    Deux appels :
      1. tokeninfo, qui dit pour quelle application le jeton a été émis.
         On refuse tout jeton dont l'audience n'est pas notre Client ID.
      2. userinfo, qui donne l'email et le nom.

    Retourne {"sub", "email", "email_verified", "name", "given_name", "picture"}.
    """
    if not GOOGLE_CLIENT_ID:
        raise OAuthNotConfigured("GOOGLE_CLIENT_ID absent de la configuration serveur.")

    info = _get(GOOGLE_TOKENINFO_URL, {"access_token": access_token})

    audience = info.get("aud") or info.get("azp")
    if audience != GOOGLE_CLIENT_ID:
        logger.warning("OAuth Google: audience inattendue %r", audience)
        raise OAuthError("Ce jeton Google n'a pas été émis pour NAVIRE.")

    profile = _get(GOOGLE_USERINFO_URL, {"access_token": access_token})

    email = (profile.get("email") or "").strip().lower()
    if not email:
        raise OAuthError("Google n'a pas communiqué d'adresse email.")

    # Google renvoie parfois email_verified sous forme de chaîne "true".
    verified = profile.get("email_verified", info.get("email_verified"))
    verified = str(verified).lower() == "true"
    if not verified:
        raise OAuthError("Cette adresse Google n'est pas vérifiée chez Google.")

    return {
        "provider": "google",
        "sub": str(profile.get("sub") or info.get("sub") or ""),
        "email": email,
        "email_verified": True,
        "name": profile.get("name") or "",
        "given_name": profile.get("given_name") or "",
        "picture": profile.get("picture") or "",
    }


# ============================================================
# Facebook
# ============================================================

def verify_facebook(access_token: str) -> dict:
    """
    Valide un access_token Facebook et retourne le profil.

    debug_token indique l'application à laquelle le jeton appartient et s'il
    est toujours valide ; l'appel est authentifié par le secret de l'app, qui
    ne quitte jamais le serveur.

    Facebook ne renvoie que des adresses déjà confirmées de son côté, mais un
    compte peut n'en avoir aucune (inscription par numéro de téléphone) : dans
    ce cas email vaut None et l'appelant demande une adresse à vérifier par
    code.
    """
    if not FACEBOOK_APP_ID or not FACEBOOK_APP_SECRET:
        raise OAuthNotConfigured("FACEBOOK_APP_ID / FACEBOOK_APP_SECRET absents de la configuration serveur.")

    debug = _get(
        FACEBOOK_DEBUG_URL,
        {
            "input_token": access_token,
            "access_token": f"{FACEBOOK_APP_ID}|{FACEBOOK_APP_SECRET}",
        },
    ).get("data", {})

    if not debug.get("is_valid"):
        raise OAuthError("Jeton Facebook invalide ou expiré.")

    if str(debug.get("app_id")) != str(FACEBOOK_APP_ID):
        logger.warning("OAuth Facebook: app_id inattendu %r", debug.get("app_id"))
        raise OAuthError("Ce jeton Facebook n'a pas été émis pour NAVIRE.")

    profile = _get(
        FACEBOOK_ME_URL,
        {"fields": "id,name,first_name,email", "access_token": access_token},
    )

    email = (profile.get("email") or "").strip().lower() or None

    return {
        "provider": "facebook",
        "sub": str(profile.get("id") or debug.get("user_id") or ""),
        "email": email,
        "email_verified": bool(email),
        "name": profile.get("name") or "",
        "given_name": profile.get("first_name") or "",
        "picture": "",
    }


def verify_provider_token(provider: str, access_token: str) -> dict:
    provider = (provider or "").strip().lower()
    if provider == "google":
        return verify_google(access_token)
    if provider == "facebook":
        return verify_facebook(access_token)
    raise OAuthError("Fournisseur inconnu.")


# ============================================================
# Flux par fenêtre (échange de serveur à serveur)
# ============================================================
"""
Pourquoi ce second flux.

L'embed vit dans une iframe cloisonnée par Framer : son origine vaut "null".
Google refuse alors la demande (origin=null, erreur 400 invalid_request), et
aucune origine déclarée en console ne peut correspondre à "null".

La parade consiste à sortir l'échange de l'iframe : le bouton ouvre une vraie
fenêtre sur CE serveur, tout se joue là en haut niveau sur un domaine déclaré,
puis la fenêtre renvoie un ticket à l'embed et se ferme.

Le ticket est un jeton signé par nous, valable 10 minutes, qui contient le
profil déjà vérifié auprès du fournisseur. L'embed ne voit jamais le jeton
Google ou Facebook lui-même.
"""

import secrets
from datetime import datetime, timedelta, timezone

import jwt

from app.core.config import (
    JWT_SECRET,
    JWT_ALGO,
    GOOGLE_CLIENT_SECRET,
    API_BASE_URL,
)

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
FACEBOOK_AUTH_URL = "https://www.facebook.com/v19.0/dialog/oauth"
FACEBOOK_TOKEN_URL = "https://graph.facebook.com/v19.0/oauth/access_token"

STATE_SCOPE = "oauth_state"
TICKET_SCOPE = "oauth_ticket"
STATE_TTL_MINUTES = 10
TICKET_TTL_MINUTES = 10


def _now() -> datetime:
    return datetime.now(timezone.utc)


def callback_url(provider: str, request_base_url: str = "") -> str:
    """
    Adresse de retour déclarée chez le fournisseur.

    API_BASE_URL prime : derrière le proxy de Render, l'URL reconstruite depuis
    la requête peut arriver en http, ce que les fournisseurs refusent.
    """
    base = (API_BASE_URL or request_base_url or "").rstrip("/")
    if base.startswith("http://") and "localhost" not in base and "127.0.0.1" not in base:
        base = "https://" + base[len("http://"):]
    return f"{base}/auth/{provider}/callback"


# ── Protection contre les requêtes forgées ────────────────────────────────

def issue_state(provider: str) -> str:
    payload = {
        "scope": STATE_SCOPE,
        "provider": provider,
        "nonce": secrets.token_urlsafe(16),
        "iat": int(_now().timestamp()),
        "exp": int((_now() + timedelta(minutes=STATE_TTL_MINUTES)).timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)


def check_state(state: str, provider: str) -> bool:
    try:
        payload = jwt.decode(state, JWT_SECRET, algorithms=[JWT_ALGO])
    except Exception:
        return False
    return payload.get("scope") == STATE_SCOPE and payload.get("provider") == provider


# ── Ticket rendu à l'embed ────────────────────────────────────────────────

def issue_ticket(profile: dict) -> str:
    """
    Le profil a déjà été vérifié auprès du fournisseur. On le scelle dans un
    jeton signé : c'est ce que l'embed renverra à /auth/oauth.
    """
    payload = {
        "scope": TICKET_SCOPE,
        "provider": profile["provider"],
        "sub": profile["sub"],
        "email": profile.get("email") or "",
        "name": profile.get("name") or "",
        "given_name": profile.get("given_name") or "",
        "iat": int(_now().timestamp()),
        "exp": int((_now() + timedelta(minutes=TICKET_TTL_MINUTES)).timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)


def read_ticket(ticket: str, provider: str) -> dict:
    """Relit un ticket émis par nous. Lève OAuthError s'il est invalide."""
    try:
        payload = jwt.decode(ticket, JWT_SECRET, algorithms=[JWT_ALGO])
    except Exception:
        raise OAuthError("Session de connexion expirée, recommence.")

    if payload.get("scope") != TICKET_SCOPE:
        raise OAuthError("Jeton de connexion invalide.")
    if payload.get("provider") != provider:
        raise OAuthError("Jeton de connexion émis pour un autre fournisseur.")

    return {
        "provider": payload["provider"],
        "sub": payload.get("sub") or "",
        "email": (payload.get("email") or "").strip().lower() or None,
        "email_verified": True,
        "name": payload.get("name") or "",
        "given_name": payload.get("given_name") or "",
        "picture": "",
    }


# ── Construction des adresses d'autorisation ──────────────────────────────

def google_auth_url(redirect_uri: str, state: str) -> str:
    from urllib.parse import urlencode
    if not GOOGLE_CLIENT_ID:
        raise OAuthNotConfigured("GOOGLE_CLIENT_ID absent de la configuration serveur.")
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",
        "access_type": "online",
    }
    return GOOGLE_AUTH_URL + "?" + urlencode(params)


def facebook_auth_url(redirect_uri: str, state: str) -> str:
    from urllib.parse import urlencode
    if not FACEBOOK_APP_ID:
        raise OAuthNotConfigured("FACEBOOK_APP_ID absent de la configuration serveur.")
    params = {
        "client_id": FACEBOOK_APP_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "public_profile,email",
        "state": state,
    }
    return FACEBOOK_AUTH_URL + "?" + urlencode(params)


# ── Échange du code contre un jeton, puis lecture du profil ───────────────

def _post(url: str, data: dict) -> dict:
    try:
        response = httpx.post(url, data=data, timeout=TIMEOUT)
    except Exception as exc:
        logger.error("OAuth: échange impossible sur %s : %s", url, exc)
        raise OAuthError("Fournisseur injoignable.")

    if response.status_code != 200:
        logger.warning("OAuth: échange refusé (%s) : %s", response.status_code, response.text[:300])
        raise OAuthError("Le fournisseur a refusé l'échange.")

    try:
        return response.json()
    except Exception:
        raise OAuthError("Réponse illisible du fournisseur.")


def exchange_google_code(code: str, redirect_uri: str) -> dict:
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise OAuthNotConfigured("GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET absents de la configuration serveur.")

    data = _post(GOOGLE_TOKEN_URL, {
        "code": code,
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    })

    access_token = data.get("access_token")
    if not access_token:
        raise OAuthError("Google n'a pas rendu de jeton.")

    # verify_google revalide l'audience : le code vient d'être échangé avec
    # nos propres identifiants, mais le contrôle ne coûte rien et garde un
    # seul chemin de vérification.
    return verify_google(access_token)


def exchange_facebook_code(code: str, redirect_uri: str) -> dict:
    if not FACEBOOK_APP_ID or not FACEBOOK_APP_SECRET:
        raise OAuthNotConfigured("FACEBOOK_APP_ID / FACEBOOK_APP_SECRET absents de la configuration serveur.")

    data = _get(FACEBOOK_TOKEN_URL, {
        "code": code,
        "client_id": FACEBOOK_APP_ID,
        "client_secret": FACEBOOK_APP_SECRET,
        "redirect_uri": redirect_uri,
    })

    access_token = data.get("access_token")
    if not access_token:
        raise OAuthError("Facebook n'a pas rendu de jeton.")

    return verify_facebook(access_token)
