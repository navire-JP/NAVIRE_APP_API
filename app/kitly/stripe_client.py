"""
Client Stripe Kitly — strictement séparé du Stripe NAVIRE.

NAVIRE et MEOLES écrivent tous deux dans la variable globale `stripe.api_key`
(chacun « reprend la main » avant ses appels). Kitly ne la touche JAMAIS : il
passe par une instance `stripe.StripeClient` construite avec la clé du compte
Stripe Kitly. Aucun appel Kitly ne peut donc partir sur le compte NAVIRE, et
inversement, quel que soit l'ordre des requêtes.

Libellé bancaire : il est défini par le compte Stripe lui-même (Dashboard
Kitly → Settings → Public details → Statement descriptor = « KITLY »). D'où un
compte Stripe dédié à Kitly plutôt qu'un simple jeu de clés partagé.
"""

from __future__ import annotations

import stripe
from fastapi import HTTPException

from app.kitly.config import KITLY_STRIPE_SECRET_KEY

_client: stripe.StripeClient | None = None
_client_key: str = ""


def stripe_client() -> stripe.StripeClient:
    """Instance partagée ; 503 si la clé Kitly n'est pas configurée."""
    global _client, _client_key
    key = KITLY_STRIPE_SECRET_KEY
    if not key:
        raise HTTPException(503, detail="Paiements Kitly non configurés (KITLY_STRIPE_SECRET_KEY).")
    if _client is None or _client_key != key:
        _client = stripe.StripeClient(api_key=key, max_network_retries=2)
        _client_key = key
    return _client


def kitly_metadata(**extra) -> dict:
    """Métadonnées posées sur chaque objet Stripe créé par Kitly."""
    return {"app": "kitly", **{k: str(v) for k, v in extra.items() if v is not None}}


def is_kitly_object(obj) -> bool:
    try:
        return (obj.get("metadata") or {}).get("app") == "kitly"
    except Exception:
        return False


_SECRET_RE = None


def stripe_reason(exc) -> str:
    """Raison lisible d'une erreur Stripe (ex. « No such price: … »), sans aucune clé."""
    import re

    global _SECRET_RE
    if _SECRET_RE is None:
        _SECRET_RE = re.compile(r"\b(sk|rk|pk)_(live|test)_[A-Za-z0-9]{6,}|\bwhsec_[A-Za-z0-9]{6,}")
    msg = getattr(exc, "user_message", None) or getattr(exc, "_message", None) or str(exc) or exc.__class__.__name__
    return _SECRET_RE.sub("•••", str(msg))[:400]
