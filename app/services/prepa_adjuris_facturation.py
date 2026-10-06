"""
app/services/prepa_adjuris_facturation.py
==========================================
Facturation Prép'AdJuris : la partie qui touche la base et Stripe.

Le calcul lui-même est dans prepa_adjuris_billing.py (pur, testé). Ce module :
  - lit le calendrier des séances ;
  - crée l'échéancier Stripe d'un nouvel abonnement (webhook) ;
  - recalcule et pousse les échéanciers après une modification de l'agenda
    (avec un mode simulation pour l'aperçu de la console) ;
  - gère les impayés : 7 jours de grâce, relance à J+5, retrait à J+7 ;
  - attribue et retire le grade ;
  - fournit les vues de la console (facturation, alertes).

Appelé par : prepa_adjuris.py (checkout), subscriptions.py (webhook),
prepa_adjuris_agenda.py (console), prepa_adjuris_espace.py (anciens
endpoints de séances) et le job horaire (main.py).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import stripe
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import STRIPE_SECRET_KEY
from app.core.prepa_adjuris_config import (
    PREPA_CRENEAUX,
    PREPA_DELAI_IMPAYE_JOURS,
    PREPA_PRICES,
    PREPA_PRIX_SEANCE_CENTS,
    PREPA_RELANCE_IMPAYE_JOURS,
    matiere_label,
    matiere_niveau,
    prices_configures,
)
from app.db.models import PrepaAdjurisEnrollment, PrepaAdjurisSeance, User
from app.services.prepa_adjuris_billing import (
    Echeancier,
    calculer_echeancier,
    comparer,
    date_fr,
    date_prelevement,
    fin_abonnement,
    fin_programme,
    fusionner,
    parse_mois,
    phases_mensuelles,
    recalculer,
    seances_hebdomadaires,
)

logger = logging.getLogger(__name__)

# Inscriptions dont la facturation court encore.
STATUTS_EN_COURS = ("active", "payment_failed", "suspendu")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _cle_stripe() -> None:
    """Force la clé NAVIRE : MEOLES écrase stripe.api_key au démarrage."""
    stripe.api_key = STRIPE_SECRET_KEY


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ============================================================
# Calendrier et devis
# ============================================================

def dates_seances(db: Session, matiere_key: str) -> list[datetime]:
    """
    Début des séances prévues d'une matière. Les séances communes
    (matiere_key NULL, ex. 10 septembre) et les annulées ne comptent pas.

    Filet de sécurité : tant que l'agenda ne contient AUCUNE séance à venir
    pour la matière (ni prévue, ni annulée), on utilise le créneau habituel
    du niveau (PREPA_CRENEAUX), une séance par semaine. Sans lui, une matière
    dont l'agenda n'est pas encore saisi refuserait toute inscription. Dès
    qu'une séance future est saisie, seul l'agenda compte.
    """
    lignes = db.execute(
        select(PrepaAdjurisSeance.date_debut, PrepaAdjurisSeance.statut).where(
            PrepaAdjurisSeance.matiere_key == matiere_key,
        )
    ).all()
    prevues = [_aware(d) for d, statut in lignes if statut == "prevue"]

    maintenant = utcnow()
    if any(_aware(d) > maintenant for d, _ in lignes):
        return prevues

    creneau = PREPA_CRENEAUX.get(matiere_niveau(matiere_key))
    if not creneau:
        return prevues
    jour_semaine, heure, _duree = creneau
    logger.warning(
        "Agenda vide pour %s : calendrier de secours (créneau %s %s).",
        matiere_key, jour_semaine, heure,
    )
    return prevues + seances_hebdomadaires(jour_semaine, heure, maintenant)


def calculer_devis(
    db: Session,
    matieres: list[str],
    inscrit_le: datetime,
    facturable_apres: datetime | None = None,
) -> list[Echeancier]:
    """Un échéancier par matière, pour une inscription à `inscrit_le`."""
    return [
        calculer_echeancier(k, dates_seances(db, k), inscrit_le, facturable_apres)
        for k in matieres
    ]


def echeancier_de(enrollment: PrepaAdjurisEnrollment) -> Echeancier | None:
    if not enrollment.echeancier:
        return None
    try:
        return Echeancier.from_dict(enrollment.echeancier)
    except (KeyError, ValueError, TypeError):
        logger.error("Échéancier illisible pour l'inscription %s", enrollment.id)
        return None


def _inscriptions_abonnement(db: Session, sub_id: str) -> list[PrepaAdjurisEnrollment]:
    return list(db.execute(
        select(PrepaAdjurisEnrollment)
        .where(PrepaAdjurisEnrollment.stripe_subscription_id == sub_id)
        .order_by(PrepaAdjurisEnrollment.id)
    ).scalars().all())


# ============================================================
# Grade
# ============================================================

def attribuer_grade(user: User, matieres: list[str], echeanciers: list[Echeancier]) -> None:
    """
    Grade PREPA tant que l'abonnement est actif. prepa_expires_at sert de
    filet (has_active_prepa_access l'exige) : fin de l'abonnement + délai
    d'impayé. Le retrait réel suit les événements Stripe.
    """
    fin = fin_abonnement(echeanciers) or (utcnow() + timedelta(days=31))
    fin += timedelta(days=PREPA_DELAI_IMPAYE_JOURS)
    user.plan = "prepa"
    user.prepa_annee = matiere_niveau(matieres[0])
    actuelle = _aware(user.prepa_expires_at)
    if not actuelle or actuelle < fin:
        user.prepa_expires_at = fin


def retirer_grade_si_plus_actif(db: Session, user: User) -> bool:
    """Retire le grade si l'élève n'a plus aucune matière active (payée ou
    accordée à la main). Retourne True si le grade a été retiré."""
    encore_actif = db.execute(
        select(PrepaAdjurisEnrollment.id).where(
            PrepaAdjurisEnrollment.user_id == user.id,
            PrepaAdjurisEnrollment.status == "active",
        )
    ).first()
    if encore_actif or user.plan != "prepa":
        return False
    user.plan = "free"
    user.prepa_expires_at = utcnow()
    return True


def _roles(user: User | None, matieres: list[str], ajouter: bool) -> None:
    if not user or not user.discord_id:
        return
    from app.bot_discord.role_sync import assign_adjuris_role_sync, remove_adjuris_role_sync
    for key in matieres:
        (assign_adjuris_role_sync if ajouter else remove_adjuris_role_sync)(user.discord_id, key)


def _user_de(db: Session, enrollment: PrepaAdjurisEnrollment) -> User | None:
    if enrollment.user_id:
        return db.execute(select(User).where(User.id == enrollment.user_id)).scalar_one_or_none()
    if enrollment.email:
        return db.execute(select(User).where(User.email == enrollment.email)).scalar_one_or_none()
    return None


# ============================================================
# Échéancier Stripe
# ============================================================

def _price_id(item) -> str:
    price = item.get("price") if hasattr(item, "get") else item["price"]
    return price if isinstance(price, str) else price["id"]


def _phase_existante(phase) -> dict:
    """Phase déjà commencée, renvoyée telle quelle à Stripe (on ne modifie
    jamais ce qui a déjà été facturé)."""
    out = {
        "start_date": phase["start_date"],
        "end_date": phase["end_date"],
        "items": [{"price": _price_id(i), "quantity": i.get("quantity") or 0} for i in phase["items"]],
        "proration_behavior": "none",
    }
    if phase.get("billing_cycle_anchor") == "phase_start":
        out["billing_cycle_anchor"] = "phase_start"
    return out


def _phases_futures(echeanciers: list[Echeancier], apres: int) -> list[dict]:
    """Phases mensuelles qui commencent à partir de `apres` (timestamp).
    Stripe déduit le début d'une phase de la fin de la précédente : on ne
    transmet start_date que pour la première phase de la liste."""
    prices = {e.matiere_key: PREPA_PRICES[e.matiere_key]["recurring"] for e in echeanciers}
    futures = [p for p in phases_mensuelles(echeanciers, prices) if p["start_date"] >= apres]
    for p in futures:
        p.pop("start_date", None)
    return futures


def _schedule_id(sub_id: str, inscriptions: list[PrepaAdjurisEnrollment]) -> str | None:
    for e in inscriptions:
        if e.stripe_schedule_id:
            return e.stripe_schedule_id
    sub = stripe.Subscription.retrieve(sub_id)
    sched = sub.get("schedule")
    if sched:
        return sched if isinstance(sched, str) else sched["id"]
    return None


def pousser_echeancier_stripe(db: Session, sub_id: str) -> str | None:
    """
    Crée ou met à jour l'échéancier Stripe d'un abonnement à partir des
    échéanciers stockés en base (toutes ses matières). La phase en cours est
    conservée telle quelle ; seules les phases futures sont réécrites.
    Lève stripe.StripeError en cas d'échec (l'appelant marque le statut).
    """
    inscriptions = [
        e for e in _inscriptions_abonnement(db, sub_id)
        if e.echeancier and e.status in STATUTS_EN_COURS
    ]
    echeanciers = [x for x in (echeancier_de(e) for e in inscriptions) if x]
    if not echeanciers:
        return None

    _cle_stripe()
    schedule_id = _schedule_id(sub_id, inscriptions)
    if schedule_id:
        schedule = stripe.SubscriptionSchedule.retrieve(schedule_id)
        if schedule.get("status") not in ("active", "not_started"):
            logger.info("Échéancier %s inactif (%s) : rien à pousser.", schedule_id, schedule.get("status"))
            return schedule_id
    else:
        schedule = stripe.SubscriptionSchedule.create(from_subscription=sub_id)
        schedule_id = schedule["id"]

    courante = schedule.get("current_phase") or {}
    debut_courant = courante.get("start_date")
    phase_courante = next(
        (p for p in schedule["phases"] if p["start_date"] == debut_courant),
        schedule["phases"][0],
    )
    phases = [_phase_existante(phase_courante)] + _phases_futures(
        echeanciers, phase_courante["end_date"]
    )
    stripe.SubscriptionSchedule.modify(
        schedule_id, phases=phases, end_behavior="cancel", proration_behavior="none"
    )

    for e in inscriptions:
        e.stripe_schedule_id = schedule_id
        e.echeancier_statut = "ok"
    db.commit()
    return schedule_id


# ============================================================
# Recalcul après modification de l'agenda
# ============================================================

def _impact(e: PrepaAdjurisEnrollment, ecart) -> dict:
    return {
        "enrollment_id": e.id,
        "email": e.email,
        "matiere_key": e.matiere_key,
        "matiere_label": matiere_label(e.matiere_key),
        "mois": ecart.mois,
        "date_prelevement": date_prelevement(parse_mois(ecart.mois)).isoformat(),
        "avant": ecart.avant,
        "apres": ecart.apres,
        "delta_cents": ecart.delta_cents,
        "type": ecart.type,
    }


def synchroniser_abonnement(
    db: Session,
    sub_id: str,
    maintenant: datetime | None = None,
    simulation: bool = False,
    forcer_stripe: bool = False,
) -> list[dict]:
    """
    Recalcule les échéanciers d'un abonnement d'après l'agenda actuel.

      - mois pas encore prélevé : la quantité de la phase Stripe est ajustée ;
      - mois déjà prélevé, séance en moins : crédit de 20 € par séance sur le
        solde client Stripe, déduit automatiquement du prélèvement suivant ;
      - mois déjà prélevé, séance en plus : rien n'est facturé a posteriori.

    simulation=True : ne touche ni Stripe ni la base, renvoie seulement les
    écarts (aperçu de la console). En cas d'échec Stripe, l'inscription passe
    en "a_resynchroniser" et le job horaire réessaie.
    """
    maintenant = maintenant or utcnow()
    inscriptions = [
        e for e in _inscriptions_abonnement(db, sub_id)
        if e.echeancier and e.status in STATUTS_EN_COURS
    ]

    impacts: list[dict] = []
    calculs = []
    for e in inscriptions:
        ancien = echeancier_de(e)
        if not ancien:
            continue
        nouveau = recalculer(ancien, dates_seances(db, e.matiere_key))
        ecarts = comparer(ancien, nouveau, maintenant)
        impacts += [_impact(e, x) for x in ecarts]
        if ancien.seance_prepayee != nouveau.seance_prepayee:
            impacts.append({
                "enrollment_id": e.id,
                "email": e.email,
                "matiere_key": e.matiere_key,
                "matiere_label": matiere_label(e.matiere_key),
                "type": "seance_prepayee",
                "avant": ancien.seance_prepayee.isoformat() if ancien.seance_prepayee else None,
                "apres": nouveau.seance_prepayee.isoformat() if nouveau.seance_prepayee else None,
                "delta_cents": 0,
            })
        calculs.append((e, ancien, nouveau, ecarts))

    if simulation:
        return impacts

    a_pousser = forcer_stripe or any(
        e.echeancier_statut == "a_resynchroniser" for e in inscriptions
    )
    _cle_stripe()
    for e, ancien, nouveau, ecarts in calculs:
        # Crédits d'abord, et enregistrés aussitôt : un crédit ne doit jamais
        # être versé deux fois, même si la suite échoue.
        for x in ecarts:
            if x.type != "credit":
                continue
            if not e.stripe_customer_id:
                logger.error("Crédit impossible (pas de client Stripe) : inscription %s", e.id)
                continue
            try:
                stripe.Customer.create_balance_transaction(
                    e.stripe_customer_id,
                    amount=x.delta_cents,      # négatif = crédit
                    currency="eur",
                    description=(
                        f"Prép'AdJuris — {matiere_label(e.matiere_key)} : "
                        f"{x.avant - x.apres} séance(s) annulée(s) en {x.mois}"
                    ),
                )
            except stripe.StripeError as exc:
                logger.error("Crédit Stripe échoué pour l'inscription %s : %s", e.id, exc)
                e.echeancier_statut = "a_resynchroniser"
        e.echeancier = fusionner(ancien, nouveau, maintenant).to_dict()
        if any(not x.deja_preleve for x in ecarts):
            a_pousser = True
    db.commit()

    if a_pousser and inscriptions:
        try:
            pousser_echeancier_stripe(db, sub_id)
        except stripe.StripeError as exc:
            logger.error("Mise à jour de l'échéancier Stripe %s échouée : %s", sub_id, exc)
            for e in inscriptions:
                e.echeancier_statut = "a_resynchroniser"
            db.commit()
    return impacts


def abonnements_concernes(db: Session, matieres: list[str]) -> list[str]:
    """Abonnements dont la facturation dépend de ces matières."""
    return sorted(set(db.execute(
        select(PrepaAdjurisEnrollment.stripe_subscription_id).where(
            PrepaAdjurisEnrollment.matiere_key.in_(matieres),
            PrepaAdjurisEnrollment.status.in_(STATUTS_EN_COURS),
            PrepaAdjurisEnrollment.echeancier.isnot(None),
            PrepaAdjurisEnrollment.stripe_subscription_id.isnot(None),
        )
    ).scalars().all()))


def resynchroniser_matieres(
    db: Session, matieres: list[str], simulation: bool = False
) -> list[dict]:
    """Recalcule tous les abonnements concernés par une modification de
    l'agenda sur ces matières. Ne lève jamais : l'agenda est déjà enregistré,
    une erreur Stripe est rattrapée par le job horaire."""
    matieres = [m for m in dict.fromkeys(matieres) if m]
    impacts: list[dict] = []
    for sub_id in abonnements_concernes(db, matieres):
        try:
            impacts += synchroniser_abonnement(db, sub_id, simulation=simulation)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Recalcul de l'abonnement %s impossible : %s", sub_id, exc)
            if not simulation:
                db.rollback()
                for e in _inscriptions_abonnement(db, sub_id):
                    if e.echeancier:
                        e.echeancier_statut = "a_resynchroniser"
                db.commit()
    return impacts


# ============================================================
# Impayés
# ============================================================

def _matieres_txt(inscriptions: list[PrepaAdjurisEnrollment]) -> str:
    return ", ".join(matiere_label(e.matiere_key) for e in inscriptions)


def _url_facture_ouverte(sub_id: str) -> str | None:
    try:
        _cle_stripe()
        factures = stripe.Invoice.list(subscription=sub_id, status="open", limit=1)
        for f in factures.get("data", []):
            return f.get("hosted_invoice_url")
    except stripe.StripeError as exc:
        logger.warning("Factures ouvertes illisibles pour %s : %s", sub_id, exc)
    return None


def _envoyer(email: str | None, sujet_html: tuple[str, str]) -> None:
    if not email:
        return
    from app.services.email import send_mail
    send_mail(email, *sujet_html)


def marquer_impaye(db: Session, sub_id: str, facture: dict | None = None) -> None:
    """
    invoice.payment_failed : l'accès est CONSERVÉ pendant 7 jours. Les
    rôles et le grade ne sont retirés que par traiter_impayes(). Stripe
    relivre cet événement à chaque nouvelle tentative : le mail ne part
    qu'au premier échec.
    """
    inscriptions = [
        e for e in _inscriptions_abonnement(db, sub_id)
        if e.status in ("active", "payment_failed")
    ]
    if not inscriptions:
        return
    premier_echec = any(e.impaye_depuis is None for e in inscriptions)
    maintenant = utcnow()
    for e in inscriptions:
        e.status = "payment_failed"
        e.impaye_depuis = e.impaye_depuis or maintenant
    db.commit()

    if premier_echec:
        from app.services.email import mail_prepa_adjuris_impaye
        url = (facture or {}).get("hosted_invoice_url")
        _envoyer(
            inscriptions[0].email,
            mail_prepa_adjuris_impaye("echec", _matieres_txt(inscriptions), url),
        )


def regulariser(db: Session, sub_id: str) -> None:
    """
    invoice.paid : si plus aucune facture de l'abonnement n'est ouverte, les
    inscriptions en impayé repassent actives ; grade et rôles sont rendus
    s'ils avaient été retirés.
    """
    inscriptions = [
        e for e in _inscriptions_abonnement(db, sub_id)
        if e.status in ("payment_failed", "suspendu")
    ]
    if not inscriptions:
        return
    if _url_facture_ouverte(sub_id):
        return  # un paiement à 0 € ne solde pas une ancienne facture impayée

    suspendues = [e for e in inscriptions if e.status == "suspendu"]
    for e in inscriptions:
        e.status = "active"
        e.impaye_depuis = None
        e.impaye_relance_le = None
    db.commit()

    if suspendues:
        user = _user_de(db, suspendues[0])
        if user:
            echeanciers = [x for x in (echeancier_de(e) for e in inscriptions) if x]
            attribuer_grade(user, [e.matiere_key for e in inscriptions], echeanciers)
            db.commit()
            _roles(user, [e.matiere_key for e in suspendues], ajouter=True)


def traiter_impayes(db: Session, maintenant: datetime | None = None) -> dict:
    """Job horaire : relance à J+5, retrait du grade et des rôles à J+7."""
    from app.services.email import mail_prepa_adjuris_impaye

    maintenant = maintenant or utcnow()
    en_impaye = db.execute(
        select(PrepaAdjurisEnrollment).where(
            PrepaAdjurisEnrollment.status == "payment_failed",
            PrepaAdjurisEnrollment.impaye_depuis.isnot(None),
        )
    ).scalars().all()

    par_abonnement: dict[str, list[PrepaAdjurisEnrollment]] = {}
    for e in en_impaye:
        par_abonnement.setdefault(e.stripe_subscription_id or f"#{e.id}", []).append(e)

    relances = suspensions = 0
    for sub_id, groupe in par_abonnement.items():
        depuis = min(_aware(e.impaye_depuis) for e in groupe)
        limite = depuis + timedelta(days=PREPA_DELAI_IMPAYE_JOURS)
        url = _url_facture_ouverte(sub_id) if not sub_id.startswith("#") else None

        if maintenant >= limite:
            for e in groupe:
                e.status = "suspendu"
            db.commit()
            user = _user_de(db, groupe[0])
            _roles(user, [e.matiere_key for e in groupe], ajouter=False)
            if user and retirer_grade_si_plus_actif(db, user):
                db.commit()
            _envoyer(groupe[0].email, mail_prepa_adjuris_impaye("suspendu", _matieres_txt(groupe), url))
            suspensions += 1
            logger.info("Adjuris : accès suspendu pour impayé (%s)", groupe[0].email)
        elif (
            maintenant >= depuis + timedelta(days=PREPA_RELANCE_IMPAYE_JOURS)
            and all(e.impaye_relance_le is None for e in groupe)
        ):
            for e in groupe:
                e.impaye_relance_le = maintenant
            db.commit()
            _envoyer(
                groupe[0].email,
                mail_prepa_adjuris_impaye(
                    "relance", _matieres_txt(groupe), url, date_retrait=f"le {date_fr(limite)}"
                ),
            )
            relances += 1
    return {"relances": relances, "suspensions": suspensions}


def terminer_abonnement(db: Session, sub_id: str) -> None:
    """customer.subscription.deleted : fin de programme ou résiliation.
    Rôles retirés immédiatement, grade retiré s'il ne reste aucune matière."""
    inscriptions = [
        e for e in _inscriptions_abonnement(db, sub_id)
        if e.status not in ("cancelled", "expired")
    ]
    if not inscriptions:
        return
    for e in inscriptions:
        e.status = "cancelled"
    db.commit()

    user = _user_de(db, inscriptions[0])
    _roles(user, [e.matiere_key for e in inscriptions], ajouter=False)
    if user and retirer_grade_si_plus_actif(db, user):
        db.commit()


