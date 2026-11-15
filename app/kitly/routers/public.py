"""
Embed 2 — Page profil publique (kitly.fr/<slug>) : vitrine, fiche offre,
fiche projet, CGV du vendeur, formulaire de contact, signalement.

Ces routes ne demandent aucune connexion et ne renvoient que des données
publiques (serializers.*_public).
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.kitly import emails
from app.kitly.models import (
    KitlyLead,
    KitlyOffer,
    KitlyPortfolioProject,
    KitlyProfile,
    KitlyProfileView,
    KitlyReport,
    KitlySellerTermsVersion,
    KitlyUser,
)
from app.kitly.plans import get_plan
from app.kitly.queries import get_public_profile, public_profiles, published_offers
from app.kitly.schemas import LeadIn, ReportIn, ViewIn
from app.kitly.security import optional_user
from app.kitly.serializers import offer_public, portfolio_public, profile_public, terms_version_out
from app.kitly.utils import client_ip, looks_like_bot, rate_guard, rate_hit, today, visitor_hash

router = APIRouter(prefix="/public", tags=["kitly · public"])


def _profile_or_404(db: Session, slug: str) -> KitlyProfile:
    profile = get_public_profile(db, slug)
    if not profile:
        raise HTTPException(404, detail="Cette vitrine n'existe pas ou n'est pas publiée.")
    return profile


def _has_terms(profile: KitlyProfile) -> bool:
    return bool(profile.terms and profile.terms.current_version_id)


@router.get("/profiles/{slug}")
def get_vitrine(slug: str, db: Session = Depends(get_db)):
    profile = _profile_or_404(db, slug)
    limits = get_plan(db, profile.user.plan_code).limits or {}
    offers = published_offers(db, profile.id)
    portfolio = [p for p in profile.portfolio if p.is_visible]
    return profile_public(
        profile, offers, portfolio,
        show_branding=not limits.get("remove_branding", False),
        has_terms=_has_terms(profile),
    )


@router.get("/profiles/{slug}/offers/{offer_id}")
def get_offer(slug: str, offer_id: int, db: Session = Depends(get_db)):
    profile = _profile_or_404(db, slug)
    offer = db.get(KitlyOffer, offer_id)
    if not offer or offer.profile_id != profile.id or offer.status != "published":
        raise HTTPException(404, detail="Offre introuvable.")
    return {"offer": offer_public(offer), "seller": {"slug": profile.slug, "display_name": profile.display_name,
                                                     "avatar_url": profile.avatar_url}, "has_terms": _has_terms(profile)}


@router.get("/profiles/{slug}/portfolio/{project_id}")
def get_project(slug: str, project_id: int, db: Session = Depends(get_db)):
    profile = _profile_or_404(db, slug)
    p = db.get(KitlyPortfolioProject, project_id)
    if not p or p.profile_id != profile.id or not p.is_visible:
        raise HTTPException(404, detail="Projet introuvable.")
    return {"project": portfolio_public(p)}


@router.get("/profiles/{slug}/terms")
def get_terms(slug: str, version: int | None = None, db: Session = Depends(get_db)):
    """CGV du vendeur : version en vigueur, ou une version antérieure publiée (?version=N)."""
    profile = _profile_or_404(db, slug)
    if not profile.terms:
        raise HTTPException(404, detail="Ce vendeur n'a pas encore publié ses CGV.")
    q = select(KitlySellerTermsVersion).where(KitlySellerTermsVersion.terms_id == profile.terms.id)
    if version is None:
        q = q.where(KitlySellerTermsVersion.id == profile.terms.current_version_id)
    else:
        q = q.where(KitlySellerTermsVersion.number == version,
                    KitlySellerTermsVersion.status.in_(("published", "superseded")))
    v = db.execute(q).scalar_one_or_none()
    if not v:
        raise HTTPException(404, detail="Version de CGV introuvable.")
    return {"seller": profile.display_name, "terms": terms_version_out(v)}


@router.post("/profiles/{slug}/view", status_code=202)
def record_view(
    slug: str, payload: ViewIn, request: Request,
    viewer: KitlyUser | None = Depends(optional_user),
    db: Session = Depends(get_db),
):
    """
    Enregistre une visite unique (au plus une par visiteur et par jour). Les
    robots et le propriétaire de la vitrine ne sont pas comptés.
    """
    profile = _profile_or_404(db, slug)
    if looks_like_bot(request) or (viewer and viewer.id == profile.user_id):
        return {"counted": False}
    day = today()
    vh = visitor_hash(request, day)
    exists = db.execute(
        select(KitlyProfileView.id).where(
            KitlyProfileView.profile_id == profile.id, KitlyProfileView.visitor_hash == vh, KitlyProfileView.day == day
        )
    ).first()
    if exists:
        return {"counted": False}
    db.add(KitlyProfileView(profile_id=profile.id, visitor_hash=vh, day=day, source=payload.source))
    try:
        db.commit()
    except IntegrityError:  # deux onglets en même temps
        db.rollback()
        return {"counted": False}
    return {"counted": True}


@router.post("/profiles/{slug}/leads", status_code=201)
def create_lead(
    slug: str, payload: LeadIn, request: Request, background: BackgroundTasks, db: Session = Depends(get_db),
):
    """Formulaire de contact / demande de devis / demande de rendez-vous."""
    profile = _profile_or_404(db, slug)
    ip = client_ip(request)
    rate_guard(db, f"lead:ip:{ip}", 8, 3600, "Trop de messages envoyés, réessaie plus tard.")
    rate_guard(db, f"lead:ip:{ip}:p{profile.id}", 3, 3600, "Tu as déjà contacté ce professionnel, il va te répondre.")
    rate_hit(db, f"lead:ip:{ip}")
    rate_hit(db, f"lead:ip:{ip}:p{profile.id}")

    if payload.website:  # pot de miel rempli : robot. Réponse identique, rien n'est enregistré.
        return {"ok": True}

    offer_title = None
    if payload.offer_id is not None:
        offer = db.get(KitlyOffer, payload.offer_id)
        if not offer or offer.profile_id != profile.id or offer.status != "published":
            raise HTTPException(422, detail="Offre introuvable.")
        offer_title = offer.title

    lead = KitlyLead(
        profile_id=profile.id, offer_id=payload.offer_id, kind=payload.kind, name=payload.name,
        email=str(payload.email).lower(), phone=payload.phone, message=payload.message, budget=payload.budget,
        source=payload.source,
    )
    db.add(lead)
    db.commit()

    to = profile.contact_email or profile.user.email
    subject, html = emails.mail_new_lead(profile.user.first_name, payload.kind, payload.name,
                                         str(payload.email), payload.message, offer_title)
    background.add_task(emails.send, to, subject, html, str(payload.email))
    return {"ok": True}


@router.post("/profiles/{slug}/report", status_code=201)
def report_profile(slug: str, payload: ReportIn, request: Request, db: Session = Depends(get_db)):
    """Signalement d'un contenu (mécanisme de notification exigé par le DSA)."""
    profile = _profile_or_404(db, slug)
    key = f"report:ip:{client_ip(request)}"
    rate_guard(db, key, 5, 3600, "Trop de signalements, réessaie plus tard.")
    rate_hit(db, key)
    db.add(KitlyReport(profile_id=profile.id, reason=payload.reason, details=payload.details,
                       reporter_email=str(payload.email or "")))
    db.commit()
    return {"ok": True, "message": "Merci, l'équipe Kitly va examiner ce signalement."}


@router.get("/domains/{host}")
def resolve_domain(host: str, db: Session = Depends(get_db)):
    """Domaine personnalisé → slug de la vitrine (si la formule l'inclut toujours)."""
    host = host.strip().lower()
    profile = db.execute(public_profiles().where(KitlyProfile.custom_domain == host)).scalar_one_or_none()
    if not profile or not (get_plan(db, profile.user.plan_code).limits or {}).get("custom_domain"):
        raise HTTPException(404, detail="Domaine inconnu.")
    return {"slug": profile.slug}
