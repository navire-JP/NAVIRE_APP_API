"""
Modèle de données Kitly — toutes les tables sont préfixées « kitly_ ».

Elles s'enregistrent dans la même Base que NAVIRE (même base de données, même
create_all au démarrage) mais aucune ne référence une table NAVIRE : les deux
produits peuvent être séparés plus tard sans migration croisée.

Choix structurants, pensés dès le départ (cf. cahier des charges) :
  - une seule table d'offres (KitlyOffer) pour les 4 catégories — produit
    physique, produit numérique, prestation, rendez-vous —, les champs propres
    à chaque catégorie vivent dans `details` (validé par catégorie) ;
  - les réponses au questionnaire juridique et les CGV versionnées du vendeur
    existent dès maintenant, même si le générateur arrive plus tard : une
    commande pointe vers la version de CGV acceptée ;
  - chaque commande porte son canal (lien direct / marketplace) : seule la
    marketplace donne lieu à commission ;
  - aucune IP n'est stockée en clair (statistiques par empreinte salée).
"""

from __future__ import annotations

import secrets
from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _public_ref() -> str:
    return "K" + secrets.token_hex(5).upper()


# ============================================================
# Comptes
# ============================================================

class KitlyUser(Base):
    """Compte privé Kitly (distinct du profil public, distinct des comptes NAVIRE)."""

    __tablename__ = "kitly_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    first_name: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    last_name: Mapped[str] = mapped_column(String(80), default="", nullable=False)

    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Incrémenté pour révoquer tous les jetons (« se déconnecter partout »,
    # changement de mot de passe).
    token_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Abonnement courant (dénormalisé depuis KitlySubscription pour les lectures)
    plan_code: Mapped[str] = mapped_column(String(20), default="free", nullable=False)
    stripe_customer_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    # Consentements (preuve : date + version du document)
    cgu_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cgu_version: Mapped[str] = mapped_column(String(20), default="", nullable=False)
    privacy_ack_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    privacy_version: Mapped[str] = mapped_column(String(20), default="", nullable=False)
    marketing_opt_in: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)

    profile = relationship("KitlyProfile", back_populates="user", uselist=False, cascade="all, delete-orphan")
    subscriptions = relationship("KitlySubscription", back_populates="user", cascade="all, delete-orphan")
    connected_account = relationship(
        "KitlyConnectedAccount", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )


