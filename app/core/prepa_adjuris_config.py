"""
app/core/prepa_adjuris_config.py
=================================
Données Stripe statiques du programme Prép'AdJuris : 9 matières (3 par
niveau L1/L2/L3), 2 Prices Stripe par matière (recurring + one_time) déjà
créés dans le dashboard, et le nombre de séances payantes par mois.

Règles métier (voir spec) :
  - 10 septembre : séance commune gratuite, hors décompte, jamais facturée.
  - L'inscription paie le one_time (une des séances de septembre).
  - PREPA_MONTHLY_QUANTITIES donne le nombre BRUT de séances par mois
    [septembre, octobre, novembre, décembre]. Le -1 de septembre (car le
    one_time en couvre déjà une) est appliqué au moment de l'utilisation,
    pas stocké ici.
"""

from __future__ import annotations

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
}

# Ordre : [septembre, octobre, novembre, décembre] — nombre BRUT de séances,
# AVANT l'ajustement -1 de septembre (le one_time du checkout en couvre une).
PREPA_MONTHLY_QUANTITIES: dict[str, list[int]] = {
    "L1_droit_constit":               [3, 5, 4, 4],
    "L1_intro_au_droit":              [3, 5, 4, 4],
    "L1_droit_ijae":                  [3, 5, 4, 4],
    "L2_droit_administratif":         [4, 4, 4, 5],
    "L2_droit_des_obligations":       [4, 4, 4, 5],
    "L2_droit_penal":                 [4, 4, 4, 5],
    "L3_droit_des_societes":          [4, 4, 4, 5],
    "L3_droit_des_suretes":           [4, 4, 4, 5],
    "L3_droit_des_contrats_speciaux": [4, 4, 4, 5],
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
}


def matiere_niveau(matiere_key: str) -> str:
    """Niveau porté par la clé, ex: 'L1_droit_constit' -> 'L1'."""
    return matiere_key.partition("_")[0]


def matiere_label(matiere_key: str) -> str:
    """Libellé lisible, ex: 'L1_droit_constit' -> 'L1 – Droit constitutionnel'."""
    niveau, _, rest = matiere_key.partition("_")
    nom = PREPA_MATIERE_NAMES.get(matiere_key) or rest.replace("_", " ").capitalize()
    return f"{niveau} – {nom}"
