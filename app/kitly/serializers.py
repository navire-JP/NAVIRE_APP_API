"""
Mise en forme des réponses.

Deux familles bien séparées : *_owner (le freelance connecté voit tout) et
*_public (seulement ce qu'il a choisi de rendre public). Les routes publiques
n'utilisent QUE les fonctions *_public : aucune donnée de compte (email de
connexion, Stripe, juridique privé) ne peut y fuiter par oubli.
"""

from __future__ import annotations

from app.kitly import catalog
from app.kitly.config import KITLY_PROFILE_URL_TEMPLATE
from app.kitly.models import (
    KitlyLead,
    KitlyOffer,
    KitlyPortfolioProject,
    KitlyProfile,
    KitlySellerLegalProfile,
    KitlySellerTermsVersion,
    KitlyUser,
)
from app.kitly.utils import iso


def user_out(user: KitlyUser) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "email_verified": user.email_verified_at is not None,
        "plan": user.plan_code,
        "is_admin": user.is_admin,
        "marketing_opt_in": user.marketing_opt_in,
        "created_at": iso(user.created_at),
    }


def price_label(offer: KitlyOffer) -> str:
    def eur(c: int | None) -> str:
        if c is None:
            return ""
        s = f"{c / 100:.2f}".replace(".", ",")
        return s.removesuffix(",00") + " €"

    if offer.price_mode == "free":
        return "Gratuit"
    if offer.price_mode == "quote":
        return "Sur devis"
    if offer.price_mode == "range" and offer.price_max_cents:
        return f"{eur(offer.price_cents)} – {eur(offer.price_max_cents)}"
    if offer.price_mode == "range":
        return f"À partir de {eur(offer.price_cents)}"
    return eur(offer.price_cents)


def offer_public(offer: KitlyOffer) -> dict:
    details = dict(offer.details or {})
    if offer.kind == "physical":
        # Le stock exact n'est pas public : seulement « disponible / épuisé ».
        stock = details.pop("stock", None)
        details["in_stock"] = stock is None or stock > 0
        details["variants"] = [
            {k: v for k, v in var.items() if k != "stock"} | {"in_stock": var.get("stock") is None or var["stock"] > 0}
            for var in details.get("variants", [])
        ]
    return {
        "id": offer.id,
        "kind": offer.kind,
        "title": offer.title,
        "description": offer.description,
        "price_mode": offer.price_mode,
        "price_cents": offer.price_cents,
        "price_max_cents": offer.price_max_cents,
        "price_label": price_label(offer),
        "currency": offer.currency,
        "images": offer.images,
        "badge": offer.badge,
        "cta": offer.cta,
        "details": details,
        "featured": offer.featured,
    }


def offer_owner(offer: KitlyOffer, needs_terms: bool = False) -> dict:
    return {
        **offer_public(offer),
        "details": offer.details,
        "vat_rate_bps": offer.vat_rate_bps,
        "status": offer.status,
        "position": offer.position,
        # Une offre « à commander » ne pourra être payée qu'avec des CGV publiées.
        "needs_terms": needs_terms and offer.cta == "order",
        "created_at": iso(offer.created_at),
        "updated_at": iso(offer.updated_at),
    }


def portfolio_public(p: KitlyPortfolioProject) -> dict:
    return {
        "id": p.id,
        "title": p.title,
        "description": p.description,
        "cover_url": p.cover_url,
        "media": p.media,
        "client_name": p.client_name,
        "external_url": p.external_url,
        "tags": p.tags,
    }


def portfolio_owner(p: KitlyPortfolioProject) -> dict:
    return {**portfolio_public(p), "is_visible": p.is_visible, "position": p.position,
            "created_at": iso(p.created_at), "updated_at": iso(p.updated_at)}


def theme_with_defaults(theme: dict | None) -> dict:
    t = {**catalog.DEFAULT_THEME, **(theme or {})}
    t["colors"] = {**catalog.DEFAULT_THEME["colors"], **((theme or {}).get("colors") or {})}
    t["background"] = {**catalog.DEFAULT_THEME["background"], **((theme or {}).get("background") or {})}
    return t


def sections_with_defaults(sections: list | None) -> list:
    """Sections enregistrées, complétées par celles ajoutées depuis (visibles, en fin de liste)."""
    seen = [s for s in (sections or []) if s.get("key") in catalog.SECTION_KEYS]
    keys = {s["key"] for s in seen}
    return seen + [{"key": k, "visible": True} for k in catalog.SECTION_KEYS if k not in keys]


def public_url(profile: KitlyProfile) -> str:
    return KITLY_PROFILE_URL_TEMPLATE.replace("{slug}", profile.slug)


def profile_owner(profile: KitlyProfile) -> dict:
    return {
        "id": profile.id,
        "slug": profile.slug,
        "url": public_url(profile),
        "display_name": profile.display_name,
        "headline": profile.headline,
        "category": profile.category,
        "specialties": profile.specialties,
        "location": profile.location,
        "bio": profile.bio,
        "about": profile.about,
        "experience": profile.experience,
        "skills": profile.skills,
        "languages": profile.languages,
        "avatar_url": profile.avatar_url,
        "banner_url": profile.banner_url,
        "logo_url": profile.logo_url,
        "contact_email": profile.contact_email,
        "phone": profile.phone,
        "website": profile.website,
        "socials": profile.socials,
        "stats": profile.stats,
        "blocks": profile.blocks,
        "theme": theme_with_defaults(profile.theme),
        "sections": sections_with_defaults(profile.sections),
        "custom_domain": profile.custom_domain,
        "is_published": profile.is_published,
        "published_at": iso(profile.published_at),
        "first_published_at": iso(profile.first_published_at),
        "marketplace_opt_in": profile.marketplace_opt_in,
        "promoted": profile.promoted,
        "is_suspended": profile.is_suspended,
        "suspended_reason": profile.suspended_reason,
        "updated_at": iso(profile.updated_at),
    }


