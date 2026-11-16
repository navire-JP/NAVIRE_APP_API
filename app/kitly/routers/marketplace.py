"""
Embed 3 — Marketplace : nouveaux profils, profils populaires, recherche.

Transparence du classement (DSA art. 27, C. conso L. 111-7) : le tri organique
ne dépend jamais de la formule payée. Les profils mis en avant contre
rémunération sont renvoyés à part, dans `sponsored`, et doivent être affichés
avec la mention « Sponsorisé ».
"""

from __future__ import annotations

import random
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.kitly import catalog
from app.kitly.config import KITLY_NEW_PROFILES_DAYS, KITLY_POPULAR_WINDOW_DAYS
from app.kitly.models import KitlyOffer, KitlyProfile, KitlyUser
from app.kitly.queries import (
    marketplace_profiles,
    min_price_subquery,
    plans_with,
    popular_profile_ids,
    popularity_subquery,
)
from app.kitly.serializers import card_public
from app.kitly.utils import aware, normalize_text, now

router = APIRouter(prefix="/marketplace", tags=["kitly · marketplace"])

RANKING_INFO = {
    "organic": (
        "Les résultats sont classés selon leur pertinence par rapport à ta recherche (correspondance avec le nom, "
        "le métier, les spécialités, la présentation et les offres), puis selon le nombre de visites uniques reçues "
        f"sur les {KITLY_POPULAR_WINDOW_DAYS} derniers jours. Le tri « nouveaux » suit la date de première "
        "publication, le tri « prix » le prix d'appel affiché."
    ),
    "sponsored": (
        "Les profils marqués « Sponsorisé » bénéficient d'une mise en avant incluse dans la formule Premium+. "
        "Ils sont affichés séparément et n'influencent pas le classement des autres résultats."
    ),
    "eligibility": (
        "Seules les vitrines publiées, dont le titulaire a choisi d'apparaître dans la marketplace et dont la "
        "formule inclut le référencement, sont affichées. Les nouveaux comptes y figurent gratuitement pendant "
        "leur premier mois (offre de bienvenue)."
    ),
}


def _cards(db: Session, profiles: list[KitlyProfile], sponsored: bool = False) -> list[dict]:
    if not profiles:
        return []
    ids = [p.id for p in profiles]
    offers = db.execute(
        select(KitlyOffer).where(KitlyOffer.profile_id.in_(ids), KitlyOffer.status == "published")
        .order_by(KitlyOffer.featured.desc(), KitlyOffer.position, KitlyOffer.id)
    ).scalars().all()
    by_profile: dict[int, list[KitlyOffer]] = {}
    for o in offers:
        by_profile.setdefault(o.profile_id, []).append(o)
    new_since = now() - timedelta(days=KITLY_NEW_PROFILES_DAYS)
    out = []
    for p in profiles:
        own = by_profile.get(p.id, [])
        prices = [o.price_cents for o in own if o.price_mode in ("fixed", "range", "free") and o.price_cents is not None]
        out.append(card_public(
            p, own[:2], min(prices) if prices else None,
            is_new=bool(p.first_published_at and aware(p.first_published_at) >= new_since),
            sponsored=sponsored, plan=p.user.plan_code,
        ))
    return out


@router.get("/categories")
def categories(db: Session = Depends(get_db)):
    q = (
        marketplace_profiles(db)
        .with_only_columns(KitlyProfile.category, func.count())
        .group_by(KitlyProfile.category)
    )
    counts = {cat: n for cat, n in db.execute(q).all()}
    return {"items": [{**c, "count": counts.get(c["key"], 0)} for c in catalog.CATEGORIES]}


@router.get("/new")
def new_profiles(limit: int = Query(12, ge=1, le=48), db: Session = Depends(get_db)):
    """« Les nouveaux sur Kitly » : premières publications des 7 derniers jours."""
    since = now() - timedelta(days=KITLY_NEW_PROFILES_DAYS)
    rows = db.execute(
        marketplace_profiles(db).where(KitlyProfile.first_published_at >= since)
        .order_by(KitlyProfile.first_published_at.desc()).limit(limit)
    ).scalars().all()
    return {"items": _cards(db, rows), "window_days": KITLY_NEW_PROFILES_DAYS}


