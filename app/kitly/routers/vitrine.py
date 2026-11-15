"""
Tableau de bord du freelance — « Ma vitrine », portfolio, offres, demandes,
statistiques, médias. Toutes les routes sont limitées au compte connecté :
un identifiant d'un autre freelance renvoie 404, jamais ses données.
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.kitly import catalog
from app.kitly.media import MEDIA_PURPOSES, check_image, upload_image
from app.kitly.models import (
    KitlyLead,
    KitlyOffer,
    KitlyPortfolioProject,
    KitlyProfile,
    KitlyProfileView,
    KitlyUser,
)
from app.kitly.plans import plan_limits
from app.kitly.queries import clear_popular_cache, refresh_search_text
from app.kitly.schemas import (
    DETAILS_MODELS,
    LeadUpdateIn,
    OfferIn,
    PortfolioIn,
    ProfileIn,
    ReorderIn,
)
from app.kitly.security import current_user
from app.kitly.serializers import lead_out, offer_owner, portfolio_owner, profile_owner
from app.kitly.utils import now, slug_problem, slugify, today

router = APIRouter(tags=["kitly · vitrine"])


def _upsell(feature: str) -> HTTPException:
    return HTTPException(403, detail={"message": f"{feature} : disponible avec une formule supérieure.",
                                      "code": "plan_required"})


def require_profile(user: KitlyUser = Depends(current_user)) -> KitlyProfile:
    if not user.profile:
        raise HTTPException(404, detail="Tu n'as pas encore créé ta vitrine.")
    return user.profile


def _slug_taken(db: Session, slug: str, exclude_id: int | None = None) -> bool:
    q = select(KitlyProfile.id).where(KitlyProfile.slug == slug)
    if exclude_id:
        q = q.where(KitlyProfile.id != exclude_id)
    return db.execute(q).first() is not None


def _suggest_slug(db: Session, base: str) -> str:
    base = slugify(base) or "ma-vitrine"
    if len(base) < 3:
        base = f"{base}-pro"
    candidate, i = base, 2
    while slug_problem(candidate) or _slug_taken(db, candidate):
        candidate = f"{base[:36]}-{i}"
        i += 1
    return candidate


# ============================================================
# Slug
# ============================================================

@router.get("/slugs/check")
def check_slug(slug: str, db: Session = Depends(get_db), user: KitlyUser = Depends(current_user)):
    slug = slug.strip().lower()
    problem = slug_problem(slug)
    own_id = user.profile.id if user.profile else None
    if not problem and _slug_taken(db, slug, exclude_id=own_id):
        problem = "Cette adresse est déjà prise."
    return {"slug": slug, "available": problem is None, "reason": problem,
            "suggestion": None if problem is None else _suggest_slug(db, slug)}


# ============================================================
# Vitrine
# ============================================================

def _apply_profile(db: Session, user: KitlyUser, profile: KitlyProfile, payload: ProfileIn) -> None:
    limits = plan_limits(db, user)
    data = payload.model_dump(exclude_unset=True)

    if "slug" in data and data["slug"] != profile.slug:
        problem = slug_problem(data["slug"] or "")
        if problem:
            raise HTTPException(422, detail={"field": "slug", "message": problem})
        if _slug_taken(db, data["slug"], exclude_id=profile.id):
            raise HTTPException(409, detail={"field": "slug", "message": "Cette adresse est déjà prise.",
                                             "suggestion": _suggest_slug(db, data["slug"])})
        profile.slug = data["slug"]

    if "theme" in data and data["theme"] is not None:
        incoming = payload.theme.model_dump(exclude_none=True)
        if not limits.get("advanced_theme"):
            tpl = incoming.get("template")
            if tpl and tpl not in catalog.BASIC_TEMPLATES:
                raise _upsell("Ce template")
            if any(k in incoming for k in ("colors", "font_heading", "font_body", "background")):
                raise _upsell("La personnalisation des couleurs, typographies et fonds")
        merged = dict(profile.theme or {})
        for key, value in incoming.items():
            merged[key] = {**(merged.get(key) or {}), **value} if isinstance(value, dict) else value
        profile.theme = merged

    if "blocks" in data and data["blocks"] is not None:
        if len(data["blocks"]) > int(limits.get("blocks_max", 10)):
            raise _upsell(f"Plus de {limits.get('blocks_max', 10)} blocs")
        profile.blocks = [b.model_dump() for b in payload.blocks]

    if "custom_domain" in data:
        domain = data["custom_domain"] or None
        if domain and not limits.get("custom_domain"):
            raise _upsell("Le domaine personnalisé")
        if domain and db.execute(
            select(KitlyProfile.id).where(KitlyProfile.custom_domain == domain, KitlyProfile.id != profile.id)
        ).first():
            raise HTTPException(409, detail="Ce domaine est déjà relié à une autre vitrine.")
        profile.custom_domain = domain

    if data.get("promoted") and not limits.get("marketplace_boost"):
        raise _upsell("La mise en avant dans la marketplace")

    for key in ("socials", "stats", "sections"):
        if key in data and data[key] is not None:
            setattr(profile, key, [x.model_dump() for x in getattr(payload, key)])

    simple = {"display_name", "headline", "category", "specialties", "location", "bio", "about", "experience",
              "skills", "languages", "avatar_url", "banner_url", "logo_url", "contact_email", "phone", "website",
              "marketplace_opt_in", "promoted"}
    for key in simple & data.keys():
        value = data[key]
        if value is None and key not in ("promoted", "marketplace_opt_in"):
            continue
        setattr(profile, key, value if value is not None else False)


@router.post("/me/profile", status_code=201)
def create_profile(payload: ProfileIn, user: KitlyUser = Depends(current_user), db: Session = Depends(get_db)):
    if user.profile:
        raise HTTPException(409, detail="Ta vitrine existe déjà.")
    slug = payload.slug or _suggest_slug(db, payload.display_name or f"{user.first_name}-{user.last_name}")
    profile = KitlyProfile(
        user_id=user.id,
        slug="__pending__" + str(user.id),
        display_name=payload.display_name or f"{user.first_name} {user.last_name}".strip(),
        contact_email=user.email,
        theme=dict(catalog.DEFAULT_THEME),
        sections=list(catalog.DEFAULT_SECTIONS),
    )
    db.add(profile)
    db.flush()
    payload.slug = slug  # l'affectation marque le champ comme « fourni »
    _apply_profile(db, user, profile, payload)
    refresh_search_text(db, profile)
    db.commit()
    db.refresh(profile)
    return {"profile": profile_owner(profile)}


@router.get("/me/profile")
def get_profile(profile: KitlyProfile = Depends(require_profile)):
    return {"profile": profile_owner(profile)}


@router.patch("/me/profile")
def update_profile(
    payload: ProfileIn,
    user: KitlyUser = Depends(current_user),
    profile: KitlyProfile = Depends(require_profile),
    db: Session = Depends(get_db),
):
    """Mise à jour partielle : le front peut sauvegarder automatiquement champ par champ."""
    _apply_profile(db, user, profile, payload)
    refresh_search_text(db, profile)
    db.commit()
    db.refresh(profile)
    return {"profile": profile_owner(profile)}


@router.post("/me/profile/publish")
def publish_profile(
    user: KitlyUser = Depends(current_user),
    profile: KitlyProfile = Depends(require_profile),
    db: Session = Depends(get_db),
):
    missing = []
    if not user.email_verified_at:
        missing.append({"key": "verify_email", "message": "Confirme ton adresse email."})
    if not profile.display_name.strip():
        missing.append({"key": "display_name", "message": "Indique ton nom ou nom commercial."})
    if not profile.headline.strip():
        missing.append({"key": "headline", "message": "Indique ton métier."})
    if profile.is_suspended:
        raise HTTPException(403, detail="Ta vitrine est suspendue par la modération : " + profile.suspended_reason)
    if missing:
        raise HTTPException(422, detail={"message": "Encore quelques informations avant de publier.", "missing": missing})
    profile.is_published = True
    profile.published_at = now()
    profile.first_published_at = profile.first_published_at or now()
    db.commit()
    clear_popular_cache()
    return {"profile": profile_owner(profile)}


@router.post("/me/profile/unpublish")
def unpublish_profile(profile: KitlyProfile = Depends(require_profile), db: Session = Depends(get_db)):
    profile.is_published = False
    db.commit()
    return {"profile": profile_owner(profile)}


# ============================================================
# Médias
# ============================================================

@router.post("/me/media")
async def upload_media(
    file: UploadFile = File(...),
    purpose: str = Form("offer"),
    user: KitlyUser = Depends(current_user),
):
    """Envoie une image et rend son URL, à placer ensuite dans le profil / l'offre / le projet."""
    if purpose not in MEDIA_PURPOSES:
        raise HTTPException(422, detail="Usage d'image inconnu.")
    # Lecture bornée : un fichier trop lourd n'est jamais chargé en entier en mémoire.
    from app.kitly import media

    check_image(file.content_type, 1)
    data = await file.read(media.KITLY_MAX_IMAGE_BYTES + 1)
    check_image(file.content_type, len(data))
    return {"url": upload_image(data, user.id, purpose)}


