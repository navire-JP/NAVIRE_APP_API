"""
app/services/professeurs.py
============================
Espace professeurs (chargés de TD) de Prép'AdJuris. Routes :
app/routers/professeurs.py.

Règles :
  - Un groupe (PrepaAdjurisClasse) appartient à une matière. Une matière peut
    en avoir plusieurs ; un étudiant sans groupe choisi
    (PrepaAdjurisEnrollment.classe_id NULL, ou groupe d'une autre matière ou
    désactivé) est dans le premier groupe actif de sa matière.
  - Un professeur est attaché par un admin, avec le grade « ChargéTD » par
    défaut, à un ou plusieurs groupes (PrepaAdjurisAffectation).
  - Les étudiants comptés sont les inscriptions actives (ou en impayé, accès
    encore ouvert) du groupe.
  - Prévisionnel : par séance de la matière (même calendrier que la
    facturation, dates_seances), remu_seance_cents + remu_etudiant_cents ×
    étudiants inscrits avant le début de la séance. Estimation : les
    séances passées sont comptées avec les étudiants inscrits aujourd'hui.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.prepa_adjuris_config import PREPA_PRICES, matiere_label, matiere_niveau
from app.db.models import (
    PrepaAdjurisAffectation,
    PrepaAdjurisClasse,
    PrepaAdjurisEnrollment,
    PrepaAdjurisProfesseur,
    PrepaAdjurisRessource,
    User,
)
from app.services.prepa_adjuris_billing import cle_mois, fin_programme, mois_de
from app.services.prepa_adjuris_facturation import dates_seances

GRADE_PROFESSEUR = "ChargéTD"
STATUTS_PROFESSEUR = ("active", "suspendu")

# Inscriptions qui comptent comme étudiants du groupe.
STATUTS_ETUDIANT = ("active", "payment_failed")

# Rémunération proposée à l'affectation, modifiable groupe par groupe.
REMU_SEANCE_CENTS = int(os.getenv("PROF_REMU_SEANCE_CENTS", "0"))
REMU_ETUDIANT_CENTS = int(os.getenv("PROF_REMU_ETUDIANT_CENTS", "1000"))

# Salons Discord des groupes créés au premier démarrage.
_DISCORD_GUILD = "1131156852265201715"
GROUPES_PAR_DEFAUT: dict[str, str] = {
    "L1_intro_au_droit":              "1533885279876677794",
    "L1_droit_ijae":                  "1533885633846837398",
    "L1_droit_constit":               "1533885528947036344",
    "L2_droit_penal":                 "1533885924121903154",
    "L2_droit_des_obligations":       "1533885800956170400",
    "L2_droit_administratif":         "1533885741619220582",
    "L3_droit_des_societes":          "1533886138119356416",
    "L3_droit_des_contrats_speciaux": "1533886081165037608",
    "L3_droit_des_suretes":           "1533886002379362314",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def aware(dt: datetime | None) -> datetime | None:
    """SQLite rend des dates naïves : on les relit en UTC pour comparer."""
    if dt is None or dt.tzinfo is not None:
        return dt
    return dt.replace(tzinfo=timezone.utc)


def discord_url(channel_id: str) -> str:
    return f"https://discord.com/channels/{_DISCORD_GUILD}/{channel_id}"


def creer_groupes_par_defaut(db: Session) -> int:
    """Un groupe par matière avec son salon Discord, si aucun groupe n'existe
    encore. Ne recrée rien ensuite : un groupe supprimé dans la console ne
    revient pas au démarrage suivant."""
    if db.execute(select(PrepaAdjurisClasse.id).limit(1)).first():
        return 0
    for key, channel in GROUPES_PAR_DEFAUT.items():
        db.add(PrepaAdjurisClasse(matiere_key=key, nom=matiere_label(key), discord_url=discord_url(channel)))
    db.commit()
    return len(GROUPES_PAR_DEFAUT)


# ============================================================
# Groupes et étudiants
# ============================================================

def groupes_par_matiere(db: Session) -> dict[str, list[PrepaAdjurisClasse]]:
    """Groupes actifs, par matière, dans l'ordre de création."""
    out: dict[str, list[PrepaAdjurisClasse]] = {}
    for c in db.execute(
        select(PrepaAdjurisClasse).where(PrepaAdjurisClasse.is_active.is_(True)).order_by(PrepaAdjurisClasse.id)
    ).scalars():
        out.setdefault(c.matiere_key, []).append(c)
    return out


def groupe_de(e: PrepaAdjurisEnrollment, par_matiere: dict[str, list[PrepaAdjurisClasse]]) -> int | None:
    groupes = par_matiere.get(e.matiere_key) or []
    if any(c.id == e.classe_id for c in groupes):
        return e.classe_id
    return groupes[0].id if groupes else None


