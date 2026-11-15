"""
Back-office Kitly (comptes Kitly is_admin ; promotion via KITLY_ADMIN_EMAILS).

Distinct de la console NAVIRE : un admin NAVIRE n'a aucun droit ici.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.kitly.models import KitlyPlan, KitlyProfile, KitlyReport, KitlyUser
from app.kitly.plans import DEFAULT_PLANS, plan_out
from app.kitly.queries import clear_popular_cache
from app.kitly.schemas import AdminPlanIn, SuspendIn
from app.kitly.security import admin_user
from app.kitly.utils import iso

router = APIRouter(prefix="/admin", tags=["kitly · admin"], dependencies=[Depends(admin_user)])

KNOWN_LIMITS = set(DEFAULT_PLANS[0]["limits"])


@router.get("/overview")
def overview(db: Session = Depends(get_db)):
    count = lambda q: db.execute(select(func.count()).select_from(q.subquery())).scalar_one()  # noqa: E731
    by_plan = dict(db.execute(select(KitlyUser.plan_code, func.count()).group_by(KitlyUser.plan_code)).all())
    return {
        "users": count(select(KitlyUser.id)),
        "users_by_plan": by_plan,
        "profiles": count(select(KitlyProfile.id)),
        "published_profiles": count(select(KitlyProfile.id).where(KitlyProfile.is_published.is_(True))),
        "open_reports": count(select(KitlyReport.id).where(KitlyReport.status == "open")),
    }


@router.get("/plans")
def admin_plans(db: Session = Depends(get_db)):
    plans = db.execute(select(KitlyPlan).order_by(KitlyPlan.position)).scalars()
    return {"items": [{**plan_out(p), "stripe_price_id": p.stripe_price_id, "is_active": p.is_active} for p in plans],
            "known_limits": sorted(KNOWN_LIMITS)}


@router.patch("/plans/{code}")
def update_plan(code: str, payload: AdminPlanIn, db: Session = Depends(get_db)):
    """Limites, prix affiché, textes : modifiables sans redéploiement."""
    plan = db.get(KitlyPlan, code)
    if not plan:
        raise HTTPException(404, detail="Formule inconnue.")
    data = payload.model_dump(exclude_none=True)
    if "limits" in data:
        unknown = set(data["limits"]) - KNOWN_LIMITS
        if unknown:
            raise HTTPException(422, detail=f"Limites inconnues : {', '.join(sorted(unknown))}.")
        data["limits"] = {**(plan.limits or {}), **data["limits"]}
    for key, value in data.items():
        setattr(plan, key, value)
    db.commit()
    clear_popular_cache()
    return {"plan": {**plan_out(plan), "stripe_price_id": plan.stripe_price_id, "is_active": plan.is_active}}


@router.get("/reports")
def reports(status: str = "open", page: int = Query(1, ge=1), db: Session = Depends(get_db)):
    rows = db.execute(
        select(KitlyReport, KitlyProfile.slug).join(KitlyProfile, KitlyProfile.id == KitlyReport.profile_id)
        .where(KitlyReport.status == status).order_by(KitlyReport.created_at.desc()).offset((page - 1) * 50).limit(50)
    ).all()
    return {"items": [
        {"id": r.id, "profile_id": r.profile_id, "slug": slug, "reason": r.reason, "details": r.details,
         "reporter_email": r.reporter_email, "status": r.status, "created_at": iso(r.created_at)}
        for r, slug in rows
    ]}


@router.patch("/reports/{report_id}")
def update_report(report_id: int, status: str, db: Session = Depends(get_db)):
    if status not in ("open", "reviewed", "dismissed"):
        raise HTTPException(422, detail="Statut inconnu.")
    r = db.get(KitlyReport, report_id)
    if not r:
        raise HTTPException(404, detail="Signalement introuvable.")
    r.status = status
    db.commit()
    return {"ok": True}


@router.post("/profiles/{profile_id}/suspend")
def suspend(profile_id: int, payload: SuspendIn, db: Session = Depends(get_db)):
    p = db.get(KitlyProfile, profile_id)
    if not p:
        raise HTTPException(404, detail="Vitrine introuvable.")
    p.is_suspended = True
    p.suspended_reason = payload.reason
    db.commit()
    clear_popular_cache()
    return {"ok": True}


@router.post("/profiles/{profile_id}/unsuspend")
def unsuspend(profile_id: int, db: Session = Depends(get_db)):
    p = db.get(KitlyProfile, profile_id)
    if not p:
        raise HTTPException(404, detail="Vitrine introuvable.")
    p.is_suspended = False
    p.suspended_reason = ""
    db.commit()
    clear_popular_cache()
    return {"ok": True}
