from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
import asyncio
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
from app.routers.auth import router as auth_router
from app.routers.admin import router as admin_router
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
scheduler.add_job(
    check_expired_subscriptions,
    trigger="interval",
    hours=1,
    args=[SessionLocal],
    id="check_expired_subscriptions",
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

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🚀 Startup: ensuring storage dirs...")
    ensure_storage_dirs()

    print("🚀 Startup: creating database tables if needed...")
    Base.metadata.create_all(bind=engine)

    print("🚀 Startup: running discord migrations...")
    run_discord_migrations()

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