def inscriptions_du_groupe(
    db: Session, classe: PrepaAdjurisClasse, par_matiere: dict | None = None
) -> list[PrepaAdjurisEnrollment]:
    par_matiere = par_matiere if par_matiere is not None else groupes_par_matiere(db)
    rows = db.execute(
        select(PrepaAdjurisEnrollment)
        .where(
            PrepaAdjurisEnrollment.matiere_key == classe.matiere_key,
            PrepaAdjurisEnrollment.status.in_(STATUTS_ETUDIANT),
        )
        .order_by(PrepaAdjurisEnrollment.id)
    ).scalars().all()
    return [e for e in rows if groupe_de(e, par_matiere) == classe.id]


def groupes_de_l_etudiant(db: Session, user_id: int) -> set[int]:
    """Groupes de l'étudiant, toutes matières confondues (inscriptions actives)."""
    par_matiere = groupes_par_matiere(db)
    rows = db.execute(
        select(PrepaAdjurisEnrollment).where(
            PrepaAdjurisEnrollment.user_id == user_id,
            PrepaAdjurisEnrollment.status == "active",
        )
    ).scalars().all()
    return {g for g in (groupe_de(e, par_matiere) for e in rows) if g is not None}


def ressource_visible(r: PrepaAdjurisRessource, groupes: set[int]) -> bool:
    """Une ressource de groupe n'est visible que des étudiants du groupe."""
    return r.classe_id is None or r.classe_id in groupes


def etudiant_row(db: Session, e: PrepaAdjurisEnrollment, avec_email: bool = True) -> dict:
    u = db.get(User, e.user_id) if e.user_id else None
    return {
        "enrollment_id": e.id,
        "user_id": e.user_id,
        "username": u.username if u else None,
        "email": (u.email if u else e.email) if avec_email else None,
        "university": u.university if u else None,
        "discord_lie": bool(u and u.discord_id),
        "status": e.status,
        "inscrit_le": _iso(aware(e.inscrit_le or e.created_at)),
    }


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def classe_row(c: PrepaAdjurisClasse) -> dict:
    return {
        "id": c.id,
        "matiere_key": c.matiere_key,
        "matiere_label": matiere_label(c.matiere_key),
        "niveau": matiere_niveau(c.matiere_key),
        "nom": c.nom,
        "discord_url": c.discord_url,
        "liens": c.liens or [],
        "is_active": c.is_active,
    }


def matiere_valide(key: str) -> bool:
    return key in PREPA_PRICES


# ============================================================
# Rémunération
# ============================================================

def previsionnel(
    db: Session,
    affectation: PrepaAdjurisAffectation,
    inscriptions: list[PrepaAdjurisEnrollment],
    maintenant: datetime | None = None,
) -> dict:
    """Gains par séance et par mois d'une affectation, jusqu'à la fin du
    programme. `realise` = séances déjà commencées, `a_venir` = les autres."""
    maintenant = maintenant or utcnow()
    fin = fin_programme()
    debuts = [aware(e.inscrit_le or e.created_at) for e in inscriptions]

    mois: dict[str, dict] = {}
    realise = a_venir = nb_passees = nb_futures = 0
    for d in sorted(dates_seances(db, affectation.classe.matiere_key)):
        if mois_de(d) > fin:
            continue
        nb = sum(1 for x in debuts if x is None or x < d)
        montant = affectation.remu_seance_cents + affectation.remu_etudiant_cents * nb
        m = mois.setdefault(cle_mois(mois_de(d)), {"mois": cle_mois(mois_de(d)), "seances": 0, "montant_cents": 0})
        m["seances"] += 1
        m["montant_cents"] += montant
        if d <= maintenant:
            realise += montant
            nb_passees += 1
        else:
            a_venir += montant
            nb_futures += 1

    return {
        "remu_seance_cents": affectation.remu_seance_cents,
        "remu_etudiant_cents": affectation.remu_etudiant_cents,
        "par_seance_cents": affectation.remu_seance_cents + affectation.remu_etudiant_cents * len(inscriptions),
        "seances_passees": nb_passees,
        "seances_a_venir": nb_futures,
        "realise_cents": realise,
        "a_venir_cents": a_venir,
        "total_cents": realise + a_venir,
        "mois": list(mois.values()),
    }


def get_professeur(db: Session, user_id: int) -> PrepaAdjurisProfesseur | None:
    return db.execute(
        select(PrepaAdjurisProfesseur).where(PrepaAdjurisProfesseur.user_id == user_id)
    ).scalar_one_or_none()