# ============================================================
# Portfolio
# ============================================================

def _own_project(db: Session, profile: KitlyProfile, project_id: int) -> KitlyPortfolioProject:
    p = db.get(KitlyPortfolioProject, project_id)
    if not p or p.profile_id != profile.id:
        raise HTTPException(404, detail="Projet introuvable.")
    return p


@router.get("/me/portfolio")
def list_portfolio(profile: KitlyProfile = Depends(require_profile)):
    return {"items": [portfolio_owner(p) for p in profile.portfolio]}


@router.post("/me/portfolio", status_code=201)
def create_project(
    payload: PortfolioIn,
    user: KitlyUser = Depends(current_user),
    profile: KitlyProfile = Depends(require_profile),
    db: Session = Depends(get_db),
):
    if not payload.title:
        raise HTTPException(422, detail="Donne un titre à ton projet.")
    limit = int(plan_limits(db, user).get("portfolio_max", 6))
    if len(profile.portfolio) >= limit:
        raise _upsell(f"Plus de {limit} projets")
    data = payload.model_dump(exclude_none=True)
    data["media"] = [m.model_dump() for m in payload.media] if payload.media else []
    p = KitlyPortfolioProject(profile_id=profile.id, position=len(profile.portfolio), **data)
    db.add(p)
    db.commit()
    db.refresh(p)
    return {"item": portfolio_owner(p)}