class KitlyEmailCode(Base):
    """Code à 6 chiffres (haché) : vérification d'adresse ou mot de passe oublié."""

    __tablename__ = "kitly_email_codes"
    __table_args__ = (UniqueConstraint("email", "purpose", name="uq_kitly_email_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(320), index=True, nullable=False)
    purpose: Mapped[str] = mapped_column(String(20), nullable=False)  # verify | reset
    code_hash: Mapped[str] = mapped_column(String, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class KitlyRateEvent(Base):
    """Journal minimal pour la limitation de débit (connexion, formulaires publics)."""

    __tablename__ = "kitly_rate_events"
    __table_args__ = (Index("ix_kitly_rate_key_time", "key", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


# ============================================================
# Formules (configurables depuis le back-office)
# ============================================================

class KitlyPlan(Base):
    __tablename__ = "kitly_plans"

    code: Mapped[str] = mapped_column(String(20), primary_key=True)  # free | custom | premium
    name: Mapped[str] = mapped_column(String(40), nullable=False)
    tagline: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    price_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # TTC
    currency: Mapped[str] = mapped_column(String(3), default="eur", nullable=False)
    interval: Mapped[str] = mapped_column(String(10), default="month", nullable=False)
    # Price ID Stripe ; si vide, lu dans KITLY_STRIPE_PRICE_<CODE>
    stripe_price_id: Mapped[str] = mapped_column(String, default="", nullable=False)
    limits: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    features: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)


class KitlySubscription(Base):
    """Abonnement Stripe Billing (compte Stripe Kitly)."""

    __tablename__ = "kitly_subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("kitly_users.id", ondelete="CASCADE"), index=True, nullable=False)
    stripe_subscription_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    plan_code: Mapped[str] = mapped_column(String(20), nullable=False)
    # Statut Stripe : active | trialing | past_due | unpaid | canceled | incomplete…
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)

    user = relationship("KitlyUser", back_populates="subscriptions")


class KitlyStripeEvent(Base):
    """Événements webhook déjà traités (Stripe peut relivrer)."""

    __tablename__ = "kitly_stripe_events"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # evt_…
    type: Mapped[str] = mapped_column(String(80), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class KitlyConnectedAccount(Base):
    """Compte Stripe Connect du freelance (encaissement des ventes)."""

    __tablename__ = "kitly_connected_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("kitly_users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    stripe_account_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    country: Mapped[str] = mapped_column(String(2), default="FR", nullable=False)
    # Seule source de vérité : les champs renvoyés par Stripe (jamais le retour
    # du navigateur). Le compte n'est « actif » que si Stripe le dit.
    details_submitted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    charges_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    payouts_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    requirements_due: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    disabled_reason: Mapped[str] = mapped_column(String, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)

    user = relationship("KitlyUser", back_populates="connected_account")


# ============================================================
# Vitrine
# ============================================================

class KitlyProfile(Base):
    """
    Vitrine publique d'un freelance (kitly.fr/<slug>). Séparée du compte : rien
    de ce qui est ici n'est privé, sauf ce que les routes publiques filtrent.
    """

    __tablename__ = "kitly_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("kitly_users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    slug: Mapped[str] = mapped_column(String(40), unique=True, index=True, nullable=False)

    # Présentation professionnelle
    display_name: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    headline: Mapped[str] = mapped_column(String(120), default="", nullable=False)  # métier
    category: Mapped[str] = mapped_column(String(40), default="", index=True, nullable=False)
    specialties: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    location: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    bio: Mapped[str] = mapped_column(String(300), default="", nullable=False)  # accroche courte
    about: Mapped[str] = mapped_column(Text, default="", nullable=False)  # présentation détaillée
    experience: Mapped[str] = mapped_column(Text, default="", nullable=False)
    skills: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    languages: Mapped[list] = mapped_column(JSON, default=list, nullable=False)

    avatar_url: Mapped[str] = mapped_column(String, default="", nullable=False)
    banner_url: Mapped[str] = mapped_column(String, default="", nullable=False)
    logo_url: Mapped[str] = mapped_column(String, default="", nullable=False)

    # Coordonnées professionnelles affichées
    contact_email: Mapped[str] = mapped_column(String(320), default="", nullable=False)
    phone: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    website: Mapped[str] = mapped_column(String, default="", nullable=False)

    # Côté « Linktree » : réseaux, chiffres clés, blocs libres (liens, vidéos…)
    socials: Mapped[list] = mapped_column(JSON, default=list, nullable=False)  # [{network,url}]
    stats: Mapped[list] = mapped_column(JSON, default=list, nullable=False)  # [{label,value}]
    blocks: Mapped[list] = mapped_column(JSON, default=list, nullable=False)  # [{id,type,…}]

    # Personnalisation : template, couleurs, typos, fond, forme des boutons
    theme: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    # Ordre et visibilité des sections : [{key, visible}]
    sections: Mapped[list] = mapped_column(JSON, default=list, nullable=False)

    custom_domain: Mapped[str | None] = mapped_column(String(253), unique=True, nullable=True)

    # Publication
    is_published: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Première publication : sert à « Les nouveaux sur Kitly » (une dépublication
    # suivie d'une republication ne fait pas repasser le profil pour « nouveau »).
    first_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)

    # Marketplace
    marketplace_opt_in: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Mise en avant payante (Premium+) — toujours affichée comme « sponsorisé »
    promoted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Texte de recherche normalisé (minuscules, sans accents) : nom, métier,
    # spécialités, compétences, accroche, lieu et titres des offres publiées.
    search_text: Mapped[str] = mapped_column(Text, default="", nullable=False)

    # Modération
    is_suspended: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    suspended_reason: Mapped[str] = mapped_column(String(300), default="", nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)

    user = relationship("KitlyUser", back_populates="profile")
    offers = relationship(
        "KitlyOffer", back_populates="profile", cascade="all, delete-orphan", order_by="KitlyOffer.position"
    )
    portfolio = relationship(
        "KitlyPortfolioProject",
        back_populates="profile",
        cascade="all, delete-orphan",
        order_by="KitlyPortfolioProject.position",
    )
    legal_profile = relationship(
        "KitlySellerLegalProfile", back_populates="profile", uselist=False, cascade="all, delete-orphan"
    )
    terms = relationship("KitlySellerTerms", back_populates="profile", uselist=False, cascade="all, delete-orphan")


class KitlyPortfolioProject(Base):
    __tablename__ = "kitly_portfolio_projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("kitly_profiles.id", ondelete="CASCADE"), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    cover_url: Mapped[str] = mapped_column(String, default="", nullable=False)
    media: Mapped[list] = mapped_column(JSON, default=list, nullable=False)  # [{type:image|video,url,caption}]
    client_name: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    external_url: Mapped[str] = mapped_column(String, default="", nullable=False)
    tags: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_visible: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)

    profile = relationship("KitlyProfile", back_populates="portfolio")


class KitlyOffer(Base):
    """
    Offre commerciale : produit physique, produit numérique, prestation de
    service ou rendez-vous. Le formulaire et le questionnaire juridique
    dépendent de `kind`.
    """

    __tablename__ = "kitly_offers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("kitly_profiles.id", ondelete="CASCADE"), index=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # physical | digital | service | appointment
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)

    # Prix : fixe, fourchette, sur devis ou gratuit. Montants en centimes TTC.
    price_mode: Mapped[str] = mapped_column(String(10), default="fixed", nullable=False)  # fixed|range|quote|free
    price_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    price_max_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="eur", nullable=False)
    vat_rate_bps: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 2000 = 20 %, None = non applicable

    images: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    badge: Mapped[str] = mapped_column(String(30), default="", nullable=False)  # « E-book », « Template »…
    # Action proposée au client : contact | quote | book | order
    cta: Mapped[str] = mapped_column(String(10), default="contact", nullable=False)
    # Champs propres à la catégorie (stock, variantes, livraison, durée, livrables…)
    details: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    status: Mapped[str] = mapped_column(String(10), default="draft", nullable=False)  # draft|published|archived
    featured: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)

    profile = relationship("KitlyProfile", back_populates="offers")


