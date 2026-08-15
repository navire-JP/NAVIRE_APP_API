"""
app/routers/subscriptions.py
=============================
Router abonnements — Phase 2 (Stripe).

Endpoints utilisateur :
  GET  /subscriptions/me               → plan actuel + limites
  POST /subscriptions/checkout         → crée une Stripe Checkout Session
  POST /subscriptions/cancel           → résilie via Stripe
  POST /subscriptions/reactivate       → annule une résiliation programmée
  POST /subscriptions/portal           → ouvre le Stripe Customer Portal
  GET  /subscriptions/promo/{code}     → valide un code promo

Cycle de vie d'un abonnement (exemple mensuel) :
  15 janvier   paiement    → plan=membre, status=active, période →15 février
  15 juillet   reconduction→ invoice.payment_succeeded, période →15 août
  15 août      reconduction→ période →15 septembre
  31 août      résiliation → status=cancelled, plan CONSERVÉ (accès payé)
  15 septembre fin période → customer.subscription.deleted → plan=free
Le grade n'est donc jamais retiré avant current_period_end. Si Stripe ne
livre pas l'event de fin, le filet de sécurité est double : check_expired_
subscriptions (job horaire) et _sync_expired_plan (à la lecture de /me).

Endpoints Stripe :
  POST /subscriptions/webhook          → reçoit les events Stripe

Endpoints admin :
  GET    /subscriptions/admin/prices
  GET    /subscriptions/admin/list
  POST   /subscriptions/admin/set-plan/{user_id}
  GET    /subscriptions/admin/promo
  POST   /subscriptions/admin/promo
  PATCH  /subscriptions/admin/promo/{promo_id}
  DELETE /subscriptions/admin/promo/{promo_id}
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Literal

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import select, desc, update, and_, or_

from app.db.database import get_db
from app.db.models import (
    User,
    Subscription,
    PromoCode,
    PromoRedemption,
    PendingSubscription,
    PrepaAdjurisEnrollment,
    DiscordLinkCode,
)
from app.routers.auth import get_current_user
from app.core.limits import get_limits
from app.core.config import (
    STRIPE_SECRET_KEY,
    STRIPE_WEBHOOK_SECRET,
    STRIPE_PRICES,
    STRIPE_SUCCESS_URL,
    STRIPE_CANCEL_URL,
    FRONTEND_URL,
)
from app.core.prepa_adjuris_config import (
    PREPA_PRICES,
    PREPA_MONTHLY_QUANTITIES,
    matiere_label,
    matiere_niveau,
)
from app.core.navire_plans import (
    PriceUnavailable,
    catalog_overview,
    plan_from_price_id,
    plan_label,
    resolve_price_id,
)
from app.services.email import (
    send_mail,
    mail_pending_subscription,
    mail_prepa_adjuris_link_code,
    mail_discord_link_code,
)
from app.services.discord_link import (
    PURCHASE_TTL_DAYS,
    SOURCE_PURCHASE,
    active_codes,
    issue_code,
)
from app.bot_discord.role_sync import assign_adjuris_role_sync, remove_adjuris_role_sync

logger = logging.getLogger(__name__)

# NE PAS setter stripe.api_key globalement ici — MEOLES l'écrase au démarrage.
# La clé est forcée dans chaque fonction qui appelle Stripe.

router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])


# ============================================================
# Helpers internes
# ============================================================

def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _stripe() -> None:
    """Force la clé NAVIRE avant tout appel Stripe."""
    stripe.api_key = STRIPE_SECRET_KEY


def require_admin(user: User = Depends(get_current_user)) -> User:
    if not getattr(user, "is_admin", False):
        raise HTTPException(status_code=403, detail="Admin only")
    return user


def get_or_create_subscription(db: Session, user: User) -> Subscription:
    """Retourne la subscription de l'user, ou en crée une free si absente."""
    sub = db.execute(
        select(Subscription).where(Subscription.user_id == user.id)
    ).scalar_one_or_none()

    if not sub:
        sub = Subscription(
            user_id=user.id,
            plan="free",
            billing_cycle=None,
            status="active",
        )
        db.add(sub)
        db.commit()
        db.refresh(sub)

    return sub


def _sync_user_plan(db: Session, user: User, plan: str) -> None:
    """Cache User.plan — toujours appeler après un changement de plan."""
    user.plan = plan
    db.commit()


def _get_or_create_stripe_customer(user: User, sub: Subscription, db: Session) -> str:
    """
    Retourne le stripe_customer_id existant ou en crée un nouveau.
    Stocke l'id en base pour les prochains appels.
    """
    if sub.stripe_customer_id:
        return sub.stripe_customer_id

    _stripe()
    customer = stripe.Customer.create(
        email=user.email,
        name=user.username,
        metadata={"user_id": str(user.id)},
    )
    sub.stripe_customer_id = customer.id
    db.commit()
    return customer.id


def _downgrade_to_free(db: Session, user: User, sub: Subscription, status: str = "cancelled") -> None:
    """Repasse l'user en free. Utilisé par le webhook et le endpoint cancel."""
    sub.plan = "free"
    sub.billing_cycle = None
    sub.status = status
    sub.cancelled_at = utcnow()
    sub.current_period_end = None
    sub.stripe_subscription_id = None
    db.commit()
    _sync_user_plan(db, user, "free")


def _stripe_period(stripe_sub) -> tuple[datetime | None, datetime | None]:
    """
    (début, fin) de la période en cours d'un abonnement Stripe.

    Stripe a déplacé current_period_start/end de l'abonnement vers ses items à
    partir de l'API 2025-04 : on lit l'emplacement historique, et on retombe
    sur le premier item si l'objet ne le porte plus. Sans ça, la fin de période
    resterait NULL et le grade ne s'éteindrait jamais tout seul.
    """
    def _ts(value) -> datetime | None:
        return datetime.fromtimestamp(value, tz=timezone.utc) if value else None

    start = _ts(stripe_sub.get("current_period_start"))
    end = _ts(stripe_sub.get("current_period_end"))
    if start and end:
        return start, end

    items = ((stripe_sub.get("items") or {}).get("data") or [])
    if items:
        first = items[0]
        start = start or _ts(first.get("current_period_start"))
        end = end or _ts(first.get("current_period_end"))
    return start, end


