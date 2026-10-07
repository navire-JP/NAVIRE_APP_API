"""
app/routers/prepa_adjuris.py
=============================
Router Prép'AdJuris (soutien scolaire par matière, Stripe + accès Discord).

Endpoints utilisateur (auth NAVIRE) :
  GET  /prepa/adjuris/me                → matières actives de l'utilisateur
  POST /prepa/adjuris/checkout-session  → crée la Stripe Checkout Session

Endpoint bot Discord (header x-bot-secret) :
  POST /prepa/adjuris/link-discord      → valide un code, lie discord_id,
                                           attribue les rôles des matières actives

Endpoints publics (aucune auth — formulaire embarqué sur le site) :
  POST /prepa/adjuris/inscription       → pré-inscription (manifestation d'intérêt)
  POST /prepa/adjuris/checkout          → pré-inscription + Checkout Stripe
  GET  /prepa/adjuris/echeancier        → devis : ce qui sera prélevé et quand

Endpoints admin (header X-Admin-Code) :
  GET  /prepa/adjuris/admin/inscriptions      → liste JSON
  GET  /prepa/adjuris/admin/inscriptions.csv  → export CSV (ouvrable dans Drive)

Le traitement du paiement lui-même (checkout.session.completed, échecs de
paiement, résiliation) est géré par le webhook Stripe existant dans
app/routers/subscriptions.py — pas ici, pour ne pas dupliquer la logique
de vérification de signature Stripe.
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import datetime, timedelta, timezone

import stripe
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select, desc
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import (
    AdjurisPromoCode,
    DiscordLinkCode,
    PrepaAdjurisEnrollment,
    PrepaAdjurisInscription,
    User,
)
from app.routers.auth import get_current_user
from app.routers.admin import verify_admin_code
from app.routers.discord_bot import _require_bot
from app.routers.subscriptions import _stripe
from app.core.config import (
    FRONTEND_URL,
    DISCORD_GUILD_ID,
    DISCORD_PREPA_ADJURIS_CHANNEL_ID,
)
from app.core.prepa_adjuris_config import (
    PREPA_NIVEAUX,
    PREPA_PRICES,
    PREPA_MATIERE_NAMES,
    PREPA_PRIX_SEANCE_CENTS,
    matiere_label,
    matiere_niveau,
    prices_configures,
)
from app.bot_discord.role_sync import assign_adjuris_role_sync
from app.services.prepa_adjuris_billing import (
    Echeancier,
    date_prelevement,
    devis_vers_metadata,
    parse_mois,
    prelevements,
    texte_recap,
)
from app.services.prepa_adjuris_facturation import calculer_devis

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/prepa/adjuris", tags=["prepa-adjuris"])

VALID_NIVEAUX = set(PREPA_NIVEAUX)


# ============================================================
# Schemas
# ============================================================

class PrepaAdjurisCheckoutIn(BaseModel):
    """Une matière (matiere_key) ou plusieurs (matieres). matiere_key est
    conservé pour ne pas casser un appel existant."""
    matiere_key: str | None = None
    matieres: list[str] | None = None
    promo_code: str | None = None


class LinkDiscordAdjurisIn(BaseModel):
    discord_id: str
    email: str
    code: str


class PrepaAdjurisInscriptionIn(BaseModel):
    """Payload du formulaire public. Les longueurs sont bornées ici : l'endpoint
    est ouvert, on ne fait confiance à rien de ce qui arrive."""
    prenom: str = Field(..., min_length=1, max_length=80)
    nom: str = Field(..., min_length=1, max_length=80)
    email: EmailStr
    niveau: str = Field(..., max_length=4)
    matieres: list[str] = Field(..., min_length=1, max_length=len(PREPA_PRICES))

    # Honeypot : champ invisible pour un humain, rempli par la plupart des bots.
    # S'il est non vide, on répond OK sans rien enregistrer.
    website: str = ""

    promo_code: str | None = None


# ============================================================
# Routes utilisateur
# ============================================================

@router.get("/me")
def my_adjuris_enrollments(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Matières de l'utilisateur connecté — alimente le tableau de bord /prepa.

    `items` inclut aussi les inscriptions en échec de paiement : sans elles,
    un étudiant dont le prélèvement a échoué ne verrait plus rien et croirait
    avoir perdu son accès sans explication. `matieres` ne liste que les
    matières actives, celles qui bloquent un nouvel achat.
    """
    rows = db.execute(
        select(PrepaAdjurisEnrollment).where(
            PrepaAdjurisEnrollment.user_id == user.id,
            PrepaAdjurisEnrollment.status.in_(("active", "payment_failed", "suspendu")),
        )
    ).scalars().all()

    actives = [r.matiere_key for r in rows if r.status == "active"]
    niveaux = sorted({matiere_niveau(r.matiere_key) for r in rows})

    return {
        "matieres": actives,
        "niveau": niveaux[0] if len(niveaux) == 1 else None,
        "discord_url": (
            f"https://discord.com/channels/{DISCORD_GUILD_ID}/{DISCORD_PREPA_ADJURIS_CHANNEL_ID}"
            if DISCORD_GUILD_ID and DISCORD_PREPA_ADJURIS_CHANNEL_ID else None
        ),
        "discord_lie": bool(user.discord_id),
        "items": [
            {
                "matiere_key": r.matiere_key,
                "label": PREPA_MATIERE_NAMES.get(r.matiere_key, r.matiere_key),
                "niveau": matiere_niveau(r.matiere_key),
                "status": r.status,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
    }


def _validate_adjuris_promo(db: Session, code: str) -> AdjurisPromoCode:
    """Vérifie qu'un code promo AdJuris est utilisable. Lève 400 sinon.

    Pas de vérification par utilisateur (contrairement à _validate_promo dans
    subscriptions.py) : le checkout AdJuris est ouvert sans authentification
    (formulaire public), il n'y a pas toujours d'user_id à qui rattacher une
    utilisation.
    """
    promo = db.execute(
        select(AdjurisPromoCode).where(AdjurisPromoCode.code == code.strip().upper())
    ).scalar_one_or_none()

    if not promo:
        raise HTTPException(400, detail={"code": "PROMO_NOT_FOUND", "message": "Code promo introuvable."})
    if not promo.is_active:
        raise HTTPException(400, detail={"code": "PROMO_INACTIVE", "message": "Code promo inactif."})
    if promo.expires_at and promo.expires_at < datetime.now(timezone.utc):
        raise HTTPException(400, detail={"code": "PROMO_EXPIRED", "message": "Code promo expiré."})
    if promo.max_uses is not None and promo.uses_count >= promo.max_uses:
        raise HTTPException(400, detail={"code": "PROMO_EXHAUSTED", "message": "Code promo épuisé."})

    return promo


# Cache en mémoire : le product Stripe d'une matière ne change jamais après
# création du Price, pas besoin de le re-résoudre à chaque checkout.
_ADJURIS_PRODUCT_ID_CACHE: dict[str, str] = {}


def _adjuris_stripe_product_id(matiere_key: str) -> str:
    """Product Stripe associé au Price one_time de la matière — nécessaire
    pour construire un price_data (prix promo dynamique) sur ce produit."""
    if matiere_key not in _ADJURIS_PRODUCT_ID_CACHE:
        price = stripe.Price.retrieve(PREPA_PRICES[matiere_key]["one_time"])
        _ADJURIS_PRODUCT_ID_CACHE[matiere_key] = price["product"]
    return _ADJURIS_PRODUCT_ID_CACHE[matiere_key]


def _devis_ou_erreur(
    db: Session,
    matieres: list[str],
    inscrit_le: datetime,
    maintenant: datetime,
) -> list[Echeancier]:
    """
    Devis de l'inscription, ou HTTP 400 si une matière ne peut plus être
    souscrite (Prices Stripe absents, plus aucune séance à venir, programme
    terminé).
    """
    sans_prix = [m for m in matieres if not prices_configures(m)]
    if sans_prix:
        raise HTTPException(status_code=400, detail={
            "code": "MATIERE_NON_OUVERTE",
            "message": f"Le paiement de {matiere_label(sans_prix[0])} n'est pas encore ouvert.",
        })

    # Marge de 2 h : si le prélèvement du mois tombe dans moins de 2 h (dernier
    # jour du mois, tard le soir), on démarre au mois suivant. Stripe exige une
    # date de premier prélèvement encore future au moment où l'élève paie.
    devis = calculer_devis(
        db, matieres, inscrit_le, facturable_apres=maintenant + timedelta(hours=2)
    )

    terminees = [e.matiere_key for e in devis if e.seance_prepayee is None]
    if terminees:
        raise HTTPException(status_code=400, detail={
            "code": "PLUS_DE_SEANCE",
            "message": f"Plus aucune séance à venir pour {matiere_label(terminees[0])}.",
        })
    if not devis or not devis[0].mois:
        raise HTTPException(status_code=400, detail={
            "code": "PROGRAMME_TERMINE",
            "message": "Le programme Prép'AdJuris est terminé pour cette période.",
        })
    return devis


def _creer_checkout_session(
    db: Session,
    matieres: list[str],
    email: str,
    user: User | None = None,
    metadata_extra: dict | None = None,
    override_price_cents: int | None = None,
    success_path: str = "/prepa-merci",
    inscrit_le: datetime | None = None,
    inscription_deja_payee: bool = False,
):
    """
    Construit et crée la Checkout Session. Partagé par tous les points
    d'entrée (formulaire public, espace connecté, lien de paiement admin) :
    c'est ce qui garantit qu'ils facturent à l'identique.

    La page Stripe est un paiement simple : 20 € par matière (le one_time),
    qui règlent d'avance la prochaine séance, et la carte est enregistrée
    pour les prélèvements de fin de mois (setup_future_usage). Pas de mode
    "subscription" : Stripe y afficherait « puis X € par mois », alors que
    le montant varie chaque mois avec le nombre de séances.

    L'abonnement et son échéancier mois par mois sont créés par le webhook
    (subscriptions._creer_abonnement_adjuris), avec le devis figé dans la
    metadata : exactement ce que l'élève a vu.

    override_price_cents : code promo validé en amont, remplace le prix
    d'inscription matière par matière. Le mensuel n'est jamais concerné.

    inscrit_le / inscription_deja_payee : lien de paiement créé par un admin
    pour un élève qui a déjà réglé ses 20 € autrement (inscription antidatée,
    pas de one_time). Les mois déjà passés ne sont jamais facturés.
    Rien à encaisser (inscription déjà réglée, promo à 0 €) : mode "setup",
    la page enregistre seulement la carte.
    """
    niveaux = {matiere_niveau(m) for m in matieres}
    if len(niveaux) > 1:
        raise HTTPException(
            status_code=400,
            detail="Les matières d'un même paiement doivent appartenir au même niveau.",
        )

    maintenant = datetime.now(timezone.utc)
    inscrit_le = inscrit_le or maintenant
    devis = _devis_ou_erreur(db, matieres, inscrit_le, maintenant)

    premier_mois = devis[0].depuis
    premier_prelevement = date_prelevement(parse_mois(premier_mois))

    _stripe()  # force la clé NAVIRE

    line_items = []
    if not inscription_deja_payee:
        for e in devis:
            key = e.matiere_key
            if override_price_cents is not None:
                line_items.append({
                    "price_data": {
                        "currency": "eur",
                        "product": _adjuris_stripe_product_id(key),
                        "unit_amount": override_price_cents,
                    },
                    "quantity": 1,
                })
            else:
                line_items.append({"price": PREPA_PRICES[key]["one_time"], "quantity": 1})

    inscrit_iso = inscrit_le.isoformat()
    metadata = {
        "matiere_keys": ",".join(matieres),
        "email": email,
        "inscrit_le": inscrit_iso,
        "echeancier": devis_vers_metadata(devis),
    }
    if inscription_deja_payee:
        metadata["inscription_deja_payee"] = "1"
    metadata.update(metadata_extra or {})

    # Le devis ne doit pas pouvoir devenir faux entre l'ouverture du lien et
    # le paiement : le lien expire avant la prochaine séance, et avant le
    # premier prélèvement (le webhook crée l'abonnement avec cette date, qui
    # doit encore être future). Stripe impose entre 30 minutes et 24 heures,
    # bornes exclues. Lien admin (inscription antidatée) : le devis ne dépend
    # pas du moment du paiement.
    expire = min(
        maintenant + timedelta(hours=23, minutes=55),
        premier_prelevement - timedelta(minutes=15),
    )
    if not inscription_deja_payee:
        prochaine = min(e.seance_prepayee for e in devis if e.seance_prepayee)
        expire = min(expire, prochaine)
    expire = max(expire, maintenant + timedelta(minutes=31))

    inscription_cents = (
        None if inscription_deja_payee
        else (override_price_cents if override_price_cents is not None else PREPA_PRIX_SEANCE_CENTS)
    )

    params = {
        "customer_email": email,
        "success_url": f"{FRONTEND_URL}{success_path}?session_id={{CHECKOUT_SESSION_ID}}",
        "cancel_url": f"{FRONTEND_URL}/prepa-adjuris",
        "metadata": metadata,
        "expires_at": int(expire.timestamp()),
        "custom_text": {
            "submit": {"message": texte_recap(devis, inscription_cents=inscription_cents)}
        },
    }
    if user:
        params["client_reference_id"] = str(user.id)

    try:
        if inscription_cents:
            params.update({
                "mode": "payment",
                "line_items": line_items,
                # Un client Stripe neuf par paiement, comme avant en mode
                # abonnement : la carte y est enregistrée et devient son moyen
                # de paiement par défaut sans toucher à un autre abonnement.
                "customer_creation": "always",
                "payment_intent_data": {
                    "setup_future_usage": "off_session",
                    "metadata": {"matiere_keys": ",".join(matieres), "inscrit_le": inscrit_iso},
                },
            })
        else:
            # Mode setup : Checkout ne crée pas de client, on le crée ici.
            client = stripe.Customer.create(
                email=email, metadata={"origine": "prepa_adjuris"}
            )
            params.pop("customer_email")
            params.update({
                "mode": "setup",
                "customer": client["id"],
                "currency": "eur",
                "setup_intent_data": {
                    "metadata": {"matiere_keys": ",".join(matieres), "inscrit_le": inscrit_iso},
                },
            })
        return stripe.checkout.Session.create(**params)
    except stripe.StripeError as e:
        raise HTTPException(status_code=502, detail=f"Erreur Stripe : {str(e)}")


def _devis_public(devis: list[Echeancier], inscription_cents: int | None) -> dict:
    """Devis sérialisé pour le site et la console."""
    return {
        "inscription": {
            "par_matiere_cents": inscription_cents,
            "total_cents": (inscription_cents or 0) * len(devis),
            "seances_prepayees": {
                e.matiere_key: e.seance_prepayee.isoformat() if e.seance_prepayee else None
                for e in devis
            },
        },
        "prelevements": prelevements(devis),
        "total_mensuel_cents": sum(p["montant_cents"] for p in prelevements(devis)),
        "texte": texte_recap(devis, inscription_cents=inscription_cents),
    }


@router.post("/checkout-session")
def create_prepa_adjuris_checkout(
    payload: PrepaAdjurisCheckoutIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Checkout depuis l'espace connecté (tableau de bord /prepa). Accepte une
    matière (matiere_key) ou plusieurs (matieres) en un seul paiement.
    """
    matieres = payload.matieres or ([payload.matiere_key] if payload.matiere_key else [])
    matieres = list(dict.fromkeys(m for m in matieres if m))
    if not matieres:
        raise HTTPException(status_code=400, detail="Aucune matière sélectionnée.")

    inconnues = [m for m in matieres if m not in PREPA_PRICES]
    if inconnues:
        raise HTTPException(status_code=400, detail=f"Matière inconnue : {inconnues[0]}")

    # Une matière en impayé n'est pas « libre » : l'élève doit régler la
    # facture ouverte, pas créer un second abonnement.
    deja = set(db.execute(
        select(PrepaAdjurisEnrollment.matiere_key).where(
            PrepaAdjurisEnrollment.user_id == user.id,
            PrepaAdjurisEnrollment.status.in_(("active", "payment_failed", "suspendu")),
        )
    ).scalars().all())
    a_payer = [m for m in matieres if m not in deja]
    if not a_payer:
        raise HTTPException(status_code=400, detail={
            "code": "ALREADY_ENROLLED",
            "message": "Vous êtes déjà inscrit à ces matières.",
        })

    override_price_cents = None
    promo = None
    if payload.promo_code:
        promo = _validate_adjuris_promo(db, payload.promo_code)
        override_price_cents = promo.override_price_cents

    session = _creer_checkout_session(
        db, a_payer, user.email, user=user,
        override_price_cents=override_price_cents,
        success_path="/login",
    )

    if promo:
        promo.uses_count += 1
        db.commit()

    return {"checkout_url": session.url, "matieres": a_payer}


# ============================================================
# Route bot Discord
# ============================================================

@router.post("/link-discord", dependencies=[Depends(_require_bot)])
def link_discord_adjuris(payload: LinkDiscordAdjurisIn, db: Session = Depends(get_db)):
    """
    Valide un code de liaison et lie discord_id au compte NAVIRE correspondant
    à `email`, puis attribue le rôle de chaque matière active de ce user (pas
    seulement celle qui a généré le code — utile si plusieurs matières payées
    avant la liaison). Retourne toujours 200 : les échecs "attendus" (code
    invalide/expiré/déjà utilisé, email inconnu, discord déjà lié ailleurs)
    sont signalés via {"ok": false, "message": ...}, pas via une erreur HTTP.
    """
    email = payload.email.strip().lower()
    code = payload.code.strip().upper()

    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if not user:
        return {"ok": False, "message": "Aucun compte NAVIRE avec cet email."}

    now = datetime.now(timezone.utc)
    code_row = db.execute(
        select(DiscordLinkCode).where(
            DiscordLinkCode.user_id == user.id,
            DiscordLinkCode.code == code,
        )
    ).scalar_one_or_none()

    if not code_row or code_row.used_at is not None or code_row.expires_at < now:
        return {"ok": False, "message": "Code invalide, déjà utilisé ou expiré."}

    conflict = db.execute(
        select(User).where(User.discord_id == payload.discord_id)
    ).scalar_one_or_none()
    if conflict and conflict.id != user.id:
        return {"ok": False, "message": "Ce compte Discord est déjà lié à un autre compte NAVIRE."}

    user.discord_id = payload.discord_id
    code_row.used_at = now
    db.commit()

    enrollments = db.execute(
        select(PrepaAdjurisEnrollment).where(
            PrepaAdjurisEnrollment.user_id == user.id,
            PrepaAdjurisEnrollment.status == "active",
        )
    ).scalars().all()

    for enrollment in enrollments:
        assign_adjuris_role_sync(user.discord_id, enrollment.matiere_key)

    return {"ok": True, "matieres": [e.matiere_key for e in enrollments]}


# ============================================================
# Formulaire public du site (pré-inscription, sans paiement)
# ============================================================

def _validate_inscription(payload: PrepaAdjurisInscriptionIn) -> tuple[str, list[str]]:
    """Valide niveau + matières. Retourne (niveau, matieres dédoublonnées)."""
    niveau = payload.niveau.strip().upper()
    if niveau not in VALID_NIVEAUX:
        raise HTTPException(status_code=400, detail=f"Niveau invalide ({', '.join(PREPA_NIVEAUX)}).")

    # Dédoublonne en gardant l'ordre de sélection.
    matieres = list(dict.fromkeys(payload.matieres))

    inconnues = [m for m in matieres if m not in PREPA_PRICES]
    if inconnues:
        raise HTTPException(status_code=400, detail=f"Matière inconnue : {inconnues[0]}")

    hors_niveau = [m for m in matieres if matiere_niveau(m) != niveau]
    if hors_niveau:
        raise HTTPException(
            status_code=400,
            detail=f"La matière {hors_niveau[0]} n'appartient pas au niveau {niveau}.",
        )

    return niveau, matieres


def _upsert_inscription(
    db: Session, payload: PrepaAdjurisInscriptionIn, niveau: str, matieres: list[str]
) -> list[str]:
    """
    Enregistre (ou met à jour) la pré-inscription. Une nouvelle soumission avec
    le même email met à jour la ligne et fusionne les matières, au lieu de
    créer un doublon. Retourne les matières finalement enregistrées.
    """
    email = payload.email.strip().lower()
    prenom = payload.prenom.strip()
    nom = payload.nom.strip()

    existing = db.execute(
        select(PrepaAdjurisInscription).where(PrepaAdjurisInscription.email == email)
    ).scalar_one_or_none()

    if existing:
        existing.prenom = prenom
        existing.nom = nom
        # Un changement de niveau repart des seules matières du nouveau niveau ;
        # sinon on fusionne avec ce qui avait déjà été demandé.
        if existing.niveau == niveau:
            matieres = list(dict.fromkeys(list(existing.matieres or []) + matieres))
        existing.niveau = niveau
        existing.matieres = matieres
        db.commit()
        return matieres

    db.add(PrepaAdjurisInscription(
        prenom=prenom, nom=nom, email=email, niveau=niveau, matieres=matieres,
    ))
    db.commit()
    return matieres


@router.post("/inscription")
def create_prepa_adjuris_inscription(
    payload: PrepaAdjurisInscriptionIn,
    db: Session = Depends(get_db),
):
    """
    Enregistre une pré-inscription sans paiement (manifestation d'intérêt).
    Conservé pour un usage hors tunnel de paiement ; le formulaire du site
    appelle /checkout, qui enregistre la même chose PUIS redirige vers Stripe.
    """
    if payload.website.strip():
        return {"ok": True}  # bot : on ne lui signale pas la détection

    niveau, matieres = _validate_inscription(payload)
    matieres = _upsert_inscription(db, payload, niveau, matieres)
    return {"ok": True, "matieres": matieres}


@router.post("/checkout")
def create_prepa_adjuris_public_checkout(
    payload: PrepaAdjurisInscriptionIn,
    db: Session = Depends(get_db),
):
    """
    Point d'entrée du formulaire public : enregistre la pré-inscription (donc
    le lead est gardé même si le paiement est abandonné) puis crée la Checkout
    Session Stripe et renvoie son URL.

    Pas d'authentification, volontairement : l'embed vit dans une iframe et ne
    peut pas lire le token du site parent. L'identité repose sur l'email, et le
    webhook rattache le paiement au compte NAVIRE (existant ou créé ensuite).

    Toutes les matières appartiennent au même niveau (validé ci-dessous), donc
    elles partagent le même calendrier de quantités : un seul abonnement Stripe
    à N items suffit, avec les mêmes quantités par phase pour tous les items.
    """
    if payload.website.strip():
        raise HTTPException(status_code=400, detail="Requête invalide.")

    niveau, matieres = _validate_inscription(payload)
    _upsert_inscription(db, payload, niveau, matieres)

    email = payload.email.strip().lower()

    # Retire les matières déjà payées, pour ne pas facturer deux fois.
    deja_payees = set(db.execute(
        select(PrepaAdjurisEnrollment.matiere_key).where(
            PrepaAdjurisEnrollment.email == email,
            PrepaAdjurisEnrollment.status.in_(("active", "payment_failed", "suspendu")),
        )
    ).scalars().all())
    a_payer = [m for m in matieres if m not in deja_payees]

    if not a_payer:
        raise HTTPException(status_code=400, detail={
            "code": "ALREADY_ENROLLED",
            "message": "Tu es déjà inscrit à ces matières.",
        })

    # Si un compte NAVIRE existe déjà pour cet email, on le référence tout de
    # suite ; sinon le webhook rattachera par email.
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()

    override_price_cents = None
    promo = None
    if payload.promo_code:
        promo = _validate_adjuris_promo(db, payload.promo_code)
        override_price_cents = promo.override_price_cents

    session = _creer_checkout_session(
        db, a_payer, email, user=user,
        metadata_extra={
            "niveau": niveau,
            "prenom": payload.prenom.strip()[:80],
            "nom": payload.nom.strip()[:80],
        },
        override_price_cents=override_price_cents,
    )

    if promo:
        promo.uses_count += 1
        db.commit()

    return {"checkout_url": session.url, "matieres": a_payer}


@router.get("/echeancier")
def devis_inscription(
    matieres: str = Query(..., description="Clés séparées par des virgules"),
    promo_code: str | None = None,
    db: Session = Depends(get_db),
):
    """
    Ce qui serait encaissé et prélevé pour une inscription maintenant :
    affiché par le site avant le paiement. Public, sans effet de bord (un
    code promo est vérifié mais pas consommé).
    """
    keys = list(dict.fromkeys(m.strip() for m in matieres.split(",") if m.strip()))
    inconnues = [m for m in keys if m not in PREPA_PRICES]
    if not keys or inconnues:
        raise HTTPException(status_code=400, detail=f"Matière inconnue : {inconnues[0] if inconnues else '—'}")

    inscription_cents = PREPA_PRIX_SEANCE_CENTS
    if promo_code:
        inscription_cents = _validate_adjuris_promo(db, promo_code).override_price_cents

    maintenant = datetime.now(timezone.utc)
    devis = _devis_ou_erreur(db, keys, maintenant, maintenant)
    return {"matieres": keys, **_devis_public(devis, inscription_cents)}


# ============================================================
# Admin — consultation et export des pré-inscriptions
# ============================================================

def _all_inscriptions(db: Session) -> list[PrepaAdjurisInscription]:
    return db.execute(
        select(PrepaAdjurisInscription).order_by(desc(PrepaAdjurisInscription.created_at))
    ).scalars().all()


@router.get("/admin/inscriptions", dependencies=[Depends(verify_admin_code)])
def admin_list_inscriptions(db: Session = Depends(get_db)):
    """Liste des pré-inscriptions, plus récentes d'abord. Header : X-Admin-Code."""
    rows = _all_inscriptions(db)
    return {
        "total": len(rows),
        "items": [
            {
                "id": r.id,
                "prenom": r.prenom,
                "nom": r.nom,
                "email": r.email,
                "niveau": r.niveau,
                "matieres": r.matieres,
                "matieres_labels": [matiere_label(m) for m in (r.matieres or [])],
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in rows
        ],
    }


@router.get("/admin/inscriptions.csv", dependencies=[Depends(verify_admin_code)])
def admin_export_inscriptions_csv(db: Session = Depends(get_db)):
    """
    Export CSV des pré-inscriptions, à déposer/importer dans Drive ou Sheets.
    Séparateur ';' et BOM UTF-8 : Excel et Sheets en FR l'ouvrent alors
    directement en colonnes, sans écran d'import.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(["Date", "Prénom", "Nom", "Email", "Niveau", "Nb matières", "Matières"])

    for r in _all_inscriptions(db):
        labels = [matiere_label(m) for m in (r.matieres or [])]
        writer.writerow([
            r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else "",
            r.prenom,
            r.nom,
            r.email,
            r.niveau,
            len(labels),
            " | ".join(labels),
        ])

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return Response(
        content=buffer.getvalue().encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="prepa-adjuris-inscriptions-{today}.csv"'
        },
    )
