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
