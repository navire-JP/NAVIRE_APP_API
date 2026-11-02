"""
app/services/cours_live.py
===========================
Cours lancés en direct depuis Discord (salon #cours-start) et extras.
Routes : app/routers/discord_bot.py (/discord/cours/…), cog :
app/bot_discord/cogs/cours_start.py.

Règles :
  - Un professeur actif lance un cours pour l'un de ses groupes. Un seul
    cours en cours à la fois par professeur : relancer rouvre le cours en cours.
  - Le cours dure PREPA_COURS_DUREE_MINUTES (1 h). Au-delà, il est en extra.
  - Une fois l'extra dépassé de PREPA_EXTRA_SEUIL_MINUTES (10 min), le
    professeur peut ajouter l'extra : les étudiants du groupe sont listés, il
    retire ceux qui partent.
  - À la fin du cours, un extra ajouté fait compter le cours
    PREPA_EXTRA_DUREE_COMPTEE (1 h 30) et facture PREPA_EXTRA_PRIX_CENTS
    (10 €) à chaque étudiant resté. Le supplément est ajouté au prochain
    prélèvement mensuel de son abonnement (élément de facture Stripe en
    attente). Une inscription sans abonnement Stripe (accès accordé à la main)
    est marquée « sans_paiement » pour la console.
  - Le professeur touche remu_extra_etudiant_cents par étudiant facturé
    (affectation), ajouté à sa rémunération du mois.
  - Un cours non terminé est clos par le job horaire après
    PREPA_COURS_CLOTURE_AUTO_HEURES ; les échecs Stripe y sont réessayés.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import stripe
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import STRIPE_SECRET_KEY
from app.core.prepa_adjuris_config import (
    PREPA_COURS_CLOTURE_AUTO_HEURES,
    PREPA_COURS_DUREE_MINUTES,
    PREPA_EXTRA_DUREE_COMPTEE,
    PREPA_EXTRA_PRIX_CENTS,
    PREPA_EXTRA_SEUIL_MINUTES,
    matiere_label,
)
from app.db.models import (
    PrepaAdjurisAffectation,
    PrepaAdjurisClasse,
    PrepaAdjurisCours,
    PrepaAdjurisCoursEleve,
    PrepaAdjurisEnrollment,
    PrepaAdjurisInscription,
    PrepaAdjurisProfesseur,
    PrepaAdjurisSeance,
    User,
)
from app.services import professeurs as profs
from app.services.prepa_adjuris_billing import cle_mois, mois_de

logger = logging.getLogger(__name__)

# Inscriptions dont l'abonnement Stripe prélève encore.
_STATUTS_FACTURABLES = ("active", "payment_failed")


class CoursErreur(Exception):
    """Refus attendu (affiché tel quel au professeur)."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


aware = profs.aware


# ============================================================
# Professeur et groupes
# ============================================================

def professeur_par_discord(db: Session, discord_id: str) -> PrepaAdjurisProfesseur | None:
    """Professeur actif dont le compte NAVIRE est lié à ce compte Discord."""
    return db.execute(
        select(PrepaAdjurisProfesseur)
        .join(User, User.id == PrepaAdjurisProfesseur.user_id)
        .where(User.discord_id == str(discord_id), PrepaAdjurisProfesseur.status == "active")
    ).scalar_one_or_none()


def groupes_du_professeur(prof: PrepaAdjurisProfesseur) -> list[PrepaAdjurisClasse]:
    return sorted(
        (a.classe for a in prof.affectations if a.classe and a.classe.is_active),
        key=lambda c: (c.matiere_key, c.id),
    )


def _label_groupe(c: PrepaAdjurisClasse) -> str:
    label = matiere_label(c.matiere_key)
    return label if c.nom in ("", label) else f"{label} · {c.nom}"


