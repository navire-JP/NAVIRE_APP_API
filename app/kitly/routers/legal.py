"""
« Mes documents juridiques » — identité du vendeur, questionnaire, CGV versionnées.

Rappel (cahier des charges §18) : ces CGV sont celles du FREELANCE envers SES
clients, jamais celles de Kitly. Le générateur automatique arrive au sprint
Juridique ; en attendant, le vendeur peut saisir ou coller ses articles. Le
cycle de vie est déjà définitif :

  brouillon (modifiable) → publiée (immuable, empreinte SHA-256) → remplacée

et une version publiée n'est jamais supprimée : une commande pointe vers la
version acceptée par l'acheteur.
"""

from __future__ import annotations

import hashlib
import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.kitly.legal_rules import QUESTIONNAIRE_VERSION, QUESTIONS, SCOPES, validate_answers
from app.kitly.models import (
    KitlyLegalAnswers,
    KitlyProfile,
    KitlySellerLegalProfile,
    KitlySellerTerms,
    KitlySellerTermsVersion,
)
from app.kitly.routers.vitrine import require_profile
from app.kitly.schemas import LegalAnswersIn, LegalProfileIn, TermsDraftIn, TermsPublishIn
from app.kitly.serializers import TERMS_DISCLAIMER, terms_version_out
from app.kitly.utils import iso, now

router = APIRouter(tags=["kitly · juridique"])


@router.get("/legal/questionnaire")
def questionnaire():
    """Questions par périmètre ; le front n'affiche que celles des types d'offres proposés."""
    return {"version": QUESTIONNAIRE_VERSION, "scopes": QUESTIONS, "disclaimer": TERMS_DISCLAIMER}


# ============================================================
# Identité juridique du vendeur
# ============================================================

def _legal_out(lp: KitlySellerLegalProfile | None) -> dict | None:
    if lp is None:
        return None
    cols = [c.key for c in KitlySellerLegalProfile.__table__.columns if c.key not in ("id", "profile_id")]
    out = {k: getattr(lp, k) for k in cols}
    out["updated_at"] = iso(lp.updated_at)
    return out


@router.get("/me/legal-profile")
def get_legal_profile(profile: KitlyProfile = Depends(require_profile)):
    return {"legal_profile": _legal_out(profile.legal_profile)}


@router.put("/me/legal-profile")
def put_legal_profile(payload: LegalProfileIn, profile: KitlyProfile = Depends(require_profile),
                      db: Session = Depends(get_db)):
    lp = profile.legal_profile
    if lp is None:
        lp = KitlySellerLegalProfile(profile_id=profile.id)
        db.add(lp)
    for key, value in payload.model_dump(exclude_none=True).items():
        setattr(lp, key, value)
    db.commit()
    db.refresh(lp)
    return {"legal_profile": _legal_out(lp)}


# ============================================================
# Questionnaire
# ============================================================

def _answers(db: Session, profile_id: int, scope: str) -> KitlyLegalAnswers | None:
    return db.execute(
        select(KitlyLegalAnswers).where(KitlyLegalAnswers.profile_id == profile_id, KitlyLegalAnswers.scope == scope)
    ).scalar_one_or_none()


def _sells_to_consumers(db: Session, profile_id: int) -> bool:
    general = _answers(db, profile_id, "general")
    return bool((general.answers if general else {}).get("sells_to_consumers", True))


@router.get("/me/legal-answers")
def all_answers(profile: KitlyProfile = Depends(require_profile), db: Session = Depends(get_db)):
    rows = db.execute(select(KitlyLegalAnswers).where(KitlyLegalAnswers.profile_id == profile.id)).scalars()
    return {"version": QUESTIONNAIRE_VERSION,
            "answers": {r.scope: {"answers": r.answers, "updated_at": iso(r.updated_at)} for r in rows}}


@router.put("/me/legal-answers/{scope}")
def put_answers(scope: str, payload: LegalAnswersIn, profile: KitlyProfile = Depends(require_profile),
                db: Session = Depends(get_db)):
    """
    Enregistre les réponses d'un périmètre (fusion avec les précédentes, pour
    l'enregistrement automatique). Refuse (422) toute réponse contraire aux
    droits impératifs des consommateurs.
    """
    if scope not in SCOPES:
        raise HTTPException(404, detail="Périmètre inconnu.")
    row = _answers(db, profile.id, scope)
    merged = {**(row.answers if row else {}), **payload.answers}
    b2c = merged.get("sells_to_consumers", True) if scope == "general" else _sells_to_consumers(db, profile.id)
    clean, errors, warnings = validate_answers(scope, merged, sells_to_consumers=b2c)
    if errors:
        raise HTTPException(422, detail={"message": "Certaines réponses ne sont pas possibles.", "errors": errors,
                                         "warnings": warnings})
    if row is None:
        row = KitlyLegalAnswers(profile_id=profile.id, scope=scope, answers=clean,
                                questionnaire_version=QUESTIONNAIRE_VERSION)
        db.add(row)
    else:
        row.answers = clean
        row.questionnaire_version = QUESTIONNAIRE_VERSION
    db.commit()
    return {"scope": scope, "answers": clean, "warnings": warnings}


