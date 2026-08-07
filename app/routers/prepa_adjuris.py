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

Endpoint public (aucune auth — formulaire embarqué sur le site) :
  POST /prepa/adjuris/inscription       → pré-inscription (manifestation d'intérêt)

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
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select, desc
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import (
    DiscordLinkCode,
    PrepaAdjurisEnrollment,
    PrepaAdjurisInscription,
    User,
)
from app.routers.auth import get_current_user
from app.routers.admin import verify_admin_code
from app.routers.discord_bot import _require_bot
from app.routers.subscriptions import _stripe
from app.core.config import FRONTEND_URL
from app.core.prepa_adjuris_config import (
    PREPA_PRICES,
    PREPA_MONTHLY_QUANTITIES,
    PREPA_FIRST_BILLING_DATE,
    matiere_label,
    matiere_niveau,
)
from app.bot_discord.role_sync import assign_adjuris_role_sync

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/prepa/adjuris", tags=["prepa-adjuris"])

VALID_NIVEAUX = {"L1", "L2", "L3"}


# ============================================================
# Schemas
# ============================================================

class PrepaAdjurisCheckoutIn(BaseModel):
    matiere_key: str


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
    matieres: list[str] = Field(..., min_length=1, max_length=9)

    # Honeypot : champ invisible pour un humain, rempli par la plupart des bots.
    # S'il est non vide, on répond OK sans rien enregistrer.
    website: str = ""


# ============================================================
# Routes utilisateur
# ============================================================

@router.get("/me")
def my_adjuris_enrollments(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Matières actives de l'utilisateur connecté — sert de gate pour l'UI /prepa."""
    rows = db.execute(
        select(PrepaAdjurisEnrollment).where(
            PrepaAdjurisEnrollment.user_id == user.id,
            PrepaAdjurisEnrollment.status == "active",
        )
    ).scalars().all()

    return {
        "matieres": [r.matiere_key for r in rows],
        "items": [
            {
                "matiere_key": r.matiere_key,
                "status": r.status,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
    }


@router.post("/checkout-session")
def create_prepa_adjuris_checkout(
    payload: PrepaAdjurisCheckoutIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Crée une Checkout Session en mode subscription combinant le one_time
    (une des séances de septembre) et le recurring (le reste du mois),
    conformément au pattern Stripe "recurring plan with a one-time setup fee".
    """
    if payload.matiere_key not in PREPA_PRICES:
        raise HTTPException(status_code=400, detail="Matière inconnue.")

    existing = db.execute(
        select(PrepaAdjurisEnrollment).where(
            PrepaAdjurisEnrollment.user_id == user.id,
            PrepaAdjurisEnrollment.matiere_key == payload.matiere_key,
            PrepaAdjurisEnrollment.status == "active",
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail={
            "code": "ALREADY_ENROLLED",
            "message": "Vous êtes déjà inscrit à cette matière.",
        })

    _stripe()  # force la clé NAVIRE

    prices = PREPA_PRICES[payload.matiere_key]
    quantites = PREPA_MONTHLY_QUANTITIES[payload.matiere_key]
    septembre_recurring_qty = quantites[0] - 1

    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            customer_email=user.email,
            client_reference_id=str(user.id),
            line_items=[
                {"price": prices["one_time"], "quantity": 1},
                {"price": prices["recurring"], "quantity": septembre_recurring_qty},
            ],
            success_url=f"{FRONTEND_URL}/prepa-merci?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{FRONTEND_URL}/prepa-adjuris",
            metadata={"matiere_key": payload.matiere_key},
        )
    except stripe.StripeError as e:
        raise HTTPException(status_code=502, detail=f"Erreur Stripe : {str(e)}")

    return {"checkout_url": session.url}


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

def _first_billing_timestamp() -> int | None:
    """
    Timestamp du premier prélèvement mensuel, passé à Stripe en trial_end.
    Tant que ce trial court, seul le paiement d'inscription (20 €) est encaissé.

    Retourne None si la date est passée ou trop proche : Stripe exige un
    trial_end à plus de 48 h, et un étudiant qui s'inscrit après cette date doit
    de toute façon être facturé immédiatement.
    """
    try:
        jour = datetime.strptime(PREPA_FIRST_BILLING_DATE, "%Y-%m-%d")
    except ValueError:
        logger.error(
            "PREPA_ADJURIS_FIRST_BILLING invalide (%s) — attendu AAAA-MM-JJ. "
            "Trial désactivé : le récurrent sera facturé dès le checkout.",
            PREPA_FIRST_BILLING_DATE,
        )
        return None

    # Fin de journée, pour que le prélèvement tombe bien le jour dit.
    echeance = jour.replace(hour=22, minute=0, tzinfo=timezone.utc)
    if echeance < datetime.now(timezone.utc) + timedelta(hours=49):
        return None
    return int(echeance.timestamp())


def _validate_inscription(payload: PrepaAdjurisInscriptionIn) -> tuple[str, list[str]]:
    """Valide niveau + matières. Retourne (niveau, matieres dédoublonnées)."""
    niveau = payload.niveau.strip().upper()
    if niveau not in VALID_NIVEAUX:
        raise HTTPException(status_code=400, detail="Niveau invalide (L1, L2 ou L3).")

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
            PrepaAdjurisEnrollment.status == "active",
        )
    ).scalars().all())
    a_payer = [m for m in matieres if m not in deja_payees]

    if not a_payer:
        raise HTTPException(status_code=400, detail={
            "code": "ALREADY_ENROLLED",
            "message": "Tu es déjà inscrit à ces matières.",
        })

    _stripe()  # force la clé NAVIRE

    # Le one_time couvre une séance de septembre → -1 sur le recurring.
    septembre_recurring_qty = PREPA_MONTHLY_QUANTITIES[a_payer[0]][0] - 1

    line_items = []
    for key in a_payer:
        line_items.append({"price": PREPA_PRICES[key]["one_time"], "quantity": 1})
        if septembre_recurring_qty > 0:
            line_items.append({
                "price": PREPA_PRICES[key]["recurring"],
                "quantity": septembre_recurring_qty,
            })

    # Si un compte NAVIRE existe déjà pour cet email, on le référence tout de
    # suite ; sinon le webhook rattachera par email.
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()

    params = {
        "mode": "subscription",
        "customer_email": email,
        "line_items": line_items,
        "success_url": f"{FRONTEND_URL}/prepa-merci?session_id={{CHECKOUT_SESSION_ID}}",
        "cancel_url": f"{FRONTEND_URL}/prepa-adjuris",
        "metadata": {
            "matiere_keys": ",".join(a_payer),
            "niveau": niveau,
            "email": email,
            "prenom": payload.prenom.strip()[:80],
            "nom": payload.nom.strip()[:80],
        },
    }
    if user:
        params["client_reference_id"] = str(user.id)

    # Trial jusqu'au premier prélèvement : sans lui, Stripe facturerait le
    # récurrent dès le checkout (60 € en L1 au lieu des 20 € d'inscription).
    trial_end = _first_billing_timestamp()
    if trial_end:
        params["subscription_data"] = {"trial_end": trial_end}

    try:
        session = stripe.checkout.Session.create(**params)
    except stripe.StripeError as e:
        raise HTTPException(status_code=502, detail=f"Erreur Stripe : {str(e)}")

    return {"checkout_url": session.url, "matieres": a_payer}


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
