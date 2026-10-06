"""
app/core/prepa_adjuris_config.py
=================================
Données statiques du programme Prép'AdJuris : matières (3 par niveau de
licence + Distribution en M1), 2 Prices Stripe par matière (recurring +
one_time) et réglages de la facturation.

Règles de facturation (cahier des charges) :
  - L'inscription paie 20 € par matière : c'est le paiement d'avance de la
    PROCHAINE séance de la matière, pas un essai gratuit.
  - Seules les séances qui commencent après l'inscription sont dues.
  - Le reste est prélevé à terme échu, le dernier jour de chaque mois à
    PREPA_HEURE_PRELEVEMENT (heure de Paris) : 20 € × séances du mois.
  - Le calendrier des séances (table prepa_adjuris_seances) est la seule
    source du calcul. Voir app/services/prepa_adjuris_billing.py.
"""

from __future__ import annotations

import os

# ── Facturation ───────────────────────────────────────────────
PREPA_TZ = "Europe/Paris"

# Prix d'une séance, en centimes. Doit correspondre aux Prices Stripe
# (recurring et one_time) de chaque matière.
PREPA_PRIX_SEANCE_CENTS = 2000

# Heure du prélèvement mensuel, le dernier jour du mois, heure de Paris.
# Le dernier cours d'une journée se termine au plus tard à 23 h : tout cours
# du dernier jour est donc passé au moment du prélèvement.
PREPA_HEURE_PRELEVEMENT = os.getenv("PREPA_ADJURIS_HEURE_PRELEVEMENT", "23:30")

# Dernier mois facturé (AAAA-MM). Plus aucun prélèvement ensuite.
PREPA_FIN_PROGRAMME = os.getenv("PREPA_ADJURIS_FIN_PROGRAMME", "2026-12")

# Impayé : délai de grâce avant retrait du grade et des rôles Discord, et
# jour de la relance par email (« accès retiré dans 2 jours »).
PREPA_DELAI_IMPAYE_JOURS = 7
PREPA_RELANCE_IMPAYE_JOURS = 5

# ── Niveaux et créneaux ───────────────────────────────────────
PREPA_NIVEAUX: tuple[str, ...] = ("L1", "L2", "L3", "M1")

# Créneau habituel par niveau : (jour de la semaine, heure de Paris, durée en
# minutes). Lundi = 0 … dimanche = 6. Sert à pré-remplir la création de séries
# dans l'agenda de la console ; la facturation, elle, ne lit que les séances
# réellement saisies.
PREPA_CRENEAUX: dict[str, tuple[int, str, int]] = {
    "L1": (3, "21:00", 60),   # jeudi
    "L2": (0, "21:00", 60),   # lundi
    "L3": (1, "21:00", 60),   # mardi
    "M1": (5, "15:30", 60),   # samedi
}

# ── Prices Stripe ─────────────────────────────────────────────
PREPA_PRICES: dict[str, dict[str, str]] = {
    "L1_droit_constit": {
        "recurring": "price_1U0Mp3LeRHpDiZMsi6n48xTM",
        "one_time":  "price_1U0Mp3LeRHpDiZMsCVz5Rj2T",
    },
    "L1_intro_au_droit": {
        "recurring": "price_1U0MyOLeRHpDiZMsJ8967YAZ",
        "one_time":  "price_1U0MyfLeRHpDiZMsAJbHyzV8",
    },
    "L1_droit_ijae": {
        "recurring": "price_1U0Mz5LeRHpDiZMsablXi4pC",
        "one_time":  "price_1U0MzJLeRHpDiZMstogG0SMW",
    },
    "L2_droit_administratif": {
        "recurring": "price_1U0N1lLeRHpDiZMs1V5cJxC8",
        "one_time":  "price_1U0N23LeRHpDiZMssP039IN9",
    },
    "L2_droit_des_obligations": {
        "recurring": "price_1U0N3HLeRHpDiZMsryAnyXex",
        "one_time":  "price_1U0N3ULeRHpDiZMsc6E0xhBZ",
    },
    "L2_droit_penal": {
        "recurring": "price_1U0N4VLeRHpDiZMsg22wZPbD",
        "one_time":  "price_1U0N4mLeRHpDiZMszPw0VeHO",
    },
    "L3_droit_des_societes": {
        "recurring": "price_1U0N79LeRHpDiZMsrQUJ8LDt",
        "one_time":  "price_1U0N7OLeRHpDiZMskqnMLeA0",
    },
    "L3_droit_des_suretes": {
        "recurring": "price_1U0N9TLeRHpDiZMsYEPXS7uj",
        "one_time":  "price_1U0N9hLeRHpDiZMsZMoeTN6S",
    },
    "L3_droit_des_contrats_speciaux": {
        "recurring": "price_1U0NAVLeRHpDiZMssxngPXGN",
        "one_time":  "price_1U0NAnLeRHpDiZMsvoglijia",
    },
    # M1 — Prices à créer dans le dashboard Stripe (produit « Prép'AdJuris -
    # M1 DISTRIBUTION by NAVIRE », 20 € mensuel + 20 € one_time), puis à
    # renseigner ici ou par variable d'environnement. Tant qu'ils sont vides,
    # le paiement de cette matière est refusé avec un message explicite.
    "M1_distribution": {
        "recurring": os.getenv("STRIPE_PRICE_ADJURIS_M1_DISTRIBUTION_RECURRING", ""),
        "one_time":  os.getenv("STRIPE_PRICE_ADJURIS_M1_DISTRIBUTION_ONE_TIME", ""),
    },
}

# Libellés d'affichage (emails, export CSV, formulaire public). Séparé des
# clés techniques : celles-ci sont figées côté Stripe et ne doivent pas bouger.
PREPA_MATIERE_NAMES: dict[str, str] = {
    "L1_droit_constit":               "Droit constitutionnel",
    "L1_intro_au_droit":              "Introduction au droit",
    "L1_droit_ijae":                  "Droit IJAE",
    "L2_droit_administratif":         "Droit administratif",
    "L2_droit_des_obligations":       "Droit des obligations",
    "L2_droit_penal":                 "Droit pénal",
    "L3_droit_des_societes":          "Droit des sociétés",
    "L3_droit_des_suretes":           "Droit des sûretés",
    "L3_droit_des_contrats_speciaux": "Droit des contrats spéciaux",
    "M1_distribution":                "Distribution",
}


def matiere_niveau(matiere_key: str) -> str:
    """Niveau porté par la clé, ex: 'L1_droit_constit' -> 'L1'."""
    return matiere_key.partition("_")[0]


def matiere_label(matiere_key: str) -> str:
    """Libellé lisible, ex: 'L1_droit_constit' -> 'L1 – Droit constitutionnel'."""
    niveau, _, rest = matiere_key.partition("_")
    nom = PREPA_MATIERE_NAMES.get(matiere_key) or rest.replace("_", " ").capitalize()
    return f"{niveau} – {nom}"


def matieres_du_niveau(niveau: str) -> list[str]:
    """Clés des matières d'un niveau, dans l'ordre de la config."""
    return [k for k in PREPA_PRICES if matiere_niveau(k) == niveau]


def prices_configures(matiere_key: str) -> bool:
    """Les deux Prices Stripe de la matière sont-ils renseignés ?"""
    p = PREPA_PRICES.get(matiere_key) or {}
    return bool(p.get("recurring")) and bool(p.get("one_time"))
