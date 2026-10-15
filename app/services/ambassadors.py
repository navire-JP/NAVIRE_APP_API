"""
app/services/ambassadors.py
============================
Programme ambassadeur : rattachement des prospects et commissions.

Règles (cahier des charges) :
  - Le programme ne porte que sur Prép'AdJuris.
  - Commission UNIQUE de 2 € par matière dont l'inscription (20 €) est payée
    par le filleul. Rien sur les prélèvements mensuels qui suivent.
  - Le versement est fait à la main par virement : ce module ne fait que tenir
    le registre (dû, disponible, versé, annulé). Aucun appel Stripe.

Parcours d'un prospect :
  1. Il ouvre /rejoindre?ref=CODE ; la page affiche le nom choisi par
     l'ambassadeur (GET /ambassadors/ref/{code}).
  2. Il crée son compte NAVIRE (register ou Google/Facebook) avec ce code :
     attach_referral() le rattache à l'ambassadeur.
  3. Il paie son inscription : le webhook Stripe appelle record_commission(),
     qui crée la commission si le payeur est un filleul.

Garde-fous du rattachement :
  - premier code utilisé = seul code retenu (une ligne par compte et par email) ;
  - impossible de se parrainer soi-même ;
  - un élève déjà inscrit à Prép'AdJuris n'est pas un prospect : on ne le
    rattache pas, sinon un ambassadeur pourrait « capter » des élèves acquis.

Aucune fonction de ce module ne doit faire échouer une inscription ou un
paiement : les appelants les entourent d'un try/except, et elles renvoient
None plutôt que de lever quand le rattachement n'a pas lieu d'être.
"""

from __future__ import annotations

import logging
import re
import secrets
import unicodedata
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db.models import (
    Ambassador,
    AmbassadorCommission,
    AmbassadorReferral,
    PrepaAdjurisEnrollment,
    User,
)

logger = logging.getLogger(__name__)

# ── Réglages du programme ──────────────────────────────────────
COMMISSION_PAR_MATIERE_CENTS = 200

# Une commission n'est versable qu'après ce délai : c'est la durée du droit de
# rétractation, pendant laquelle un remboursement l'annule.
DELAI_DISPONIBLE_JOURS = 14

# À changer quand les conditions changent : chaque candidature garde la
# version qu'elle a acceptée.
TERMS_VERSION = "2026-10"

STATUTS_AMBASSADEUR = ("pending", "approved", "rejected", "suspended")

# Statuts d'une inscription Prép'AdJuris qui font de l'élève un client.
_STATUTS_CLIENT = ("active", "payment_failed", "suspendu", "cancelled", "expired")

_CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]{2,31}$")
# Sans 0/O ni 1/I/L : le code se dicte et se recopie sans erreur.
_ALPHABET_SUFFIXE = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def aware(dt: datetime | None) -> datetime | None:
    """SQLite rend des dates naïves : on les relit en UTC pour comparer."""
    if dt is None or dt.tzinfo is not None:
        return dt
    return dt.replace(tzinfo=timezone.utc)


# ============================================================
# Codes
# ============================================================

def normalize_code(code: str | None) -> str | None:
    """Code saisi ou lu dans l'adresse → forme canonique, ou None s'il est
    manifestement invalide (évite une requête pour n'importe quelle chaîne)."""
    c = (code or "").strip().upper()
    return c if _CODE_RE.match(c) else None


def _slug(texte: str) -> str:
    ascii_ = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Z0-9]", "", ascii_.upper())[:10]


def generate_code(db: Session, display_name: str) -> str:
    """JULIE-7K2 : lisible (le nom choisi) et impossible à deviner (suffixe)."""
    base = _slug(display_name) or "NAVIRE"
    for _ in range(20):
        suffixe = "".join(secrets.choice(_ALPHABET_SUFFIXE) for _ in range(3))
        code = f"{base}-{suffixe}"
        if not db.execute(select(Ambassador.id).where(Ambassador.code == code)).first():
            return code
    # 31^3 combinaisons par nom : n'arrive qu'avec des milliers de « JULIE ».
    return f"{base}-{secrets.token_hex(3).upper()}"


def find_active(db: Session, code: str | None) -> Ambassador | None:
    """Ambassadeur approuvé portant ce code, sinon None."""
    c = normalize_code(code)
    if not c:
        return None
    return db.execute(
        select(Ambassador).where(Ambassador.code == c, Ambassador.status == "approved")
    ).scalar_one_or_none()


# ============================================================
# Rattachement des prospects
# ============================================================

