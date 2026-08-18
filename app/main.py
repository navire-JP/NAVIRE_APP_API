from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
import asyncio
import logging
import threading
import os

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import delete

from app.core.config import APP_NAME, APP_VERSION, CORS_ORIGINS, ensure_storage_dirs
from app.db.database import Base, engine, SessionLocal
from app.db import models  # noqa: F401
from app.db.models import QcmSessionHistory
from app.db.migrate_discord_fields import run_discord_migrations
from app.db.auth_migration import run_auth_migration
from app.db.prepa_migration import run_prepa_migration
from app.db.prepa_adjuris_migration import run_prepa_adjuris_migration
from app.db.navire_migration import run_navire_migration, ensure_vector_extension
from app.routers.auth import router as auth_router
from app.routers.admin import router as admin_router
from app.routers.assets import router as assets_router
from app.routers.meta import router as meta_router
from app.routers.users import router as users_router
from app.routers.files import router as files_router
from app.routers.qcm import router as qcm_router
from app.routers.flash import router as flash_router
from app.routers.elo import router as elo_router
from app.routers.admin_console import router as admin_console_router
from app.routers.stats import router as stats_router
from app.routers.subscriptions import router as subscriptions_router, check_expired_subscriptions
from app.routers.veille import router as veille_router
from app.routers.leaderboard import router as leaderboard_router
from app.routers.cab import router as cab_router
from app.routers.discord_bot import router as discord_bot_router
from app.routers.prepa import router as prepa_router
from app.routers.prepa_adjuris import router as prepa_adjuris_router
from app.routers.prepa_adjuris_espace import (
    router as prepa_adjuris_espace_router,
    expirer_acces_adjuris,
)
from app.routers.navire import router as navire_router
# ============================================================
# MEOLES — import isolé
# ============================================================
from app.meoles_site import meoles_models  # noqa: F401 — enregistre CartSession + CartItem dans Base
from app.meoles_site.cart_routes import router as meoles_cart_router
from app.meoles_site.stripe_routes import router as meoles_stripe_router
from app.meoles_site.custom_routes import router as meoles_custom_router
from app.meoles_site.admin_routes import router as meoles_admin_router

# ============================================================
# APScheduler — purge QcmSessionHistory > 6 mois d'inactivité
# ============================================================

def _purge_old_history() -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=180)
    db = SessionLocal()
    try:
        result = db.execute(
            delete(QcmSessionHistory).where(QcmSessionHistory.last_activity_at < cutoff)
        )
        db.commit()
        deleted = result.rowcount
        if deleted:
            print(f"🧹 Purge QcmSessionHistory : {deleted} entrée(s) supprimée(s) (cutoff={cutoff.date()})")
    except Exception as e:
        db.rollback()
        print(f"❌ Purge QcmSessionHistory échouée : {e}")
    finally:
        db.close()


scheduler = BackgroundScheduler(timezone="UTC")
scheduler.add_job(_purge_old_history, trigger="cron", hour=3, minute=0)


def _daily_email_health() -> None:
    """
    Contrôle quotidien de la chaîne d'envoi.

    Le signalement d'une panne d'email est venu d'une élève, vingt heures
    après le début de l'incident. Ce job ne remplace pas une vraie alerte,
    mais il inscrit chaque jour l'état réel dans les logs : une panne devient
    datable au lieu d'être découverte par un prospect.
    """
    try:
        from app.services.email import brevo_diagnostic

        diag = brevo_diagnostic()
        if diag.get("erreur"):
            print(f"[email-health] ⚠️  {diag['erreur']}")
        elif diag.get("sender_is_verified") is False:
            print(
                f"[email-health] ⚠️  Expéditeur {diag.get('sender_configured')} NON validé "
                "chez Brevo — les emails ne partent pas."
            )
        else:
            print(f"[email-health] ✅ Expéditeur {diag.get('sender_configured')} validé.")
    except Exception as exc:
        print(f"[email-health] ⚠️  Contrôle impossible : {exc}")


scheduler.add_job(_daily_email_health, trigger="cron", hour=7, minute=30)
scheduler.add_job(
    check_expired_subscriptions,
    trigger="interval",
    hours=1,
    args=[SessionLocal],
    id="check_expired_subscriptions",
    replace_existing=True,
)
scheduler.add_job(
    expirer_acces_adjuris,
    trigger="interval",
    hours=1,
    args=[SessionLocal],
    id="expirer_acces_adjuris",
    replace_existing=True,
)


def _expire_deepwork_sessions() -> None:
    """
    Filet de sécurité : une session deep work encore « active » après une
    journée entière n'a pas été clôturée normalement (membre resté branché,
    coupure côté bot). Sa durée réelle étant inconnue, elle est marquée périmée
    et sort des statistiques plutôt que de gonfler les totaux.
    """
    from app.services.deepwork import expire_long_sessions

    db = SessionLocal()
    try:
        closed = expire_long_sessions(db)
        if closed:
            print(f"🧹 Deep work : {closed} session(s) périmée(s) clôturée(s)")
    except Exception as e:
        db.rollback()
        print(f"❌ Expiration des sessions deep work échouée : {e}")
    finally:
        db.close()


scheduler.add_job(
    _expire_deepwork_sessions,
    trigger="interval",
    hours=1,
    id="expire_deepwork_sessions",
    replace_existing=True,
)

# ============================================================
# Discord bot — thread daemon
# ============================================================

