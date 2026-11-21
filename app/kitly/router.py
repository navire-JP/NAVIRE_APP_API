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
from app.kitly.routers import admin, auth, billing, cart, legal, marketplace, pages, public, shop, vitrine

router = APIRouter(prefix="/kitly")
router.include_router(auth.router)
router.include_router(vitrine.router)
router.include_router(legal.router)
router.include_router(public.router)
router.include_router(marketplace.router)
router.include_router(billing.router)
router.include_router(shop.router)
router.include_router(cart.router)
router.include_router(admin.router)
router.include_router(pages.router)


@router.get("/health", tags=["kitly · meta"])
def kitly_health():
    """Santé + diagnostic de configuration (aucune valeur secrète : seulement ce qui manque)."""
    from app.kitly.config import KITLY_ADMIN_EMAILS, KITLY_CORS_ORIGINS, KITLY_FRONTEND_URL

    from app.kitly.emails import public_status

    return {"ok": True, "app": "kitly", "cors_origins": KITLY_CORS_ORIGINS,
            "frontend_url": KITLY_FRONTEND_URL, "missing_env": missing_env(), "email": public_status(),
            # Nombre d'adresses autorisées sur la console /admin (jamais les adresses elles-mêmes).
            "admin_emails_configured": len(KITLY_ADMIN_EMAILS)}


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
        "layouts": sorted(catalog.LAYOUTS),
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
    def _email_check():
        from app.kitly.emails import check_sender

        st = check_sender()
        if st["sender_ok"]:
            print(f"[kitly] ✅ Email — expéditeur {st['sender']} accepté par Brevo.")
        else:
            print(f"[kitly] ⚠️  Email — {st['detail']}")
            if st["usable"]:
                print(f"[kitly]    Expéditeurs utilisables chez Brevo : {', '.join(st['usable'])}")

    import threading

    threading.Thread(target=_email_check, daemon=True, name="kitly-email-check").start()

    db = SessionLocal()
    try:
        seed_plans(db)
    except Exception as exc:  # ne doit jamais empêcher NAVIRE de démarrer
        print(f"[kitly] ⚠️  Formules non initialisées : {exc}")
    finally:
        db.close()


def expire_grants(db) -> int:
    """Termine les formules offertes arrivées à échéance et recalcule la formule des comptes concernés."""
    from sqlalchemy import select

    from app.kitly.models import KitlyPlanGrant, KitlyUser
    from app.kitly.queries import clear_popular_cache
    from app.kitly.routers.billing import recompute_plan
    from app.kitly.utils import now

    expired = db.execute(select(KitlyPlanGrant).where(KitlyPlanGrant.revoked_at.is_(None),
                                                      KitlyPlanGrant.expires_at.is_not(None),
                                                      KitlyPlanGrant.expires_at < now())).scalars().all()
    for g in expired:
        g.revoked_at = now()
        db.flush()
        user = db.get(KitlyUser, g.user_id)
        if user:
            recompute_plan(db, user)
    if expired:
        clear_popular_cache()
    return len(expired)


def kitly_grants_job() -> None:
    """Toutes les 30 minutes : une formule offerte « 30 jours » s'arrête à l'heure prévue."""
    db = SessionLocal()
    try:
        expire_grants(db)
        db.commit()
    except Exception as exc:
        db.rollback()
        print(f"[kitly] ⚠️  Fin des formules offertes échouée : {exc}")
    finally:
        db.close()


def kitly_purge_job() -> None:
    """Tâche planifiée : purge du journal de limitation de débit, fin des formules offertes expirées."""
    from app.kitly.utils import purge_rate_events

    db = SessionLocal()
    try:
        purge_rate_events(db)
        expire_grants(db)
        db.commit()
    except Exception as exc:
        db.rollback()
        print(f"[kitly] ⚠️  Purge des limites échouée : {exc}")
    finally:
        db.close()
