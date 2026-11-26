"""
app/routers/parrainage.py
==========================
Parrainage étudiant (Prép'AdJuris). Règles et garde-fous :
app/services/parrainage.py.

Le lien d'un parrain mène à la même page que celui d'un ambassadeur
(/rejoindre?ref=CODE) : GET /ambassadors/ref/{code}, /ambassadors/invite/{code}
et POST /ambassadors/claim reconnaissent les deux sortes de codes, tout comme
l'inscription (referral_code) et le checkout Prép'AdJuris (ref).

Endpoints publics :
  GET  /parrainage/terms               → conditions du programme
  GET  /parrainage/embed               → page d'embed /parrainage

Endpoints utilisateur (auth NAVIRE, page /parrainage) :
  GET   /parrainage/me                 → statut (none | active | suspended) et éligibilité
  POST  /parrainage/join               → active le lien (élève Prép'AdJuris, conditions acceptées)
  PATCH /parrainage/me                 → nom affiché
  GET   /parrainage/me/dashboard       → lien, statistiques, filleuls, récompenses

Endpoints console (auth NAVIRE, is_admin) :
  GET  /parrainage/admin/summary
  GET  /parrainage/admin/list?status=
  GET  /parrainage/admin/rewards?status=&parrain_id=
  GET  /parrainage/admin/{id}
  POST /parrainage/admin/{id}/suspend | reactivate
  POST /parrainage/admin/rewards/{id}/credit   → crédite tout de suite (même avant le délai)
  POST /parrainage/admin/rewards/{id}/cancel
"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import Parrain, ParrainageFilleul, ParrainageRecompense, User
from app.routers.ambassadors import _iso, embed_page, referral_link, require_admin, share_link
from app.routers.auth import get_current_user
from app.services import parrainage as svc
from app.services.email import mail_parrainage_bienvenue, send_mail

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/parrainage", tags=["parrainage"])


def _eur(cents: int) -> str:
    return f"{cents / 100:.2f} €".replace(".", ",")


# ============================================================
# Conditions du programme
# ============================================================

def _terms() -> list[str]:
    credit = _eur(svc.PARRAINAGE_CREDIT_PAR_MATIERE_CENTS)
    return [
        "Le parrainage est réservé aux élèves inscrits à Prép'AdJuris et porte uniquement sur "
        "les inscriptions à Prép'AdJuris.",
        f"Tu gagnes {credit} par matière dont un ami venu par ton lien paie l'inscription. "
        "La récompense est unique : rien sur les mensualités qui suivent.",
        "La récompense est une réduction : elle est créditée automatiquement sur ton compte "
        "Prép'AdJuris et déduite de tes prochains prélèvements. Elle n'est pas versée en argent.",
        f"Elle est créditée {svc.DELAI_CREDIT_JOURS} jours après le paiement de ton ami (délai "
        "de rétractation). Un paiement remboursé pendant ce délai annule la récompense.",
        "Un filleul est rattaché au premier lien qu'il utilise (parrain ou ambassadeur), à la "
        "création de son compte NAVIRE ou au paiement. Un étudiant déjà inscrit à Prép'AdJuris "
        "ne compte pas.",
        "Te parrainer toi-même ou créer de faux comptes entraîne la suspension du lien et "
        "l'annulation des récompenses concernées.",
        "NAVIRE peut suspendre un lien et faire évoluer ces conditions ; les récompenses déjà "
        "créditées restent acquises.",
    ]


@router.get("/terms")
def get_terms():
    return {
        "version": svc.TERMS_VERSION,
        "credit_par_matiere_cents": svc.PARRAINAGE_CREDIT_PAR_MATIERE_CENTS,
        "prix_inscription_cents": 2000,
        "delai_credit_jours": svc.DELAI_CREDIT_JOURS,
        "conditions": _terms(),
    }


@router.get("/embed", response_class=HTMLResponse, include_in_schema=False)
def embed():
    return embed_page("parrainage")


# ============================================================
# Page /parrainage (connecté)
# ============================================================

class JoinIn(BaseModel):
    display_name: str = Field(..., min_length=2, max_length=40)
    accept_terms: bool
    terms_version: str | None = None


class UpdateMeIn(BaseModel):
    display_name: str | None = Field(None, min_length=2, max_length=40)


def _get_mine(db: Session, user: User) -> Parrain | None:
    return db.execute(select(Parrain).where(Parrain.user_id == user.id)).scalar_one_or_none()


def _me_payload(db: Session, user: User, p: Parrain | None) -> dict:
    if p is None:
        return {
            "status": "none",
            "eligible": svc.eligible(db, user),
            "terms_version": svc.TERMS_VERSION,
        }
    out = {
        "status": p.status,
        "eligible": svc.eligible(db, user),
        "terms_version": svc.TERMS_VERSION,
        "display_name": p.display_name,
        "joined_at": _iso(p.created_at),
    }
    if p.status == "active":
        out["code"] = p.code
        out["link"] = referral_link(p.code)
        out["share_link"] = share_link(p.code)
    return out


@router.get("/me")
def get_me(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _me_payload(db, user, _get_mine(db, user))


@router.post("/join")
def join(payload: JoinIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if not payload.accept_terms:
        raise HTTPException(400, detail="Tu dois accepter les conditions du parrainage.")

    p = _get_mine(db, user)
    if p is not None:
        if p.status == "suspended":
            raise HTTPException(400, detail={
                "code": "SUSPENDED", "message": "Ton lien de parrainage est suspendu : contacte l'équipe.",
            })
        return _me_payload(db, user, p)

    if not svc.eligible(db, user):
        raise HTTPException(403, detail={
            "code": "NOT_ELIGIBLE",
            "message": "Le parrainage est réservé aux élèves inscrits à Prép'AdJuris.",
        })

    nom = payload.display_name.strip()
    p = Parrain(
        user_id=user.id,
        code=svc.generate_code(db, nom),
        display_name=nom,
        status="active",
        terms_version=svc.TERMS_VERSION,
        terms_accepted_at=svc.utcnow(),
    )
    db.add(p)
    db.commit()
    db.refresh(p)

    try:
        send_mail(user.email, *mail_parrainage_bienvenue(
            p.display_name, referral_link(p.code), _eur(svc.PARRAINAGE_CREDIT_PAR_MATIERE_CENTS)
        ))
    except Exception:  # noqa: BLE001 — l'email ne bloque jamais l'inscription
        logger.exception("Email de bienvenue parrainage non envoyé (user %s)", user.id)

    return _me_payload(db, user, p)


@router.patch("/me")
def update_me(payload: UpdateMeIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    p = _get_mine(db, user)
    if p is None:
        raise HTTPException(404, detail="Tu n'es pas encore inscrit au parrainage.")
    if payload.display_name is not None:
        p.display_name = payload.display_name.strip()
    db.commit()
    return _me_payload(db, user, p)


@router.get("/me/dashboard")
def dashboard(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    p = _get_mine(db, user)
    if p is None:
        raise HTTPException(403, detail="Inscris-toi d'abord au parrainage.")
    return {
        "parrain": _me_payload(db, user, p),
        "credit_par_matiere_cents": svc.PARRAINAGE_CREDIT_PAR_MATIERE_CENTS,
        "prix_inscription_cents": 2000,
        "delai_credit_jours": svc.DELAI_CREDIT_JOURS,
        "stats": svc.stats(db, p),
        "earnings": svc.earnings(db, p.id),
        "referrals": svc.referrals_list(db, p.id),
    }


# ============================================================
# Console
# ============================================================

def _admin_row(db: Session, p: Parrain) -> dict:
    u = db.get(User, p.user_id)
    inscription = svc.inscription_en_cours(db, u) if u else None
    return {
        "id": p.id,
        "user_id": p.user_id,
        "username": u.username if u else None,
        "email": u.email if u else None,
        "status": p.status,
        "code": p.code,
        "link": referral_link(p.code),
        "display_name": p.display_name,
        "stripe_customer_id": inscription.stripe_customer_id if inscription else None,
        "terms_version": p.terms_version,
        "joined_at": _iso(p.created_at),
        "suspended_at": _iso(p.suspended_at),
        "stats": svc.stats(db, p),
        "earnings": svc.earnings(db, p.id),
    }


def _get_or_404(db: Session, parrain_id: int) -> Parrain:
    p = db.get(Parrain, parrain_id)
    if p is None:
        raise HTTPException(404, detail="Parrain introuvable.")
    return p


def _reward_row(db: Session, r: ParrainageRecompense, cache: dict[int, Parrain], now) -> dict:
    p = cache.setdefault(r.parrain_id, db.get(Parrain, r.parrain_id))
    f = r.filleul
    return {
        "id": r.id,
        "parrain_id": r.parrain_id,
        "parrain": p.display_name if p else None,
        "parrain_code": p.code if p else None,
        "referral_email": f.referred_email if f else None,
        "amount_cents": r.amount_cents,
        "matieres": r.matiere_keys or [],
        "status": svc.reward_state(r, now),
        "available_at": _iso(r.available_at),
        "credited_at": _iso(r.credited_at),
        "stripe_customer_id": r.stripe_customer_id,
        "stripe_balance_transaction_id": r.stripe_balance_transaction_id,
        "last_error": r.last_error,
        "cancelled_reason": r.cancelled_reason,
        "stripe_checkout_session_id": r.stripe_checkout_session_id,
        "created_at": _iso(r.created_at),
    }


@router.get("/admin/summary")
def admin_summary(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    par_statut = dict(db.execute(
        select(Parrain.status, func.count(Parrain.id)).group_by(Parrain.status)
    ).all())
    now = svc.utcnow()
    totaux = {"pending": 0, "available": 0, "credited": 0, "cancelled": 0}
    for r in db.execute(select(ParrainageRecompense)).scalars():
        totaux[svc.reward_state(r, now)] += r.amount_cents
    return {
        "parrains": {s: par_statut.get(s, 0) for s in svc.STATUTS_PARRAIN},
        "referrals": db.execute(select(func.count(ParrainageFilleul.id))).scalar_one(),
        "rewards": {f"{k}_cents": v for k, v in totaux.items()},
    }


@router.get("/admin/list")
def admin_list(
    status: Literal["active", "suspended"] | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    q = select(Parrain).order_by(desc(Parrain.created_at))
    if status:
        q = q.where(Parrain.status == status)
    return {"items": [_admin_row(db, p) for p in db.execute(q).scalars().all()]}


@router.get("/admin/rewards")
def admin_rewards(
    status: Literal["pending", "available", "credited", "cancelled"] | None = None,
    parrain_id: int | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    """`available` = délai passé mais crédit Stripe pas encore posé (le job
    horaire s'en charge ; last_error explique un échec)."""
    q = select(ParrainageRecompense).order_by(desc(ParrainageRecompense.created_at))
    if parrain_id:
        q = q.where(ParrainageRecompense.parrain_id == parrain_id)
    if status in ("credited", "cancelled"):
        q = q.where(ParrainageRecompense.status == status)
    elif status:
        q = q.where(ParrainageRecompense.status == "pending")
    now = svc.utcnow()
    cache: dict[int, Parrain] = {}
    items = [
        row for row in (_reward_row(db, r, cache, now) for r in db.execute(q).scalars().all())
        if not status or row["status"] == status
    ]
    return {"items": items, "total_cents": sum(i["amount_cents"] for i in items)}


@router.get("/admin/{parrain_id}")
def admin_detail(parrain_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    p = _get_or_404(db, parrain_id)
    row = _admin_row(db, p)
    row["referrals"] = svc.referrals_list(db, p.id, with_email=True)
    return row


@router.post("/admin/{parrain_id}/suspend")
def admin_suspend(parrain_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    """Coupe le lien : plus de nouveau filleul ni de nouvelle récompense.
    Les récompenses déjà enregistrées suivent leur cours."""
    p = _get_or_404(db, parrain_id)
    if p.status != "active":
        raise HTTPException(400, detail="Seul un parrain actif peut être suspendu.")
    p.status = "suspended"
    p.suspended_at = svc.utcnow()
    db.commit()
    return _admin_row(db, p)


@router.post("/admin/{parrain_id}/reactivate")
def admin_reactivate(parrain_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    p = _get_or_404(db, parrain_id)
    if p.status != "suspended":
        raise HTTPException(400, detail="Seul un parrain suspendu peut être réactivé.")
    p.status = "active"
    p.suspended_at = None
    db.commit()
    return _admin_row(db, p)


@router.post("/admin/rewards/{reward_id}/credit")
def admin_credit(reward_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    """Crédite tout de suite sur Stripe, sans attendre le délai ni le job."""
    r = db.get(ParrainageRecompense, reward_id)
    if r is None:
        raise HTTPException(404, detail="Récompense introuvable.")
    if r.status != "pending":
        raise HTTPException(400, detail="Seule une récompense en attente peut être créditée.")
    if not svc.crediter(db, r):
        raise HTTPException(502, detail=r.last_error or "Crédit Stripe impossible.")
    return _reward_row(db, r, {}, svc.utcnow())


class CancelIn(BaseModel):
    reason: str = Field(..., min_length=2, max_length=200)


@router.post("/admin/rewards/{reward_id}/cancel")
def admin_cancel(
    reward_id: int,
    payload: CancelIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    r = db.get(ParrainageRecompense, reward_id)
    if r is None:
        raise HTTPException(404, detail="Récompense introuvable.")
    if r.status != "pending":
        raise HTTPException(400, detail="Seule une récompense non créditée peut être annulée.")
    r.status = "cancelled"
    r.cancelled_reason = payload.reason.strip()
    db.commit()
    return {"ok": True, "id": r.id}