# ============================================================
# Demandes, visites, signalements
# ============================================================

class KitlyLead(Base):
    """Demande entrante : contact, devis ou demande de rendez-vous."""

    __tablename__ = "kitly_leads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("kitly_profiles.id", ondelete="CASCADE"), index=True, nullable=False)
    offer_id: Mapped[int | None] = mapped_column(ForeignKey("kitly_offers.id", ondelete="SET NULL"), nullable=True)
    kind: Mapped[str] = mapped_column(String(10), default="contact", nullable=False)  # contact|quote|booking
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    phone: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    budget: Mapped[str] = mapped_column(String(60), default="", nullable=False)
    source: Mapped[str] = mapped_column(String(12), default="direct", nullable=False)  # direct | marketplace
    status: Mapped[str] = mapped_column(String(10), default="new", nullable=False)  # new|read|replied|archived|spam
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True, nullable=False)


class KitlyProfileView(Base):
    """
    Visite unique d'une vitrine : au plus une ligne par (vitrine, visiteur, jour).
    Le visiteur est une empreinte salée (IP + navigateur + jour), jamais l'IP.
    Recharger la page ne gonfle donc pas les chiffres, et l'empreinte change
    chaque jour : pas de suivi d'un visiteur dans le temps.
    """

    __tablename__ = "kitly_profile_views"
    __table_args__ = (
        UniqueConstraint("profile_id", "visitor_hash", "day", name="uq_kitly_view_daily"),
        Index("ix_kitly_view_profile_day", "profile_id", "day"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("kitly_profiles.id", ondelete="CASCADE"), nullable=False)
    visitor_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    source: Mapped[str] = mapped_column(String(12), default="direct", nullable=False)  # direct | marketplace
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class KitlyReport(Base):
    """Signalement d'une vitrine (obligation de notification, DSA)."""

    __tablename__ = "kitly_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("kitly_profiles.id", ondelete="CASCADE"), index=True, nullable=False)
    reason: Mapped[str] = mapped_column(String(30), nullable=False)
    details: Mapped[str] = mapped_column(Text, default="", nullable=False)
    reporter_email: Mapped[str] = mapped_column(String(320), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(12), default="open", nullable=False)  # open|reviewed|dismissed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


# ============================================================
# Juridique vendeur
# ============================================================

class KitlySellerLegalProfile(Base):
    """
    Identité juridique du vendeur (privée sauf ce qu'exigent les mentions de
    pied de vitrine). Base de l'identification des vendeurs professionnels
    (DSA) et des déclarations DAC7.
    """

    __tablename__ = "kitly_seller_legal_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("kitly_profiles.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    seller_type: Mapped[str] = mapped_column(String(20), default="individual", nullable=False)  # individual|company
    is_professional: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    legal_name: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    trade_name: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    legal_form: Mapped[str] = mapped_column(String(60), default="", nullable=False)  # EI, micro, SASU…
    siren: Mapped[str] = mapped_column(String(20), default="", nullable=False)
    vat_number: Mapped[str] = mapped_column(String(30), default="", nullable=False)
    vat_exempt: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)  # franchise en base (art. 293 B)
    rcs_rm: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    address_line: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    postal_code: Mapped[str] = mapped_column(String(20), default="", nullable=False)
    city: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    country: Mapped[str] = mapped_column(String(2), default="FR", nullable=False)
    professional_email: Mapped[str] = mapped_column(String(320), default="", nullable=False)
    phone: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    mediator_name: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    mediator_url: Mapped[str] = mapped_column(String, default="", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)

    profile = relationship("KitlyProfile", back_populates="legal_profile")


