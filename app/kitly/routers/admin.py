"""
Back-office Kitly (comptes Kitly is_admin ; promotion via KITLY_ADMIN_EMAILS).

Distinct de la console NAVIRE : un admin NAVIRE n'a aucun droit ici, et un
admin Kitly n'en a aucun chez NAVIRE.

Toutes les actions qui modifient quelque chose sont inscrites au journal
(kitly_admin_actions) : qui, quoi, sur quoi, quand.
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.kitly import emails
from app.kitly.config import (
    KITLY_CONTACT_EMAIL,
    KITLY_CORS_ORIGINS,
    KITLY_FRONTEND_URL,
    KITLY_STRIPE_PRICES,
    KITLY_STRIPE_SECRET_KEY,
    missing_env,
)
from app.kitly.models import (
    KitlyAdminAction,
    KitlyConnectedAccount,
    KitlyLead,
    KitlyOffer,
    KitlyOrder,
    KitlyPlan,
    KitlyPlanGrant,
    KitlyProfile,
    KitlyProfileView,
    KitlyReport,
    KitlySubscription,
    KitlyUser,
)
from app.kitly.plans import DEFAULT_PLANS, plan_out
from app.kitly.queries import clear_popular_cache
from app.kitly.schemas import AdminDeleteIn, AdminPlanIn, EmailTestIn, GrantIn, ReasonIn, SuspendIn
from app.kitly.security import admin_user, create_token
from app.kitly.serializers import public_url, user_out
from app.kitly.utils import aware, iso, now, today

router = APIRouter(prefix="/admin", tags=["kitly · admin"], dependencies=[Depends(admin_user)])

KNOWN_LIMITS = set(DEFAULT_PLANS[0]["limits"])
LIVE = ("active", "trialing", "past_due")
IMPERSONATION_MINUTES = 60


def _count(db: Session, q) -> int:
    return db.execute(select(func.count()).select_from(q.subquery())).scalar_one()


def log(db: Session, admin: KitlyUser, action: str, target_type: str = "", target_id="", **details) -> None:
    db.add(KitlyAdminAction(admin_id=admin.id, admin_email=admin.email, action=action,
                            target_type=target_type, target_id=str(target_id), details=details))


def _user_or_404(db: Session, user_id: int) -> KitlyUser:
    u = db.get(KitlyUser, user_id)
    if not u:
        raise HTTPException(404, detail="Compte introuvable.")
    return u


def _active_grant(db: Session, user_id: int) -> KitlyPlanGrant | None:
    for g in db.execute(select(KitlyPlanGrant).where(KitlyPlanGrant.user_id == user_id, KitlyPlanGrant.revoked_at.is_(None))
                        .order_by(KitlyPlanGrant.created_at.desc())).scalars():
        if g.expires_at is None or aware(g.expires_at) > now():
            return g
    return None


def _grant_out(g: KitlyPlanGrant | None) -> dict | None:
    return None if g is None else {"plan": g.plan_code, "reason": g.reason, "expires_at": iso(g.expires_at),
                                   "created_at": iso(g.created_at)}


# ============================================================
# Vue d'ensemble
# ============================================================

@router.get("/overview")
def overview(db: Session = Depends(get_db)):
    d7, d30 = now() - timedelta(days=7), now() - timedelta(days=30)
    by_plan = dict(db.execute(select(KitlyUser.plan_code, func.count()).group_by(KitlyUser.plan_code)).all())
    prices = {p.code: p.price_cents for p in db.execute(select(KitlyPlan)).scalars()}
    live_subs = db.execute(select(KitlySubscription.plan_code).where(KitlySubscription.status.in_(LIVE))).scalars().all()
    return {
        "users": _count(db, select(KitlyUser.id)),
        "new_users_7d": _count(db, select(KitlyUser.id).where(KitlyUser.created_at >= d7)),
        "new_users_30d": _count(db, select(KitlyUser.id).where(KitlyUser.created_at >= d30)),
        "verified_users": _count(db, select(KitlyUser.id).where(KitlyUser.email_verified_at.is_not(None))),
        "users_by_plan": by_plan,
        "profiles": _count(db, select(KitlyProfile.id)),
        "published_profiles": _count(db, select(KitlyProfile.id).where(KitlyProfile.is_published.is_(True))),
        "suspended_profiles": _count(db, select(KitlyProfile.id).where(KitlyProfile.is_suspended.is_(True))),
        "offers": _count(db, select(KitlyOffer.id).where(KitlyOffer.status == "published")),
        "active_subscriptions": len(live_subs),
        "mrr_cents": sum(prices.get(c, 0) for c in live_subs),
        "active_grants": _count(db, select(KitlyPlanGrant.id).where(KitlyPlanGrant.revoked_at.is_(None))),
        "leads_30d": _count(db, select(KitlyLead.id).where(KitlyLead.created_at >= d30, KitlyLead.status != "spam")),
        "views_30d": _count(db, select(KitlyProfileView.id).where(KitlyProfileView.day >= today() - timedelta(days=29))),
        "open_reports": _count(db, select(KitlyReport.id).where(KitlyReport.status == "open")),
        "connected_accounts_active": _count(db, select(KitlyConnectedAccount.id).where(KitlyConnectedAccount.charges_enabled.is_(True))),
        "orders_30d": _count(db, select(KitlyOrder.id).where(KitlyOrder.status == "paid", KitlyOrder.paid_at >= d30)),
        "sales_30d_cents": db.execute(select(func.coalesce(func.sum(KitlyOrder.amount_cents), 0))
                                      .where(KitlyOrder.status == "paid", KitlyOrder.paid_at >= d30)).scalar_one(),
        "commission_30d_cents": db.execute(select(func.coalesce(func.sum(KitlyOrder.commission_cents), 0))
                                           .where(KitlyOrder.status == "paid", KitlyOrder.paid_at >= d30)).scalar_one(),
    }


# ============================================================
# Comptes
# ============================================================

@router.get("/users")
def list_users(q: str = "", plan: str = "", page: int = Query(1, ge=1), page_size: int = Query(30, ge=1, le=100),
               db: Session = Depends(get_db)):
    query = select(KitlyUser).outerjoin(KitlyProfile, KitlyProfile.user_id == KitlyUser.id)
    if q.strip():
        t = f"%{q.strip().lower()}%"
        query = query.where(or_(func.lower(KitlyUser.email).like(t), func.lower(KitlyUser.first_name).like(t),
                                func.lower(KitlyUser.last_name).like(t), func.lower(KitlyProfile.slug).like(t),
                                func.lower(KitlyProfile.display_name).like(t)))
    if plan:
        query = query.where(KitlyUser.plan_code == plan)
    total = _count(db, query.with_only_columns(KitlyUser.id))
    rows = db.execute(query.order_by(KitlyUser.created_at.desc()).offset((page - 1) * page_size).limit(page_size)).scalars().all()
    items = []
    for u in rows:
        p = u.profile
        items.append({
            **user_out(u),
            "is_active": u.is_active,
            "last_login_at": iso(u.last_login_at),
            "profile": None if not p else {"id": p.id, "slug": p.slug, "display_name": p.display_name,
                                          "is_published": p.is_published, "is_suspended": p.is_suspended},
            "has_subscription": any(s.status in LIVE for s in u.subscriptions),
            "grant": _grant_out(_active_grant(db, u.id)),
        })
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/users/{user_id}")
def user_detail(user_id: int, db: Session = Depends(get_db)):
    u = _user_or_404(db, user_id)
    p = u.profile
    acc = u.connected_account
    actions = db.execute(select(KitlyAdminAction).where(KitlyAdminAction.target_type == "user",
                                                        KitlyAdminAction.target_id == str(u.id))
                         .order_by(KitlyAdminAction.created_at.desc()).limit(20)).scalars().all()
    return {
        "user": {**user_out(u), "is_active": u.is_active, "last_login_at": iso(u.last_login_at),
                 "cgu_version": u.cgu_version, "cgu_accepted_at": iso(u.cgu_accepted_at),
                 "stripe_customer_id": u.stripe_customer_id},
        "profile": None if not p else {
            "id": p.id, "slug": p.slug, "url": public_url(p), "display_name": p.display_name, "headline": p.headline,
            "is_published": p.is_published, "is_suspended": p.is_suspended, "suspended_reason": p.suspended_reason,
            "marketplace_opt_in": p.marketplace_opt_in, "promoted": p.promoted,
            "offers": _count(db, select(KitlyOffer.id).where(KitlyOffer.profile_id == p.id)),
            "leads": _count(db, select(KitlyLead.id).where(KitlyLead.profile_id == p.id)),
            "views_30d": _count(db, select(KitlyProfileView.id).where(KitlyProfileView.profile_id == p.id,
                                                                      KitlyProfileView.day >= today() - timedelta(days=29))),
        },
        "subscriptions": [{"plan": s.plan_code, "status": s.status, "period_end": iso(s.current_period_end),
                           "cancel_at_period_end": s.cancel_at_period_end, "stripe_id": s.stripe_subscription_id}
                          for s in u.subscriptions],
        "grant": _grant_out(_active_grant(db, u.id)),
        "connect": None if not acc else {"charges_enabled": acc.charges_enabled, "payouts_enabled": acc.payouts_enabled,
                                         "details_submitted": acc.details_submitted, "requirements_due": acc.requirements_due},
        "actions": [{"action": a.action, "admin": a.admin_email, "details": a.details, "at": iso(a.created_at)} for a in actions],
    }


@router.post("/users/{user_id}/grant")
def grant_plan(user_id: int, payload: GrantIn, admin: KitlyUser = Depends(admin_user), db: Session = Depends(get_db)):
    """Offre une formule (sans paiement), avec ou sans date de fin."""
    from app.kitly.routers.billing import recompute_plan

    u = _user_or_404(db, user_id)
    for g in db.execute(select(KitlyPlanGrant).where(KitlyPlanGrant.user_id == u.id, KitlyPlanGrant.revoked_at.is_(None))).scalars():
        g.revoked_at = now()
    expires = now() + timedelta(days=payload.days) if payload.days else None
    db.add(KitlyPlanGrant(user_id=u.id, plan_code=payload.plan, reason=payload.reason, granted_by=admin.id, expires_at=expires))
    db.flush()
    recompute_plan(db, u)
    log(db, admin, "grant_plan", "user", u.id, plan=payload.plan, reason=payload.reason, days=payload.days)
    db.commit()
    clear_popular_cache()
    return {"plan": u.plan_code, "grant": _grant_out(_active_grant(db, u.id))}


@router.delete("/users/{user_id}/grant")
def revoke_grant(user_id: int, admin: KitlyUser = Depends(admin_user), db: Session = Depends(get_db)):
    from app.kitly.routers.billing import recompute_plan

    u = _user_or_404(db, user_id)
    for g in db.execute(select(KitlyPlanGrant).where(KitlyPlanGrant.user_id == u.id, KitlyPlanGrant.revoked_at.is_(None))).scalars():
        g.revoked_at = now()
    db.flush()
    recompute_plan(db, u)
    log(db, admin, "revoke_grant", "user", u.id)
    db.commit()
    clear_popular_cache()
    return {"plan": u.plan_code}


@router.post("/users/{user_id}/verify-email")
def verify_email(user_id: int, admin: KitlyUser = Depends(admin_user), db: Session = Depends(get_db)):
    u = _user_or_404(db, user_id)
    u.email_verified_at = u.email_verified_at or now()
    log(db, admin, "verify_email", "user", u.id)
    db.commit()
    return {"user": user_out(u)}


@router.post("/users/{user_id}/deactivate")
def deactivate(user_id: int, payload: ReasonIn, admin: KitlyUser = Depends(admin_user), db: Session = Depends(get_db)):
    """Bloque la connexion et masque la vitrine (sans rien supprimer)."""
    u = _user_or_404(db, user_id)
    if u.id == admin.id:
        raise HTTPException(400, detail="Tu ne peux pas désactiver ton propre compte.")
    u.is_active = False
    u.token_version += 1
    log(db, admin, "deactivate", "user", u.id, reason=payload.reason)
    db.commit()
    clear_popular_cache()
    return {"ok": True}


@router.post("/users/{user_id}/reactivate")
def reactivate(user_id: int, admin: KitlyUser = Depends(admin_user), db: Session = Depends(get_db)):
    u = _user_or_404(db, user_id)
    u.is_active = True
    log(db, admin, "reactivate", "user", u.id)
    db.commit()
    clear_popular_cache()
    return {"ok": True}


@router.post("/users/{user_id}/logout-all")
def logout_all(user_id: int, admin: KitlyUser = Depends(admin_user), db: Session = Depends(get_db)):
    u = _user_or_404(db, user_id)
    u.token_version += 1
    log(db, admin, "logout_all", "user", u.id)
    db.commit()
    return {"ok": True}


@router.post("/users/{user_id}/impersonate")
def impersonate(user_id: int, admin: KitlyUser = Depends(admin_user), db: Session = Depends(get_db)):
    """
    Session courte (1 h) au nom du compte, pour l'aider ou corriger sa vitrine.
    Paiements, mot de passe et suppression restent interdits dans cette session.
    """
    u = _user_or_404(db, user_id)
    if not u.is_active:
        raise HTTPException(400, detail="Compte désactivé : réactive-le d'abord.")
    log(db, admin, "impersonate", "user", u.id)
    db.commit()
    return {"token": create_token(u, ttl_minutes=IMPERSONATION_MINUTES, impersonator=admin),
            "user": user_out(u), "expires_in_minutes": IMPERSONATION_MINUTES}


@router.delete("/users/{user_id}")
def delete_user(user_id: int, payload: AdminDeleteIn, admin: KitlyUser = Depends(admin_user), db: Session = Depends(get_db)):
    """Suppression définitive : abonnement Stripe résilié, puis compte, vitrine, offres et demandes effacés."""
    u = _user_or_404(db, user_id)
    if u.id == admin.id:
        raise HTTPException(400, detail="Tu ne peux pas supprimer ton propre compte depuis la console.")
    live = [s for s in u.subscriptions if s.status in LIVE + ("unpaid", "incomplete")]
    if live:
        from app.kitly.stripe_client import stripe_client

        for s in live:
            try:
                stripe_client().subscriptions.cancel(s.stripe_subscription_id)
            except Exception as exc:
                raise HTTPException(502, detail=f"Résiliation Stripe impossible, rien n'a été supprimé : {exc}")
    log(db, admin, "delete_user", "user", u.id, email=u.email, reason=payload.reason)
    db.delete(u)
    db.commit()
    clear_popular_cache()
    return {"ok": True}


# ============================================================
# Vitrines
# ============================================================

@router.get("/profiles")
def list_profiles(q: str = "", status: str = "", page: int = Query(1, ge=1), page_size: int = Query(30, ge=1, le=100),
                  db: Session = Depends(get_db)):
    query = select(KitlyProfile).join(KitlyUser, KitlyUser.id == KitlyProfile.user_id)
    if q.strip():
        t = f"%{q.strip().lower()}%"
        query = query.where(or_(func.lower(KitlyProfile.slug).like(t), func.lower(KitlyProfile.display_name).like(t),
                                func.lower(KitlyProfile.headline).like(t), func.lower(KitlyUser.email).like(t)))
    if status == "published":
        query = query.where(KitlyProfile.is_published.is_(True), KitlyProfile.is_suspended.is_(False))
    elif status == "draft":
        query = query.where(KitlyProfile.is_published.is_(False))
    elif status == "suspended":
        query = query.where(KitlyProfile.is_suspended.is_(True))
    total = _count(db, query.with_only_columns(KitlyProfile.id))
    rows = db.execute(query.order_by(KitlyProfile.updated_at.desc()).offset((page - 1) * page_size).limit(page_size)).scalars().all()
    since = today() - timedelta(days=29)
    return {"items": [{
        "id": p.id, "slug": p.slug, "url": public_url(p), "display_name": p.display_name, "headline": p.headline,
        "owner": {"id": p.user.id, "email": p.user.email, "plan": p.user.plan_code},
        "is_published": p.is_published, "is_suspended": p.is_suspended, "suspended_reason": p.suspended_reason,
        "promoted": p.promoted, "marketplace_opt_in": p.marketplace_opt_in,
        "offers": _count(db, select(KitlyOffer.id).where(KitlyOffer.profile_id == p.id)),
        "views_30d": _count(db, select(KitlyProfileView.id).where(KitlyProfileView.profile_id == p.id, KitlyProfileView.day >= since)),
        "updated_at": iso(p.updated_at),
    } for p in rows], "total": total, "page": page, "page_size": page_size}


@router.post("/profiles/{profile_id}/suspend")
def suspend(profile_id: int, payload: SuspendIn, admin: KitlyUser = Depends(admin_user), db: Session = Depends(get_db)):
    """Suspend une vitrine et informe son titulaire des motifs (DSA, article 17)."""
    p = db.get(KitlyProfile, profile_id)
    if not p:
        raise HTTPException(404, detail="Vitrine introuvable.")
    p.is_suspended = True
    p.suspended_reason = payload.reason
    log(db, admin, "suspend_profile", "profile", p.id, slug=p.slug, reason=payload.reason)
    db.commit()
    clear_popular_cache()
    subject, html = emails.mail_profile_suspended(p.user.first_name, p.slug, payload.reason,
                                                  KITLY_CONTACT_EMAIL or admin.email)
    notified = emails.send(p.user.email, subject, html)
    return {"ok": True, "owner_notified": notified}


@router.post("/profiles/{profile_id}/unsuspend")
def unsuspend(profile_id: int, admin: KitlyUser = Depends(admin_user), db: Session = Depends(get_db)):
    p = db.get(KitlyProfile, profile_id)
    if not p:
        raise HTTPException(404, detail="Vitrine introuvable.")
    p.is_suspended = False
    p.suspended_reason = ""
    log(db, admin, "unsuspend_profile", "profile", p.id, slug=p.slug)
    db.commit()
    clear_popular_cache()
    subject, html = emails.mail_profile_restored(p.user.first_name, p.slug)
    emails.send(p.user.email, subject, html)
    return {"ok": True}


@router.post("/profiles/{profile_id}/unpublish")
def unpublish(profile_id: int, admin: KitlyUser = Depends(admin_user), db: Session = Depends(get_db)):
    p = db.get(KitlyProfile, profile_id)
    if not p:
        raise HTTPException(404, detail="Vitrine introuvable.")
    p.is_published = False
    log(db, admin, "unpublish_profile", "profile", p.id, slug=p.slug)
    db.commit()
    clear_popular_cache()
    return {"ok": True}


# ============================================================
# Signalements
# ============================================================

@router.get("/reports")
def reports(status: str = "open", page: int = Query(1, ge=1), db: Session = Depends(get_db)):
    rows = db.execute(
        select(KitlyReport, KitlyProfile).join(KitlyProfile, KitlyProfile.id == KitlyReport.profile_id)
        .where(KitlyReport.status == status).order_by(KitlyReport.created_at.desc()).offset((page - 1) * 50).limit(50)
    ).all()
    return {"items": [
        {"id": r.id, "profile_id": r.profile_id, "slug": p.slug, "display_name": p.display_name, "url": public_url(p),
         "profile_suspended": p.is_suspended, "reason": r.reason, "details": r.details,
         "reporter_email": r.reporter_email, "status": r.status, "created_at": iso(r.created_at)}
        for r, p in rows
    ]}


@router.patch("/reports/{report_id}")
def update_report(report_id: int, status: str, admin: KitlyUser = Depends(admin_user), db: Session = Depends(get_db)):
    if status not in ("open", "reviewed", "dismissed"):
        raise HTTPException(422, detail="Statut inconnu.")
    r = db.get(KitlyReport, report_id)
    if not r:
        raise HTTPException(404, detail="Signalement introuvable.")
    r.status = status
    log(db, admin, "report_status", "report", r.id, status=status)
    db.commit()
    return {"ok": True}


# ============================================================
# Formules
# ============================================================

@router.get("/plans")
def admin_plans(db: Session = Depends(get_db)):
    plans = db.execute(select(KitlyPlan).order_by(KitlyPlan.position)).scalars()
    return {"items": [{**plan_out(p), "stripe_price_id": p.stripe_price_id, "is_active": p.is_active} for p in plans],
            "known_limits": sorted(KNOWN_LIMITS)}


@router.patch("/plans/{code}")
def update_plan(code: str, payload: AdminPlanIn, admin: KitlyUser = Depends(admin_user), db: Session = Depends(get_db)):
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
    log(db, admin, "update_plan", "plan", code, **{k: v for k, v in data.items() if k != "features"})
    db.commit()
    clear_popular_cache()
    return {"plan": {**plan_out(plan), "stripe_price_id": plan.stripe_price_id, "is_active": plan.is_active}}


# ============================================================
# Système et journal
# ============================================================

@router.get("/system")
def system():
    """Configuration effective (aucune valeur secrète)."""
    return {
        "frontend_url": KITLY_FRONTEND_URL,
        "cors_origins": KITLY_CORS_ORIGINS,
        "missing_env": missing_env(),
        "stripe": {"secret_key": bool(KITLY_STRIPE_SECRET_KEY),
                   "live_mode": KITLY_STRIPE_SECRET_KEY.startswith("sk_live") if KITLY_STRIPE_SECRET_KEY else None,
                   "prices": {k: bool(v) for k, v in KITLY_STRIPE_PRICES.items()}},
        "email": dict(emails.EMAIL_STATUS) if emails.EMAIL_STATUS.get("checked") else {"checked": False},
    }


@router.post("/system/email-check")
def email_check(admin: KitlyUser = Depends(admin_user), db: Session = Depends(get_db)):
    st = emails.check_sender()
    log(db, admin, "email_check", "system", "", sender_ok=st.get("sender_ok"))
    db.commit()
    return st


@router.post("/system/email-test")
def email_test(payload: EmailTestIn, admin: KitlyUser = Depends(admin_user), db: Session = Depends(get_db)):
    to = str(payload.to or admin.email)
    subject, html = emails.mail_test()
    res = emails.send_result(to, subject, html)
    log(db, admin, "email_test", "system", "", to=to, sent=res["sent"])
    db.commit()
    return {"to": to, **res}


@router.get("/actions")
def actions(limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db)):
    rows = db.execute(select(KitlyAdminAction).order_by(KitlyAdminAction.created_at.desc()).limit(limit)).scalars().all()
    return {"items": [{"id": a.id, "admin": a.admin_email, "action": a.action, "target_type": a.target_type,
                       "target_id": a.target_id, "details": a.details, "at": iso(a.created_at)} for a in rows]}