# ============================================================
# Job horaire
# ============================================================

def job_facturation_adjuris(db_factory) -> None:
    """
    Toutes les heures :
      1. réessaie les échéanciers Stripe non créés ou non mis à jour ;
      2. traite les impayés (relance J+5, suspension J+7).
    Ne lève jamais.
    """
    db: Session = db_factory()
    try:
        a_reprendre = db.execute(
            select(PrepaAdjurisEnrollment.stripe_subscription_id, PrepaAdjurisEnrollment.echeancier_statut)
            .where(
                PrepaAdjurisEnrollment.echeancier_statut.in_(("erreur", "a_resynchroniser")),
                PrepaAdjurisEnrollment.status.in_(STATUTS_EN_COURS),
                PrepaAdjurisEnrollment.stripe_subscription_id.isnot(None),
            )
        ).all()
        for sub_id in sorted({r[0] for r in a_reprendre}):
            try:
                synchroniser_abonnement(db, sub_id, forcer_stripe=True)
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                logger.error("Reprise de l'échéancier %s impossible : %s", sub_id, exc)

        bilan = traiter_impayes(db)
        if bilan["relances"] or bilan["suspensions"]:
            logger.info("Adjuris impayés : %s", bilan)
    except Exception as exc:  # noqa: BLE001
        logger.exception("job_facturation_adjuris : %s", exc)
        db.rollback()
    finally:
        db.close()


