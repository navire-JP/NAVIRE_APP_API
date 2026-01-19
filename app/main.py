from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import APP_NAME, APP_VERSION, CORS_ORIGINS, ensure_storage_dirs
from app.db.database import Base, engine
from app.db import models  # noqa: F401
from app.routers.auth import router as auth_router
from app.routers.admin import router as admin_router
from app.routers.meta import router as meta_router
from app.routers.users import router as users_router
from app.routers.files import router as files_router  # 👈 NEW
from app.routers.qcm import router as qcm_router

# ============================================================
# Lifespan (startup / shutdown)
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🚀 Startup: ensuring storage dirs...")
    ensure_storage_dirs()

    print("🚀 Startup: creating database tables if needed...")
    Base.metadata.create_all(bind=engine)
    yield
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
    allow_origins=CORS_ORIGINS if CORS_ORIGINS else ["*"],
    allow_credentials=False,
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


# ============================================================
# Healthcheck
# ============================================================

@app.get("/health")
def health():
    return {"ok": True}
