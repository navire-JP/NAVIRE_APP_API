"""
app/routers/ambassadors.py
===========================
Programme ambassadeur (parrainage Prép'AdJuris). Règles et garde-fous :
app/services/ambassadors.py.

Endpoints publics (page /rejoindre, sans compte) :
  GET  /ambassadors/terms               → conditions du programme
  GET  /ambassadors/ref/{code}          → nom affiché de l'ambassadeur du lien
  GET  /ambassadors/embed/{page}        → page d'embed (rejoindre | ambassadeur)

Endpoints utilisateur (auth NAVIRE, page /ambassadeur) :
  GET   /ambassadors/me                 → statut (none | pending | approved | rejected | suspended)
  POST  /ambassadors/apply              → candidature (conditions acceptées)
  PATCH /ambassadors/me                 → nom affiché, téléphone, IBAN
  GET   /ambassadors/me/dashboard       → lien, statistiques, filleuls, gains
  POST  /ambassadors/claim              → rattache le compte connecté à un code

Endpoints console (auth NAVIRE, is_admin) :
  GET  /ambassadors/admin/summary
  GET  /ambassadors/admin/list?status=
  GET  /ambassadors/admin/{id}
  POST /ambassadors/admin/{id}/approve | reject | suspend | reactivate
  GET  /ambassadors/admin/commissions?status=&ambassador_id=
  POST /ambassadors/admin/commissions/mark-paid
  POST /ambassadors/admin/commissions/{id}/cancel

Le rattachement d'un prospect se fait aussi à la création du compte
(/auth/register et /auth/oauth acceptent `referral_code`) et au paiement
(/prepa/adjuris/checkout-session et /prepa/adjuris/checkout acceptent `ref`).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.core.config import FRONTEND_URL
from app.db.database import get_db
from app.db.models import Ambassador, AmbassadorCommission, AmbassadorReferral, User
from app.routers.auth import get_current_user
from app.services import ambassadors as svc
from app.services.email import (
    mail_ambassador_application,
    mail_ambassador_approved,
    mail_ambassador_rejected,
    send_mail,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ambassadors", tags=["ambassadors"])


def require_admin(user: User = Depends(get_current_user)) -> User:
    if not getattr(user, "is_admin", False):
        raise HTTPException(status_code=403, detail="Admin only")
    return user


def _eur(cents: int) -> str:
    return f"{cents / 100:.2f} €".replace(".", ",")


def referral_link(code: str) -> str:
    return f"{FRONTEND_URL}/rejoindre?ref={code}"


def _iso(dt) -> str | None:
    dt = svc.aware(dt)
    return dt.isoformat() if dt else None


# ============================================================
# Conditions du programme
# ============================================================

TERMS = [
    "Le programme porte uniquement sur les inscriptions à Prép'AdJuris.",
    f"Tu gagnes {_eur(svc.COMMISSION_PAR_MATIERE_CENTS)} par matière dont un étudiant venu par ton "
    "lien paie l'inscription. La commission est unique : rien sur les mensualités qui suivent.",
    "Un filleul est rattaché au premier lien qu'il utilise, à la création de son compte NAVIRE "
    "ou au paiement. Un étudiant déjà inscrit à Prép'AdJuris ne compte pas.",
    "Te parrainer toi-même, créer de faux comptes ou faire de la publicité trompeuse entraîne "
    "la suspension du lien et l'annulation des commissions concernées.",
    f"Une commission devient versable {svc.DELAI_DISPONIBLE_JOURS} jours après le paiement "
    "(délai de rétractation). Un paiement remboursé annule sa commission.",
    "Les commissions disponibles sont versées par virement sur l'IBAN que tu renseignes. "
    "Il t'appartient de les déclarer selon ta situation.",
    "NAVIRE peut valider ou refuser une candidature, suspendre un lien et faire évoluer ces "
    "conditions ; les commissions déjà acquises restent dues.",
]


@router.get("/terms")
def get_terms():
    return {
        "version": svc.TERMS_VERSION,
        "commission_par_matiere_cents": svc.COMMISSION_PAR_MATIERE_CENTS,
        "prix_inscription_cents": 2000,
        "delai_disponible_jours": svc.DELAI_DISPONIBLE_JOURS,
        "conditions": TERMS,
    }


# ============================================================
# Page /rejoindre (publique)
# ============================================================

@router.get("/ref/{code}")
def resolve_referral_code(
    code: str,
    track: bool = Query(True, description="Compter la visite dans les statistiques"),
    db: Session = Depends(get_db),
):
    """
    Nom à afficher sur la page d'invitation. Un code inconnu, refusé ou
    suspendu répond valid=false : la page affiche alors l'inscription normale.
    Seul le nom choisi par l'ambassadeur est rendu, jamais son identité.
    """
    amb = svc.find_active(db, code)
    if amb is None:
        return {"valid": False}
    if track:
        amb.clicks = (amb.clicks or 0) + 1
        db.commit()
    return {
        "valid": True,
        "code": amb.code,
        "display_name": amb.display_name,
        "university": amb.university,
    }


# ============================================================
# Pages d'embed servies par l'API
# ============================================================
#
# Une iframe cloisonnée par Framer (origine "null") ne peut lire ni l'adresse
# de la page (?ref=CODE) ni le stockage du navigateur. Servies ici, les pages
# ont une vraie origine : le composant Framer (embeds/framer/NavireEmbed.tsx)
# leur passe le ?ref= de la page, et le jeton survit d'une visite à l'autre.

_EMBEDS_DIR = Path(__file__).resolve().parents[2] / "embeds"
_EMBED_PAGES = {"rejoindre": "Rejoindre NAVIRE", "ambassadeur": "Ambassadeurs NAVIRE"}


@router.get("/embed/{page}", response_class=HTMLResponse, include_in_schema=False)
def embed_page(page: str):
    titre = _EMBED_PAGES.get(page)
    if titre is None:
        raise HTTPException(404, detail="Page inconnue.")
    contenu = (_EMBEDS_DIR / f"{page}.html").read_text(encoding="utf-8")
    # Servie par l'API elle-même : appels relatifs, quel que soit le domaine.
    contenu = contenu.replace('API: "https://navire-app-api.onrender.com"', 'API: ""')
    return HTMLResponse(
        '<!DOCTYPE html><html lang="fr"><head><meta charset="UTF-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>{titre}</title></head>'
        f'<body style="margin:0;background:#0a0a0a">{contenu}</body></html>'
    )


# ============================================================
# Page /ambassadeur (connecté)
# ============================================================

class ApplyIn(BaseModel):
    display_name: str = Field(..., min_length=2, max_length=40)
    prenom: str = Field(..., min_length=1, max_length=80)
    nom: str = Field(..., min_length=1, max_length=80)
    telephone: str | None = Field(None, max_length=30)
    university: str | None = Field(None, max_length=120)
    study_level: str | None = Field(None, max_length=120)
    motivation: str = Field(..., min_length=10, max_length=2000)
    reseaux: str = Field("", max_length=1000)
    iban: str | None = Field(None, max_length=50)
    accept_terms: bool
    terms_version: str | None = None

    @field_validator("iban")
    @classmethod
    def _iban(cls, v: str | None) -> str | None:
        return _clean_iban(v)


class UpdateMeIn(BaseModel):
    display_name: str | None = Field(None, min_length=2, max_length=40)
    telephone: str | None = Field(None, max_length=30)
    iban: str | None = Field(None, max_length=50)

    @field_validator("iban")
    @classmethod
    def _iban(cls, v: str | None) -> str | None:
        return _clean_iban(v)


def _clean_iban(v: str | None) -> str | None:
    if v is None:
        return None
    s = re.sub(r"\s+", "", v).upper()
    if not s:
        return None
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{10,30}", s):
        raise ValueError("IBAN invalide.")
    # Clé de contrôle (ISO 13616, modulo 97).
    num = "".join(str(int(c, 36)) for c in s[4:] + s[:4])
    if int(num) % 97 != 1:
        raise ValueError("IBAN invalide : vérifie les caractères saisis.")
    return s


def _get_mine(db: Session, user: User) -> Ambassador | None:
    return db.execute(select(Ambassador).where(Ambassador.user_id == user.id)).scalar_one_or_none()


def _me_payload(amb: Ambassador | None) -> dict:
    if amb is None:
        return {"status": "none", "terms_version": svc.TERMS_VERSION}
    out = {
        "status": amb.status,
        "terms_version": svc.TERMS_VERSION,
        "display_name": amb.display_name,
        "prenom": amb.prenom,
        "nom": amb.nom,
        "telephone": amb.telephone,
        "university": amb.university,
        "study_level": amb.study_level,
        "iban_masked": svc.mask_iban(amb.iban),
        "applied_at": _iso(amb.applied_at),
        "decided_at": _iso(amb.decided_at),
    }
    if amb.status == "rejected":
        out["rejection_reason"] = amb.rejection_reason
    if amb.status == "approved" and amb.code:
        out["code"] = amb.code
        out["link"] = referral_link(amb.code)
    return out


@router.get("/me")
def get_me(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _me_payload(_get_mine(db, user))


@router.post("/apply")
def apply(payload: ApplyIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if not payload.accept_terms:
        raise HTTPException(400, detail="Tu dois accepter les conditions du programme.")

    amb = _get_mine(db, user)
    if amb is not None and amb.status != "rejected":
        messages = {
            "pending": "Ta candidature est déjà en cours d'examen.",
            "approved": "Tu es déjà ambassadeur.",
            "suspended": "Ton lien ambassadeur est suspendu : contacte l'équipe.",
        }
        raise HTTPException(400, detail={"code": "ALREADY_APPLIED", "message": messages[amb.status]})

    if amb is None:
        amb = Ambassador(user_id=user.id, display_name=payload.display_name.strip(), prenom="", nom="")
        db.add(amb)

    amb.display_name = payload.display_name.strip()
    amb.prenom = payload.prenom.strip()
    amb.nom = payload.nom.strip()
    amb.telephone = (payload.telephone or "").strip() or None
    amb.university = (payload.university or user.university or "").strip() or None
    amb.study_level = (payload.study_level or user.study_level or "").strip() or None
    amb.motivation = payload.motivation.strip()
    amb.reseaux = payload.reseaux.strip()
    if payload.iban:
        amb.iban = payload.iban
    amb.status = "pending"
    amb.terms_version = svc.TERMS_VERSION
    amb.terms_accepted_at = svc.utcnow()
    amb.applied_at = svc.utcnow()
    amb.decided_at = None
    amb.decided_by_id = None
    amb.rejection_reason = None
    db.commit()
    db.refresh(amb)

    try:
        send_mail(user.email, *mail_ambassador_application(amb.prenom))
    except Exception:  # noqa: BLE001 — l'email ne bloque jamais la candidature
        logger.exception("Email de candidature ambassadeur non envoyé (user %s)", user.id)

    return _me_payload(amb)


@router.patch("/me")
def update_me(payload: UpdateMeIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    amb = _get_mine(db, user)
    if amb is None:
        raise HTTPException(404, detail="Aucune candidature ambassadeur.")
    if payload.display_name is not None:
        amb.display_name = payload.display_name.strip()
    if payload.telephone is not None:
        amb.telephone = payload.telephone.strip() or None
    if payload.iban is not None:
        amb.iban = payload.iban
    db.commit()
    return _me_payload(amb)


@router.get("/me/dashboard")
def dashboard(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    amb = _get_mine(db, user)
    if amb is None or amb.status not in ("approved", "suspended"):
        raise HTTPException(403, detail="Réservé aux ambassadeurs validés.")
    return {
        "ambassador": _me_payload(amb),
        "commission_par_matiere_cents": svc.COMMISSION_PAR_MATIERE_CENTS,
        "prix_inscription_cents": 2000,
        "stats": svc.stats(db, amb),
        "earnings": svc.earnings(db, amb.id),
        "referrals": svc.referrals_list(db, amb.id),
    }


class ClaimIn(BaseModel):
    code: str = Field(..., max_length=40)


@router.post("/claim")
def claim(payload: ClaimIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """
    Page /rejoindre ouverte par quelqu'un qui a déjà un compte : il se connecte
    puis l'embed rattache le compte au code. Renvoie attached=false quand le
    rattachement n'a pas lieu (déjà filleul d'un autre, déjà client, code
    invalide, auto-parrainage) — la page continue normalement.
    """
    ref = svc.attach_referral(db, payload.code, user=user, source="claim")
    amb = db.get(Ambassador, ref.ambassador_id) if ref else None
    mine = amb is not None and svc.normalize_code(payload.code) == amb.code
    return {"attached": mine, "display_name": amb.display_name if mine else None}


# ============================================================
# Console
# ============================================================

def _admin_row(db: Session, amb: Ambassador) -> dict:
    u = db.get(User, amb.user_id)
    return {
        "id": amb.id,
        "user_id": amb.user_id,
        "username": u.username if u else None,
        "email": u.email if u else None,
        "status": amb.status,
        "code": amb.code,
        "link": referral_link(amb.code) if amb.code else None,
        "display_name": amb.display_name,
        "prenom": amb.prenom,
        "nom": amb.nom,
        "telephone": amb.telephone,
        "university": amb.university,
        "study_level": amb.study_level,
        "motivation": amb.motivation,
        "reseaux": amb.reseaux,
        "iban": amb.iban,
        "terms_version": amb.terms_version,
        "applied_at": _iso(amb.applied_at),
        "decided_at": _iso(amb.decided_at),
        "rejection_reason": amb.rejection_reason,
        "stats": svc.stats(db, amb),
        "earnings": svc.earnings(db, amb.id),
    }


def _get_or_404(db: Session, ambassador_id: int) -> Ambassador:
    amb = db.get(Ambassador, ambassador_id)
    if amb is None:
        raise HTTPException(404, detail="Ambassadeur introuvable.")
    return amb


@router.get("/admin/summary")
def admin_summary(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    par_statut = dict(db.execute(
        select(Ambassador.status, func.count(Ambassador.id)).group_by(Ambassador.status)
    ).all())
    now = svc.utcnow()
    due = {"pending": 0, "available": 0, "paid": 0, "cancelled": 0}
    for c in db.execute(select(AmbassadorCommission)).scalars():
        due[svc.commission_state(c, now)] += c.amount_cents
    return {
        "ambassadors": {s: par_statut.get(s, 0) for s in svc.STATUTS_AMBASSADEUR},
        "referrals": db.execute(select(func.count(AmbassadorReferral.id))).scalar_one(),
        "commissions": {f"{k}_cents": v for k, v in due.items()},
    }


@router.get("/admin/list")
def admin_list(
    status: Literal["pending", "approved", "rejected", "suspended"] | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    q = select(Ambassador).order_by(desc(Ambassador.applied_at))
    if status:
        q = q.where(Ambassador.status == status)
    return {"items": [_admin_row(db, a) for a in db.execute(q).scalars().all()]}


@router.get("/admin/commissions")
def admin_commissions(
    status: Literal["pending", "available", "paid", "cancelled"] | None = None,
    ambassador_id: int | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    """`available` = due et versable maintenant ; `pending` = due mais encore
    dans le délai de rétractation."""
    q = select(AmbassadorCommission).order_by(desc(AmbassadorCommission.created_at))
    if ambassador_id:
        q = q.where(AmbassadorCommission.ambassador_id == ambassador_id)
    if status in ("paid", "cancelled"):
        q = q.where(AmbassadorCommission.status == status)
    elif status:
        q = q.where(AmbassadorCommission.status == "pending")
    now = svc.utcnow()
    noms: dict[int, Ambassador] = {}
    items = []
    for c in db.execute(q).scalars().all():
        etat = svc.commission_state(c, now)
        if status and etat != status:
            continue
        amb = noms.setdefault(c.ambassador_id, db.get(Ambassador, c.ambassador_id))
        ref = c.referral
        items.append({
            "id": c.id,
            "ambassador_id": c.ambassador_id,
            "ambassador": f"{amb.prenom} {amb.nom}".strip() if amb else None,
            "ambassador_code": amb.code if amb else None,
            "iban": amb.iban if amb else None,
            "referral_email": ref.referred_email if ref else None,
            "amount_cents": c.amount_cents,
            "matieres": c.matiere_keys or [],
            "status": etat,
            "available_at": _iso(c.available_at),
            "paid_at": _iso(c.paid_at),
            "paid_reference": c.paid_reference,
            "cancelled_reason": c.cancelled_reason,
            "stripe_checkout_session_id": c.stripe_checkout_session_id,
            "created_at": _iso(c.created_at),
        })
    return {"items": items, "total_cents": sum(i["amount_cents"] for i in items)}


@router.get("/admin/{ambassador_id}")
def admin_detail(ambassador_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    amb = _get_or_404(db, ambassador_id)
    row = _admin_row(db, amb)
    row["referrals"] = svc.referrals_list(db, amb.id, with_email=True)
    return row


class RejectIn(BaseModel):
    reason: str | None = Field(None, max_length=500)


@router.post("/admin/{ambassador_id}/approve")
def admin_approve(ambassador_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    amb = _get_or_404(db, ambassador_id)
    if amb.status == "approved":
        return _admin_row(db, amb)
    amb.status = "approved"
    if not amb.code:
        amb.code = svc.generate_code(db, amb.display_name)
    amb.decided_at = svc.utcnow()
    amb.decided_by_id = admin.id
    amb.rejection_reason = None
    db.commit()

    u = db.get(User, amb.user_id)
    if u:
        try:
            send_mail(u.email, *mail_ambassador_approved(
                amb.prenom, referral_link(amb.code), _eur(svc.COMMISSION_PAR_MATIERE_CENTS)
            ))
        except Exception:  # noqa: BLE001
            logger.exception("Email de validation ambassadeur non envoyé (%s)", amb.id)
    return _admin_row(db, amb)


@router.post("/admin/{ambassador_id}/reject")
def admin_reject(
    ambassador_id: int,
    payload: RejectIn,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    amb = _get_or_404(db, ambassador_id)
    if amb.status != "pending":
        raise HTTPException(400, detail="Seule une candidature en attente peut être refusée.")
    amb.status = "rejected"
    amb.decided_at = svc.utcnow()
    amb.decided_by_id = admin.id
    amb.rejection_reason = (payload.reason or "").strip() or None
    db.commit()

    u = db.get(User, amb.user_id)
    if u:
        try:
            send_mail(u.email, *mail_ambassador_rejected(amb.prenom, amb.rejection_reason))
        except Exception:  # noqa: BLE001
            logger.exception("Email de refus ambassadeur non envoyé (%s)", amb.id)
    return _admin_row(db, amb)


@router.post("/admin/{ambassador_id}/suspend")
def admin_suspend(ambassador_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    """Coupe le lien : plus de nouveau filleul ni de nouvelle commission.
    Les commissions déjà acquises restent dues."""
    amb = _get_or_404(db, ambassador_id)
    if amb.status != "approved":
        raise HTTPException(400, detail="Seul un ambassadeur actif peut être suspendu.")
    amb.status = "suspended"
    amb.decided_at = svc.utcnow()
    amb.decided_by_id = admin.id
    db.commit()
    return _admin_row(db, amb)


@router.post("/admin/{ambassador_id}/reactivate")
def admin_reactivate(ambassador_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    amb = _get_or_404(db, ambassador_id)
    if amb.status != "suspended":
        raise HTTPException(400, detail="Seul un ambassadeur suspendu peut être réactivé.")
    amb.status = "approved"
    amb.decided_at = svc.utcnow()
    amb.decided_by_id = admin.id
    db.commit()
    return _admin_row(db, amb)


class MarkPaidIn(BaseModel):
    ids: list[int] = Field(..., min_length=1, max_length=500)
    reference: str | None = Field(None, max_length=120)
    force: bool = Field(False, description="Verser aussi une commission encore dans le délai de rétractation")


@router.post("/admin/commissions/mark-paid")
def admin_mark_paid(payload: MarkPaidIn, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    """À appeler après le virement. Seules les commissions dues sont marquées ;
    celles encore dans le délai de rétractation demandent force=true."""
    now = svc.utcnow()
    rows = db.execute(
        select(AmbassadorCommission).where(AmbassadorCommission.id.in_(payload.ids))
    ).scalars().all()
    marquees, ignorees = [], []
    for c in rows:
        etat = svc.commission_state(c, now)
        if etat == "available" or (etat == "pending" and payload.force):
            c.status = "paid"
            c.paid_at = now
            c.paid_reference = (payload.reference or "").strip() or None
            marquees.append(c.id)
        else:
            ignorees.append({"id": c.id, "status": etat})
    db.commit()
    return {
        "paid": marquees,
        "skipped": ignorees,
        "total_cents": sum(c.amount_cents for c in rows if c.id in marquees),
    }


class CancelIn(BaseModel):
    reason: str = Field(..., min_length=2, max_length=200)


@router.post("/admin/commissions/{commission_id}/cancel")
def admin_cancel_commission(
    commission_id: int,
    payload: CancelIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    c = db.get(AmbassadorCommission, commission_id)
    if c is None:
        raise HTTPException(404, detail="Commission introuvable.")
    if c.status != "pending":
        raise HTTPException(400, detail="Seule une commission non versée peut être annulée.")
    c.status = "cancelled"
    c.cancelled_reason = payload.reason.strip()
    db.commit()
    return {"ok": True, "id": c.id}