def cours_en_cours(db: Session, prof: PrepaAdjurisProfesseur) -> PrepaAdjurisCours | None:
    return db.execute(
        select(PrepaAdjurisCours)
        .where(PrepaAdjurisCours.professeur_id == prof.id, PrepaAdjurisCours.statut == "en_cours")
        .order_by(PrepaAdjurisCours.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def cours_du_professeur(db: Session, prof: PrepaAdjurisProfesseur, cours_id: int) -> PrepaAdjurisCours:
    c = db.get(PrepaAdjurisCours, cours_id)
    if c is None or c.professeur_id != prof.id:
        raise CoursErreur("Ce cours est introuvable.")
    return c


# ============================================================
# Déroulé du cours
# ============================================================

def fin_heure(c: PrepaAdjurisCours) -> datetime:
    return aware(c.started_at) + timedelta(minutes=PREPA_COURS_DUREE_MINUTES)


def extra_possible_a(c: PrepaAdjurisCours) -> datetime:
    return fin_heure(c) + timedelta(minutes=PREPA_EXTRA_SEUIL_MINUTES)


def phase(c: PrepaAdjurisCours, maintenant: datetime | None = None) -> str:
    """heure | extra (moins de 10 min) | extra_disponible | extra_ajoute | termine"""
    maintenant = maintenant or utcnow()
    if c.statut != "en_cours":
        return "termine"
    if c.extra_at:
        return "extra_ajoute"
    if maintenant < fin_heure(c):
        return "heure"
    if maintenant < extra_possible_a(c):
        return "extra"
    return "extra_disponible"


def _seance_proche(db: Session, matiere_key: str, maintenant: datetime) -> int | None:
    """Séance prévue de la matière la plus proche du lancement (±3 h)."""
    lignes = db.execute(
        select(PrepaAdjurisSeance.id, PrepaAdjurisSeance.date_debut).where(
            PrepaAdjurisSeance.matiere_key == matiere_key,
            PrepaAdjurisSeance.statut == "prevue",
            PrepaAdjurisSeance.date_debut >= maintenant - timedelta(hours=3),
            PrepaAdjurisSeance.date_debut <= maintenant + timedelta(hours=3),
        )
    ).all()
    if not lignes:
        return None
    return min(lignes, key=lambda r: abs((aware(r[1]) - maintenant).total_seconds()))[0]


def demarrer(
    db: Session, prof: PrepaAdjurisProfesseur, classe_id: int, maintenant: datetime | None = None
) -> PrepaAdjurisCours:
    maintenant = maintenant or utcnow()
    existant = cours_en_cours(db, prof)
    if existant:
        return existant
    classe = next((c for c in groupes_du_professeur(prof) if c.id == classe_id), None)
    if classe is None:
        raise CoursErreur("Ce groupe ne fait pas partie de tes groupes.")
    c = PrepaAdjurisCours(
        classe_id=classe.id,
        matiere_key=classe.matiere_key,
        professeur_id=prof.id,
        seance_id=_seance_proche(db, classe.matiere_key, maintenant),
        started_at=maintenant,
        duree_comptee_minutes=PREPA_COURS_DUREE_MINUTES,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _noms_par_email(db: Session, emails: set[str]) -> dict[str, str]:
    """Prénom et nom saisis au formulaire d'inscription, par email."""
    if not emails:
        return {}
    return {
        email.lower(): f"{prenom} {nom}".strip()
        for email, prenom, nom in db.execute(
            select(PrepaAdjurisInscription.email, PrepaAdjurisInscription.prenom, PrepaAdjurisInscription.nom)
            .where(func.lower(PrepaAdjurisInscription.email).in_(emails))
        )
    }


def _eleves_du_groupe(db: Session, classe: PrepaAdjurisClasse) -> list[PrepaAdjurisCoursEleve]:
    inscriptions = profs.inscriptions_du_groupe(db, classe)
    users = {
        u.id: u for u in db.execute(
            select(User).where(User.id.in_([e.user_id for e in inscriptions if e.user_id]))
        ).scalars()
    }
    emails = {
        ((users[e.user_id].email if e.user_id in users else e.email) or "").strip().lower()
        for e in inscriptions
    }
    noms = _noms_par_email(db, emails - {""})
    out = []
    for e in inscriptions:
        u = users.get(e.user_id)
        email = ((u.email if u else e.email) or "").strip().lower()
        nom = noms.get(email) or (u.username if u else "") or email.partition("@")[0] or f"Étudiant {e.id}"
        out.append(PrepaAdjurisCoursEleve(
            enrollment_id=e.id,
            user_id=e.user_id,
            nom=nom[:170],
            discord_id=u.discord_id if u else None,
        ))
    return sorted(out, key=lambda x: x.nom.lower())


def ajouter_extra(db: Session, c: PrepaAdjurisCours, maintenant: datetime | None = None) -> PrepaAdjurisCours:
    maintenant = maintenant or utcnow()
    p = phase(c, maintenant)
    if p == "termine":
        raise CoursErreur("Ce cours est terminé.")
    if p == "extra_ajoute":
        return c
    if p in ("heure", "extra"):
        raise CoursErreur("L'extra ne peut être ajouté qu'après 10 minutes de dépassement.")
    c.extra_at = maintenant
    for eleve in _eleves_du_groupe(db, c.classe):
        c.eleves.append(eleve)
    db.commit()
    db.refresh(c)
    return c


def basculer_eleve(db: Session, c: PrepaAdjurisCours, eleve_id: int, maintenant: datetime | None = None) -> PrepaAdjurisCours:
    """Retire l'étudiant de l'extra, ou le remet s'il était retiré."""
    if c.statut != "en_cours":
        raise CoursErreur("Ce cours est terminé.")
    eleve = next((e for e in c.eleves if e.id == eleve_id), None)
    if eleve is None:
        raise CoursErreur("Étudiant introuvable dans ce cours.")
    eleve.retire_le = None if eleve.retire_le else (maintenant or utcnow())
    db.commit()
    db.refresh(c)
    return c


def terminer(
    db: Session, c: PrepaAdjurisCours, maintenant: datetime | None = None, auto: bool = False
) -> PrepaAdjurisCours:
    if c.statut != "en_cours":
        return c
    maintenant = maintenant or utcnow()
    c.statut = "termine"
    c.ended_at = maintenant
    c.cloture_auto = auto
    if c.extra_at:
        c.duree_comptee_minutes = PREPA_EXTRA_DUREE_COMPTEE
        for e in c.eleves:
            if e.retire_le is None:
                e.extra_cents = PREPA_EXTRA_PRIX_CENTS
                e.facturation = "a_facturer"
    db.commit()
    facturer_extras(db, c)
    db.refresh(c)
    return c


# ============================================================
# Facturation des extras (Stripe)
# ============================================================

def _facturer(db: Session, c: PrepaAdjurisCours, e: PrepaAdjurisCoursEleve) -> None:
    inscription = db.get(PrepaAdjurisEnrollment, e.enrollment_id) if e.enrollment_id else None
    if (
        inscription is None
        or inscription.status not in _STATUTS_FACTURABLES
        or not inscription.stripe_customer_id
        or not inscription.stripe_subscription_id
    ):
        e.facturation = "sans_paiement"
        return
    date = aware(c.started_at).strftime("%d/%m/%Y")
    item = stripe.InvoiceItem.create(
        customer=inscription.stripe_customer_id,
        subscription=inscription.stripe_subscription_id,
        amount=e.extra_cents,
        currency="eur",
        description=f"Prép'AdJuris — {matiere_label(c.matiere_key)} : extra du cours du {date}",
        metadata={"type": "adjuris_extra", "cours_id": str(c.id), "cours_eleve_id": str(e.id)},
        # Une relance après un échec ne facture jamais deux fois.
        idempotency_key=f"adjuris-extra-{e.id}",
    )
    e.stripe_invoice_item_id = item.id
    e.facturation = "facture"


def facturer_extras(db: Session, c: PrepaAdjurisCours) -> None:
    """Ajoute les suppléments en attente au prochain prélèvement. Ne lève pas."""
    a_traiter = [e for e in c.eleves if e.facturation in ("a_facturer", "erreur") and e.extra_cents > 0]
    if not a_traiter:
        return
    stripe.api_key = STRIPE_SECRET_KEY  # MEOLES écrase la clé au démarrage
    for e in a_traiter:
        try:
            _facturer(db, c, e)
        except stripe.StripeError as exc:
            logger.error("Extra non facturé (cours %s, élève %s) : %s", c.id, e.id, exc)
            e.facturation = "erreur"
        db.commit()


def job_cours_live(db_factory, maintenant: datetime | None = None) -> None:
    """Toutes les heures : clôt les cours oubliés, réessaie la facturation
    des extras en échec. Ne lève jamais."""
    maintenant = maintenant or utcnow()
    db: Session = db_factory()
    try:
        limite = maintenant - timedelta(hours=PREPA_COURS_CLOTURE_AUTO_HEURES)
        for c in db.execute(
            select(PrepaAdjurisCours).where(
                PrepaAdjurisCours.statut == "en_cours", PrepaAdjurisCours.started_at < limite
            )
        ).scalars().all():
            fin = aware(c.started_at) + timedelta(
                minutes=PREPA_EXTRA_DUREE_COMPTEE if c.extra_at else PREPA_COURS_DUREE_MINUTES
            )
            terminer(db, c, fin, auto=True)
        ids = set(db.execute(
            select(PrepaAdjurisCoursEleve.cours_id).where(PrepaAdjurisCoursEleve.facturation == "erreur")
        ).scalars().all())
        for cours_id in sorted(ids):
            facturer_extras(db, db.get(PrepaAdjurisCours, cours_id))
    except Exception as exc:  # noqa: BLE001
        logger.exception("job_cours_live : %s", exc)
        db.rollback()
    finally:
        db.close()


# ============================================================
# Rémunération des extras
# ============================================================

def extras_par_mois(db: Session, affectation: PrepaAdjurisAffectation) -> dict[str, dict]:
    """{"2026-11": {"cours": 2, "etudiants": 9, "cents": …}} pour les extras
    terminés du groupe animés par le professeur de l'affectation."""
    lignes = db.execute(
        select(PrepaAdjurisCours.id, PrepaAdjurisCours.started_at, func.count(PrepaAdjurisCoursEleve.id))
        .join(PrepaAdjurisCoursEleve, PrepaAdjurisCoursEleve.cours_id == PrepaAdjurisCours.id)
        .where(
            PrepaAdjurisCours.classe_id == affectation.classe_id,
            PrepaAdjurisCours.professeur_id == affectation.professeur_id,
            PrepaAdjurisCours.statut == "termine",
            PrepaAdjurisCours.extra_at.is_not(None),
            PrepaAdjurisCoursEleve.extra_cents > 0,
        )
        .group_by(PrepaAdjurisCours.id, PrepaAdjurisCours.started_at)
    ).all()
    out: dict[str, dict] = {}
    for _id, debut, nb in lignes:
        m = out.setdefault(cle_mois(mois_de(aware(debut))), {"cours": 0, "etudiants": 0, "cents": 0})
        m["cours"] += 1
        m["etudiants"] += nb
        m["cents"] += nb * (affectation.remu_extra_etudiant_cents or 0)
    return out


# ============================================================
# Vue pour le bot
# ============================================================

def _ts(dt: datetime | None) -> int | None:
    return int(aware(dt).timestamp()) if dt else None


def cours_payload(c: PrepaAdjurisCours, maintenant: datetime | None = None) -> dict:
    maintenant = maintenant or utcnow()
    eleves = [
        {
            "id": e.id,
            "nom": e.nom,
            "discord_id": e.discord_id,
            "retire": e.retire_le is not None,
            "facturation": e.facturation,
        }
        for e in c.eleves
    ]
    return {
        "id": c.id,
        "statut": c.statut,
        "phase": phase(c, maintenant),
        "groupe": _label_groupe(c.classe),
        "matiere_key": c.matiere_key,
        "started_ts": _ts(c.started_at),
        "fin_heure_ts": _ts(fin_heure(c)),
        "extra_possible_ts": _ts(extra_possible_a(c)),
        "extra_ts": _ts(c.extra_at),
        "ended_ts": _ts(c.ended_at),
        "duree_comptee_minutes": c.duree_comptee_minutes,
        "extra_prix_cents": PREPA_EXTRA_PRIX_CENTS,
        "eleves": eleves,
        "nb_restants": sum(1 for e in eleves if not e["retire"]),
    }


def groupes_payload(prof: PrepaAdjurisProfesseur) -> list[dict]:
    return [{"classe_id": c.id, "label": _label_groupe(c)} for c in groupes_du_professeur(prof)]
