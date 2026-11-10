"""
Niveaux de modèle de NAVIRE AI, au choix de l'élève.

  rapide  → modèle léger, gratuit (compte dans le quota quotidien du plan)
  avance  → modèle plus capable, 1 crédit par question
  expert  → modèle de raisonnement, 4 crédits par question

Le modèle de chaque niveau se règle par variable d'environnement (Render),
sans toucher au code. Les crédits se rechargent chaque mois selon le plan
(app/core/limits.py → navire_credits_month) ; voir app/services/navire_credits.py.
"""
from __future__ import annotations

import os

DEFAULT_TIER = "rapide"

TIERS: dict[str, dict] = {
    "rapide": {
        "label": "Rapide",
        "emoji": "⚡",
        "description": "Définitions, questions simples",
        "cost": 0,
        "model": os.getenv("OPENAI_MODEL_NAVIRE", os.getenv("OPENAI_MODEL", "gpt-4o-mini")),
        "max_tokens": 1100,
    },
    "avance": {
        "label": "Avancé",
        "emoji": "🧠",
        "description": "Explications de cours, cas pratiques",
        "cost": 1,
        "model": os.getenv("OPENAI_MODEL_NAVIRE_AVANCE", "gpt-5-mini"),
        "max_tokens": 2000,
        # Modèle à raisonnement : le raisonnement consomme aussi ce budget.
        "reasoning_tokens": 6000,
        # Si le modèle choisi est retiré ou indisponible sur le compte OpenAI,
        # on essaie ceux-ci, dans l'ordre (voir candidates()).
        "fallbacks": ["gpt-5-mini", "gpt-4.1", "gpt-4o"],
    },
    "expert": {
        "label": "Expert",
        "emoji": "🎓",
        "description": "Plans de dissertation, commentaires, raisonnements longs",
        "cost": 4,
        "model": os.getenv("OPENAI_MODEL_NAVIRE_EXPERT", "gpt-5"),
        "max_tokens": 6000,
        "reasoning_tokens": 12000,
        "fallbacks": ["gpt-5", "gpt-5-mini", "gpt-4.1", "gpt-4o"],
    },
}


def get_tier(tier_id: str | None) -> tuple[str, dict]:
    tid = (tier_id or DEFAULT_TIER).strip().lower()
    if tid not in TIERS:
        tid = DEFAULT_TIER
    return tid, TIERS[tid]


# Modèle qui a répondu pour chaque niveau : évite de retenter à chaque
# question un modèle retiré (remis à zéro au redémarrage du serveur).
_WORKING: dict[str, str] = {}
_DEAD: set[str] = set()


def candidates(tier_id: str) -> list[str]:
    """
    Modèles à essayer pour un niveau, dans l'ordre : celui qui a déjà marché,
    celui de la variable d'environnement, les replis du niveau. Le modèle
    Rapide n'est PAS dans la liste : un niveau payant ne se dégrade pas en
    silence (la route signale l'erreur, sans débit).
    """
    tid, conf = get_tier(tier_id)
    out: list[str] = []
    for m in [_WORKING.get(tid), conf["model"], *conf.get("fallbacks", [])]:
        if m and m not in out and m not in _DEAD:
            out.append(m)
    return out or [conf["model"]]


def mark_working(tier_id: str, model: str) -> None:
    _WORKING[tier_id] = model


def mark_dead(model: str) -> None:
    _DEAD.add(model)


def is_model_unavailable(exc: Exception) -> bool:
    """Erreur OpenAI « modèle retiré / inconnu / non autorisé pour ce compte »."""
    text = str(exc).lower()
    code = getattr(exc, "code", None) or ""
    status = getattr(exc, "status_code", None)
    return (
        code in ("model_not_found",)
        or "model_not_found" in text
        or "has been deprecated" in text
        or "does not exist" in text
        or "do not have access to the model" in text
        or (status == 404 and "model" in text)
    )


def is_reasoning_model(model: str) -> bool:
    """
    Modèles « à raisonnement » d'OpenAI (o1, o3, o4-mini, gpt-5…) : pas de
    `temperature`, et la longueur se règle par `max_completion_tokens`.
    """
    m = (model or "").lower()
    return m.startswith(("o1", "o3", "o4", "gpt-5"))


def public_tiers() -> list[dict]:
    """Liste affichée dans le sélecteur (sans le nom du modèle)."""
    return [
        {"id": tid, "label": t["label"], "emoji": t["emoji"],
         "description": t["description"], "cost": t["cost"]}
        for tid, t in TIERS.items()
    ]
