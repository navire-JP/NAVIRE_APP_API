"""
app/services/parrainage.py
===========================
Parrainage étudiant : un élève Prép'AdJuris invite un ami et gagne un crédit
sur ses propres prélèvements, calculé et appliqué automatiquement par Stripe.

Même mécanique que le programme ambassadeur (app/services/ambassadors.py),
avec trois différences :
  - pas de candidature : tout élève Prép'AdJuris en cours peut s'inscrire,
    son lien est actif tout de suite ;
  - pas de virement : la récompense est un crédit sur le solde client Stripe
    du parrain (Customer balance), que Stripe déduit de ses prochaines
    factures — rien à verser à la main ;
  - le crédit est posé par le job horaire, une fois le délai de rétractation
    passé (un remboursement pendant ce délai annule la récompense).

Règles :
  - PARRAINAGE_CREDIT_PAR_MATIERE_CENTS par matière dont l'inscription est
    payée par le filleul. Récompense unique : rien sur les mensualités.
  - premier code utilisé = seul code retenu, tous programmes confondus (un
    filleul d'ambassadeur ne devient pas filleul d'un parrain, et inversement) ;
  - impossible de se parrainer soi-même ;
  - un élève déjà inscrit à Prép'AdJuris n'est pas un filleul.

Parcours d'un ami :
  1. Il ouvre /rejoindre?ref=CODE (même page que les ambassadeurs) ; la page
     affiche le nom choisi par le parrain (GET /ambassadors/ref/{code}, qui
     reconnaît aussi les codes de parrainage).
  2. Il crée son compte avec ce code : attach_any() le rattache au parrain.
  3. Il paie : le webhook appelle record_reward() → récompense « pending ».
  4. available_at passé : crediter_recompenses() (job horaire) crédite le
     solde Stripe du parrain → « credited ».

Comme pour les ambassadeurs, aucune fonction de ce module ne doit faire
échouer une inscription ou un paiement.
"""

from __future__ import annotations

import logging
import os
import secrets
from datetime import datetime, timedelta

import stripe
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.config import STRIPE_SECRET_KEY
from app.db.models import (
    Ambassador,
    AmbassadorReferral,
    Parrain,
    ParrainageFilleul,
    ParrainageRecompense,
    PrepaAdjurisEnrollment,
    User,
)
from app.services.ambassadors import (
    _ALPHABET_SUFFIXE,
    _deja_client,
    _slug,
    aware,
    normalize_code,
    utcnow,
)

logger = logging.getLogger(__name__)

# ── Réglages du programme ──────────────────────────────────────
# Crédit offert au parrain par matière payée par son filleul.
PARRAINAGE_CREDIT_PAR_MATIERE_CENTS = int(os.getenv("PARRAINAGE_CREDIT_PAR_MATIERE_CENTS", "200"))

# Délai avant le crédit Stripe : durée du droit de rétractation.
DELAI_CREDIT_JOURS = 14

TERMS_VERSION = "2026-11"

STATUTS_PARRAIN = ("active", "suspended")

# Inscriptions dont la facturation court encore : le parrain doit en avoir
# une pour s'inscrire, et c'est son client Stripe qui reçoit le crédit.
STATUTS_EN_COURS = ("active", "payment_failed", "suspendu")


def _cle_stripe() -> None:
    """Force la clé NAVIRE : MEOLES écrase stripe.api_key au démarrage."""
    stripe.api_key = STRIPE_SECRET_KEY


# ============================================================
# Codes
# ============================================================

def code_pris(db: Session, code: str) -> bool:
    """Un code est unique entre ambassadeurs et parrains : les deux
    programmes partagent /rejoindre?ref=CODE."""
    return bool(
        db.execute(select(Ambassador.id).where(Ambassador.code == code)).first()
        or db.execute(select(Parrain.id).where(Parrain.code == code)).first()
    )


def generate_code(db: Session, display_name: str) -> str:
    base = _slug(display_name) or "NAVIRE"
    for _ in range(20):
        suffixe = "".join(secrets.choice(_ALPHABET_SUFFIXE) for _ in range(3))
        code = f"{base}-{suffixe}"
        if not code_pris(db, code):
            return code
    return f"{base}-{secrets.token_hex(3).upper()}"


def find_active(db: Session, code: str | None) -> Parrain | None:
    """Parrain actif portant ce code, sinon None."""
    c = normalize_code(code)
    if not c:
        return None
    return db.execute(
        select(Parrain).where(Parrain.code == c, Parrain.status == "active")
    ).scalar_one_or_none()


