"""Schémas d'entrée Kitly (Pydantic v2). Les sorties sont des dicts (serializers.py)."""

from __future__ import annotations

import re
import secrets
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.kitly import catalog

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_URL = re.compile(r"^(https?://|mailto:|tel:)", re.I)


def _url_or_empty(v: str | None) -> str:
    v = (v or "").strip()
    if v and not _URL.match(v):
        v = "https://" + v
    if len(v) > 2000:
        raise ValueError("Lien trop long.")
    return v


def _password_ok(v: str) -> str:
    if len(v) < 8:
        raise ValueError("8 caractères minimum.")
    if len(v) > 128:
        raise ValueError("128 caractères maximum.")
    if not re.search(r"[A-Za-z]", v) or not re.search(r"\d", v):
        raise ValueError("Au moins une lettre et un chiffre.")
    return v


class _In(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


# ============================================================
# Auth & compte
# ============================================================

class RegisterIn(_In):
    first_name: str = Field(min_length=1, max_length=80)
    last_name: str = Field(min_length=1, max_length=80)
    email: EmailStr
    password: str
    password_confirm: str
    accept_cgu: bool
    ack_privacy: bool
    marketing_opt_in: bool = False

    _pw = field_validator("password")(lambda cls, v: _password_ok(v))

    @model_validator(mode="after")
    def _check(self):
        if self.password != self.password_confirm:
            raise ValueError("Les mots de passe ne correspondent pas.")
        if not self.accept_cgu:
            raise ValueError("L'acceptation des CGU est nécessaire pour créer un compte.")
        if not self.ack_privacy:
            raise ValueError("Merci de prendre connaissance de la politique de confidentialité.")
        return self


class LoginIn(_In):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class CodeIn(_In):
    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")


class ForgotIn(_In):
    email: EmailStr


class ResetIn(_In):
    email: EmailStr
    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")
    new_password: str
    # Compte créé par l'équipe Kitly : CGU acceptées à l'activation (premier mot de passe).
    accept_cgu: bool = False
    ack_privacy: bool = False

    _pw = field_validator("new_password")(lambda cls, v: _password_ok(v))


class AccountUpdateIn(_In):
    first_name: Optional[str] = Field(default=None, min_length=1, max_length=80)
    last_name: Optional[str] = Field(default=None, min_length=1, max_length=80)
    marketing_opt_in: Optional[bool] = None


class PasswordChangeIn(_In):
    current_password: str
    new_password: str

    _pw = field_validator("new_password")(lambda cls, v: _password_ok(v))


class AccountDeleteIn(_In):
    password: str
    confirm: Literal["SUPPRIMER"]


# ============================================================
# Vitrine
# ============================================================

class SocialIn(_In):
    network: str
    url: str

    @field_validator("network")
    @classmethod
    def _net(cls, v):
        v = v.lower()
        if v not in catalog.SOCIAL_NETWORKS:
            raise ValueError("Réseau non pris en charge.")
        return v

    _u = field_validator("url")(lambda cls, v: _url_or_empty(v))


class StatIn(_In):
    label: str = Field(max_length=30)
    value: str = Field(max_length=20)


class BlockIn(BaseModel):
    """Bloc libre : seuls les champs prévus pour son type sont conservés."""

    model_config = ConfigDict(extra="allow")
    id: Optional[str] = None
    type: str
    visible: bool = True

    @model_validator(mode="after")
    def _clean(self):
        if self.type not in catalog.BLOCK_TYPES:
            raise ValueError(f"Type de bloc inconnu : {self.type}.")
        allowed = catalog.BLOCK_TYPES[self.type]
        extra = dict(self.__pydantic_extra__ or {})
        for k in list(extra):
            if k not in allowed:
                extra.pop(k)
                continue
            v = extra[k]
            if k == "offer_id":
                extra[k] = int(v)
            elif k == "images":
                urls = v if isinstance(v, list) else []
                extra[k] = [u for u in (_url_or_empty(str(x)) for x in urls[:catalog.GALLERY_MAX]) if u]
            elif k == "image_side":
                extra[k] = "left" if v == "left" else "right"
            elif k.endswith("url"):
                extra[k] = _url_or_empty(str(v))
            else:
                extra[k] = str(v)[:(5000 if self.type == "section" else 2000) if k == "body" else 200]
        self.__pydantic_extra__ = extra
        if not self.id:
            self.id = secrets.token_hex(4)
        return self


class ThemeColorsIn(_In):
    background: Optional[str] = None
    text: Optional[str] = None
    accent: Optional[str] = None
    button_text: Optional[str] = None

    @field_validator("*")
    @classmethod
    def _hex(cls, v):
        if v is not None and not _HEX.match(v):
            raise ValueError("Couleur au format #RRGGBB.")
        return v


class ThemeBackgroundIn(_In):
    type: str = "solid"
    image_url: str = ""

    @field_validator("type")
    @classmethod
    def _t(cls, v):
        if v not in catalog.BACKGROUNDS:
            raise ValueError("Type de fond inconnu.")
        return v

    _u = field_validator("image_url")(lambda cls, v: _url_or_empty(v))


class ThemeIn(_In):
    template: Optional[str] = None
    layout: Optional[str] = None
    colors: Optional[ThemeColorsIn] = None
    font_heading: Optional[str] = None
    font_body: Optional[str] = None
    button_shape: Optional[str] = None
    background: Optional[ThemeBackgroundIn] = None

    @field_validator("template")
    @classmethod
    def _tpl(cls, v):
        if v is not None and v not in catalog.TEMPLATE_KEYS:
            raise ValueError("Template inconnu.")
        return v

    @field_validator("layout")
    @classmethod
    def _layout(cls, v):
        if v is not None and v not in catalog.LAYOUTS:
            raise ValueError("Mise en page inconnue (site, links).")
        return v

    @field_validator("font_heading", "font_body")
    @classmethod
    def _font(cls, v):
        if v is not None and v not in catalog.FONTS:
            raise ValueError("Typographie non disponible.")
        return v

    @field_validator("button_shape")
    @classmethod
    def _shape(cls, v):
        if v is not None and v not in catalog.BUTTON_SHAPES:
            raise ValueError("Forme de bouton inconnue.")
        return v


class SectionIn(_In):
    key: str
    visible: bool = True

    @field_validator("key")
    @classmethod
    def _k(cls, v):
        if v not in catalog.SECTION_KEYS:
            raise ValueError("Section inconnue.")
        return v


class ProfileIn(_In):
    """Création / mise à jour partielle de la vitrine (autosave : tout est optionnel)."""

    slug: Optional[str] = Field(default=None, max_length=40)
    display_name: Optional[str] = Field(default=None, max_length=80)
    headline: Optional[str] = Field(default=None, max_length=120)
    category: Optional[str] = None
    specialties: Optional[list[str]] = Field(default=None, max_length=12)
    location: Optional[str] = Field(default=None, max_length=120)
    bio: Optional[str] = Field(default=None, max_length=300)
    about: Optional[str] = Field(default=None, max_length=8000)
    experience: Optional[str] = Field(default=None, max_length=8000)
    skills: Optional[list[str]] = Field(default=None, max_length=30)
    languages: Optional[list[str]] = Field(default=None, max_length=12)
    avatar_url: Optional[str] = None
    banner_url: Optional[str] = None
    logo_url: Optional[str] = None
    contact_email: Optional[EmailStr | Literal[""]] = None
    phone: Optional[str] = Field(default=None, max_length=40)
    website: Optional[str] = None
    socials: Optional[list[SocialIn]] = Field(default=None, max_length=20)
    stats: Optional[list[StatIn]] = Field(default=None, max_length=6)
    blocks: Optional[list[BlockIn]] = None
    theme: Optional[ThemeIn] = None
    sections: Optional[list[SectionIn]] = None
    custom_domain: Optional[str] = Field(default=None, max_length=253)
    marketplace_opt_in: Optional[bool] = None
    promoted: Optional[bool] = None

    @field_validator("slug")
    @classmethod
    def _slug(cls, v):
        return v.lower() if v else v

    @field_validator("category")
    @classmethod
    def _cat(cls, v):
        if v not in (None, "") and v not in catalog.CATEGORY_KEYS:
            raise ValueError("Catégorie inconnue.")
        return v

    @field_validator("specialties", "skills", "languages")
    @classmethod
    def _tags(cls, v):
        if v is None:
            return v
        return [t.strip()[:40] for t in v if t and t.strip()]

    @field_validator("avatar_url", "banner_url", "logo_url", "website")
    @classmethod
    def _urls(cls, v):
        return None if v is None else _url_or_empty(v)

    @field_validator("custom_domain")
    @classmethod
    def _domain(cls, v):
        if v is None:
            return v
        v = v.strip().lower().removeprefix("https://").removeprefix("http://").strip("/")
        if v and not re.match(r"^(?=.{4,253}$)([a-z0-9-]{1,63}\.)+[a-z]{2,}$", v):
            raise ValueError("Nom de domaine invalide (ex. : www.mon-site.fr).")
        return v

    @field_validator("sections")
    @classmethod
    def _sections_unique(cls, v):
        if v is not None and len({s.key for s in v}) != len(v):
            raise ValueError("Section en double.")
        return v


class ReorderIn(_In):
    ids: list[int] = Field(min_length=1, max_length=500)


# ============================================================
# Portfolio
# ============================================================

class MediaItemIn(_In):
    type: Literal["image", "video"] = "image"
    url: str
    caption: str = Field(default="", max_length=200)

    _u = field_validator("url")(lambda cls, v: _url_or_empty(v))


class PortfolioIn(_In):
    title: Optional[str] = Field(default=None, min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=8000)
    cover_url: Optional[str] = None
    media: Optional[list[MediaItemIn]] = Field(default=None, max_length=30)
    client_name: Optional[str] = Field(default=None, max_length=120)
    external_url: Optional[str] = None
    tags: Optional[list[str]] = Field(default=None, max_length=12)
    is_visible: Optional[bool] = None

    @field_validator("cover_url", "external_url")
    @classmethod
    def _urls(cls, v):
        return None if v is None else _url_or_empty(v)


# ============================================================
# Offres — champs propres à chaque catégorie
# ============================================================

class _Details(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class VariantIn(_Details):
    id: Optional[str] = None
    label: str = Field(min_length=1, max_length=60)  # « M / Noir »
    sku: str = Field(default="", max_length=60)
    price_cents: Optional[int] = Field(default=None, ge=0, le=10_000_000)
    stock: Optional[int] = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _id(self):
        self.id = self.id or secrets.token_hex(4)
        return self


class ShippingOptionIn(_Details):
    label: str = Field(min_length=1, max_length=60)
    price_cents: int = Field(ge=0, le=1_000_000)
    delay: str = Field(default="", max_length=60)


class PhysicalDetails(_Details):
    sku: str = Field(default="", max_length=60)
    stock: Optional[int] = Field(default=None, ge=0)  # None = non suivi
    variants: list[VariantIn] = Field(default_factory=list, max_length=100)
    weight_grams: Optional[int] = Field(default=None, ge=0)
    dimensions: str = Field(default="", max_length=60)
    shipping_options: list[ShippingOptionIn] = Field(default_factory=list, max_length=10)
    delivery_zones: list[Literal["fr_metro", "fr_dom_tom", "eu", "world"]] = Field(default_factory=list)
    delivery_delay: str = Field(default="", max_length=80)
    pickup_available: bool = False
    pickup_location: str = Field(default="", max_length=160)


class DigitalDetails(_Details):
    file_format: str = Field(default="", max_length=40)  # PDF, Figma, Notion…
    delivery_mode: Literal["download", "email_link", "account_access"] = "download"
    access_duration_days: Optional[int] = Field(default=None, ge=1, le=3650)
    download_limit: Optional[int] = Field(default=None, ge=1, le=100)
    license: str = Field(default="", max_length=300)
    # Lien privé (Drive, Notion, Dropbox…) envoyé à l'acheteur après paiement ; jamais public.
    delivery_url: str = Field(default="", max_length=500)

    @field_validator("delivery_url")
    @classmethod
    def _url(cls, v: str) -> str:
        v = (v or "").strip()
        if v and not re.match(r"^https?://", v, re.I):
            v = "https://" + v
        if v and not re.match(r"^https?://[^\s/$.?#].[^\s]*$", v, re.I):
            raise ValueError("Lien de livraison invalide.")
        return v


class ServiceDetails(_Details):
    duration_label: str = Field(default="", max_length=60)  # « 2 semaines »
    delay_min_days: Optional[int] = Field(default=None, ge=0, le=730)
    delay_max_days: Optional[int] = Field(default=None, ge=0, le=730)
    deliverables: list[str] = Field(default_factory=list, max_length=20)
    revisions_included: Optional[int] = Field(default=None, ge=0, le=100)
    location_mode: Literal["remote", "onsite", "both"] = "remote"
    location: str = Field(default="", max_length=120)
    conditions: str = Field(default="", max_length=2000)


class AppointmentDetails(_Details):
    duration_minutes: int = Field(default=60, ge=5, le=24 * 60)
    location_mode: Literal["visio", "onsite", "phone"] = "visio"
    location: str = Field(default="", max_length=160)
    booking_mode: Literal["request", "instant"] = "request"
    auto_confirm: bool = False
    buffer_minutes: int = Field(default=0, ge=0, le=240)


DETAILS_MODELS: dict[str, type[_Details]] = {
    "physical": PhysicalDetails,
    "digital": DigitalDetails,
    "service": ServiceDetails,
    "appointment": AppointmentDetails,
}


class OfferIn(_In):
    kind: Optional[str] = None
    title: Optional[str] = Field(default=None, min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=8000)
    price_mode: Optional[str] = None
    price_cents: Optional[int] = Field(default=None, ge=0, le=10_000_000)
    price_max_cents: Optional[int] = Field(default=None, ge=0, le=10_000_000)
    vat_rate_bps: Optional[int] = Field(default=None, ge=0, le=10_000)
    images: Optional[list[str]] = None
    badge: Optional[str] = Field(default=None, max_length=30)
    cta: Optional[str] = None
    details: Optional[dict] = None
    status: Optional[Literal["draft", "published", "archived"]] = None
    featured: Optional[bool] = None

    @field_validator("kind")
    @classmethod
    def _kind(cls, v):
        if v is not None and v not in catalog.OFFER_KIND_KEYS:
            raise ValueError("Catégorie d'offre inconnue (physical, digital, service, appointment).")
        return v

    @field_validator("price_mode")
    @classmethod
    def _pm(cls, v):
        if v is not None and v not in catalog.PRICE_MODES:
            raise ValueError("Mode de prix inconnu (fixed, range, quote, free).")
        return v

    @field_validator("cta")
    @classmethod
    def _cta(cls, v):
        if v is not None and v not in catalog.CTA_KEYS:
            raise ValueError("Action inconnue (contact, quote, book, order).")
        return v

    @field_validator("images")
    @classmethod
    def _imgs(cls, v):
        return None if v is None else [_url_or_empty(u) for u in v if u]


# ============================================================
# Public : demandes et signalements
# ============================================================

class LeadIn(_In):
    kind: Literal["contact", "quote", "booking"] = "contact"
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    phone: str = Field(default="", max_length=40)
    message: str = Field(min_length=1, max_length=5000)
    budget: str = Field(default="", max_length=60)
    offer_id: Optional[int] = None
    source: Literal["direct", "marketplace"] = "direct"
    consent: bool = Field(description="Accepte que ses données soient transmises au freelance")
    website: str = ""  # pot de miel : un humain ne le voit pas, un robot le remplit

    @model_validator(mode="after")
    def _c(self):
        if not self.consent:
            raise ValueError("Merci d'accepter la transmission de ta demande au professionnel.")
        return self


class OrderCheckoutIn(_In):
    """Achat d'une offre depuis une vitrine (Stripe Checkout, encaissé par le freelance)."""
    email: EmailStr
    name: str = Field(default="", max_length=120)
    quantity: int = Field(default=1, ge=1, le=10)
    variant_id: Optional[str] = Field(default=None, max_length=20)
    # Index de l'option de livraison choisie, ou « pickup » (retrait sur place).
    delivery: Optional[str] = Field(default=None, max_length=10)
    accept_terms: bool = False
    waive_withdrawal: bool = False   # contenu numérique : accès immédiat, renonciation au droit de rétractation
    early_start: bool = False        # prestation : exécution demandée avant la fin du délai de rétractation
    source: Literal["direct", "marketplace"] = "direct"
    return_url: str = Field(default="", max_length=600)

    @model_validator(mode="after")
    def _terms(self):
        if not self.accept_terms:
            raise ValueError("Merci d'accepter les conditions générales de vente du vendeur.")
        return self


class CartItemIn(_In):
    """Ligne de panier (gardée dans le navigateur, revalidée par le serveur à chaque appel)."""
    offer_id: int
    variant_id: Optional[str] = Field(default=None, max_length=20)
    quantity: int = Field(default=1, ge=1, le=10)
    # Index de l'option de livraison choisie, ou « pickup » (retrait sur place).
    delivery: Optional[str] = Field(default=None, max_length=10)
    source: Literal["direct", "marketplace"] = "direct"


class CartQuoteIn(_In):
    items: list[CartItemIn] = Field(default_factory=list, max_length=30)


class ShippingAddressIn(_In):
    name: str = Field(min_length=1, max_length=120)
    line1: str = Field(min_length=1, max_length=200)
    line2: str = Field(default="", max_length=200)
    postal_code: str = Field(min_length=1, max_length=20)
    city: str = Field(min_length=1, max_length=120)
    country: str = Field(default="FR", min_length=2, max_length=2)


class CartCheckoutIn(_In):
    """Paiement d'un panier (plusieurs vendeurs) : formulaire de carte intégré à la page /checkout."""
    items: list[CartItemIn] = Field(min_length=1, max_length=30)
    email: EmailStr
    name: str = Field(min_length=1, max_length=120)
    phone: str = Field(default="", max_length=40)
    address: Optional[ShippingAddressIn] = None
    accept_terms: bool = False
    waive_withdrawal: bool = False   # contenus numériques : accès immédiat, renonciation au droit de rétractation
    early_start: bool = False        # prestations : exécution avant la fin du délai de rétractation
    replaces: Optional[str] = Field(default=None, max_length=12)  # panier précédent non payé, annulé

    @model_validator(mode="after")
    def _terms(self):
        if not self.accept_terms:
            raise ValueError("Merci d'accepter les conditions générales de vente des vendeurs.")
        return self


class OrderFulfillIn(_In):
    """Vente marquée expédiée / traitée par le freelance."""
    fulfilled: bool = True
    tracking: str = Field(default="", max_length=120)   # numéro ou lien de suivi du colis
    notify: bool = True                                 # prévenir l'acheteur par email


class LeadUpdateIn(_In):
    status: Literal["new", "read", "replied", "archived", "spam"]


class ViewIn(_In):
    source: Literal["direct", "marketplace"] = "direct"


class ReportIn(_In):
    reason: str
    details: str = Field(default="", max_length=5000)
    email: Optional[EmailStr] = None

    @field_validator("reason")
    @classmethod
    def _r(cls, v):
        if v not in catalog.REPORT_REASONS:
            raise ValueError("Motif inconnu.")
        return v


# ============================================================
# Juridique vendeur
# ============================================================

class LegalProfileIn(_In):
    seller_type: Optional[Literal["individual", "company"]] = None
    is_professional: Optional[bool] = None
    legal_name: Optional[str] = Field(default=None, max_length=160)
    trade_name: Optional[str] = Field(default=None, max_length=160)
    legal_form: Optional[str] = Field(default=None, max_length=60)
    siren: Optional[str] = Field(default=None, max_length=20)
    vat_number: Optional[str] = Field(default=None, max_length=30)
    vat_exempt: Optional[bool] = None
    rcs_rm: Optional[str] = Field(default=None, max_length=120)
    address_line: Optional[str] = Field(default=None, max_length=200)
    postal_code: Optional[str] = Field(default=None, max_length=20)
    city: Optional[str] = Field(default=None, max_length=100)
    country: Optional[str] = Field(default=None, min_length=2, max_length=2)
    professional_email: Optional[EmailStr | Literal[""]] = None
    phone: Optional[str] = Field(default=None, max_length=40)
    mediator_name: Optional[str] = Field(default=None, max_length=160)
    mediator_url: Optional[str] = None

    @field_validator("siren")
    @classmethod
    def _siren(cls, v):
        if v in (None, ""):
            return v
        digits = re.sub(r"\s", "", v)
        if not re.fullmatch(r"\d{9}|\d{14}", digits):
            raise ValueError("SIREN (9 chiffres) ou SIRET (14 chiffres).")
        return digits

    @field_validator("country")
    @classmethod
    def _country(cls, v):
        return v.upper() if v else v

    @field_validator("mediator_url")
    @classmethod
    def _murl(cls, v):
        return None if v is None else _url_or_empty(v)


class LegalAnswersIn(_In):
    answers: dict


class TermsArticleIn(_In):
    key: str = Field(min_length=1, max_length=40)
    title: str = Field(min_length=1, max_length=160)
    body: str = Field(min_length=1, max_length=20000)


class TermsDraftIn(_In):
    articles: list[TermsArticleIn] = Field(min_length=1, max_length=40)


class TermsPublishIn(_In):
    # Le vendeur accepte explicitement la version qu'il publie.
    accept: bool

    @model_validator(mode="after")
    def _a(self):
        if not self.accept:
            raise ValueError("Tu dois accepter cette version de tes CGV pour la publier.")
        return self


# ============================================================
# Paiements
# ============================================================

class CheckoutIn(_In):
    plan: Literal["custom", "premium"]


class AdminPlanIn(_In):
    name: Optional[str] = Field(default=None, max_length=40)
    tagline: Optional[str] = Field(default=None, max_length=200)
    price_cents: Optional[int] = Field(default=None, ge=0, le=1_000_000)
    stripe_price_id: Optional[str] = Field(default=None, max_length=120)
    limits: Optional[dict] = None
    features: Optional[list[str]] = None
    is_active: Optional[bool] = None


class SuspendIn(_In):
    reason: str = Field(min_length=3, max_length=300)


# ============================================================
# Back-office
# ============================================================

class GrantIn(_In):
    plan: Literal["custom", "premium"]
    reason: str = Field(min_length=3, max_length=300)
    days: Optional[int] = Field(default=None, ge=1, le=3650)  # vide = sans date de fin


class AdminCreateUserIn(_In):
    """Compte créé par l'équipe (partenaire, bêta-testeur…), avec ou sans formule offerte."""
    email: EmailStr
    first_name: str = Field(min_length=1, max_length=80)
    last_name: str = Field(default="", max_length=80)
    plan: Literal["free", "custom", "premium"] = "premium"
    days: Optional[int] = Field(default=None, ge=1, le=3650)  # vide = sans date de fin
    reason: str = Field(default="", max_length=300)
    send_invite: bool = True

    @model_validator(mode="after")
    def _why(self):
        if self.plan != "free" and len(self.reason.strip()) < 3:
            raise ValueError("Indique le motif de la formule offerte (partenaire, test…).")
        return self


class ReasonIn(_In):
    reason: str = Field(min_length=3, max_length=300)


class AdminDeleteIn(_In):
    confirm: Literal["SUPPRIMER"]
    reason: str = Field(min_length=3, max_length=300)


class EmailTestIn(_In):
    to: Optional[EmailStr] = None