class KitlyLegalAnswers(Base):
    """
    Réponses du vendeur au questionnaire juridique, par périmètre :
    general | physical | digital | service | appointment. Elles alimenteront
    le générateur de CGV ; elles sont validées contre les règles impératives
    (app/kitly/legal_rules.py) avant enregistrement.
    """

    __tablename__ = "kitly_legal_answers"
    __table_args__ = (UniqueConstraint("profile_id", "scope", name="uq_kitly_legal_scope"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("kitly_profiles.id", ondelete="CASCADE"), index=True, nullable=False)
    scope: Mapped[str] = mapped_column(String(20), nullable=False)
    answers: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    questionnaire_version: Mapped[str] = mapped_column(String(20), default="v1", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)


class KitlySellerTerms(Base):
    """CGV d'un vendeur : pointe vers la version en vigueur."""

    __tablename__ = "kitly_seller_terms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("kitly_profiles.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    current_version_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    profile = relationship("KitlyProfile", back_populates="terms")
    versions = relationship(
        "KitlySellerTermsVersion",
        back_populates="terms",
        cascade="all, delete-orphan",
        order_by="KitlySellerTermsVersion.number",
    )


class KitlySellerTermsVersion(Base):
    """
    Une version des CGV. Brouillon modifiable ; une fois publiée elle devient
    immuable (contenu + empreinte SHA-256), si bien qu'on peut toujours prouver
    quelle version s'appliquait à une commande donnée.
    """

    __tablename__ = "kitly_seller_terms_versions"
    __table_args__ = (UniqueConstraint("terms_id", "number", name="uq_kitly_terms_number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    terms_id: Mapped[int] = mapped_column(ForeignKey("kitly_seller_terms.id", ondelete="CASCADE"), index=True, nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(12), default="draft", nullable=False)  # draft|published|superseded
    # Contenu structuré : [{key, title, body}] (articles)
    articles: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    # Réponses au questionnaire figées au moment de la génération
    answers_snapshot: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    generator_version: Mapped[str] = mapped_column(String(20), default="manual", nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    seller_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)

    terms = relationship("KitlySellerTerms", back_populates="versions")


# ============================================================
# Commandes (Stripe Connect) — modèle posé, parcours d'achat au sprint 6
# ============================================================

class KitlyOrder(Base):
    __tablename__ = "kitly_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    public_ref: Mapped[str] = mapped_column(String(12), unique=True, default=_public_ref, nullable=False)
    profile_id: Mapped[int | None] = mapped_column(ForeignKey("kitly_profiles.id", ondelete="SET NULL"), index=True, nullable=True)
    offer_id: Mapped[int | None] = mapped_column(ForeignKey("kitly_offers.id", ondelete="SET NULL"), nullable=True)
    # Instantané de l'offre au moment de l'achat (titre, prix, variante…)
    offer_snapshot: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    buyer_email: Mapped[str] = mapped_column(String(320), nullable=False)
    buyer_name: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="eur", nullable=False)
    # Canal d'origine : seule la marketplace donne lieu à commission
    channel: Mapped[str] = mapped_column(String(12), default="direct", nullable=False)  # direct | marketplace
    commission_bps: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    commission_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(12), default="pending", nullable=False)  # pending|paid|refunded|canceled|disputed
    stripe_checkout_session_id: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    stripe_payment_intent_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # Version des CGV du vendeur acceptée par l'acheteur
    terms_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("kitly_seller_terms_versions.id", ondelete="RESTRICT"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


def _checkout_ref() -> str:
    return "P" + secrets.token_hex(5).upper()


class KitlyCheckout(Base):
    """
    Panier payé en une fois (plusieurs articles, plusieurs vendeurs).

    Kitly encaisse un seul paiement (PaymentIntent sur le compte Stripe Kitly,
    `transfer_group` = référence), puis reverse à chaque vendeur sa part, nette de la
    commission marketplace (Transfer vers son compte Connect). Chaque article donne une
    `KitlyOrder` chez son vendeur (référence du panier dans `offer_snapshot.checkout_ref`).
    """

    __tablename__ = "kitly_checkouts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    public_ref: Mapped[str] = mapped_column(String(12), unique=True, default=_checkout_ref, nullable=False)
    buyer_email: Mapped[str] = mapped_column(String(320), nullable=False)
    buyer_name: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    buyer_phone: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    shipping_address: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="eur", nullable=False)
    # pending | paid | canceled | refunded | partially_refunded | disputed
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    stripe_payment_intent_id: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    stripe_charge_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # Virements aux vendeurs : [{profile_id, account, amount_cents, transfer_id, error}]
    transfers: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class KitlyTermsAcceptance(Base):
    """
    Preuve d'acceptation des CGV du vendeur par un client : version, empreinte
    du contenu accepté, date, consentements spécifiques (renonciation au droit
    de rétractation pour un contenu numérique, exécution anticipée d'un service).
    """

    __tablename__ = "kitly_terms_acceptances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    terms_version_id: Mapped[int] = mapped_column(
        ForeignKey("kitly_seller_terms_versions.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    order_id: Mapped[int | None] = mapped_column(ForeignKey("kitly_orders.id", ondelete="SET NULL"), nullable=True)
    buyer_email: Mapped[str] = mapped_column(String(320), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    consents: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    visitor_hash: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    user_agent: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


# ============================================================
# Back-office
# ============================================================

class KitlyPlanGrant(Base):
    """
    Formule offerte par un admin (geste commercial, partenaire, test), avec ou
    sans date de fin. La formule effective d'un compte est la plus haute entre
    ses abonnements Stripe actifs et ses formules offertes en cours.
    """

    __tablename__ = "kitly_plan_grants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("kitly_users.id", ondelete="CASCADE"), index=True, nullable=False)
    plan_code: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    granted_by: Mapped[int | None] = mapped_column(Integer, nullable=True)  # id du compte admin
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class KitlyAdminAction(Base):
    """Journal des actions du back-office : qui a fait quoi, sur quoi, quand."""

    __tablename__ = "kitly_admin_actions"
    __table_args__ = (Index("ix_kitly_admin_action_target", "target_type", "target_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    admin_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    admin_email: Mapped[str] = mapped_column(String(320), default="", nullable=False)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    target_type: Mapped[str] = mapped_column(String(20), default="", nullable=False)  # user | profile | report | plan | system
    target_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    details: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True, nullable=False)