def _paid_access_until(sub: Subscription) -> datetime | None:
    """
    Date jusqu'à laquelle l'accès payé court encore, ou None s'il est terminé.
    Un abonnement résilié le 31 août dont la période va au 15 septembre rend
    bien le 15 septembre : l'élève garde son grade jusque-là.
    """
    if sub.plan == "free" or not sub.current_period_end:
        return None
    end = sub.current_period_end
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    return end if end > utcnow() else None


def _sync_expired_plan(db: Session, user: User, sub: Subscription) -> None:
    """
    Filet de sécurité à la lecture : un abonnement résilié dont la période est
    terminée repasse en free, même si l'event Stripe de fin de période s'est
    perdu. On ne touche PAS à un abonnement actif — sa période sera prolongée
    par invoice.payment_succeeded au moment du renouvellement, et un décalage
    de quelques minutes ne doit pas couper l'accès d'un élève qui paie.
    """
    if sub.plan == "free" or sub.status not in ("cancelled", "expired", "past_due"):
        return
    if not sub.current_period_end or _paid_access_until(sub):
        return
    if sub.status == "past_due":
        # Échec de paiement : Stripe retente pendant plusieurs jours et enverra
        # customer.subscription.deleted s'il abandonne. Rien à décider ici.
        return
    _downgrade_to_free(db, user, sub, status="expired")
    logger.info("Accès expiré : user %s repassé en free (fin de période).", user.id)


def _validate_promo(db: Session, user: User, code: str, billing_cycle: str) -> PromoCode:
    """Vérifie qu'un code promo est utilisable. Lève 400 sinon."""
    promo = db.execute(
        select(PromoCode).where(PromoCode.code == code.upper())
    ).scalar_one_or_none()

    if not promo:
        raise HTTPException(400, detail={"code": "PROMO_NOT_FOUND", "message": "Code promo introuvable."})
    if not promo.is_active:
        raise HTTPException(400, detail={"code": "PROMO_INACTIVE", "message": "Code promo inactif."})
    if promo.expires_at and promo.expires_at < utcnow():
        raise HTTPException(400, detail={"code": "PROMO_EXPIRED", "message": "Code promo expiré."})
    if promo.max_uses is not None and promo.uses_count >= promo.max_uses:
        raise HTTPException(400, detail={"code": "PROMO_EXHAUSTED", "message": "Code promo épuisé."})
    if promo.applies_to and promo.applies_to != "both" and promo.applies_to != billing_cycle:
        raise HTTPException(400, detail={
            "code": "PROMO_WRONG_CYCLE",
            "message": f"Ce code est valable uniquement pour l'abonnement '{promo.applies_to}'.",
        })

    already_used = db.execute(
        select(PromoRedemption).where(
            PromoRedemption.user_id == user.id,
            PromoRedemption.promo_code_id == promo.id,
        )
    ).scalar_one_or_none()

    if already_used:
        raise HTTPException(400, detail={"code": "PROMO_ALREADY_USED", "message": "Vous avez déjà utilisé ce code."})

    return promo


def _apply_promo(db: Session, user: User, promo: PromoCode) -> None:
    """Enregistre l'utilisation du code et incrémente uses_count."""
    db.add(PromoRedemption(user_id=user.id, promo_code_id=promo.id))
    promo.uses_count += 1
    db.commit()


# ============================================================
# Schemas
# ============================================================

class CheckoutIn(BaseModel):
    plan: Literal["membre", "membre+", "beta"]
    billing_cycle: Literal["monthly", "annual"]
    promo_code: str | None = None


class CheckoutPrepaIn(BaseModel):
    annee: Literal["L1", "L2", "L3"]


class SetPlanIn(BaseModel):
    plan: Literal["free", "membre", "membre+", "prepa"]
    billing_cycle: Literal["monthly", "annual"] | None = None
    note: str | None = None


class PromoCreateIn(BaseModel):
    code: str = Field(..., min_length=3, max_length=50)
    discount_type: Literal["percent", "fixed"]
    discount_value: float = Field(..., gt=0)
    applies_to: Literal["monthly", "annual", "both"] | None = None
    max_uses: int | None = Field(None, gt=0)
    expires_at: datetime | None = None


class PromoUpdateIn(BaseModel):
    is_active: bool | None = None
    max_uses: int | None = None
    expires_at: datetime | None = None
    discount_value: float | None = Field(None, gt=0)


# ============================================================
# Routes utilisateur
# ============================================================