# ============================================================
# Éligibilité du parrain
# ============================================================

def inscription_en_cours(db: Session, user: User) -> PrepaAdjurisEnrollment | None:
    """Inscription Prép'AdJuris en cours la plus récente, avec un client
    Stripe : c'est sur ce client que les crédits sont posés."""
    return db.execute(
        select(PrepaAdjurisEnrollment)
        .where(
            or_(PrepaAdjurisEnrollment.user_id == user.id,
                PrepaAdjurisEnrollment.email == (user.email or "").strip().lower()),
            PrepaAdjurisEnrollment.status.in_(STATUTS_EN_COURS),
            PrepaAdjurisEnrollment.stripe_customer_id.isnot(None),
        )
        .order_by(PrepaAdjurisEnrollment.id.desc())
    ).scalars().first()


def eligible(db: Session, user: User) -> bool:
    return inscription_en_cours(db, user) is not None


# ============================================================
# Rattachement des filleuls
# ============================================================

def referral_for(db: Session, user: User | None = None, email: str | None = None) -> ParrainageFilleul | None:
    conditions = []
    if user is not None:
        conditions.append(ParrainageFilleul.referred_user_id == user.id)
    mail = (email or (user.email if user is not None else "") or "").strip().lower()
    if mail:
        conditions.append(ParrainageFilleul.referred_email == mail)
    if not conditions:
        return None
    return db.execute(
        select(ParrainageFilleul).where(or_(*conditions)).order_by(ParrainageFilleul.id)
    ).scalars().first()


def _filleul_ambassadeur(db: Session, user: User | None, mail: str) -> bool:
    conditions = [AmbassadorReferral.referred_email == mail]
    if user is not None:
        conditions.append(AmbassadorReferral.referred_user_id == user.id)
    return db.execute(select(AmbassadorReferral.id).where(or_(*conditions))).first() is not None


def attach_referral(
    db: Session,
    code: str | None,
    *,
    user: User | None = None,
    email: str | None = None,
    source: str = "register",
    exclure_customer: str | None = None,
) -> ParrainageFilleul | None:
    """
    Rattache un ami (compte, ou email pour un paiement sans compte) au parrain
    du code. Renvoie la ligne du filleul, créée ou existante ; None si rien ne
    s'applique. Valide le commit lui-même.
    """
    mail = (email or (user.email if user is not None else "") or "").strip().lower()
    if not mail:
        return None

    existant = referral_for(db, user, mail)
    if existant is not None:
        if user is not None and existant.referred_user_id is None:
            existant.referred_user_id = user.id
            db.commit()
        return existant

    parrain = find_active(db, code)
    if parrain is None:
        return None

    proprietaire = db.get(User, parrain.user_id)
    if (user is not None and user.id == parrain.user_id) or (
        proprietaire is not None and (proprietaire.email or "").strip().lower() == mail
    ):
        return None  # auto-parrainage

    if _filleul_ambassadeur(db, user, mail):
        return None  # déjà rattaché à un ambassadeur : premier lien retenu
    if _deja_client(db, user, mail, exclure_customer):
        return None

    ref = ParrainageFilleul(
        parrain_id=parrain.id,
        referred_user_id=user.id if user is not None else None,
        referred_email=mail,
        source=source,
    )
    db.add(ref)
    db.commit()
    db.refresh(ref)
    return ref


def attach_any(
    db: Session,
    code: str | None,
    *,
    user: User | None = None,
    email: str | None = None,
    source: str = "register",
) -> AmbassadorReferral | ParrainageFilleul | None:
    """
    Point d'entrée commun aux deux programmes (inscription, OAuth, checkout,
    claim) : le code du lien /rejoindre est celui d'un ambassadeur ou d'un
    parrain. Le premier rattachement existant, tous programmes confondus,
    l'emporte.
    """
    from app.services import ambassadors

    ref = ambassadors.attach_referral(db, code, user=user, email=email, source=source)
    if ref is not None:
        return ref
    return attach_referral(db, code, user=user, email=email, source=source)


# ============================================================
# Récompenses
# ============================================================