@router.patch("/me/portfolio/{project_id}")
def update_project(
    project_id: int, payload: PortfolioIn,
    profile: KitlyProfile = Depends(require_profile), db: Session = Depends(get_db),
):
    p = _own_project(db, profile, project_id)
    data = payload.model_dump(exclude_none=True)
    if payload.media is not None:
        data["media"] = [m.model_dump() for m in payload.media]
    for key, value in data.items():
        setattr(p, key, value)
    db.commit()
    return {"item": portfolio_owner(p)}


@router.delete("/me/portfolio/{project_id}")
def delete_project(project_id: int, profile: KitlyProfile = Depends(require_profile), db: Session = Depends(get_db)):
    db.delete(_own_project(db, profile, project_id))
    db.commit()
    return {"ok": True}


@router.post("/me/portfolio/reorder")
def reorder_portfolio(payload: ReorderIn, profile: KitlyProfile = Depends(require_profile), db: Session = Depends(get_db)):
    _reorder(profile.portfolio, payload.ids)
    db.commit()
    return {"items": [portfolio_owner(p) for p in sorted(profile.portfolio, key=lambda x: x.position)]}


def _reorder(items: list, ids: list[int]) -> None:
    by_id = {i.id: i for i in items}
    if set(ids) != set(by_id):
        raise HTTPException(422, detail="La liste doit contenir exactement tous tes éléments.")
    for pos, item_id in enumerate(ids):
        by_id[item_id].position = pos


