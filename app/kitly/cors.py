"""
CORS séparé NAVIRE / Kitly.

Une seule application FastAPI, mais deux listes d'origines autorisées :
  - routes /kitly/*   → KITLY_CORS_ORIGINS uniquement (site Framer Kitly) ;
  - toutes les autres → CORS_ORIGINS de NAVIRE, inchangé.

Le site Kitly ne peut donc pas appeler les routes NAVIRE depuis un navigateur,
et inversement. Kitly s'authentifie par en-tête Bearer (pas de cookie) : les
« credentials » CORS ne lui sont pas ouverts.
"""

from __future__ import annotations

from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from app.kitly.config import KITLY_CORS_ORIGINS

KITLY_PREFIX = "/kitly"


class SplitCORSMiddleware:
    def __init__(self, app: ASGIApp, navire_origins: list[str]):
        self.navire = CORSMiddleware(
            app, allow_origins=navire_origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
        )
        self.kitly = CORSMiddleware(
            app, allow_origins=KITLY_CORS_ORIGINS, allow_credentials=False, allow_methods=["*"], allow_headers=["*"],
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "") if scope["type"] in ("http", "websocket") else ""
        if path == KITLY_PREFIX or path.startswith(KITLY_PREFIX + "/"):
            await self.kitly(scope, receive, send)
        else:
            await self.navire(scope, receive, send)