def record_reward(
    db: Session,
    session: dict,
    user: User | None,
    email: str,
    matiere_keys: list[str],
) -> ParrainageRecompense | None:
    """
    Appelé par le webhook (checkout.session.completed Prép'AdJuris). Crée la
    récompense si le payeur est un filleul et que des frais d'inscription ont
    été encaissés. Idempotent : une seule récompense par Checkout Session.
    """
    session_id = session.get("id") or ""
    if not session_id or not matiere_keys:
        return None

    deja = db.execute(
        select(ParrainageRecompense).where(ParrainageRecompense.stripe_checkout_session_id == session_id)
    ).scalar_one_or_none()
    if deja is not None:
        return deja

    meta = session.get("metadata") or {}
    if (
        session.get("mode") != "payment"
        or session.get("payment_status") != "paid"
        or not (session.get("amount_total") or 0)
        or meta.get("inscription_deja_payee") == "1"
    ):
        return None

    ref = attach_referral(
        db, meta.get("parrain_code"), user=user, email=email, source="checkout",
        exclure_customer=session.get("customer"),
    )
    if ref is None:
        return None

    parrain = db.get(Parrain, ref.parrain_id)
    if parrain is None or parrain.status != "active":
        return None

    recompense = ParrainageRecompense(
        parrain_id=parrain.id,
        filleul_id=ref.id,
        amount_cents=PARRAINAGE_CREDIT_PAR_MATIERE_CENTS * len(matiere_keys),
        matiere_keys=list(matiere_keys),
        stripe_checkout_session_id=session_id,
        stripe_payment_intent_id=session.get("payment_intent"),
        status="pending",
        available_at=utcnow() + timedelta(days=DELAI_CREDIT_JOURS),
    )
    db.add(recompense)
    db.commit()
    db.refresh(recompense)
    logger.info(
        "Parrain %s : récompense %s c€ (session %s)", parrain.code, recompense.amount_cents, session_id
    )
    return recompense


def cancel_for_refund(db: Session, payment_intent_id: str | None, reason: str = "Paiement remboursé") -> int:
    """charge.refunded / litige : annule la récompense non encore créditée.
    Une récompense déjà créditée reste acquise (à régulariser à la main)."""
    if not payment_intent_id:
        return 0
    rows = db.execute(
        select(ParrainageRecompense).where(
            ParrainageRecompense.stripe_payment_intent_id == payment_intent_id,
            ParrainageRecompense.status == "pending",
        )
    ).scalars().all()
    for r in rows:
        r.status = "cancelled"
        r.cancelled_reason = reason[:200]
    if rows:
        db.commit()
    return len(rows)


def reward_state(r: ParrainageRecompense, now: datetime | None = None) -> str:
    """pending se lit « en attente » avant available_at, « à créditer »
    (available) après — le job horaire s'en charge."""
    if r.status != "pending":
        return r.status
    return "available" if aware(r.available_at) <= (now or utcnow()) else "pending"


def crediter(db: Session, r: ParrainageRecompense) -> bool:
    """
    Pose le crédit sur le solde client Stripe du parrain. Stripe le déduit
    automatiquement de ses prochaines factures Prép'AdJuris. La clé
    d'idempotence garantit un seul crédit par récompense, même si l'écriture
    en base échoue après l'appel. Renvoie True si la récompense est créditée.
    """
    if r.status != "pending":
        return r.status == "credited"
    parrain = db.get(Parrain, r.parrain_id)
    user = db.get(User, parrain.user_id) if parrain else None
    inscription = inscription_en_cours(db, user) if user else None
    if inscription is None:
        r.last_error = "Aucune inscription Prép'AdJuris en cours pour le parrain."
        db.commit()
        return False

    filleul = r.filleul.referred_user.username if r.filleul and r.filleul.referred_user else "un ami"
    n = len(r.matiere_keys or [])
    _cle_stripe()
    try:
        tx = stripe.Customer.create_balance_transaction(
            inscription.stripe_customer_id,
            amount=-r.amount_cents,       # négatif = crédit
            currency="eur",
            description=f"Parrainage NAVIRE : inscription de {filleul} ({n} matière{'s' if n > 1 else ''})",
            metadata={"parrainage_recompense_id": str(r.id), "parrain_code": parrain.code},
            idempotency_key=f"navire-parrainage-{r.id}",
        )
    except stripe.StripeError as exc:
        r.last_error = str(exc)[:300]
        db.commit()
        logger.error("Parrainage : crédit Stripe échoué pour la récompense %s : %s", r.id, exc)
        return False

    r.status = "credited"
    r.credited_at = utcnow()
    r.stripe_customer_id = inscription.stripe_customer_id
    r.stripe_balance_transaction_id = tx.get("id") if hasattr(tx, "get") else getattr(tx, "id", None)
    r.last_error = None
    db.commit()
    return True