# ============================================================
# Offres
# ============================================================

def _own_offer(db: Session, profile: KitlyProfile, offer_id: int) -> KitlyOffer:
    o = db.get(KitlyOffer, offer_id)
    if not o or o.profile_id != profile.id:
        raise HTTPException(404, detail="Offre introuvable.")
    return o


def _has_terms(profile: KitlyProfile) -> bool:
    return bool(profile.terms and profile.terms.current_version_id)


def _validate_offer(offer: KitlyOffer, limits: dict) -> None:
    """Cohérence prix / catégorie, après fusion des champs envoyés."""
    if offer.price_mode == "fixed" and offer.price_cents is None:
        raise HTTPException(422, detail={"field": "price_cents", "message": "Indique un prix."})
    if offer.price_mode == "range":
        if offer.price_cents is None:
            raise HTTPException(422, detail={"field": "price_cents", "message": "Indique un prix de départ."})
        if offer.price_max_cents is not None and offer.price_max_cents < offer.price_cents:
            raise HTTPException(422, detail={"field": "price_max_cents", "message": "Le prix maximum doit dépasser le prix de départ."})
    if offer.price_mode in ("quote", "free"):
        offer.price_cents = 0 if offer.price_mode == "free" else None
        offer.price_max_cents = None
    if offer.cta == "order" and offer.price_mode == "quote":
        raise HTTPException(422, detail={"field": "cta", "message": "Une offre sur devis ne peut pas être commandée directement."})
    if offer.cta == "book" and offer.kind != "appointment":
        raise HTTPException(422, detail={"field": "cta", "message": "La réservation est réservée aux rendez-vous."})
    if offer.kind == "appointment" and offer.cta == "book" and not limits.get("appointments"):
        raise HTTPException(403, detail={"message": "La prise de rendez-vous en ligne est disponible à partir de Custom. "
                                                    "Utilise « contact » en attendant.", "code": "plan_required"})
    max_images = int(limits.get("images_per_offer", 3))
    if len(offer.images or []) > max_images:
        raise _upsell(f"Plus de {max_images} images par offre")


def _clean_details(kind: str, details: dict | None) -> dict:
    model = DETAILS_MODELS[kind]
    try:
        return model.model_validate(details or {}).model_dump(mode="json")
    except Exception as exc:
        errors = getattr(exc, "errors", lambda: [])()
        raise HTTPException(422, detail={"field": "details", "message": "Champs de l'offre invalides.",
                                         "errors": [{"loc": e.get("loc"), "msg": e.get("msg")} for e in errors]})


@router.get("/me/offers")
def list_offers(profile: KitlyProfile = Depends(require_profile)):
    needs = not _has_terms(profile)
    return {"items": [offer_owner(o, needs) for o in profile.offers], "kinds": catalog.OFFER_KINDS}


@router.get("/me/offers/{offer_id}")
def get_offer(offer_id: int, profile: KitlyProfile = Depends(require_profile), db: Session = Depends(get_db)):
    return {"item": offer_owner(_own_offer(db, profile, offer_id), not _has_terms(profile))}