def _start_discord_bot() -> None:
    if not os.getenv("DISCORD_TOKEN"):
        print("⚠️  DISCORD_TOKEN absent — bot Discord désactivé")
        return

    from app.bot_discord.bot import run_bot

    def _run():
        try:
            asyncio.run(run_bot())
        except Exception as e:
            print(f"❌ Bot Discord crash : {e}")

    threading.Thread(target=_run, daemon=True, name="discord-bot").start()
    print("✅ Bot Discord lancé en thread daemon")


# ============================================================
# Lifespan (startup / shutdown)
# ============================================================

# ============================================================
# Bruit des logs — /health toutes les 5 s noie tout le reste
# ============================================================
# Le probe de Render appelle /health en continu : sans ce filtre, retrouver
# trois lignes utiles dans les logs demande de scroller des minutes. Seule la
# ligne d'accès est masquée ; une erreur sur /health resterait visible.
class _NoHealthAccessLog(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return "/health" not in record.getMessage()


logging.getLogger("uvicorn.access").addFilter(_NoHealthAccessLog())


# ============================================================
# Variables d'environnement — contrôle au démarrage
# ============================================================
# BREVO_API_KEY a pu manquer plusieurs jours sans que rien ne le signale :
# les inscriptions échouaient en silence. Le démarrage dit maintenant ce qui
# manque, et n'empêche jamais le service de monter — une clé Stripe absente
# ne doit pas priver tout le monde de l'application.
REQUIRED_ENV = [
    "DATABASE_URL",
    "JWT_SECRET",
    "BREVO_API_KEY",
    "BREVO_SENDER_EMAIL",
    "STRIPE_SECRET_KEY",
    "STRIPE_WEBHOOK_SECRET",
    "OPENAI_API_KEY",
]


def _check_env() -> None:
    missing = [k for k in REQUIRED_ENV if not os.getenv(k)]
    if missing:
        print(f"[boot] ⚠️  VARIABLES MANQUANTES : {', '.join(missing)}")
        if "BREVO_API_KEY" in missing:
            print("[boot] ⚠️  Aucun email ne partira : inscription impossible.")
    else:
        print("[boot] ✅ Variables d'environnement : toutes présentes.")


def _check_email_sender() -> None:
    """
    Vérifie auprès de Brevo que l'expéditeur configuré est bien validé.

    C'est le piège silencieux : un expéditeur non validé fait accepter l'appel
    par Brevo puis ne délivre rien. Sans ce contrôle, la panne ne se voit que
    lorsqu'un élève signale qu'il n'a jamais reçu son code.
    """
    try:
        from app.services.email import brevo_diagnostic

        diag = brevo_diagnostic()
        if diag.get("erreur"):
            print(f"[boot] ⚠️  Email — {diag['erreur']}")
        elif diag.get("sender_is_verified") is False:
            print(
                f"[boot] ⚠️  Email — expéditeur {diag.get('sender_configured')} "
                "NON validé chez Brevo : les messages seront acceptés puis jamais délivrés."
            )
        elif diag.get("sender_is_verified") is True:
            print(f"[boot] ✅ Email — expéditeur {diag.get('sender_configured')} validé.")
    except Exception as exc:  # ne doit jamais empêcher le démarrage
        print(f"[boot] ⚠️  Contrôle email impossible : {exc}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _check_env()
    _check_email_sender()

    print("🚀 Startup: ensuring storage dirs...")
    ensure_storage_dirs()

    print("🚀 Startup: ensuring pgvector extension...")
    ensure_vector_extension()

    print("🚀 Startup: creating database tables if needed...")
    Base.metadata.create_all(bind=engine)

    print("🚀 Startup: creating navire vector index...")
    run_navire_migration()

    print("🚀 Startup: running auth migrations...")
    run_auth_migration()

    print("🚀 Startup: running discord migrations...")
    run_discord_migrations()

    print("🚀 Startup: running prepasserelle migration...")
    run_prepa_migration()

    print("🚀 Startup: running prepa adjuris migration...")
    run_prepa_adjuris_migration()

    print("🚀 Startup: starting scheduler...")
    scheduler.start()

    print("🚀 Startup: starting Discord bot...")
    _start_discord_bot()

    yield

    print("🛑 Shutdown: stopping scheduler...")
    scheduler.shutdown(wait=False)
    print("🛑 Shutdown")


app = FastAPI(
    title=APP_NAME,
    version=APP_VERSION,
    lifespan=lifespan,
)

# ============================================================
# Middleware
# ============================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
# Routers
# ============================================================

app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(assets_router)
app.include_router(meta_router)
app.include_router(users_router)
app.include_router(files_router)
app.include_router(qcm_router)
app.include_router(flash_router)
app.include_router(elo_router)
app.include_router(admin_console_router)
app.include_router(stats_router)
app.include_router(subscriptions_router)
app.include_router(veille_router)
app.include_router(leaderboard_router)
app.include_router(cab_router)
app.include_router(discord_bot_router)
app.include_router(prepa_router)
app.include_router(prepa_adjuris_router)
app.include_router(prepa_adjuris_espace_router)
app.include_router(navire_router)

# ============================================================
# MEOLES — Routers
# ============================================================
app.include_router(meoles_cart_router)
app.include_router(meoles_stripe_router)
app.include_router(meoles_custom_router)
app.include_router(meoles_admin_router)

# ============================================================
# Healthcheck
# ============================================================

@app.get("/health")
def health():
    return {"ok": True}