def legal_footer_public(legal: KitlySellerLegalProfile | None, has_terms: bool, slug: str) -> dict:
    """Mentions du pied de vitrine : identité du vendeur et liens juridiques."""
    out = {
        "seller": None,
        "terms_url": f"/kitly/public/profiles/{slug}/terms" if has_terms else None,
        "has_terms": has_terms,
    }
    if legal:
        out["seller"] = {
            "name": legal.trade_name or legal.legal_name,
            "legal_name": legal.legal_name,
            "legal_form": legal.legal_form,
            "is_professional": legal.is_professional,
            "siren": legal.siren if legal.is_professional else "",
            "vat_number": legal.vat_number,
            "address": ", ".join(x for x in [legal.address_line, f"{legal.postal_code} {legal.city}".strip()] if x)
            if legal.is_professional else legal.city,
            "email": legal.professional_email,
            "mediator_name": legal.mediator_name,
            "mediator_url": legal.mediator_url,
        }
    return out


def profile_public(
    profile: KitlyProfile,
    offers: list[KitlyOffer],
    portfolio: list[KitlyPortfolioProject],
    show_branding: bool,
    has_terms: bool,
) -> dict:
    by_id = {o.id: o for o in offers}
    blocks = []
    for b in profile.blocks or []:
        if not b.get("visible", True):
            continue
        if b.get("type") == "offer":
            offer = by_id.get(b.get("offer_id"))
            if offer is None:
                continue  # offre retirée ou non publiée : le bloc disparaît
            b = {**b, "offer": offer_public(offer)}
        blocks.append(b)
    return {
        "slug": profile.slug,
        "url": public_url(profile),
        "display_name": profile.display_name,
        "headline": profile.headline,
        "category": profile.category,
        "specialties": profile.specialties,
        "location": profile.location,
        "bio": profile.bio,
        "about": profile.about,
        "experience": profile.experience,
        "skills": profile.skills,
        "languages": profile.languages,
        "avatar_url": profile.avatar_url,
        "banner_url": profile.banner_url,
        "logo_url": profile.logo_url,
        "contact": {"email": profile.contact_email, "phone": profile.phone, "website": profile.website},
        "socials": profile.socials,
        "stats": profile.stats,
        "blocks": blocks,
        "theme": theme_with_defaults(profile.theme),
        "sections": [s for s in sections_with_defaults(profile.sections) if s.get("visible", True)],
        "offers": [offer_public(o) for o in offers],
        "portfolio": [portfolio_public(p) for p in portfolio],
        "show_kitly_branding": show_branding,
        "legal": legal_footer_public(profile.legal_profile, has_terms, profile.slug),
    }


def card_public(profile: KitlyProfile, top_offers: list[KitlyOffer], min_price: int | None,
                is_new: bool = False, sponsored: bool = False, plan: str = "") -> dict:
    """Carte marketplace."""
    return {
        "slug": profile.slug,
        "url": public_url(profile),
        "display_name": profile.display_name,
        "headline": profile.headline,
        "category": profile.category,
        "location": profile.location,
        "bio": profile.bio,
        "avatar_url": profile.avatar_url,
        "banner_url": profile.banner_url,
        "top_offers": [{"id": o.id, "title": o.title, "price_label": price_label(o)} for o in top_offers],
        "starting_price_cents": min_price,
        "is_new": is_new,
        "sponsored": sponsored,
        "badge": "Premium+" if plan == "premium" else "",
    }


def lead_out(lead: KitlyLead, offer_title: str | None = None) -> dict:
    return {
        "id": lead.id,
        "kind": lead.kind,
        "name": lead.name,
        "email": lead.email,
        "phone": lead.phone,
        "message": lead.message,
        "budget": lead.budget,
        "offer_id": lead.offer_id,
        "offer_title": offer_title,
        "source": lead.source,
        "status": lead.status,
        "created_at": iso(lead.created_at),
    }


def terms_version_out(v: KitlySellerTermsVersion, with_content: bool = True) -> dict:
    out = {
        "id": v.id,
        "number": v.number,
        "status": v.status,
        "generator_version": v.generator_version,
        "content_hash": v.content_hash,
        "effective_at": iso(v.effective_at),
        "published_at": iso(v.published_at),
        "created_at": iso(v.created_at),
        "updated_at": iso(v.updated_at),
        "disclaimer": TERMS_DISCLAIMER,
    }
    if with_content:
        out["articles"] = v.articles
    return out


TERMS_DISCLAIMER = (
    "Ces conditions générales de vente sont un modèle personnalisé, établi à partir des informations "
    "déclarées par le vendeur, qui en reste seul responsable. Elles ne constituent pas une garantie de "
    "conformité juridique ; une vérification par un professionnel du droit peut être nécessaire. "
    "Kitly fournit l'outil de publication et n'est pas partie au contrat entre le vendeur et son client."
)