def referral_for(db: Session, user: User | None = None, email: str | None = None) -> AmbassadorReferral | None:
    conditions = []
    if user is not None:
        conditions.append(AmbassadorReferral.referred_user_id == user.id)
    mail = (email or (user.email if user is not None else "") or "").strip().lower()
    if mail:
        conditions.append(AmbassadorReferral.referred_email == mail)
    if not conditions:
        return None
    return db.execute(
        select(AmbassadorReferral).where(or_(*conditions)).order_by(AmbassadorReferral.id)
    ).scalars().first()


def _deja_client(db: Session, user: User | None, email: str, exclure_customer: str | None = None) -> bool:
    """Déjà inscrit à Prép'AdJuris ? exclure_customer : le client Stripe du
    paiement en cours, dont le webhook vient d'enregistrer les inscriptions
    (chaque Checkout crée son propre client) — elles ne comptent pas."""
    conditions = [PrepaAdjurisEnrollment.email == email]
    if user is not None:
        conditions.append(PrepaAdjurisEnrollment.user_id == user.id)
    q = select(PrepaAdjurisEnrollment.id).where(
        or_(*conditions), PrepaAdjurisEnrollment.status.in_(_STATUTS_CLIENT)
    )
    if exclure_customer:
        q = q.where(or_(
            PrepaAdjurisEnrollment.stripe_customer_id.is_(None),
            PrepaAdjurisEnrollment.stripe_customer_id != exclure_customer,
        ))
    return db.execute(q).first() is not None


def attach_referral(
    db: Session,
    code: str | None,
    *,
    user: User | None = None,
    email: str | None = None,
    source: str = "register",
    exclure_customer: str | None = None,
) -> AmbassadorReferral | None:
    """
    Rattache un prospect (compte, ou simple email pour le formulaire public) à
    l'ambassadeur du code. Renvoie la ligne de parrainage du prospect, qu'elle
    vienne d'être créée ou qu'elle existait déjà ; None si rien ne s'applique.
    Valide le commit lui-même.
    """
    mail = (email or (user.email if user is not None else "") or "").strip().lower()
    if not mail:
        return None

    existant = referral_for(db, user, mail)
    if existant is not None:
        # Payé sans compte puis compte créé : on complète la ligne.
        if user is not None and existant.referred_user_id is None:
            existant.referred_user_id = user.id
            db.commit()
        return existant

    amb = find_active(db, code)
    if amb is None:
        return None

    proprietaire = db.get(User, amb.user_id)
    if (user is not None and user.id == amb.user_id) or (
        proprietaire is not None and proprietaire.email.strip().lower() == mail
    ):
        return None  # auto-parrainage

    if _deja_client(db, user, mail, exclure_customer):
        return None

    ref = AmbassadorReferral(
        ambassador_id=amb.id,
        referred_user_id=user.id if user is not None else None,
        referred_email=mail,
        source=source,
    )
    db.add(ref)
    db.commit()
    db.refresh(ref)
    return ref


def link_user(db: Session, user: User) -> None:
    """À l'inscription sans code : rattache le compte à un parrainage déjà
    enregistré sur son email (paiement fait par le formulaire public)."""
    attach_referral(db, None, user=user)


# ============================================================
# Commissions
# ============================================================

def record_commission(
    db: Session,
    session: dict,
    user: User | None,
    email: str,
    matiere_keys: list[str],
) -> AmbassadorCommission | None:
    """
    Appelé par le webhook (checkout.session.completed Prép'AdJuris), une fois
    les inscriptions enregistrées. Crée la commission si le payeur est un
    filleul et que des frais d'inscription ont réellement été encaissés.
    Idempotent : une seule commission par Checkout Session.
    """
    session_id = session.get("id") or ""
    if not session_id or not matiere_keys:
        return None

    deja = db.execute(
        select(AmbassadorCommission).where(AmbassadorCommission.stripe_checkout_session_id == session_id)
    ).scalar_one_or_none()
    if deja is not None:
        return deja

    meta = session.get("metadata") or {}
    # Pas de frais d'inscription encaissés : lien admin antidaté, code promo
    # à 0 € (mode "setup"), paiement non abouti.
    if (
        session.get("mode") != "payment"
        or session.get("payment_status") != "paid"
        or not (session.get("amount_total") or 0)
        or meta.get("inscription_deja_payee") == "1"
    ):
        return None

    ref = attach_referral(
        db, meta.get("ambassador_code"), user=user, email=email, source="checkout",
        exclure_customer=session.get("customer"),
    )
    if ref is None:
        return None

    amb = db.get(Ambassador, ref.ambassador_id)
    if amb is None or amb.status != "approved":
        return None

    commission = AmbassadorCommission(
        ambassador_id=amb.id,
        referral_id=ref.id,
        amount_cents=COMMISSION_PAR_MATIERE_CENTS * len(matiere_keys),
        matiere_keys=list(matiere_keys),
        stripe_checkout_session_id=session_id,
        stripe_payment_intent_id=session.get("payment_intent"),
        status="pending",
        available_at=utcnow() + timedelta(days=DELAI_DISPONIBLE_JOURS),
    )
    db.add(commission)
    db.commit()
    db.refresh(commission)
    logger.info(
        "Ambassadeur %s : commission %s c€ (session %s)", amb.code, commission.amount_cents, session_id
    )
    return commission


