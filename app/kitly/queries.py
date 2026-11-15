"""Requêtes partagées : visibilité publique, éligibilité marketplace, popularité."""

from __future__ import annotations

import time
from datetime import timedelta

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.kitly.config import KITLY_POPULAR_CACHE_SECONDS
from app.kitly.models import KitlyOffer, KitlyPlan, KitlyProfile, KitlyProfileView, KitlyUser
from app.kitly.utils import normalize_text, today


def plans_with(db: Session, feature: str) -> list[str]:
    return [p.code for p in db.execute(select(KitlyPlan)).scalars() if (p.limits or {}).get(feature)]


def public_profiles() -> Select:
    """Vitrines consultables : publiées, non suspendues, compte actif."""
    return (
        select(KitlyProfile)
        .join(KitlyUser, KitlyUser.id == KitlyProfile.user_id)
        .where(KitlyProfile.is_published.is_(True), KitlyProfile.is_suspended.is_(False), KitlyUser.is_active.is_(True))
    )


def marketplace_profiles(db: Session) -> Select:
    """Vitrines référencées dans la marketplace : publiques + formule éligible + accord du freelance."""
    return public_profiles().where(
        KitlyProfile.marketplace_opt_in.is_(True),
        KitlyUser.plan_code.in_(plans_with(db, "marketplace") or ["__none__"]),
    )


def get_public_profile(db: Session, slug: str) -> KitlyProfile | None:
    return db.execute(public_profiles().where(KitlyProfile.slug == slug.lower())).scalar_one_or_none()


def published_offers(db: Session, profile_id: int) -> list[KitlyOffer]:
    return list(db.execute(
        select(KitlyOffer).where(KitlyOffer.profile_id == profile_id, KitlyOffer.status == "published")
        .order_by(KitlyOffer.position, KitlyOffer.id)
    ).scalars())


def min_price_subquery():
    """Prix d'appel par vitrine (offres publiées à prix affiché)."""
    return (
        select(KitlyOffer.profile_id.label("pid"), func.min(KitlyOffer.price_cents).label("min_price"))
        .where(KitlyOffer.status == "published", KitlyOffer.price_mode.in_(("fixed", "range", "free")),
               KitlyOffer.price_cents.is_not(None))
        .group_by(KitlyOffer.profile_id)
        .subquery()
    )


def popularity_subquery(window_days: int):
    """Visites uniques (1 par visiteur et par jour) sur la période de référence."""
    since = today() - timedelta(days=window_days - 1)
    return (
        select(KitlyProfileView.profile_id.label("pid"), func.count().label("visits"))
        .where(KitlyProfileView.day >= since)
        .group_by(KitlyProfileView.profile_id)
        .subquery()
    )


# Classement populaire : recalculé au plus toutes les KITLY_POPULAR_CACHE_SECONDS
# (« actualisé régulièrement »), pas à chaque affichage.
_popular_cache: dict[tuple, tuple[float, list[int]]] = {}


def popular_profile_ids(db: Session, window_days: int, limit: int) -> list[int]:
    key = (window_days, limit)
    hit = _popular_cache.get(key)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    pop = popularity_subquery(window_days)
    q = (
        marketplace_profiles(db)
        .join(pop, pop.c.pid == KitlyProfile.id)
        .order_by(pop.c.visits.desc(), KitlyProfile.first_published_at.desc())
        .limit(limit)
    )
    ids = [p.id for p in db.execute(q).scalars()]
    _popular_cache[key] = (time.monotonic() + KITLY_POPULAR_CACHE_SECONDS, ids)
    return ids


def clear_popular_cache() -> None:
    _popular_cache.clear()


def refresh_search_text(db: Session, profile: KitlyProfile) -> None:
    """À appeler après toute modification de la vitrine ou de ses offres (avant commit)."""
    db.flush()
    titles = db.execute(
        select(KitlyOffer.title).where(KitlyOffer.profile_id == profile.id, KitlyOffer.status == "published")
    ).scalars().all()
    parts = [profile.display_name, profile.headline, profile.bio, profile.location,
             " ".join(profile.specialties or []), " ".join(profile.skills or []), " ".join(titles)]
    profile.search_text = normalize_text(" ".join(p for p in parts if p))[:4000]
