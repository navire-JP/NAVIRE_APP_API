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
  - Pilotage (console) : un montant fixé pour un mois
    (PrepaAdjurisRemuneration) remplace le montant calculé. Aucun lien avec
    Stripe : le versement est un virement manuel, marqué « versé » dans la
    console, qui fige le montant.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.prepa_adjuris_config import PREPA_PRICES, matiere_label, matiere_niveau
from app.db.models import (
    PrepaAdjurisAffectation,
    PrepaAdjurisClasse,
    PrepaAdjurisEnrollment,
    PrepaAdjurisProfesseur,
    PrepaAdjurisRemuneration,
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
REMU_ETUDIANT_CENTS = int(os.getenv("PROF_REMU_ETUDIANT_CENTS", "0"))

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


def _compte(e: PrepaAdjurisEnrollment, comptes: dict[str, int]) -> str:
    """Une personne = un compte NAVIRE, sinon un email. Une inscription payée
    avant la création du compte (user_id NULL) rejoint le compte du même email."""
    if e.user_id:
        return f"u{e.user_id}"
    email = (e.email or "").strip().lower()
    return f"u{comptes[email]}" if email in comptes else f"e{email}"


def _comptes_par_email(db: Session, rows: list[PrepaAdjurisEnrollment]) -> dict[str, int]:
    emails = {(e.email or "").strip().lower() for e in rows if not e.user_id and e.email}
    if not emails:
        return {}
    return {
        email.lower(): uid
        for uid, email in db.execute(select(User.id, User.email).where(func.lower(User.email).in_(emails)))
    }


def _encore_valide(e: PrepaAdjurisEnrollment, maintenant: datetime) -> bool:
    """Inscription qui donne accès : active ou en impayé (accès conservé), et
    pas un accès accordé à la main arrivé à échéance (le job horaire passe
    ensuite le statut à expired)."""
    if e.status not in STATUTS_ETUDIANT:
        return False
    fin = aware(e.expires_at)
    return fin is None or fin > maintenant


def eleves_de_la_matiere(
    db: Session, matiere_key: str, par_matiere: dict | None = None
) -> list[tuple[int | None, list[PrepaAdjurisEnrollment]]]:
    """
    Une entrée par étudiant de la matière : (groupe, ses inscriptions). Un même
    étudiant peut avoir plusieurs lignes (accès accordé puis payé, paiement
    avant le compte, réinscription) : il ne compte qu'une fois. Son groupe est
    le dernier groupe choisi dans la console sur l'une de ses lignes, sinon le
    premier groupe actif de la matière.
    """
    par_matiere = par_matiere if par_matiere is not None else groupes_par_matiere(db)
    maintenant = utcnow()
    rows = [
        e for e in db.execute(
            select(PrepaAdjurisEnrollment)
            .where(PrepaAdjurisEnrollment.matiere_key == matiere_key)
            .order_by(PrepaAdjurisEnrollment.id)
        ).scalars().all()
        if _encore_valide(e, maintenant)
    ]
    comptes = _comptes_par_email(db, rows)
    personnes: dict[str, list[PrepaAdjurisEnrollment]] = {}
    for e in rows:
        personnes.setdefault(_compte(e, comptes), []).append(e)

    actifs = {c.id for c in par_matiere.get(matiere_key) or []}
    defaut = (par_matiere.get(matiere_key) or [None])[0]
    out = []
    for lignes in personnes.values():
        choisis = [e.classe_id for e in sorted(lignes, key=lambda x: x.id) if e.classe_id in actifs]
        groupe = choisis[-1] if choisis else (defaut.id if defaut else None)
        out.append((groupe, lignes))
    return out


def principale(lignes: list[PrepaAdjurisEnrollment]) -> PrepaAdjurisEnrollment:
    """La ligne qui représente l'étudiant : active, rattachée à un compte, la
    plus ancienne (sa vraie date d'arrivée)."""
    return min(lignes, key=lambda e: (e.status != "active", e.user_id is None, e.id))


def inscriptions_du_groupe(
    db: Session, classe: PrepaAdjurisClasse, par_matiere: dict | None = None
) -> list[PrepaAdjurisEnrollment]:
    """Les étudiants du groupe, une inscription chacun."""
    return [
        principale(lignes)
        for groupe, lignes in eleves_de_la_matiere(db, classe.matiere_key, par_matiere)
        if groupe == classe.id
    ]


def lignes_de_l_etudiant(db: Session, e: PrepaAdjurisEnrollment) -> list[PrepaAdjurisEnrollment]:
    """Toutes les inscriptions de la même personne dans la même matière (pour
    la changer de groupe d'un coup)."""
    rows = db.execute(
        select(PrepaAdjurisEnrollment).where(PrepaAdjurisEnrollment.matiere_key == e.matiere_key)
    ).scalars().all()
    comptes = _comptes_par_email(db, rows + [e])
    cle = _compte(e, comptes)
    return [x for x in rows if _compte(x, comptes) == cle]


def groupes_de_l_etudiant(db: Session, user_id: int) -> set[int]:
    """Groupes de l'étudiant, toutes matières confondues (inscriptions actives)."""
    par_matiere = groupes_par_matiere(db)
    matieres = set(db.execute(
        select(PrepaAdjurisEnrollment.matiere_key).where(
            PrepaAdjurisEnrollment.user_id == user_id,
            PrepaAdjurisEnrollment.status == "active",
        )
    ).scalars().all())
    groupes = set()
    for key in matieres:
        for groupe, lignes in eleves_de_la_matiere(db, key, par_matiere):
            if groupe is not None and any(e.user_id == user_id for e in lignes):
                groupes.add(groupe)
    return groupes


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

def pilotage(db: Session, affectation_ids: list[int]) -> dict[tuple[int, str], PrepaAdjurisRemuneration]:
    """Lignes de pilotage des affectations, par (affectation_id, mois)."""
    if not affectation_ids:
        return {}
    rows = db.execute(
        select(PrepaAdjurisRemuneration).where(PrepaAdjurisRemuneration.affectation_id.in_(affectation_ids))
    ).scalars()
    return {(r.affectation_id, r.mois): r for r in rows}


def previsionnel(
    db: Session,
    affectation: PrepaAdjurisAffectation,
    inscriptions: list[PrepaAdjurisEnrollment],
    maintenant: datetime | None = None,
    lignes: dict[tuple[int, str], PrepaAdjurisRemuneration] | None = None,
) -> dict:
    """Gains par séance et par mois d'une affectation, jusqu'à la fin du
    programme. `realise` = séances déjà commencées, `a_venir` = les autres.
    Un mois au montant fixé dans la console prend ce montant : il est réparti
    entre réalisé et à venir comme le montant calculé (au prorata), ou, si le
    calcul donne 0, compté réalisé une fois le mois commencé."""
    maintenant = maintenant or utcnow()
    fin = fin_programme()
    debuts = [aware(e.inscrit_le or e.created_at) for e in inscriptions]
    lignes = lignes if lignes is not None else pilotage(db, [affectation.id])

    mois: dict[str, dict] = {}
    nb_passees = nb_futures = 0
    for d in sorted(dates_seances(db, affectation.classe.matiere_key)):
        if mois_de(d) > fin:
            continue
        nb = sum(1 for x in debuts if x is None or x < d)
        montant = affectation.remu_seance_cents + affectation.remu_etudiant_cents * nb
        cle = cle_mois(mois_de(d))
        m = mois.setdefault(cle, {"mois": cle, "seances": 0, "calcule_cents": 0, "_realise": 0})
        m["seances"] += 1
        m["calcule_cents"] += montant
        if d <= maintenant:
            m["_realise"] += montant
            nb_passees += 1
        else:
            nb_futures += 1

    # Mois fixés à la main sans séance au calendrier (prime, rattrapage…).
    for (aff_id, cle), r in lignes.items():
        if aff_id == affectation.id and cle not in mois:
            mois[cle] = {"mois": cle, "seances": 0, "calcule_cents": 0, "_realise": 0}

    en_cours = cle_mois(mois_de(maintenant))
    realise = a_venir = 0
    for cle in sorted(mois):
        m = mois[cle]
        r = lignes.get((affectation.id, cle))
        fixe = r.montant_cents if r is not None else None
        final = m["calcule_cents"] if fixe is None else fixe
        if fixe is None:
            part = m["_realise"]
        elif m["calcule_cents"]:
            part = round(fixe * m["_realise"] / m["calcule_cents"])
        else:
            part = fixe if cle <= en_cours else 0
        if r is not None and r.verse_le:
            part = final
        realise += part
        a_venir += final - part
        m.update({
            "fixe_cents": fixe,
            "montant_cents": final,
            "note": r.note if r is not None else "",
            "verse_le": _iso(aware(r.verse_le)) if r is not None else None,
            "reference": r.reference if r is not None else None,
        })
        del m["_realise"]

    return {
        "remu_seance_cents": affectation.remu_seance_cents,
        "remu_etudiant_cents": affectation.remu_etudiant_cents,
        "par_seance_cents": affectation.remu_seance_cents + affectation.remu_etudiant_cents * len(inscriptions),
        "seances_passees": nb_passees,
        "seances_a_venir": nb_futures,
        "realise_cents": realise,
        "a_venir_cents": a_venir,
        "total_cents": realise + a_venir,
        "verse_cents": sum(m["montant_cents"] for m in mois.values() if m["verse_le"]),
        "mois": [mois[k] for k in sorted(mois)],
    }


def get_professeur(db: Session, user_id: int) -> PrepaAdjurisProfesseur | None:
    return db.execute(
        select(PrepaAdjurisProfesseur).where(PrepaAdjurisProfesseur.user_id == user_id)
    ).scalar_one_or_none()
