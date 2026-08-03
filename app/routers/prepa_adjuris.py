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
from datetime import datetime, timezone

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
    matiere_label,
    matiere_niveau,
)
from app.bot_discord.role_sync import assign_adjuris_role_sync

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

@router.post("/inscription")
def create_prepa_adjuris_inscription(
    payload: PrepaAdjurisInscriptionIn,
    db: Session = Depends(get_db),
):
    """
    Enregistre une pré-inscription venue du formulaire embarqué sur le site.
    Aucune authentification : l'embed est public. Aucun accès n'est ouvert ici
    — c'est une manifestation d'intérêt, le paiement passe par /checkout-session.

    Upsert par email : une nouvelle soumission avec le même email met à jour la
    ligne et fusionne les matières, au lieu de créer un doublon.
    """
    # Bot détecté → on répond OK pour ne pas lui signaler la détection.
    if payload.website.strip():
        return {"ok": True}

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
        return {"ok": True, "matieres": matieres}

    row = PrepaAdjurisInscription(
        prenom=prenom,
        nom=nom,
        email=email,
        niveau=niveau,
        matieres=matieres,
    )
    db.add(row)
    db.commit()

    return {"ok": True, "matieres": matieres}


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
