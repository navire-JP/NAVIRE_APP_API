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
  - Extras : remu_extra_etudiant_cents × étudiants facturés, par cours
    prolongé (app/services/cours_live.py), ajouté au mois du cours.
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
# Par étudiant resté et facturé lors d'un extra (voir cours_live.py).
REMU_EXTRA_ETUDIANT_CENTS = int(os.getenv("PROF_REMU_EXTRA_ETUDIANT_CENTS", "0"))

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
            m["seances_passees"] = m.get("seances_passees", 0) + 1
            nb_passees += 1
        else:
            nb_futures += 1

    # Extras (cours prolongés, lancés depuis Discord) : déjà réalisés.
    from app.services.cours_live import extras_par_mois

    nb_extras = 0
    for cle, x in extras_par_mois(db, affectation).items():
        m = mois.setdefault(cle, {"mois": cle, "seances": 0, "calcule_cents": 0, "_realise": 0})
        m["extras"] = x["cours"]
        m["extras_cents"] = x["cents"]
        m["calcule_cents"] += x["cents"]
        m["_realise"] += x["cents"]
        nb_extras += x["cours"]

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
        m.setdefault("extras", 0)
        m.setdefault("extras_cents", 0)
        m.setdefault("seances_passees", 0)
        m.update({
            "fixe_cents": fixe,
            "montant_cents": final,
            "realise_cents": part,
            "note": r.note if r is not None else "",
            "verse_le": _iso(aware(r.verse_le)) if r is not None else None,
            "reference": r.reference if r is not None else None,
        })
        del m["_realise"]

    return {
        "remu_seance_cents": affectation.remu_seance_cents,
        "remu_etudiant_cents": affectation.remu_etudiant_cents,
        "remu_extra_etudiant_cents": affectation.remu_extra_etudiant_cents or 0,
        "extras": nb_extras,
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


# ============================================================
# Détail d'un mois : séance par séance, au centime près
# ============================================================

def payant(e: PrepaAdjurisEnrollment) -> bool:
    """Inscription réglée par Stripe. Un accès accordé à la main (source
    « admin », sans abonnement) ne rapporte rien à NAVIRE."""
    return bool(e.stripe_subscription_id) and e.source != "admin"


def noms_etudiants(db: Session, inscriptions: list[PrepaAdjurisEnrollment]) -> dict[int, str]:
    """Nom affiché de chaque inscription : prénom + nom du formulaire, sinon
    pseudo NAVIRE, sinon début de l'email."""
    from app.db.models import PrepaAdjurisInscription

    users = {
        u.id: u for u in db.execute(
            select(User).where(User.id.in_([e.user_id for e in inscriptions if e.user_id]))
        ).scalars()
    } if any(e.user_id for e in inscriptions) else {}
    emails = {
        e.id: ((users[e.user_id].email if e.user_id in users else e.email) or "").strip().lower()
        for e in inscriptions
    }
    formulaire = {}
    if any(emails.values()):
        formulaire = {
            (email or "").lower(): f"{prenom or ''} {nom or ''}".strip()
            for email, prenom, nom in db.execute(
                select(PrepaAdjurisInscription.email, PrepaAdjurisInscription.prenom, PrepaAdjurisInscription.nom)
                .where(func.lower(PrepaAdjurisInscription.email).in_(set(emails.values()) - {""}))
            )
        }
    out = {}
    for e in inscriptions:
        u = users.get(e.user_id)
        email = emails[e.id]
        out[e.id] = formulaire.get(email) or (u.username if u else "") or email.partition("@")[0] or f"Étudiant {e.id}"
    return out


def repartir(total: int, poids: list[int]) -> list[int]:
    """Répartit `total` centimes selon `poids`, au centime près (plus forts
    restes) : la somme est exactement `total`."""
    somme = sum(poids)
    if not poids or somme <= 0:
        return [0] * len(poids)
    parts = [total * p // somme for p in poids]
    restes = [total * p % somme for p in poids]
    for i in sorted(range(len(poids)), key=lambda i: restes[i], reverse=True)[: total - sum(parts)]:
        parts[i] += 1
    return parts


def detail_mois(
    db: Session,
    affectation: PrepaAdjurisAffectation,
    inscriptions: list[PrepaAdjurisEnrollment],
    cle: str,
    maintenant: datetime | None = None,
    lignes: dict[tuple[int, str], PrepaAdjurisRemuneration] | None = None,
    avec_navire: bool = True,
) -> dict:
    """Séances d'un mois pour une affectation : élèves comptés, extras, part du
    professeur, encaissé et part NAVIRE. Mêmes règles que previsionnel() : la
    somme des parts professeur (ajustement compris) est le montant du mois.

    Encaissé (estimation, avant frais Stripe) : PREPA_PRIX_SEANCE_CENTS par
    élève payant compté à la séance, et le supplément facturé de chaque élève
    resté à un extra (sauf accès sans paiement). Part NAVIRE = encaissé − part
    professeur."""
    from app.core.prepa_adjuris_config import PREPA_PRIX_SEANCE_CENTS
    from app.db.models import PrepaAdjurisCours, PrepaAdjurisSeance

    maintenant = maintenant or utcnow()
    fin = fin_programme()
    lignes = lignes if lignes is not None else pilotage(db, [affectation.id])
    noms = noms_etudiants(db, inscriptions)
    debuts = [(e, aware(e.inscrit_le or e.created_at)) for e in inscriptions]
    # Payant si l'une des lignes de la personne est réglée par Stripe (accès
    # accordé à la main, puis abonnement pris : la ligne principale est la
    # plus ancienne, sans abonnement).
    ids = {e.id for e in inscriptions}
    payants = {e.id for e in inscriptions if payant(e)}
    for _groupe, lignes_e in eleves_de_la_matiere(db, affectation.classe.matiere_key):
        p = principale(lignes_e)
        if p.id in ids and any(payant(x) for x in lignes_e):
            payants.add(p.id)

    dates = [
        d for d in sorted(dates_seances(db, affectation.classe.matiere_key))
        if mois_de(d) <= fin and cle_mois(mois_de(d)) == cle
    ]
    seances: list[dict] = []
    for d in dates:
        presents = sorted((e for e, x in debuts if x is None or x < d), key=lambda e: noms[e.id].lower())
        part_prof = affectation.remu_seance_cents + affectation.remu_etudiant_cents * len(presents)
        encaisse = PREPA_PRIX_SEANCE_CENTS * sum(1 for e in presents if e.id in payants)
        seances.append({
            "date": _iso(d),
            "passee": d <= maintenant,
            "hors_agenda": False,
            "eleves": [
                {"enrollment_id": e.id, "nom": noms[e.id], "payant": e.id in payants,
                 **({"encaisse_cents": PREPA_PRIX_SEANCE_CENTS if e.id in payants else 0} if avec_navire else {})}
                for e in presents
            ],
            "nb_eleves": len(presents),
            "part_prof_fixe_cents": affectation.remu_seance_cents,
            "part_prof_eleves_cents": affectation.remu_etudiant_cents * len(presents),
            "part_prof_cents": part_prof,
            "encaisse_cents": encaisse,
            "cours": None,
            "extra": None,
        })

    # Cours lancés depuis Discord : rattachés à leur séance, extras compris.
    cours = db.execute(
        select(PrepaAdjurisCours).where(
            PrepaAdjurisCours.classe_id == affectation.classe_id,
            PrepaAdjurisCours.professeur_id == affectation.professeur_id,
            PrepaAdjurisCours.statut == "termine",
        ).order_by(PrepaAdjurisCours.started_at)
    ).scalars().all()
    cours = [c for c in cours if cle_mois(mois_de(aware(c.started_at))) == cle]
    seance_dates = {}
    ids = [c.seance_id for c in cours if c.seance_id]
    if ids:
        seance_dates = {
            i: aware(d) for i, d in db.execute(
                select(PrepaAdjurisSeance.id, PrepaAdjurisSeance.date_debut).where(PrepaAdjurisSeance.id.in_(ids))
            )
        }
    par_date = {s["date"]: s for s in seances}
    for c in cours:
        cible = par_date.get(_iso(seance_dates.get(c.seance_id))) if c.seance_id else None
        if cible is None or cible["cours"] is not None:
            # Cours hors agenda : seul l'extra éventuel est rémunéré.
            if not c.extra_at:
                continue
            cible = {
                "date": _iso(aware(c.started_at)), "passee": True, "hors_agenda": True, "eleves": [], "nb_eleves": 0,
                "part_prof_fixe_cents": 0, "part_prof_eleves_cents": 0, "part_prof_cents": 0, "encaisse_cents": 0,
                "cours": None, "extra": None,
            }
            seances.append(cible)
        cible["cours"] = {
            "id": c.id,
            "debut": _iso(aware(c.started_at)),
            "fin": _iso(aware(c.ended_at)),
            "duree_comptee_minutes": c.duree_comptee_minutes,
            "cloture_auto": c.cloture_auto,
        }
        if not c.extra_at:
            continue
        eleves = sorted(c.eleves, key=lambda e: (e.nom or "").lower())
        factures = [e for e in eleves if e.extra_cents > 0]
        enc = sum(e.extra_cents for e in factures if e.facturation != "sans_paiement")
        part = len(factures) * (affectation.remu_extra_etudiant_cents or 0)
        cible["extra"] = {
            "ajoute_a": _iso(aware(c.extra_at)),
            "eleves": [
                {"nom": e.nom, "reste": e.retire_le is None, "facture_cents": e.extra_cents,
                 "facturation": e.facturation}
                for e in eleves
            ],
            "nb_factures": len(factures),
            "part_prof_cents": part,
            "encaisse_cents": enc,
        }
        cible["part_prof_cents"] += part
        cible["encaisse_cents"] += enc

    seances.sort(key=lambda s: s["date"])
    calcule = sum(s["part_prof_cents"] for s in seances)
    r = lignes.get((affectation.id, cle))
    fixe = r.montant_cents if r is not None else None
    montant = calcule if fixe is None else fixe
    for s in seances:
        s["part_prof_calculee_cents"] = s["part_prof_cents"]
        s["reparti"] = False
    repartition = None
    if fixe is not None and seances:
        # Montant fixé dans la console : réparti sur les séances du mois, au
        # prorata du calcul, sinon des élèves comptés, sinon à parts égales.
        if calcule > 0:
            repartition, poids = "prorata", [s["part_prof_cents"] for s in seances]
        elif sum(s["nb_eleves"] for s in seances) > 0:
            repartition, poids = "par_eleve", [s["nb_eleves"] for s in seances]
        else:
            repartition = "par_seance"
            poids = [0 if s["hors_agenda"] else 1 for s in seances]
            if not any(poids):
                poids = [1] * len(seances)
        for s, cents in zip(seances, repartir(fixe, poids)):
            s["part_prof_cents"] = cents
            s["reparti"] = True
    encaisse = sum(s["encaisse_cents"] for s in seances)
    for s in seances:
        s["part_navire_cents"] = s["encaisse_cents"] - s["part_prof_cents"]
        if not avec_navire:
            for k in ("encaisse_cents", "part_navire_cents"):
                s.pop(k)
            if s["extra"]:
                s["extra"].pop("encaisse_cents")

    out = {
        "mois": cle,
        "affectation_id": affectation.id,
        "classe_id": affectation.classe_id,
        "classe": affectation.classe.nom,
        "bareme": {
            "remu_seance_cents": affectation.remu_seance_cents,
            "remu_etudiant_cents": affectation.remu_etudiant_cents,
            "remu_extra_etudiant_cents": affectation.remu_extra_etudiant_cents or 0,
            **({"prix_seance_cents": PREPA_PRIX_SEANCE_CENTS} if avec_navire else {}),
        },
        "seances": seances,
        "totaux": {
            "seances": sum(1 for s in seances if not s["hors_agenda"]),
            "extras": sum(1 for s in seances if s["extra"]),
            "part_prof_seances_cents": sum(s["part_prof_calculee_cents"] - (s["extra"] or {}).get("part_prof_cents", 0) for s in seances),
            "part_prof_extras_cents": sum((s["extra"] or {}).get("part_prof_cents", 0) for s in seances),
            "calcule_cents": calcule,
            "fixe_cents": fixe,
            "repartition": repartition,
            # Reste non réparti : mois fixé sans aucune séance.
            "ajustement_cents": montant - sum(s["part_prof_cents"] for s in seances),
            "note": r.note if r is not None else "",
            "part_prof_cents": montant,
            "verse_le": _iso(aware(r.verse_le)) if r is not None else None,
        },
    }
    if avec_navire:
        out["totaux"]["encaisse_cents"] = encaisse
        out["totaux"]["part_navire_cents"] = encaisse - montant
    return out
