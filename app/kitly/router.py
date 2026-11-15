"""
Point d'entrée Kitly : un seul routeur à inclure dans app/main.py, et une
fonction de démarrage. Toutes les routes vivent sous /kitly.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.db.database import SessionLocal
from app.kitly import catalog
from app.kitly import models  # noqa: F401 — enregistre les tables kitly_* dans Base
from app.kitly.config import KITLY_PUBLIC_HOST, missing_env
from app.kitly.routers import admin, auth, billing, legal, marketplace, public, vitrine

router = APIRouter(prefix="/kitly")
router.include_router(auth.router)
router.include_router(vitrine.router)
router.include_router(legal.router)
router.include_router(public.router)
router.include_router(marketplace.router)
router.include_router(billing.router)
router.include_router(admin.router)


@router.get("/health", tags=["kitly · meta"])
def kitly_health():
    return {"ok": True, "app": "kitly"}


@router.get("/catalog", tags=["kitly · meta"])
def kitly_catalog():
    """Référentiels pour les formulaires du front (catégories, types d'offres, templates…)."""
    return {
        "public_host": KITLY_PUBLIC_HOST,
        "categories": catalog.CATEGORIES,
        "offer_kinds": catalog.OFFER_KINDS,
        "price_modes": sorted(catalog.PRICE_MODES),
        "ctas": sorted(catalog.CTA_KEYS),
        "social_networks": sorted(catalog.SOCIAL_NETWORKS),
        "block_types": {k: sorted(v) for k, v in catalog.BLOCK_TYPES.items()},
        "sections": catalog.SECTION_KEYS,
        "templates": catalog.TEMPLATES,
        "fonts": sorted(catalog.FONTS),
        "button_shapes": sorted(catalog.BUTTON_SHAPES),
        "backgrounds": sorted(catalog.BACKGROUNDS),
        "default_theme": catalog.DEFAULT_THEME,
        "report_reasons": sorted(catalog.REPORT_REASONS),
    }


def kitly_startup() -> None:
    """Appelé au démarrage de l'API (après create_all) : formules par défaut, contrôle de config."""
    from app.kitly.plans import seed_plans

    missing = missing_env()
    if missing:
        print(f"[kitly] ⚠️  Variables manquantes : {', '.join(missing)}")
    else:
        print("[kitly] ✅ Configuration complète.")
    db = SessionLocal()
    try:
        seed_plans(db)
    except Exception as exc:  # ne doit jamais empêcher NAVIRE de démarrer
        print(f"[kitly] ⚠️  Formules non initialisées : {exc}")
    finally:
        db.close()


def kitly_purge_job() -> None:
    """Tâche planifiée : purge du journal de limitation de débit."""
    from app.kitly.utils import purge_rate_events

    db = SessionLocal()
    try:
        purge_rate_events(db)
    except Exception as exc:
        db.rollback()
        print(f"[kitly] ⚠️  Purge des limites échouée : {exc}")
    finally:
        db.close()
