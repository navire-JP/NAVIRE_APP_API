"""
Tests du calcul de facturation Prép'AdJuris (module pur, sans base ni Stripe).

Lancer : pytest tests/test_prepa_adjuris_billing.py
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.services.prepa_adjuris_billing import (
    Echeancier,
    calculer_echeancier,
    comparer,
    date_prelevement,
    devis_depuis_metadata,
    devis_vers_metadata,
    fin_abonnement,
    fusionner,
    mois_de,
    phases_mensuelles,
    prelevements,
    recalculer,
    texte_recap,
)

PARIS = ZoneInfo("Europe/Paris")
FIN = (2026, 12)


def paris(*args) -> datetime:
    return datetime(*args, tzinfo=PARIS)


def hebdo(premier: datetime, jusqu_au: datetime) -> list[datetime]:
    """Une séance par semaine, à la même heure de Paris (DST compris)."""
    dates, d = [], premier
    while d <= jusqu_au:
        dates.append(d)
        local = d.astimezone(PARIS)
        nxt = (local.replace(tzinfo=None) + timedelta(days=7)).replace(tzinfo=PARIS)
        d = nxt
    return dates


# Calendrier décidé : L1 jeudi 21 h, L2 lundi 21 h, L3 mardi 21 h, M1 samedi 15 h 30.
L1 = hebdo(paris(2026, 10, 1, 21, 0), paris(2026, 12, 31, 23, 0))
L3 = hebdo(paris(2026, 10, 6, 21, 0), paris(2026, 12, 31, 23, 0))
M1 = hebdo(paris(2026, 10, 3, 15, 30), paris(2026, 12, 31, 23, 0))


# ── Dates de prélèvement ─────────────────────────────────────

def test_prelevement_dernier_jour_23h30_paris_ete_et_hiver():
    sept = date_prelevement((2026, 9)).astimezone(PARIS)
    assert (sept.day, sept.hour, sept.minute) == (30, 23, 30)   # heure d'été
    octo = date_prelevement((2026, 10)).astimezone(PARIS)
    assert (octo.day, octo.hour, octo.minute) == (31, 23, 30)   # heure d'hiver
    nov = date_prelevement((2026, 11)).astimezone(PARIS)
    assert nov.day == 30
    # Après le dernier cours possible (23 h)
    assert date_prelevement((2026, 10)) > paris(2026, 10, 31, 23, 0)


def test_mois_en_heure_de_paris():
    # 31 octobre 23 h 30 à Paris = 22 h 30 UTC, toujours octobre.
    assert mois_de(paris(2026, 10, 31, 23, 30)) == (2026, 10)
    # 1er novembre 0 h 30 à Paris = 31 octobre 23 h 30 UTC, déjà novembre.
    assert mois_de(paris(2026, 11, 1, 0, 30)) == (2026, 11)


# ── Règles du cahier des charges (L3, mardi 21 h) ────────────

def test_inscription_dimanche_avant_le_premier_cours():
    e = calculer_echeancier("L3_x", L3, paris(2026, 10, 4, 12, 0), fin=FIN)
    assert e.seance_prepayee == paris(2026, 10, 6, 21, 0)
    # Octobre : 13, 20, 27 (le 6 est prépayé)
    assert e.mois == {"2026-10": 3, "2026-11": 4, "2026-12": 5}


def test_inscription_le_jour_du_cours_avant_l_heure():
    e = calculer_echeancier("L3_x", L3, paris(2026, 10, 6, 15, 0), fin=FIN)
    assert e.seance_prepayee == paris(2026, 10, 6, 21, 0)
    assert e.mois["2026-10"] == 3


def test_inscription_apres_le_debut_du_cours_ne_le_compte_pas():
    e = calculer_echeancier("L3_x", L3, paris(2026, 10, 6, 21, 1), fin=FIN)
    assert e.seance_prepayee == paris(2026, 10, 13, 21, 0)
    assert e.mois["2026-10"] == 2  # 20 et 27


def test_inscription_le_week_end_de_la_semaine_2():
    e = calculer_echeancier("L3_x", L3, paris(2026, 10, 10, 11, 0), fin=FIN)
    assert e.seance_prepayee == paris(2026, 10, 13, 21, 0)
    assert e.mois["2026-10"] == 2


def test_une_seule_seance_restante_dans_le_mois_donne_zero():
    # Inscrit le 25 : il reste le mardi 27, qui est prépayé → 0 € fin octobre.
    e = calculer_echeancier("L3_x", L3, paris(2026, 10, 25, 10, 0), fin=FIN)
    assert e.seance_prepayee == paris(2026, 10, 27, 21, 0)
    assert e.mois["2026-10"] == 0
    assert e.mois["2026-11"] == 4


def test_inscription_dernier_jour_apres_le_prelevement_passe_au_mois_suivant():
    e = calculer_echeancier("L3_x", L3, paris(2026, 10, 31, 23, 45), fin=FIN)
    assert "2026-10" not in e.mois
    assert e.depuis == "2026-11"
    assert e.seance_prepayee == paris(2026, 11, 3, 21, 0)
    assert e.mois == {"2026-11": 3, "2026-12": 5}


def test_exemple_decembre_semaine_2():
    # Inscrit en semaine 2 de décembre, après le cours du mardi 8 :
    # 20 € pour la semaine 3 (15/12), puis prélèvement fin décembre des suivants.
    e = calculer_echeancier("L3_x", L3, paris(2026, 12, 9, 10, 0), fin=FIN)
    assert e.seance_prepayee == paris(2026, 12, 15, 21, 0)
    assert e.mois == {"2026-12": 2}  # 22 et 29


def test_plus_aucune_seance():
    e = calculer_echeancier("L3_x", L3, paris(2026, 12, 30, 10, 0), fin=FIN)
    assert e.seance_prepayee is None
    assert e.total_seances == 0


def test_rien_apres_la_fin_du_programme():
    dates = L3 + [paris(2027, 1, 5, 21, 0)]
    e = calculer_echeancier("L3_x", dates, paris(2026, 10, 4, 12, 0), fin=FIN)
    assert "2027-01" not in e.mois


def test_inscription_antidatee_m1():
    # Élève M1 : 20 € déjà réglés pour le cours du 3 octobre. Lien de paiement
    # créé le 4 octobre avec une inscription datée du 3 avant le cours.
    e = calculer_echeancier(
        "M1_distribution", M1, paris(2026, 10, 3, 12, 0),
        facturable_apres=paris(2026, 10, 4, 12, 0), fin=FIN,
    )
    assert e.seance_prepayee == paris(2026, 10, 3, 15, 30)
    assert e.mois == {"2026-10": 4, "2026-11": 4, "2026-12": 4}


def test_dates_sans_fuseau_considerees_utc():
    naive = [datetime(2026, 10, 13, 19, 0), datetime(2026, 10, 20, 19, 0)]
    e = calculer_echeancier("L3_x", naive, datetime(2026, 10, 10, 9, 0, tzinfo=timezone.utc), fin=FIN)
    assert e.seance_prepayee.tzinfo is not None
    assert e.mois["2026-10"] == 1


# ── Recalcul après modification de l'agenda ──────────────────

def test_annulation_dans_un_mois_futur_ajuste():
    ancien = calculer_echeancier("L3_x", L3, paris(2026, 10, 4, 12, 0), fin=FIN)
    agenda = [d for d in L3 if d != paris(2026, 11, 10, 21, 0)]
    nouveau = recalculer(ancien, agenda)
    ecarts = comparer(ancien, nouveau, paris(2026, 10, 15, 12, 0))
    assert [(e.mois, e.avant, e.apres, e.type) for e in ecarts] == [("2026-11", 4, 3, "ajustement")]
    stocke = fusionner(ancien, nouveau, paris(2026, 10, 15, 12, 0))
    assert stocke.mois["2026-11"] == 3


def test_annulation_dans_un_mois_deja_preleve_donne_un_credit_une_seule_fois():
    ancien = calculer_echeancier("L3_x", L3, paris(2026, 10, 4, 12, 0), fin=FIN)
    agenda = [d for d in L3 if d != paris(2026, 10, 27, 21, 0)]
    apres_prelevement = paris(2026, 11, 2, 12, 0)

    nouveau = recalculer(ancien, agenda)
    ecarts = comparer(ancien, nouveau, apres_prelevement)
    assert [(e.mois, e.type, e.delta_cents) for e in ecarts] == [("2026-10", "credit", -2000)]

    stocke = fusionner(ancien, nouveau, apres_prelevement)
    assert stocke.mois["2026-10"] == 3          # ce qui a été facturé
    assert stocke.credits == {"2026-10": 1}

    # Second recalcul : plus rien à créditer.
    assert comparer(stocke, recalculer(stocke, agenda), apres_prelevement) == []


def test_annulation_de_la_seance_prepayee_decale_la_suivante():
    ancien = calculer_echeancier("L3_x", L3, paris(2026, 10, 4, 12, 0), fin=FIN)
    agenda = [d for d in L3 if d != paris(2026, 10, 6, 21, 0)]
    nouveau = recalculer(ancien, agenda)
    assert nouveau.seance_prepayee == paris(2026, 10, 13, 21, 0)
    assert nouveau.mois["2026-10"] == 2


def test_deplacement_d_un_mois_a_l_autre():
    ancien = calculer_echeancier("L3_x", L3, paris(2026, 10, 4, 12, 0), fin=FIN)
    agenda = [d for d in L3 if d != paris(2026, 10, 27, 21, 0)] + [paris(2026, 11, 2, 21, 0)]
    nouveau = recalculer(ancien, agenda)
    assert nouveau.mois["2026-10"] == 2
    assert nouveau.mois["2026-11"] == 5


def test_ajout_dans_un_mois_deja_preleve_n_est_pas_facture_a_posteriori():
    ancien = calculer_echeancier("L3_x", L3, paris(2026, 10, 4, 12, 0), fin=FIN)
    agenda = L3 + [paris(2026, 10, 29, 21, 0)]
    nouveau = recalculer(ancien, agenda)
    ecarts = comparer(ancien, nouveau, paris(2026, 11, 2, 12, 0))
    assert [e.type for e in ecarts] == ["non_facture"]
    assert fusionner(ancien, nouveau, paris(2026, 11, 2, 12, 0)).mois["2026-10"] == 3


# ── Phases Stripe ────────────────────────────────────────────

def test_phases_une_par_mois_ouverte_au_prelevement():
    e = calculer_echeancier("L3_x", L3, paris(2026, 10, 4, 12, 0), fin=FIN)
    phases = phases_mensuelles([e], {"L3_x": "price_rec"})
    assert len(phases) == 3
    assert phases[0]["start_date"] == int(date_prelevement((2026, 10)).timestamp())
    assert phases[0]["items"] == [{"price": "price_rec", "quantity": 3}]
    assert phases[-1]["items"] == [{"price": "price_rec", "quantity": 5}]
    # Phases contiguës, facture émise à l'ouverture de chacune
    for a, b in zip(phases, phases[1:]):
        assert a["end_date"] == b["start_date"]
    assert all(p["billing_cycle_anchor"] == "phase_start" for p in phases)
    # La dernière se ferme fin janvier, sans prélèvement janvier
    assert phases[-1]["end_date"] == int(date_prelevement((2027, 1)).timestamp())
    assert fin_abonnement([e]) == date_prelevement((2027, 1))


def test_phases_plusieurs_matieres_quantites_distinctes():
    a = calculer_echeancier("L3_a", L3, paris(2026, 10, 4, 12, 0), fin=FIN)
    b = calculer_echeancier("L3_b", L3[:5], paris(2026, 10, 4, 12, 0), fin=FIN)
    phases = phases_mensuelles([a, b], {"L3_a": "pa", "L3_b": "pb"})
    assert phases[1]["items"] == [{"price": "pa", "quantity": 4}, {"price": "pb", "quantity": 1}]


# ── Devis et textes ──────────────────────────────────────────

def test_metadata_aller_retour_et_taille():
    inscrit = paris(2026, 10, 4, 12, 0)
    devis = [calculer_echeancier(k, L1, inscrit, fin=FIN) for k in ("L1_droit_constit", "L1_intro_au_droit", "L1_droit_ijae")]
    brut = devis_vers_metadata(devis)
    assert len(brut) <= 500
    relu = devis_depuis_metadata(brut, inscrit)
    assert [r.mois for r in relu] == [d.mois for d in devis]
    assert [r.seance_prepayee for r in relu] == [d.seance_prepayee for d in devis]


def test_metadata_illisible():
    assert devis_depuis_metadata("", datetime.now(timezone.utc)) is None
    assert devis_depuis_metadata("{pas du json", datetime.now(timezone.utc)) is None


def test_prelevements_et_texte():
    e = calculer_echeancier("L3_droit_des_societes", L3, paris(2026, 10, 10, 11, 0), fin=FIN)
    lignes = prelevements([e])
    assert [l["montant_cents"] for l in lignes] == [4000, 8000, 10000]
    texte = texte_recap([e])
    assert "20 €" in texte and "mardi 13 octobre" in texte
    assert "40 € le 31 octobre" in texte and "100 € le 31 décembre" in texte
    assert len(texte) <= 1200
    assert texte_recap([e], inscription_cents=None).startswith("Inscription déjà réglée")


def test_serialisation():
    e = calculer_echeancier("L3_x", L3, paris(2026, 10, 4, 12, 0), fin=FIN)
    e.credits = {"2026-10": 1}
    assert Echeancier.from_dict(e.to_dict()) == e


# ── Calendrier de secours (agenda pas encore saisi) ──────────

def test_seances_hebdomadaires_creneau_l1():
    from app.services.prepa_adjuris_billing import seances_hebdomadaires
    dates = seances_hebdomadaires(3, "21:00", paris(2026, 10, 6, 10, 0), fin=FIN)
    locales = [d.astimezone(PARIS) for d in dates]
    assert locales[0] == paris(2026, 10, 8, 21, 0)          # jeudi suivant
    assert all(d.weekday() == 3 and d.hour == 21 for d in locales)  # DST compris
    assert locales[-1] == paris(2026, 12, 31, 21, 0)
    e = calculer_echeancier("L1_x", dates, paris(2026, 10, 6, 10, 0), fin=FIN)
    assert e.seance_prepayee == paris(2026, 10, 8, 21, 0)
    assert e.mois == {"2026-10": 3, "2026-11": 4, "2026-12": 5}