# ============================================================
# Vues console
# ============================================================

def resume_facturation(e: PrepaAdjurisEnrollment, maintenant: datetime | None = None) -> dict:
    """Échéancier d'une inscription, prêt à afficher."""
    maintenant = maintenant or utcnow()
    ech = echeancier_de(e)
    base = {
        "enrollment_id": e.id,
        "email": e.email,
        "user_id": e.user_id,
        "matiere_key": e.matiere_key,
        "matiere_label": matiere_label(e.matiere_key),
        "niveau": matiere_niveau(e.matiere_key),
        "status": e.status,
        "source": e.source,
        "echeancier_statut": e.echeancier_statut,
        "stripe_subscription_id": e.stripe_subscription_id,
        "stripe_schedule_id": e.stripe_schedule_id,
        "inscrit_le": _aware(e.inscrit_le).isoformat() if e.inscrit_le else None,
        "impaye_depuis": _aware(e.impaye_depuis).isoformat() if e.impaye_depuis else None,
        "historique": ech is None,
    }
    if not ech:
        return {**base, "seance_prepayee": None, "mois": [], "prochain_prelevement": None}

    lignes = []
    for k, q in ech.mois.items():
        quand = date_prelevement(parse_mois(k))
        lignes.append({
            "mois": k,
            "date": quand.isoformat(),
            "seances": q,
            "montant_cents": q * PREPA_PRIX_SEANCE_CENTS,
            "credits": ech.credits.get(k, 0),
            "preleve": quand <= maintenant,
        })
    prochain = next((l for l in lignes if not l["preleve"]), None)
    return {
        **base,
        "seance_prepayee": ech.seance_prepayee.isoformat() if ech.seance_prepayee else None,
        "mois": lignes,
        "prochain_prelevement": prochain,
    }