def crediter_recompenses(db: Session, maintenant: datetime | None = None) -> list[ParrainageRecompense]:
    """Crédite toutes les récompenses dont le délai est passé. Renvoie celles
    qui viennent d'être créditées."""
    now = maintenant or utcnow()
    dues = db.execute(
        select(ParrainageRecompense)
        .where(ParrainageRecompense.status == "pending", ParrainageRecompense.available_at <= now)
        .order_by(ParrainageRecompense.id)
    ).scalars().all()
    faites = []
    for r in dues:
        try:
            if crediter(db, r):
                faites.append(r)
        except Exception:  # noqa: BLE001 — une récompense en échec ne bloque pas les autres
            db.rollback()
            logger.exception("Parrainage : récompense %s non créditée", r.id)
    return faites


def job_parrainage(db_factory) -> None:
    """Job horaire : crédite les récompenses dues et prévient les parrains.
    Ne lève jamais."""
    from app.services.email import mail_parrainage_credit, send_mail

    db: Session = db_factory()
    try:
        for r in crediter_recompenses(db):
            try:
                parrain = db.get(Parrain, r.parrain_id)
                u = db.get(User, parrain.user_id) if parrain else None
                if u:
                    montant = f"{r.amount_cents / 100:.2f} €".replace(".", ",")
                    send_mail(u.email, *mail_parrainage_credit(parrain.display_name, montant))
            except Exception:  # noqa: BLE001
                logger.exception("Parrainage : email de crédit non envoyé (%s)", r.id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("job_parrainage : %s", exc)
        db.rollback()
    finally:
        db.close()


# ============================================================
# Lecture : tableau de bord et console
# ============================================================

def earnings(db: Session, parrain_id: int) -> dict:
    now = utcnow()
    totaux = {"pending": 0, "available": 0, "credited": 0, "cancelled": 0}
    for r in db.execute(
        select(ParrainageRecompense).where(ParrainageRecompense.parrain_id == parrain_id)
    ).scalars():
        totaux[reward_state(r, now)] += r.amount_cents
    return {
        "pending_cents": totaux["pending"],
        "available_cents": totaux["available"],
        "credited_cents": totaux["credited"],
        "cancelled_cents": totaux["cancelled"],
        "total_earned_cents": totaux["pending"] + totaux["available"] + totaux["credited"],
    }


def stats(db: Session, parrain: Parrain) -> dict:
    inscrits = db.execute(
        select(func.count(ParrainageFilleul.id)).where(ParrainageFilleul.parrain_id == parrain.id)
    ).scalar_one()
    actives = db.execute(
        select(ParrainageRecompense.filleul_id, ParrainageRecompense.matiere_keys).where(
            ParrainageRecompense.parrain_id == parrain.id,
            ParrainageRecompense.status != "cancelled",
        )
    ).all()
    payants = len({fid for fid, _ in actives})
    matieres = sum(len(keys or []) for _, keys in actives)
    return {
        "clicks": parrain.clicks,
        "signups": inscrits,
        "paying": payants,
        "matieres": matieres,
        "conversion_rate": round(payants / inscrits, 3) if inscrits else 0.0,
    }


def referrals_list(db: Session, parrain_id: int, *, with_email: bool = False) -> list[dict]:
    """Filleuls d'un parrain. Côté parrain, seul le pseudo est montré."""
    now = utcnow()
    refs = db.execute(
        select(ParrainageFilleul)
        .where(ParrainageFilleul.parrain_id == parrain_id)
        .order_by(ParrainageFilleul.created_at.desc())
    ).scalars().all()
    recompenses: dict[int, list[ParrainageRecompense]] = {}
    for r in db.execute(
        select(ParrainageRecompense).where(ParrainageRecompense.parrain_id == parrain_id)
    ).scalars():
        recompenses.setdefault(r.filleul_id, []).append(r)

    out = []
    for f in refs:
        rs = recompenses.get(f.id, [])
        actives = [r for r in rs if r.status != "cancelled"]
        statut = "paid" if actives else ("refunded" if rs else "signed_up")
        item = {
            "id": f.id,
            "username": f.referred_user.username if f.referred_user else None,
            "joined_at": aware(f.created_at).isoformat(),
            "status": statut,
            "matieres": sorted({k for r in actives for k in (r.matiere_keys or [])}),
            "reward_cents": sum(r.amount_cents for r in actives),
            "reward_status": reward_state(actives[0], now) if actives else None,
        }
        if with_email:
            item["email"] = f.referred_email
            item["source"] = f.source
        out.append(item)
    return out