@router.post("/me/offers", status_code=201)
def create_offer(
    payload: OfferIn,
    user: KitlyUser = Depends(current_user),
    profile: KitlyProfile = Depends(require_profile),
    db: Session = Depends(get_db),
):
    """
    « Que souhaites-tu proposer sur Kitly ? » : `kind` est obligatoire et
    détermine les champs (`details`) et, plus tard, le questionnaire juridique.
    """
    if not payload.kind:
        raise HTTPException(422, detail={"field": "kind", "message": "Choisis ce que tu proposes."})
    if not payload.title:
        raise HTTPException(422, detail={"field": "title", "message": "Donne un titre à ton offre."})
    limits = plan_limits(db, user)
    active = [o for o in profile.offers if o.status != "archived"]
    if len(active) >= int(limits.get("offers_max", 5)):
        raise _upsell(f"Plus de {limits.get('offers_max', 5)} offres")
    if payload.featured and not limits.get("marketplace_boost"):
        raise _upsell("La mise en avant des offres")

    offer = KitlyOffer(
        profile_id=profile.id,
        kind=payload.kind,
        title=payload.title,
        description=payload.description or "",
        price_mode=payload.price_mode or ("quote" if payload.kind == "service" and payload.price_cents is None else "fixed"),
        price_cents=payload.price_cents,
        price_max_cents=payload.price_max_cents,
        vat_rate_bps=payload.vat_rate_bps,
        images=payload.images or [],
        badge=payload.badge or "",
        cta=payload.cta or catalog.DEFAULT_CTA[payload.kind],
        details=_clean_details(payload.kind, payload.details),
        status=payload.status or "draft",
        featured=bool(payload.featured),
        position=len(profile.offers),
    )
    if offer.kind == "appointment" and offer.cta == "book" and not limits.get("appointments") and not payload.cta:
        offer.cta = "contact"  # repli silencieux quand le front n'a rien demandé
    _validate_offer(offer, limits)
    db.add(offer)
    refresh_search_text(db, profile)
    db.commit()
    db.refresh(offer)
    first_offer = len(profile.offers) == 1
    return {
        "item": offer_owner(offer, not _has_terms(profile)),
        # Première offre : le front propose le questionnaire CGV de cette catégorie.
        "suggest_terms_questionnaire": first_offer or not _has_terms(profile),
        "questionnaire_scope": offer.kind,
    }


@router.patch("/me/offers/{offer_id}")
def update_offer(
    offer_id: int, payload: OfferIn,
    user: KitlyUser = Depends(current_user),
    profile: KitlyProfile = Depends(require_profile),
    db: Session = Depends(get_db),
):
    offer = _own_offer(db, profile, offer_id)
    limits = plan_limits(db, user)
    data = payload.model_dump(exclude_unset=True)
    if "kind" in data and data["kind"] and data["kind"] != offer.kind:
        offer.kind = data["kind"]
        offer.details = _clean_details(offer.kind, data.get("details") or {})
    elif "details" in data and data["details"] is not None:
        offer.details = _clean_details(offer.kind, data["details"])
    if data.get("featured") and not limits.get("marketplace_boost"):
        raise _upsell("La mise en avant des offres")
    if data.get("status") in ("draft", "published") and offer.status == "archived":
        active = [o for o in profile.offers if o.status != "archived" and o.id != offer.id]
        if len(active) >= int(limits.get("offers_max", 5)):
            raise _upsell(f"Plus de {limits.get('offers_max', 5)} offres")
    for key in ("title", "description", "price_mode", "price_cents", "price_max_cents", "vat_rate_bps",
                "images", "badge", "cta", "status", "featured"):
        if key in data and (data[key] is not None or key in ("price_cents", "price_max_cents", "vat_rate_bps")):
            setattr(offer, key, data[key])
    _validate_offer(offer, limits)
    refresh_search_text(db, profile)
    db.commit()
    db.refresh(offer)
    return {"item": offer_owner(offer, not _has_terms(profile))}


@router.delete("/me/offers/{offer_id}")
def delete_offer(offer_id: int, profile: KitlyProfile = Depends(require_profile), db: Session = Depends(get_db)):
    db.delete(_own_offer(db, profile, offer_id))
    refresh_search_text(db, profile)
    db.commit()
    return {"ok": True}


@router.post("/me/offers/reorder")
def reorder_offers(payload: ReorderIn, profile: KitlyProfile = Depends(require_profile), db: Session = Depends(get_db)):
    _reorder(profile.offers, payload.ids)
    db.commit()
    needs = not _has_terms(profile)
    return {"items": [offer_owner(o, needs) for o in sorted(profile.offers, key=lambda x: x.position)]}


# ============================================================
# Demandes (leads)
# ============================================================