def alertes(db: Session, maintenant: datetime | None = None) -> list[dict]:
    """Ce qui demande une action dans la console."""
    maintenant = maintenant or utcnow()
    sorties: list[dict] = []
    programme_en_cours = maintenant < date_prelevement(fin_programme())

    # Matières sans séance à venir alors que le programme continue.
    if programme_en_cours:
        horizon = maintenant + timedelta(days=14)
        for key in PREPA_PRICES:
            prochaine = db.execute(
                select(PrepaAdjurisSeance.date_debut).where(
                    PrepaAdjurisSeance.matiere_key == key,
                    PrepaAdjurisSeance.statut == "prevue",
                    PrepaAdjurisSeance.date_debut > maintenant,
                ).order_by(PrepaAdjurisSeance.date_debut).limit(1)
            ).scalar_one_or_none()
            if prochaine is None:
                sorties.append({
                    "niveau": "erreur", "type": "aucune_seance", "matiere_key": key,
                    "message": f"{matiere_label(key)} : aucune séance à venir dans l'agenda. "
                               "Les nouvelles inscriptions sont refusées et rien n'est facturé.",
                })
            elif _aware(prochaine) > horizon:
                sorties.append({
                    "niveau": "info", "type": "trou_agenda", "matiere_key": key,
                    "message": f"{matiere_label(key)} : prochaine séance le "
                               f"{date_fr(prochaine, avec_jour=True)}, rien dans les 14 jours.",
                })

    for key in PREPA_PRICES:
        if not prices_configures(key):
            sorties.append({
                "niveau": "erreur", "type": "prix_stripe", "matiere_key": key,
                "message": f"{matiere_label(key)} : Prices Stripe non configurés, "
                           "le paiement de cette matière est refusé.",
            })

    inscriptions = db.execute(
        select(PrepaAdjurisEnrollment).where(
            PrepaAdjurisEnrollment.status.in_(STATUTS_EN_COURS)
        )
    ).scalars().all()
    for e in inscriptions:
        if e.echeancier_statut in ("erreur", "a_resynchroniser"):
            sorties.append({
                "niveau": "erreur", "type": "echeancier", "enrollment_id": e.id,
                "matiere_key": e.matiere_key,
                "message": f"{e.email} — {matiere_label(e.matiere_key)} : échéancier Stripe "
                           f"{'non créé' if e.echeancier_statut == 'erreur' else 'à mettre à jour'} "
                           "(nouvelle tentative automatique toutes les heures).",
            })
        if e.status in ("payment_failed", "suspendu"):
            sorties.append({
                "niveau": "alerte", "type": "impaye", "enrollment_id": e.id,
                "matiere_key": e.matiere_key,
                "message": f"{e.email} — {matiere_label(e.matiere_key)} : "
                           + ("accès suspendu pour impayé." if e.status == "suspendu"
                              else f"impayé depuis le {date_fr(e.impaye_depuis)}."),
            })
        ech = echeancier_de(e)
        if ech and ech.mois:
            dernier = max(ech.mois)
            if ech.credits.get(dernier):
                sorties.append({
                    "niveau": "alerte", "type": "a_rembourser", "enrollment_id": e.id,
                    "matiere_key": e.matiere_key,
                    "message": f"{e.email} — {matiere_label(e.matiere_key)} : "
                               f"{ech.credits[dernier] * PREPA_PRIX_SEANCE_CENTS // 100} € crédités "
                               "après le dernier prélèvement, à rembourser depuis Stripe.",
                })
    return sorties
