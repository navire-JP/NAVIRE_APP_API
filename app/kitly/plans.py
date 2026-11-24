"""
Formules Kitly et leurs limites.

Les valeurs ci-dessous ne servent qu'à créer les lignes kitly_plans au premier
démarrage : ensuite la base fait foi, et le back-office modifie prix, limites
et textes sans redéploiement (PATCH /kitly/admin/plans/{code}). Une limite
ajoutée ici plus tard est complétée dans les lignes existantes sans écraser
les valeurs déjà réglées.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.kitly.config import KITLY_STRIPE_PRICES
from app.kitly.models import KitlyAdminAction, KitlyPlan, KitlyUser

PLAN_CODES = ("free", "custom", "premium")
PAID_PLANS = ("custom", "premium")

# Ordre de grandeur, pour savoir si un changement est une montée ou une descente.
PLAN_RANK = {"free": 0, "custom": 1, "premium": 2}

DEFAULT_PLANS: list[dict] = [
    {
        "code": "free",
        "name": "Free",
        "tagline": "Pour créer sa première vitrine professionnelle.",
        "price_cents": 0,
        "position": 0,
        "limits": {
            "offers_max": 5,
            "portfolio_max": 6,
            "images_per_offer": 3,
            "blocks_max": 10,
            "custom_domain": False,
            "advanced_theme": False,
            "appointments": False,
            "stats": False,
            "remove_branding": False,
            "marketplace": False,
            "marketplace_boost": False,
            "acquisition_stats": False,
            "custom_font": False,
        },
        "features": [
            "Vitrine publique et URL kitly.fr/ton-nom",
            "Présentation du profil",
            "Personnalisation basique",
            "Portfolio limité",
            "Quelques produits et prestations",
            "Formulaire de contact",
        ],
    },
    {
        "code": "custom",
        "name": "Custom",
        "tagline": "Pour personnaliser et développer sa vitrine professionnelle.",
        "price_cents": 700,
        "position": 1,
        "limits": {
            "offers_max": 25,
            "portfolio_max": 30,
            "images_per_offer": 8,
            "blocks_max": 40,
            "custom_domain": True,
            "advanced_theme": True,
            "appointments": True,
            "stats": True,
            "remove_branding": True,
            "marketplace": False,
            "marketplace_boost": False,
            "acquisition_stats": False,
            "custom_font": False,
        },
        "features": [
            "Tout Free",
            "Domaine personnalisé",
            "Personnalisation graphique avancée",
            "Plus de produits et prestations",
            "Rendez-vous et disponibilités",
            "Statistiques de fréquentation",
            "Sans branding Kitly",
        ],
    },
    {
        "code": "premium",
        "name": "Premium+",
        "tagline": "Pour développer son activité et accéder à la marketplace.",
        "price_cents": 1490,
        "position": 2,
        "limits": {
            "offers_max": 100,
            "portfolio_max": 100,
            "images_per_offer": 12,
            "blocks_max": 100,
            "custom_domain": True,
            "advanced_theme": True,
            "appointments": True,
            "stats": True,
            "remove_branding": True,
            "marketplace": True,
            "marketplace_boost": True,
            "acquisition_stats": True,
            "custom_font": True,
        },
        "features": [
            "Tout Custom",
            "Référencement dans la marketplace",
            "Présence dans les résultats de recherche",
            "Profil enrichi et mise en avant des prestations",
            "Statistiques d'acquisition (visites marketplace)",
            "Ta propre police d'écriture sur ta vitrine",
        ],
    },
]


# Avantages ajoutés après coup aux formules déjà en base (code, clé, texte). Chacun n'est
# inscrit qu'une fois, noté dans le journal admin (action « add_feature ») : une ligne retirée
# ensuite depuis la console ne revient pas au démarrage suivant.
FEATURE_ADDITIONS: list[tuple[str, str, str]] = [
    ("premium", "custom_font", "Ta propre police d'écriture sur ta vitrine"),
]


def seed_plans(db: Session) -> None:
    """Crée les formules manquantes, complète les limites absentes et ajoute les nouveaux avantages."""
    existing = {p.code: p for p in db.execute(select(KitlyPlan)).scalars()}
    for spec in DEFAULT_PLANS:
        plan = existing.get(spec["code"])
        if plan is None:
            plan = KitlyPlan(**spec)
            db.add(plan)
            existing[spec["code"]] = plan
            continue
        merged = {**spec["limits"], **(plan.limits or {})}
        if merged != plan.limits:
            plan.limits = merged
    for code, key, text in FEATURE_ADDITIONS:
        plan, target = existing.get(code), f"{code}:{key}"
        done = db.execute(select(KitlyAdminAction.id).where(
            KitlyAdminAction.action == "add_feature", KitlyAdminAction.target_type == "plan",
            KitlyAdminAction.target_id == target)).first()
        if plan is None or done:
            continue
        if text not in (plan.features or []):
            plan.features = [*(plan.features or []), text]  # nouvelle liste : SQLAlchemy détecte la modification
        db.add(KitlyAdminAction(admin_email="système", action="add_feature", target_type="plan",
                                target_id=target, details={"feature": text}))
    db.commit()


def get_plan(db: Session, code: str) -> KitlyPlan:
    plan = db.get(KitlyPlan, code)
    if plan is None:
        seed_plans(db)
        plan = db.get(KitlyPlan, code) or db.get(KitlyPlan, "free")
    return plan


def plan_limits(db: Session, user: KitlyUser) -> dict:
    return dict(get_plan(db, user.plan_code or "free").limits or {})


def stripe_price_for(plan: KitlyPlan) -> str:
    return plan.stripe_price_id or KITLY_STRIPE_PRICES.get(plan.code, "")


def plan_for_price(db: Session, price_id: str) -> str | None:
    for plan in db.execute(select(KitlyPlan)).scalars():
        if price_id and stripe_price_for(plan) == price_id:
            return plan.code
    return None


def plan_out(plan: KitlyPlan) -> dict:
    return {
        "code": plan.code,
        "name": plan.name,
        "tagline": plan.tagline,
        "price_cents": plan.price_cents,
        "currency": plan.currency,
        "interval": plan.interval,
        "price_label": _price_label(plan),
        "features": plan.features,
        "limits": plan.limits,
    }


def _price_label(plan: KitlyPlan) -> str:
    if not plan.price_cents:
        return "0 €"
    euros = f"{plan.price_cents / 100:.2f}".replace(".", ",").replace(",00", "")
    return f"{euros} € / mois"