@router.get("/me")
def get_my_subscription(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Plan actuel + statut + limites complètes.

    Lit aussi la fin de l'accès payé : après une résiliation, l'élève garde son
    grade jusqu'à `access_until`. Si cette date est passée sans que Stripe ait
    livré son event, le plan est remis à free ici même.
    """
    sub = get_or_create_subscription(db, user)
    _sync_expired_plan(db, user, sub)
    limits = get_limits(user.plan)
    access_until = _paid_access_until(sub)

    return {
        "plan": user.plan,
        "plan_label": plan_label(user.plan),
        "status": sub.status,
        "billing_cycle": sub.billing_cycle,
        "current_period_end": sub.current_period_end,
        "cancelled_at": sub.cancelled_at,
        # Résilié mais encore payé : le grade tient jusqu'à cette date.
        "access_until": access_until,
        "renews": sub.status == "active" and user.plan != "free",
        "limits": {
            "qcm_per_day": limits["qcm_per_day"],
            "flashcards_total": limits["flashcards_total"],
            "files_total": limits["files_total"],
            "file_ttl_hours": limits["file_ttl_hours"],
        },
    }


@router.post("/checkout")
def create_checkout_session(
    payload: CheckoutIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Crée une Stripe Checkout Session et retourne la checkout_url.
    Le front (embed d'offres) redirige l'user vers cette URL pour le paiement.
    Après paiement, Stripe envoie checkout.session.completed au webhook, qui
    pose le grade `membre` ou `membre+`.

    Le Price facturé vient de app/core/navire_plans.py : variable
    d'environnement si elle existe, sinon Price retrouvé (ou créé) dans le
    compte Stripe au tarif public. L'embed peut donc générer un lien de
    paiement sans qu'aucun price_id n'ait été posé à la main.
    """
    _stripe()  # force la clé NAVIRE

    sub = get_or_create_subscription(db, user)
    _sync_expired_plan(db, user, sub)

    # Bloquer si un accès payé court encore — y compris après une résiliation
    # programmée (status="cancelled" mais période non terminée). Sans ce
    # second cas, un élève ayant résilié le 31 août pouvait repayer aussitôt
    # et se retrouver avec deux abonnements Stripe en parallèle.
    if user.plan != "free":
        access_until = _paid_access_until(sub)
        if sub.status == "active":
            raise HTTPException(400, detail={
                "code": "ALREADY_SUBSCRIBED",
                "message": f"Abonnement actif ({plan_label(user.plan)}). Résiliez d'abord.",
            })
        if access_until:
            raise HTTPException(400, detail={
                "code": "CANCELLED_STILL_ACTIVE",
                "message": (
                    f"Votre abonnement {plan_label(user.plan)} reste actif jusqu'au "
                    f"{access_until.strftime('%d/%m/%Y')}. Réactivez-le depuis votre "
                    "profil plutôt que d'en souscrire un second."
                ),
                "access_until": access_until.isoformat(),
            })
        if sub.status == "past_due" and sub.stripe_subscription_id:
            # Prélèvement en échec : Stripe retente sur le même abonnement.
            # En ouvrir un second ferait payer deux fois le même élève dès que
            # sa carte repasse.
            raise HTTPException(400, detail={
                "code": "PAYMENT_PAST_DUE",
                "message": "Un prélèvement de votre abonnement a échoué. "
                           "Mettez votre moyen de paiement à jour depuis votre profil.",
            })

    # Price Stripe du couple (plan, cycle)
    try:
        price_id = resolve_price_id(payload.plan, payload.billing_cycle)
    except PriceUnavailable as exc:
        logger.error("Price Stripe indisponible (%s/%s) : %s",
                     payload.plan, payload.billing_cycle, exc)
        raise HTTPException(503, detail={
            "code": "PRICE_UNAVAILABLE",
            "message": "Le service de paiement est momentanément indisponible. "
                       "Réessaie dans quelques minutes.",
        })

    # Créer ou récupérer le customer Stripe
    customer_id = _get_or_create_stripe_customer(user, sub, db)

    # Validation code promo
    promo = None
    stripe_coupon_id = None
    if payload.promo_code:
        promo = _validate_promo(db, user, payload.promo_code, payload.billing_cycle)
        if promo.stripe_coupon_id:
            stripe_coupon_id = promo.stripe_coupon_id

    checkout_params: dict = {
        "customer": customer_id,
        "mode": "subscription",
        "client_reference_id": str(user.id),
        "line_items": [{"price": price_id, "quantity": 1}],
        "success_url": STRIPE_SUCCESS_URL,
        "cancel_url": STRIPE_CANCEL_URL,
        "metadata": {
            "user_id": str(user.id),
            "plan": payload.plan,
            "billing_cycle": payload.billing_cycle,
            "promo_code": payload.promo_code or "",
        },
        "subscription_data": {
            "metadata": {
                "user_id": str(user.id),
                "plan": payload.plan,
                "billing_cycle": payload.billing_cycle,
            }
        },
    }

    # Stripe refuse discounts et allow_promotion_codes ensemble : un code NAVIRE
    # adossé à un coupon Stripe fait sauter le champ « code promo » de la page
    # de paiement, sinon la session n'est pas créée du tout (502).
    if stripe_coupon_id:
        checkout_params["discounts"] = [{"coupon": stripe_coupon_id}]
    else:
        checkout_params["allow_promotion_codes"] = True

    try:
        session = stripe.checkout.Session.create(**checkout_params)
    except stripe.StripeError as e:
        raise HTTPException(502, detail=f"Erreur Stripe : {str(e)}")

    return {
        "checkout_url": session.url,
        "session_id": session.id,
    }


@router.post("/cancel")
def cancel_subscription(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Résilie en fin de période (cancel_at_period_end=True).
    L'user garde son accès jusqu'à current_period_end.
    Le downgrade effectif arrive via webhook customer.subscription.deleted.
    """
    _stripe()  # force la clé NAVIRE

    sub = get_or_create_subscription(db, user)

    if user.plan == "free":
        raise HTTPException(400, detail={
            "code": "ALREADY_FREE",
            "message": "Vous êtes déjà sur le plan gratuit.",
        })

    if not sub.stripe_subscription_id:
        # Plan forcé par admin → downgrade immédiat sans Stripe
        _downgrade_to_free(db, user, sub)
        return {"ok": True, "plan": "free", "message": "Abonnement résilié."}

    try:
        stripe.Subscription.modify(
            sub.stripe_subscription_id,
            cancel_at_period_end=True,
        )
    except stripe.StripeError as e:
        raise HTTPException(502, detail=f"Erreur Stripe : {str(e)}")

    sub.status = "cancelled"
    sub.cancelled_at = utcnow()
    db.commit()

    access_until = _paid_access_until(sub)
    if access_until:
        message = (
            "Résiliation programmée. Votre "
            f"{plan_label(sub.plan)} reste actif jusqu'au "
            f"{access_until.strftime('%d/%m/%Y')}."
        )
    else:
        message = "Résiliation programmée. Votre accès reste actif jusqu'à la fin de la période."

    return {
        "ok": True,
        "message": message,
        "current_period_end": sub.current_period_end,
        "access_until": access_until,
    }


@router.post("/reactivate")
def reactivate_subscription(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Annule une résiliation programmée, tant que la période payée court encore.

    Sans cet endpoint, un élève qui résilie le 31 août et change d'avis le
    5 septembre n'avait aucun moyen de revenir en arrière : /checkout refuse
    (accès encore actif) et il aurait fallu attendre le 15 septembre pour
    repayer, en perdant l'accès entre-temps.
    """
    _stripe()  # force la clé NAVIRE

    sub = get_or_create_subscription(db, user)
    _sync_expired_plan(db, user, sub)

    if user.plan == "free" or not _paid_access_until(sub):
        raise HTTPException(400, detail={
            "code": "NOTHING_TO_REACTIVATE",
            "message": "Aucun abonnement à réactiver — souscrivez une offre.",
        })

    if sub.status == "active":
        return {"ok": True, "message": "Votre abonnement est déjà actif.",
                "current_period_end": sub.current_period_end}

    if not sub.stripe_subscription_id:
        # Plan posé à la main par un admin : rien à rouvrir côté Stripe.
        sub.status = "active"
        sub.cancelled_at = None
        db.commit()
        return {"ok": True, "message": "Abonnement réactivé.",
                "current_period_end": sub.current_period_end}

    try:
        stripe.Subscription.modify(
            sub.stripe_subscription_id,
            cancel_at_period_end=False,
        )
    except stripe.StripeError as e:
        raise HTTPException(502, detail=f"Erreur Stripe : {str(e)}")

    sub.status = "active"
    sub.cancelled_at = None
    db.commit()

    return {
        "ok": True,
        "message": f"Abonnement {plan_label(sub.plan)} réactivé — il se renouvellera normalement.",
        "current_period_end": sub.current_period_end,
    }


@router.post("/portal")
def create_portal_session(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Stripe Customer Portal : l'user gère lui-même sa CB, ses factures, son abonnement.
    """
    _stripe()  # force la clé NAVIRE

    sub = get_or_create_subscription(db, user)

    if not sub.stripe_customer_id:
        raise HTTPException(400, detail={
            "code": "NO_STRIPE_CUSTOMER",
            "message": "Aucun compte de facturation trouvé.",
        })

    try:
        session = stripe.billing_portal.Session.create(
            customer=sub.stripe_customer_id,
            return_url=STRIPE_SUCCESS_URL,
        )
    except stripe.StripeError as e:
        raise HTTPException(502, detail=f"Erreur Stripe : {str(e)}")

    return {"portal_url": session.url}


@router.get("/promo/{code}")
def validate_promo_code(
    code: str,
    billing_cycle: Literal["monthly", "annual"] = "monthly",
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Valide un code promo sans l'appliquer (affichage temps réel Framer)."""
    promo = _validate_promo(db, user, code, billing_cycle)
    return {
        "valid": True,
        "code": promo.code,
        "discount_type": promo.discount_type,
        "discount_value": promo.discount_value,
        "applies_to": promo.applies_to,
        "expires_at": promo.expires_at,
    }


@router.post("/checkout-prepa")
def create_checkout_prepa(
    payload: CheckoutPrepaIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Crée une Stripe Checkout Session en mode "payment" (paiement unique) pour
    le programme PREPASSERELLE.

    Le price_id est fixe (STRIPE_PRICES["prepa"]["onetime"]).
    L'année choisie (L1/L2/L3) est stockée en metadata pour l'activer au webhook.
    L'accès expire le 31 août 2026 (hardcodé pour le lancement juillet 2026).
    """
    _stripe()

    # Bloquer si déjà accès prepa actif
    from app.core.limits import has_active_prepa_access
    if has_active_prepa_access(user):
        raise HTTPException(400, detail={
            "code": "PREPA_ALREADY_ACTIVE",
            "message": "Vous avez déjà un accès PREPASSERELLE actif.",
        })

    price_id = STRIPE_PRICES.get("prepa", {}).get("onetime")
    if not price_id:
        raise HTTPException(500, detail="Configuration Stripe PREPASSERELLE manquante.")

    sub = get_or_create_subscription(db, user)
    customer_id = _get_or_create_stripe_customer(user, sub, db)

    try:
        session = stripe.checkout.Session.create(
            customer=customer_id,
            mode="payment",
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=STRIPE_SUCCESS_URL,
            cancel_url=STRIPE_CANCEL_URL,
            metadata={
                "user_id": str(user.id),
                "plan": "prepa",
                "prepa_annee": payload.annee,
            },
        )
    except stripe.StripeError as e:
        raise HTTPException(502, detail=f"Erreur Stripe : {str(e)}")

    return {
        "checkout_url": session.url,
        "session_id": session.id,
    }


# ============================================================
# Prép'AdJuris — helpers webhook
# ============================================================

def _purchase_link_code(db: Session, user: User) -> DiscordLinkCode | None:
    """
    Code de liaison longue durée à envoyer après un achat. Réutilise celui déjà
    en circulation s'il en reste un valable, pour ne pas invalider le code d'un
    email précédent quand plusieurs achats se suivent.
    """
    for row in active_codes(db, user.id):
        if row.source == SOURCE_PURCHASE:
            return row
    try:
        return issue_code(db, user, SOURCE_PURCHASE, enforce_rate_limit=False)
    except RuntimeError:
        logger.error("Code de liaison Discord non généré pour user %s", user.id)
        return None


def send_discord_link_invite(db: Session, user: User, context_label: str = "") -> None:
    """
    Envoie par email le code permettant de lier le compte Discord après un
    achat. Sans effet si le compte est déjà lié : la synchronisation des rôles
    se fait alors toute seule (cogs/sync_account.py).

    L'élève peut toujours obtenir un code plus court depuis son profil : ce mail
    n'est qu'un raccourci pour ne pas l'obliger à repasser par le site.
    """
    if user.discord_id:
        return

    code_row = _purchase_link_code(db, user)
    if not code_row:
        return

    subject, html = mail_discord_link_code(
        user_id=user.id,
        email=user.email,
        code=code_row.code,
        validity_label=f"{PURCHASE_TTL_DAYS} jours",
        context_label=context_label,
    )
    send_mail(user.email, subject, html)


def send_adjuris_discord_invite(db: Session, user: User, matieres: list[str]) -> None:
    """
    Ouvre l'accès Discord après paiement : rôles directs si le compte est déjà
    lié, sinon un code de liaison à usage unique envoyé par email.
    Appelé par le webhook, et par auth.py au rattachement d'un paiement fait
    avant la création du compte.
    """
    if not matieres:
        return

    if user.discord_id:
        for key in matieres:
            assign_adjuris_role_sync(user.discord_id, key)
        return

    code_row = _purchase_link_code(db, user)
    if not code_row:
        return

    libelles = ", ".join(matiere_label(k) for k in matieres)
    subject, html = mail_prepa_adjuris_link_code(
        user.email, code_row.code, libelles, user_id=user.id
    )
    send_mail(user.email, subject, html)


def _handle_prepa_adjuris_checkout(db: Session, session: dict, matiere_keys: list[str]) -> None:
    """
    Traite un checkout.session.completed Prép'AdJuris : enregistre une ligne par
    matière, convertit l'abonnement en Subscription Schedule pour les mois
    suivants (quantités oct/nov/déc, arrêt automatique après décembre), puis
    ouvre l'accès Discord.

    Un Checkout Session ne crée qu'UN abonnement : si plusieurs matières ont été
    payées, toutes les lignes partagent le même stripe_subscription_id, et
    l'abonnement porte un item recurring par matière. Comme le formulaire impose
    un niveau unique, toutes partagent aussi le même calendrier de quantités.

    Le paiement peut venir d'un email sans compte NAVIRE : dans ce cas les
    lignes sont créées sans user_id et rattachées à l'inscription (auth.py).
    """
    matiere_keys = [k for k in matiere_keys if k in PREPA_PRICES]
    if not matiere_keys:
        return

    sub_id = session.get("subscription")
    if not sub_id:
        return

    # Idempotence : Stripe relivre le même event en cas de timeout.
    already = db.execute(
        select(PrepaAdjurisEnrollment).where(
            PrepaAdjurisEnrollment.stripe_subscription_id == sub_id
        )
    ).first()
    if already:
        return

    meta = session.get("metadata") or {}
    email = (
        meta.get("email")
        or (session.get("customer_details") or {}).get("email")
        or session.get("customer_email")
        or ""
    ).strip().lower()

    user = None
    user_id_raw = session.get("client_reference_id")
    if user_id_raw:
        user = db.execute(select(User).where(User.id == int(user_id_raw))).scalar_one_or_none()
    if not user and email:
        user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()

    if not user and not email:
        logger.error("Adjuris : paiement %s sans email ni compte — impossible à rattacher.", sub_id)
        return

    for key in matiere_keys:
        db.add(PrepaAdjurisEnrollment(
            user_id=user.id if user else None,
            email=email or (user.email if user else None),
            matiere_key=key,
            stripe_subscription_id=sub_id,
            stripe_customer_id=session.get("customer"),
            status="active",
            source="stripe",
        ))
    db.commit()

    # ── Conversion en Subscription Schedule (oct/nov/déc) ──────
    # Engagement 4 mois (sept → déc) : end_behavior="cancel" pour que
    # l'abonnement s'arrête automatiquement après la phase de décembre —
    # pas de reconduction implicite, l'étudiant se réinscrit ensuite.
    prochains_mois = PREPA_MONTHLY_QUANTITIES[matiere_keys[0]][1:]  # oct, nov, déc

    try:
        schedule = stripe.SubscriptionSchedule.create(from_subscription=sub_id)
        future_phases = [
            {
                "items": [
                    {"price": PREPA_PRICES[k]["recurring"], "quantity": q}
                    for k in matiere_keys
                ],
                "iterations": 1,
            }
            for q in prochains_mois
        ]
        stripe.SubscriptionSchedule.modify(
            schedule["id"],
            phases=[schedule["phases"][0]] + future_phases,
            end_behavior="cancel",
        )
        db.execute(
            update(PrepaAdjurisEnrollment)
            .where(PrepaAdjurisEnrollment.stripe_subscription_id == sub_id)
            .values(stripe_schedule_id=schedule["id"])
        )
        db.commit()
    except stripe.StripeError as e:
        logger.error("Adjuris : échec création Subscription Schedule pour %s : %s", sub_id, e)

    # ── Grade NAVIRE ─────────────────────────────────────────
    # Sans ça le badge PREPA n'apparaissait jamais sur le profil après un
    # vrai paiement — seul un grant admin manuel le posait. L'engagement
    # Stripe s'arrête après la phase de décembre (end_behavior="cancel" plus
    # haut) : même échéance ici, pas de renouvellement implicite.
    if user:
        today = datetime.now(timezone.utc)
        expiry = datetime(today.year, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
        user.plan = "prepa"
        user.prepa_annee = matiere_niveau(matiere_keys[0])
        user.prepa_expires_at = expiry
        db.commit()

    # ── Accès Discord ──────────────────────────────────────────
    if user:
        send_adjuris_discord_invite(db, user, matiere_keys)
    else:
        # Pas encore de compte NAVIRE : on invite à le créer, l'accès Discord
        # sera ouvert au moment de l'inscription (auth.py).
        subject, html = mail_pending_subscription(email, "prepa-adjuris", FRONTEND_URL)
        send_mail(email, subject, html)


def _handle_adjuris_subscription_event(db: Session, stripe_sub_id: str, status: str) -> None:
    """
    Symétrique de l'attribution : passe les inscriptions concernées dans l'état
    donné et retire les rôles Discord. Boucle sur toutes les lignes, car un
    abonnement peut porter plusieurs matières.
    """
    enrollments = db.execute(
        select(PrepaAdjurisEnrollment).where(
            PrepaAdjurisEnrollment.stripe_subscription_id == stripe_sub_id
        )
    ).scalars().all()
    if not enrollments:
        return

    for enrollment in enrollments:
        enrollment.status = status
    db.commit()

    for enrollment in enrollments:
        if not enrollment.user_id:
            continue
        user = db.execute(
            select(User).where(User.id == enrollment.user_id)
        ).scalar_one_or_none()
        if user and user.discord_id:
            remove_adjuris_role_sync(user.discord_id, enrollment.matiere_key)


# ============================================================
# Webhook Stripe
# ============================================================

@router.post("/webhook")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    """
    Reçoit et traite les events Stripe.

    Events gérés :
      checkout.session.completed      → activer le plan après paiement
      invoice.payment_succeeded       → renouvellement → prolonger la période
      invoice.payment_failed          → passer en past_due
      customer.subscription.deleted   → résiliation effective → downgrade free
      customer.subscription.updated   → mise à jour dates/statut
    """
    _stripe()  # force la clé NAVIRE

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except stripe.errors.SignatureVerificationError:
        raise HTTPException(400, detail="Signature Stripe invalide.")
    except Exception:
        raise HTTPException(400, detail="Payload webhook invalide.")

    event_type = event["type"]
    data = event["data"]["object"]

    # ── checkout.session.completed ─────────────────────────
    if event_type == "checkout.session.completed":
        meta = data.get("metadata") or {}

        # Branche Prép'AdJuris — metadata dédiée, pas de "plan". Traitée à part
        # et en premier pour ne pas retomber dans la logique membre/membre+/prepa
        # ci-dessous (qui exige metadata["plan"]).
        # matiere_keys = formulaire public (N matières) ; matiere_key = ancien
        # checkout authentifié à une seule matière, conservé par compatibilité.
        matiere_keys_raw = meta.get("matiere_keys") or meta.get("matiere_key") or ""
        if matiere_keys_raw:
            keys = [k.strip() for k in matiere_keys_raw.split(",") if k.strip()]
            _handle_prepa_adjuris_checkout(db, data, keys)
            return {"ok": True}

        user_id = int(meta.get("user_id", 0))
        plan = meta.get("plan", "")
        billing_cycle = meta.get("billing_cycle", "")
        promo_code_str = meta.get("promo_code", "")
        stripe_sub_id = data.get("subscription")
        customer_email = data.get("customer_details", {}).get("email") or data.get("customer_email", "")

        if not plan:
            return {"ok": True}

        # ── Branche PREPASSERELLE (paiement unique) ─────────
        if plan == "prepa":
            prepa_annee = meta.get("prepa_annee", "")
            if not prepa_annee or not user_id:
                return {"ok": True}

            user = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
            if not user:
                return {"ok": True}

            from datetime import date
            # Accès jusqu'au 31 août de l'année en cours (lancement été 2026).
            today = date.today()
            expiry = datetime(today.year, 8, 31, 23, 59, 59, tzinfo=timezone.utc)

            user.plan = "prepa"
            user.prepa_annee = prepa_annee
            user.prepa_expires_at = expiry
            db.commit()
            print(f"✅ PREPASSERELLE activé : user {user_id}, année {prepa_annee}, expire {expiry.date()}")
            send_discord_link_invite(db, user, context_label="PREPASSERELLE")
            return {"ok": True}

        # Récupérer les dates de période depuis Stripe. La fin de période est
        # ce qui garantit le grade jusqu'au bout du mois payé : sans elle,
        # ni /me ni le job horaire ne sauraient quand le retirer.
        period_start = period_end = None
        if stripe_sub_id:
            try:
                stripe_sub = stripe.Subscription.retrieve(stripe_sub_id)
                period_start, period_end = _stripe_period(stripe_sub)
            except Exception as exc:
                logger.warning("Période Stripe illisible pour %s : %s", stripe_sub_id, exc)

        user = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none() if user_id else None

        # ── Cas : email inconnu (pas de compte NAVIRE) ──────
        if not user and customer_email:
            pending = db.execute(
                select(PendingSubscription).where(PendingSubscription.email == customer_email.lower())
            ).scalar_one_or_none()

            if pending:
                pending.plan = plan
                pending.billing_cycle = billing_cycle
                pending.stripe_subscription_id = stripe_sub_id
                pending.stripe_customer_id = data.get("customer") or pending.stripe_customer_id
                pending.current_period_start = period_start
                pending.current_period_end = period_end
            else:
                pending = PendingSubscription(
                    email=customer_email.lower(),
                    plan=plan,
                    billing_cycle=billing_cycle,
                    stripe_subscription_id=stripe_sub_id,
                    stripe_customer_id=data.get("customer"),
                    current_period_start=period_start,
                    current_period_end=period_end,
                )
                db.add(pending)

            db.flush()

            subject, html = mail_pending_subscription(customer_email, plan, FRONTEND_URL)
            mail_ok = send_mail(customer_email, subject, html)
            pending.mail_sent = mail_ok
            db.commit()

            return {"ok": True}

        if not user:
            return {"ok": True}

        sub = get_or_create_subscription(db, user)

        sub.plan = plan
        sub.billing_cycle = billing_cycle
        sub.status = "active"
        sub.stripe_subscription_id = stripe_sub_id
        sub.stripe_customer_id = data.get("customer") or sub.stripe_customer_id
        sub.current_period_start = period_start
        sub.current_period_end = period_end
        sub.cancelled_at = None
        db.commit()
        _sync_user_plan(db, user, plan)

        # Code de liaison Discord envoyé avec la confirmation d'achat, pour que
        # l'élève n'ait pas à repasser par son profil. Sans effet si déjà lié.
        send_discord_link_invite(
            db, user, context_label="NAVIRE AI+" if plan == "membre+" else "NAVIRE AI"
        )

        if promo_code_str:
            try:
                promo = db.execute(
                    select(PromoCode).where(PromoCode.code == promo_code_str.upper())
                ).scalar_one_or_none()
                if promo:
                    already = db.execute(
                        select(PromoRedemption).where(
                            PromoRedemption.user_id == user_id,
                            PromoRedemption.promo_code_id == promo.id,
                        )
                    ).scalar_one_or_none()
                    if not already:
                        _apply_promo(db, user, promo)
            except Exception:
                pass

    # ── invoice.payment_succeeded ──────────────────────────
    elif event_type == "invoice.payment_succeeded":
        stripe_sub_id = data.get("subscription")
        if stripe_sub_id:
            sub = db.execute(
                select(Subscription).where(Subscription.stripe_subscription_id == stripe_sub_id)
            ).scalar_one_or_none()

            if sub:
                try:
                    stripe_sub = stripe.Subscription.retrieve(stripe_sub_id)
                    period_start, period_end = _stripe_period(stripe_sub)
                    if period_start:
                        sub.current_period_start = period_start
                    if period_end:
                        sub.current_period_end = period_end
                    # Une reconduction ne réactive pas un abonnement résilié :
                    # cancel_at_period_end reste vrai jusqu'à la dernière
                    # facture, et repasser en "active" ici rallumerait un
                    # renouvellement que l'élève a justement arrêté.
                    if not stripe_sub.get("cancel_at_period_end"):
                        sub.status = "active"
                    db.commit()
                    user = db.execute(select(User).where(User.id == sub.user_id)).scalar_one_or_none()
                    if user:
                        _sync_user_plan(db, user, sub.plan)
                except Exception as exc:
                    logger.warning("Renouvellement non enregistré pour %s : %s", stripe_sub_id, exc)

    # ── invoice.payment_failed ─────────────────────────────
    elif event_type == "invoice.payment_failed":
        stripe_sub_id = data.get("subscription")
        if stripe_sub_id:
            sub = db.execute(
                select(Subscription).where(Subscription.stripe_subscription_id == stripe_sub_id)
            ).scalar_one_or_none()
            if sub:
                sub.status = "past_due"
                db.commit()

            _handle_adjuris_subscription_event(db, stripe_sub_id, "payment_failed")

    # ── customer.subscription.deleted ─────────────────────
    elif event_type == "customer.subscription.deleted":
        stripe_sub_id = data.get("id")
        sub = db.execute(
            select(Subscription).where(Subscription.stripe_subscription_id == stripe_sub_id)
        ).scalar_one_or_none()

        if sub:
            user = db.execute(select(User).where(User.id == sub.user_id)).scalar_one_or_none()
            if user:
                _downgrade_to_free(db, user, sub, status="expired")

        _handle_adjuris_subscription_event(db, stripe_sub_id, "cancelled")

    # ── customer.subscription.updated ─────────────────────
    elif event_type == "customer.subscription.updated":
        stripe_sub_id = data.get("id")
        stripe_status = data.get("status")

        sub = db.execute(
            select(Subscription).where(Subscription.stripe_subscription_id == stripe_sub_id)
        ).scalar_one_or_none()

        if sub:
            period_start, period_end = _stripe_period(data)
            if period_start:
                sub.current_period_start = period_start
            if period_end:
                sub.current_period_end = period_end

            cancel_at_period_end = data.get("cancel_at_period_end", False)

            if cancel_at_period_end:
                # L'user a demandé une résiliation → accès maintenu jusqu'à current_period_end
                sub.status = "cancelled"
                if not sub.cancelled_at:
                    sub.cancelled_at = utcnow()
            elif stripe_status == "active":
                sub.status = "active"
                sub.cancelled_at = None  # réactivation éventuelle
            elif stripe_status in ("past_due", "unpaid"):
                sub.status = "past_due"

            # Changement de formule fait depuis le Customer Portal (Membre →
            # Membre+, mensuel → annuel) : l'event ne porte aucune metadata
            # à jour, seul le Price dit la vérité. Sans cette relecture, l'élève
            # payait Membre+ en gardant les limites de Membre.
            items = ((data.get("items") or {}).get("data") or [])
            price_id = (items[0].get("price") or {}).get("id") if items else None
            mapping = plan_from_price_id(price_id) if price_id else None
            if mapping and stripe_status in ("active", "trialing", "past_due"):
                new_plan, new_cycle = mapping
                if new_plan != sub.plan or new_cycle != sub.billing_cycle:
                    logger.info(
                        "Changement de formule pour user %s : %s/%s → %s/%s",
                        sub.user_id, sub.plan, sub.billing_cycle, new_plan, new_cycle,
                    )
                    sub.plan = new_plan
                    sub.billing_cycle = new_cycle
                    user = db.execute(
                        select(User).where(User.id == sub.user_id)
                    ).scalar_one_or_none()
                    if user:
                        _sync_user_plan(db, user, new_plan)

            db.commit()

    return {"ok": True}


# ============================================================
# Routes admin — abonnements
# ============================================================

@router.get("/admin/prices")
def admin_list_prices(
    provision: bool = False,
    admin: User = Depends(require_admin),
):
    """
    Prices Stripe utilisés par l'embed d'offres, plan par plan.

    Par défaut, lecture seule : on regarde ce que la prod résoudrait, sans rien
    créer. `?provision=true` force la création des Prices manquants au tarif du
    catalogue (même chose que ferait le premier checkout d'un élève) — pratique
    pour récupérer les price_id et les épingler ensuite en variables
    d'environnement sur Render.
    """
    _stripe()  # force la clé NAVIRE
    return {
        "stripe_key_configured": bool(STRIPE_SECRET_KEY),
        "provisioned": provision,
        "items": catalog_overview(allow_create=provision),
    }


@router.get("/admin/list")
def admin_list_subscriptions(
    plan: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    limit = max(1, min(200, int(limit)))
    offset = max(0, int(offset))

    stmt = (
        select(Subscription, User.email, User.username)
        .join(User, Subscription.user_id == User.id)
        .order_by(desc(Subscription.updated_at))
        .limit(limit)
        .offset(offset)
    )
    if plan:
        stmt = stmt.where(Subscription.plan == plan)
    if status:
        stmt = stmt.where(Subscription.status == status)

    rows = db.execute(stmt).all()

    return {
        "items": [
            {
                "user_id": sub.user_id,
                "email": email,
                "username": username,
                "plan": sub.plan,
                "billing_cycle": sub.billing_cycle,
                "status": sub.status,
                "current_period_end": sub.current_period_end,
                "cancelled_at": sub.cancelled_at,
                "stripe_subscription_id": sub.stripe_subscription_id,
                "created_at": sub.created_at,
            }
            for sub, email, username in rows
        ],
        "limit": limit,
        "offset": offset,
    }


@router.post("/admin/set-plan/{user_id}")
def admin_set_plan(
    user_id: int,
    payload: SetPlanIn,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Forcer un plan sur un user (tests, offres commerciales, corrections)."""
    target = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
    if not target:
        raise HTTPException(404, detail="Utilisateur introuvable.")

    sub = get_or_create_subscription(db, target)
    sub.plan = payload.plan
    sub.billing_cycle = payload.billing_cycle
    sub.status = "active"
    sub.cancelled_at = None if payload.plan != "free" else sub.cancelled_at
    db.commit()
    _sync_user_plan(db, target, payload.plan)

    return {
        "ok": True,
        "user_id": user_id,
        "plan": payload.plan,
        "billing_cycle": payload.billing_cycle,
        "set_by_admin": admin.id,
    }


# ============================================================
# Routes admin — codes promo
# ============================================================

@router.get("/admin/promo")
def admin_list_promos(
    active_only: bool = False,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    stmt = select(PromoCode).order_by(desc(PromoCode.created_at))
    if active_only:
        stmt = stmt.where(PromoCode.is_active == True)

    promos = db.execute(stmt).scalars().all()
    return {
        "items": [
            {
                "id": p.id,
                "code": p.code,
                "discount_type": p.discount_type,
                "discount_value": p.discount_value,
                "applies_to": p.applies_to,
                "max_uses": p.max_uses,
                "uses_count": p.uses_count,
                "expires_at": p.expires_at,
                "is_active": p.is_active,
                "stripe_coupon_id": p.stripe_coupon_id,
                "created_at": p.created_at,
            }
            for p in promos
        ]
    }


@router.post("/admin/promo")
def admin_create_promo(
    payload: PromoCreateIn,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    _stripe()  # force la clé NAVIRE

    existing = db.execute(
        select(PromoCode).where(PromoCode.code == payload.code.upper())
    ).scalar_one_or_none()

    if existing:
        raise HTTPException(400, detail={
            "code": "PROMO_CODE_EXISTS",
            "message": f"Le code '{payload.code.upper()}' existe déjà.",
        })

    stripe_coupon_id = None
    if STRIPE_SECRET_KEY:
        try:
            coupon_params: dict = {
                "id": payload.code.upper(),
                "currency": "eur",
                "duration": "once",
            }
            if payload.discount_type == "percent":
                coupon_params["percent_off"] = payload.discount_value
            else:
                coupon_params["amount_off"] = int(payload.discount_value * 100)

            if payload.max_uses:
                coupon_params["max_redemptions"] = payload.max_uses
            if payload.expires_at:
                coupon_params["redeem_by"] = int(payload.expires_at.timestamp())

            coupon = stripe.Coupon.create(**coupon_params)
            stripe_coupon_id = coupon.id
        except stripe.StripeError:
            pass

    promo = PromoCode(
        code=payload.code.upper(),
        discount_type=payload.discount_type,
        discount_value=payload.discount_value,
        applies_to=payload.applies_to,
        max_uses=payload.max_uses,
        expires_at=payload.expires_at,
        is_active=True,
        created_by=admin.id,
        stripe_coupon_id=stripe_coupon_id,
    )
    db.add(promo)
    db.commit()
    db.refresh(promo)

    return {
        "ok": True,
        "id": promo.id,
        "code": promo.code,
        "discount_type": promo.discount_type,
        "discount_value": promo.discount_value,
        "applies_to": promo.applies_to,
        "max_uses": promo.max_uses,
        "expires_at": promo.expires_at,
        "stripe_coupon_id": stripe_coupon_id,
    }


@router.patch("/admin/promo/{promo_id}")
def admin_update_promo(
    promo_id: int,
    payload: PromoUpdateIn,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    promo = db.execute(
        select(PromoCode).where(PromoCode.id == promo_id)
    ).scalar_one_or_none()
    if not promo:
        raise HTTPException(404, detail="Code promo introuvable.")

    if payload.is_active is not None:
        promo.is_active = payload.is_active
    if payload.max_uses is not None:
        promo.max_uses = payload.max_uses
    if payload.expires_at is not None:
        promo.expires_at = payload.expires_at
    if payload.discount_value is not None:
        promo.discount_value = payload.discount_value

    db.commit()
    return {"ok": True, "id": promo.id, "code": promo.code}


# ============================================================
# Job APScheduler — vérification périodique des abonnements
# ============================================================

def check_expired_subscriptions(db_factory) -> None:
    """
    Job horaire : repasse en free les abonnements dont current_period_end
    est dépassé et dont le statut Stripe est expiré/annulé.
    Gère aussi l'expiration des accès PREPASSERELLE (prepa_expires_at).
    """
    import logging
    logger = logging.getLogger(__name__)

    _stripe()  # force la clé NAVIRE

    db: Session = db_factory()
    try:
        now = datetime.now(timezone.utc)

        # ── Expiration PREPASSERELLE ───────────────────────
        expired_prepa = db.execute(
            select(User).where(
                User.plan == "prepa",
                User.prepa_expires_at < now,
            )
        ).scalars().all()

        for user in expired_prepa:
            user.plan = "free"
            db.commit()
            logger.info("PREPASSERELLE expiré : user %s repassé en free", user.id)
            print(f"🎓 PREPASSERELLE expiré : user {user.id} → free")

        # ── Expiration abonnements Stripe ──────────────────
        # Deux cas balayés :
        #   - période terminée (le grade doit tomber) ;
        #   - période inconnue alors qu'un abonnement Stripe existe : la
        #     lecture avait échoué au moment du paiement, on la répare ici
        #     plutôt que de laisser un grade sans date de fin.
        # Un plan posé à la main par un admin (aucun stripe_subscription_id,
        # aucune période) reste intouché — il n'expire pas.
        expired_subs = db.execute(
            select(Subscription).where(
                Subscription.plan != "free",
                or_(
                    Subscription.current_period_end < now,
                    and_(
                        Subscription.current_period_end.is_(None),
                        Subscription.stripe_subscription_id.is_not(None),
                    ),
                ),
            )
        ).scalars().all()

        for sub in expired_subs:
            if sub.stripe_subscription_id:
                try:
                    stripe_sub = stripe.Subscription.retrieve(sub.stripe_subscription_id)
                    stripe_status = stripe_sub.get("status", "")

                    if stripe_status in ("active", "trialing"):
                        _, new_end = _stripe_period(stripe_sub)
                        if new_end:
                            sub.current_period_end = new_end
                        # Un abonnement résilié en fin de période reste "active"
                        # côté Stripe jusqu'au dernier jour : on ne réécrit pas
                        # son statut local, sinon la résiliation serait perdue.
                        if not stripe_sub.get("cancel_at_period_end"):
                            sub.status = "active"
                        db.commit()
                        continue

                    if stripe_status in ("canceled", "unpaid", "incomplete_expired"):
                        user = db.execute(
                            select(User).where(User.id == sub.user_id)
                        ).scalar_one_or_none()
                        if user:
                            _downgrade_to_free(db, user, sub, status="expired")
                            logger.info("Downgraded user %s to free (stripe: %s)", user.id, stripe_status)

                except stripe.StripeError as e:
                    logger.warning("Stripe retrieve failed for sub %s: %s", sub.stripe_subscription_id, e)

            elif sub.current_period_end is not None:
                # Aucun abonnement Stripe rattaché et période dépassée :
                # rien à interroger, le grade tombe.
                user = db.execute(
                    select(User).where(User.id == sub.user_id)
                ).scalar_one_or_none()
                if user:
                    _downgrade_to_free(db, user, sub, status="expired")

        cutoff = now - timedelta(days=30)
        old_pending = db.execute(
            select(PendingSubscription).where(PendingSubscription.created_at < cutoff)
        ).scalars().all()
        for p in old_pending:
            db.delete(p)
        if old_pending:
            db.commit()
            logger.info("Purged %d expired PendingSubscriptions", len(old_pending))

    except Exception as exc:
        logger.error("check_expired_subscriptions error: %s", exc)
        db.rollback()
    finally:
        db.close()


@router.delete("/admin/promo/{promo_id}")
def admin_delete_promo(
    promo_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    _stripe()  # force la clé NAVIRE

    promo = db.execute(
        select(PromoCode).where(PromoCode.id == promo_id)
    ).scalar_one_or_none()
    if not promo:
        raise HTTPException(404, detail="Code promo introuvable.")

    if promo.stripe_coupon_id and STRIPE_SECRET_KEY:
        try:
            stripe.Coupon.delete(promo.stripe_coupon_id)
        except stripe.StripeError:
            pass

    db.delete(promo)
    db.commit()
    return {"ok": True, "deleted_code": promo.code}