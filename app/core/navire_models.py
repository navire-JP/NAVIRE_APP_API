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
        "model": os.getenv("OPENAI_MODEL_NAVIRE_AVANCE", "gpt-4.1"),
        "max_tokens": 2000,
    },
    "expert": {
        "label": "Expert",
        "emoji": "🎓",
        "description": "Plans de dissertation, commentaires, raisonnements longs",
        "cost": 4,
        "model": os.getenv("OPENAI_MODEL_NAVIRE_EXPERT", "o4-mini"),
        "max_tokens": 6000,
    },
}


def get_tier(tier_id: str | None) -> tuple[str, dict]:
    tid = (tier_id or DEFAULT_TIER).strip().lower()
    if tid not in TIERS:
        tid = DEFAULT_TIER
    return tid, TIERS[tid]


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