@router.get("/me/leads")
def list_leads(
    status: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    profile: KitlyProfile = Depends(require_profile),
    db: Session = Depends(get_db),
):
    q = select(KitlyLead).where(KitlyLead.profile_id == profile.id)
    if status:
        if status not in catalog.LEAD_STATUSES:
            raise HTTPException(422, detail="Statut inconnu.")
        q = q.where(KitlyLead.status == status)
    else:
        q = q.where(KitlyLead.status != "spam")
    total = db.execute(select(func.count()).select_from(q.subquery())).scalar_one()
    rows = db.execute(q.order_by(KitlyLead.created_at.desc()).offset((page - 1) * page_size).limit(page_size)).scalars().all()
    titles = {o.id: o.title for o in profile.offers}
    unread = db.execute(
        select(func.count()).select_from(KitlyLead).where(KitlyLead.profile_id == profile.id, KitlyLead.status == "new")
    ).scalar_one()
    return {"items": [lead_out(r, titles.get(r.offer_id)) for r in rows], "total": total, "unread": unread,
            "page": page, "page_size": page_size}


@router.patch("/me/leads/{lead_id}")
def update_lead(lead_id: int, payload: LeadUpdateIn, profile: KitlyProfile = Depends(require_profile),
                db: Session = Depends(get_db)):
    lead = db.get(KitlyLead, lead_id)
    if not lead or lead.profile_id != profile.id:
        raise HTTPException(404, detail="Demande introuvable.")
    lead.status = payload.status
    db.commit()
    return {"item": lead_out(lead)}


# ============================================================
# Statistiques (vue d'ensemble du tableau de bord)
# ============================================================

@router.get("/me/stats")
def stats(
    days: int = Query(30, ge=1, le=365),
    user: KitlyUser = Depends(current_user),
    profile: KitlyProfile = Depends(require_profile),
    db: Session = Depends(get_db),
):
    """
    `visits` : visites uniques par jour (un visiteur compte une fois par jour) ;
    `visitors` : visiteurs distincts sur la période (approximation : l'empreinte
    change chaque jour, un même visiteur revenu deux jours compte deux fois).
    Détail jour par jour réservé à Custom+, provenance marketplace à Premium+.
    """
    limits = plan_limits(db, user)
    since = today() - timedelta(days=days - 1)
    base = select(KitlyProfileView).where(KitlyProfileView.profile_id == profile.id, KitlyProfileView.day >= since)
    visits = db.execute(select(func.count()).select_from(base.subquery())).scalar_one()
    visitors = db.execute(
        select(func.count(func.distinct(KitlyProfileView.visitor_hash))).where(
            KitlyProfileView.profile_id == profile.id, KitlyProfileView.day >= since
        )
    ).scalar_one()
    leads = db.execute(
        select(func.count()).select_from(KitlyLead).where(
            KitlyLead.profile_id == profile.id, KitlyLead.status != "spam",
            KitlyLead.created_at >= now() - timedelta(days=days),
        )
    ).scalar_one()
    out: dict = {
        "period_days": days,
        "visits": visits,
        "visitors": visitors,
        "leads": leads,
        "orders": 0,  # parcours de commande : sprint Monétisation
        "conversion_rate": round(100 * leads / visits, 1) if visits else 0.0,
        "series": None,
        "sources": None,
        "locked": [],
    }
    if limits.get("stats"):
        rows = db.execute(
            select(KitlyProfileView.day, func.count()).where(
                KitlyProfileView.profile_id == profile.id, KitlyProfileView.day >= since
            ).group_by(KitlyProfileView.day)
        ).all()
        per_day = {d: n for d, n in rows}
        out["series"] = [
            {"day": (since + timedelta(days=i)).isoformat(), "visits": per_day.get(since + timedelta(days=i), 0)}
            for i in range(days)
        ]
    else:
        out["locked"].append("series")
    if limits.get("acquisition_stats"):
        rows = db.execute(
            select(KitlyProfileView.source, func.count()).where(
                KitlyProfileView.profile_id == profile.id, KitlyProfileView.day >= since
            ).group_by(KitlyProfileView.source)
        ).all()
        out["sources"] = {src: n for src, n in rows}
    else:
        out["locked"].append("sources")
    return out
