from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.routers import system, files, qcm, chat, flashcards


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging(settings.LOG_LEVEL)

    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description="API backend pour l'application NAVIRE (QCM, fichiers, chat, flashcards)",
    )

    # Middleware CORS
    origins = []
    if settings.CORS_ORIGINS:
        if isinstance(settings.CORS_ORIGINS, str):
            origins = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]
        elif isinstance(settings.CORS_ORIGINS, list):
            origins = settings.CORS_ORIGINS

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins or ["*"],  # fallback si mal configuré
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Routers
    app.include_router(system.router)
    app.include_router(files.router)
    app.include_router(qcm.router)
    app.include_router(chat.router)
    app.include_router(flashcards.router)

    # Redirect root → docs
    @app.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse(url="/docs")

    return app


app = create_app()