def cancel_for_refund(db: Session, payment_intent_id: str | None, reason: str = "Paiement remboursé") -> int:
    """charge.refunded / litige : annule la commission non encore versée.
    Une commission déjà versée reste « paid » : à régulariser à la main."""
    if not payment_intent_id:
        return 0
    rows = db.execute(
        select(AmbassadorCommission).where(
            AmbassadorCommission.stripe_payment_intent_id == payment_intent_id,
            AmbassadorCommission.status == "pending",
        )
    ).scalars().all()
    for c in rows:
        c.status = "cancelled"
        c.cancelled_reason = reason[:200]
    if rows:
        db.commit()
    return len(rows)


def commission_state(c: AmbassadorCommission, now: datetime | None = None) -> str:
    """pending se lit « en attente » avant available_at, « disponible » après."""
    if c.status != "pending":
        return c.status
    return "available" if aware(c.available_at) <= (now or utcnow()) else "pending"


# ============================================================
# Lecture : tableau de bord et console
# ============================================================

def earnings(db: Session, ambassador_id: int) -> dict:
    now = utcnow()
    totaux = {"pending": 0, "available": 0, "paid": 0, "cancelled": 0}
    rows = db.execute(
        select(AmbassadorCommission).where(AmbassadorCommission.ambassador_id == ambassador_id)
    ).scalars().all()
    for c in rows:
        totaux[commission_state(c, now)] += c.amount_cents
    return {
        "pending_cents": totaux["pending"],
        "available_cents": totaux["available"],
        "paid_cents": totaux["paid"],
        "cancelled_cents": totaux["cancelled"],
        "total_earned_cents": totaux["pending"] + totaux["available"] + totaux["paid"],
    }


def stats(db: Session, amb: Ambassador) -> dict:
    inscrits = db.execute(
        select(func.count(AmbassadorReferral.id)).where(AmbassadorReferral.ambassador_id == amb.id)
    ).scalar_one()
    payants = db.execute(
        select(func.count(func.distinct(AmbassadorCommission.referral_id))).where(
            AmbassadorCommission.ambassador_id == amb.id,
            AmbassadorCommission.status != "cancelled",
        )
    ).scalar_one()
    matieres = 0
    for keys in db.execute(
        select(AmbassadorCommission.matiere_keys).where(
            AmbassadorCommission.ambassador_id == amb.id,
            AmbassadorCommission.status != "cancelled",
        )
    ).scalars():
        matieres += len(keys or [])
    return {
        "clicks": amb.clicks,
        "signups": inscrits,
        "paying": payants,
        "matieres": matieres,
        "conversion_rate": round(payants / inscrits, 3) if inscrits else 0.0,
    }


def referrals_list(db: Session, ambassador_id: int, *, with_email: bool = False) -> list[dict]:
    """
    Filleuls d'un ambassadeur. Côté ambassadeur (with_email=False), seul le
    pseudo est montré : l'email du filleul ne lui appartient pas.
    """
    now = utcnow()
    refs = db.execute(
        select(AmbassadorReferral)
        .where(AmbassadorReferral.ambassador_id == ambassador_id)
        .order_by(AmbassadorReferral.created_at.desc())
    ).scalars().all()
    commissions: dict[int, list[AmbassadorCommission]] = {}
    for c in db.execute(
        select(AmbassadorCommission).where(AmbassadorCommission.ambassador_id == ambassador_id)
    ).scalars():
        commissions.setdefault(c.referral_id, []).append(c)

    out = []
    for r in refs:
        cs = commissions.get(r.id, [])
        actives = [c for c in cs if c.status != "cancelled"]
        if actives:
            statut = "paid"
        elif cs:
            statut = "refunded"
        else:
            statut = "signed_up"
        item = {
            "id": r.id,
            "username": r.referred_user.username if r.referred_user else None,
            "joined_at": aware(r.created_at).isoformat(),
            "status": statut,
            "matieres": sorted({k for c in actives for k in (c.matiere_keys or [])}),
            "commission_cents": sum(c.amount_cents for c in actives),
            "commission_status": commission_state(actives[0], now) if actives else None,
        }
        if with_email:
            item["email"] = r.referred_email
            item["source"] = r.source
        out.append(item)
    return out


def mask_iban(iban: str | None) -> str | None:
    if not iban:
        return None
    return "•••• " + iban[-4:]