# ============================================================
# CGV versionnées
# ============================================================

def _terms(db: Session, profile: KitlyProfile, create: bool = False) -> KitlySellerTerms | None:
    if profile.terms is None and create:
        t = KitlySellerTerms(profile_id=profile.id)
        db.add(t)
        db.flush()
        db.refresh(profile)
    return profile.terms


def _own_version(db: Session, profile: KitlyProfile, version_id: int) -> KitlySellerTermsVersion:
    v = db.get(KitlySellerTermsVersion, version_id)
    if not v or not profile.terms or v.terms_id != profile.terms.id:
        raise HTTPException(404, detail="Version introuvable.")
    return v


def content_hash(articles: list[dict]) -> str:
    canonical = json.dumps(articles, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


@router.get("/me/terms")
def get_terms(profile: KitlyProfile = Depends(require_profile)):
    t = profile.terms
    if not t:
        return {"current": None, "draft": None, "versions": [], "disclaimer": TERMS_DISCLAIMER}
    current = next((v for v in t.versions if v.id == t.current_version_id), None)
    draft = next((v for v in t.versions if v.status == "draft"), None)
    return {
        "current": terms_version_out(current) if current else None,
        "draft": terms_version_out(draft) if draft else None,
        "versions": [terms_version_out(v, with_content=False) for v in reversed(t.versions) if v.status != "draft"],
        "disclaimer": TERMS_DISCLAIMER,
    }


@router.get("/me/terms/versions/{version_id}")
def get_version(version_id: int, profile: KitlyProfile = Depends(require_profile), db: Session = Depends(get_db)):
    return {"version": terms_version_out(_own_version(db, profile, version_id))}


@router.put("/me/terms/draft")
def save_draft(payload: TermsDraftIn, profile: KitlyProfile = Depends(require_profile), db: Session = Depends(get_db)):
    """Crée ou remplace le brouillon (un seul brouillon à la fois)."""
    t = _terms(db, profile, create=True)
    draft = next((v for v in t.versions if v.status == "draft"), None)
    articles = [a.model_dump() for a in payload.articles]
    if len({a["key"] for a in articles}) != len(articles):
        raise HTTPException(422, detail="Deux articles ont la même clé.")
    snapshot = {r.scope: r.answers for r in db.execute(
        select(KitlyLegalAnswers).where(KitlyLegalAnswers.profile_id == profile.id)).scalars()}
    if draft is None:
        number = (db.execute(select(func.max(KitlySellerTermsVersion.number))
                             .where(KitlySellerTermsVersion.terms_id == t.id)).scalar_one() or 0) + 1
        draft = KitlySellerTermsVersion(terms_id=t.id, number=number, status="draft")
        db.add(draft)
    draft.articles = articles
    draft.answers_snapshot = snapshot
    draft.generator_version = "manual"
    draft.content_hash = content_hash(articles)
    db.commit()
    db.refresh(draft)
    return {"draft": terms_version_out(draft)}


@router.delete("/me/terms/draft")
def delete_draft(profile: KitlyProfile = Depends(require_profile), db: Session = Depends(get_db)):
    t = profile.terms
    draft = next((v for v in t.versions if v.status == "draft"), None) if t else None
    if draft is None:
        raise HTTPException(404, detail="Aucun brouillon.")
    db.delete(draft)
    db.commit()
    return {"ok": True}


@router.post("/me/terms/draft/publish")
def publish_draft(payload: TermsPublishIn, profile: KitlyProfile = Depends(require_profile),
                  db: Session = Depends(get_db)):
    """
    Publie le brouillon : il devient la version en vigueur, l'ancienne passe en
    « remplacée » (toujours consultable). L'acceptation du vendeur est datée.
    """
    t = profile.terms
    draft = next((v for v in t.versions if v.status == "draft"), None) if t else None
    if draft is None:
        raise HTTPException(404, detail="Aucun brouillon à publier.")
    for v in t.versions:
        if v.status == "published":
            v.status = "superseded"
    stamp = now()
    draft.status = "published"
    draft.content_hash = content_hash(draft.articles)
    draft.seller_accepted_at = stamp
    draft.published_at = stamp
    draft.effective_at = stamp
    t.current_version_id = draft.id
    db.commit()
    return {"current": terms_version_out(draft)}