@router.get("/popular")
def popular(limit: int = Query(12, ge=1, le=48), db: Session = Depends(get_db)):
    """« Les profils populaires » : visites uniques sur la période de référence (classement mis en cache)."""
    ids = popular_profile_ids(db, KITLY_POPULAR_WINDOW_DAYS, limit)
    if not ids:
        return {"items": [], "window_days": KITLY_POPULAR_WINDOW_DAYS}
    rows = {p.id: p for p in db.execute(marketplace_profiles(db).where(KitlyProfile.id.in_(ids))).scalars()}
    ordered = [rows[i] for i in ids if i in rows]
    return {"items": _cards(db, ordered), "window_days": KITLY_POPULAR_WINDOW_DAYS}


@router.get("/search")
def search(
    q: str = Query("", max_length=100),
    category: str | None = None,
    location: str | None = Query(None, max_length=100),
    kind: str | None = Query(None, description="physical | digital | service | appointment"),
    budget_max_cents: int | None = Query(None, ge=0),
    sort: Literal["relevance", "new", "popular", "price_asc"] = "relevance",
    page: int = Query(1, ge=1, le=500),
    page_size: int = Query(24, ge=1, le=60),
    db: Session = Depends(get_db),
):
    price = min_price_subquery()
    pop = popularity_subquery(KITLY_POPULAR_WINDOW_DAYS)
    base = (
        marketplace_profiles(db)
        .outerjoin(price, price.c.pid == KitlyProfile.id)
        .outerjoin(pop, pop.c.pid == KitlyProfile.id)
    )

    if category:
        base = base.where(KitlyProfile.category == category)
    if location:
        base = base.where(func.lower(KitlyProfile.location).contains(location.strip().lower(), autoescape=True))
    if kind:
        base = base.where(KitlyProfile.id.in_(
            select(KitlyOffer.profile_id).where(KitlyOffer.kind == kind, KitlyOffer.status == "published")
        ))
    if budget_max_cents is not None:
        base = base.where(price.c.min_price.is_not(None), price.c.min_price <= budget_max_cents)

    score = None
    terms = [t for t in normalize_text(q).split() if len(t) >= 2][:6]
    if terms:
        parts = []
        for t in terms:
            base = base.where(KitlyProfile.search_text.contains(t, autoescape=True))
            parts += [case((func.lower(KitlyProfile.display_name).contains(t, autoescape=True), 4), else_=0),
                      case((func.lower(KitlyProfile.headline).contains(t, autoescape=True), 3), else_=0),
                      case((func.lower(KitlyProfile.bio).contains(t, autoescape=True), 1), else_=0)]
        score = sum(parts[1:], parts[0])

    visits = func.coalesce(pop.c.visits, 0)
    if sort == "new":
        order = [KitlyProfile.first_published_at.desc()]
    elif sort == "popular":
        order = [visits.desc(), KitlyProfile.first_published_at.desc()]
    elif sort == "price_asc":
        order = [case((price.c.min_price.is_(None), 1), else_=0), price.c.min_price.asc(), visits.desc()]
    else:
        order = ([score.desc()] if score is not None else []) + [visits.desc(), KitlyProfile.first_published_at.desc()]

    total = db.execute(select(func.count()).select_from(base.with_only_columns(KitlyProfile.id).subquery())).scalar_one()
    rows = db.execute(base.order_by(*order, KitlyProfile.id).offset((page - 1) * page_size).limit(page_size)).scalars().all()

    # Encart sponsorisé : jamais mêlé au classement organique.
    sponsored: list[dict] = []
    if page == 1:
        boost_plans = plans_with(db, "marketplace_boost") or ["__none__"]
        candidates = db.execute(
            base.where(KitlyProfile.promoted.is_(True), KitlyUser.plan_code.in_(boost_plans)).limit(20)
        ).scalars().all()
        sponsored = _cards(db, random.sample(candidates, min(2, len(candidates))), sponsored=True)

    return {
        "items": _cards(db, rows),
        "sponsored": sponsored,
        "total": total,
        "page": page,
        "page_size": page_size,
        "sort": sort,
        "ranking_info_url": "/kitly/marketplace/ranking-info",
    }


@router.get("/ranking-info")
def ranking_info():
    return RANKING_INFO
