# app/routers/ffd_resa.py
# FFD RESA — MENU BRIDGE (sans Stripe pour l'instant)
# + CORS géré DANS le router (pas besoin de modifier main.py)
#
# Endpoints:
#   GET  /ffd/resa/health
#   GET  /ffd/resa/menu               -> renvoie TOUT (BOISSON + PLAT)
#   GET  /ffd/resa/menu?type=BOISSON  -> renvoie seulement BOISSON
#   GET  /ffd/resa/menu?type=PLAT     -> renvoie seulement PLAT
#   OPTIONS /ffd/resa/*               -> preflight CORS (important pour navigateur)
#
# Fichier menu:
#   app/data/menu.json
# Format attendu:
# {
#   "BOISSON": [{ "id": "...", "label": "...", "price_cents": 470, "category": "SOFT" }, ...],
#   "PLAT":    [{ "id": "...", "label": "...", "price_cents": 1800, "category": "BURGERS" }, ...]
# }
#
# ENV:
#   FFD_RESA_MENU_PATH=app/data/menu.json
#   CORS_FFD=https://ffdebat.org,https://www.ffdebat.org
#   FFD_RESA_REQUIRE_API_KEY=false
#   API_KEY=NAVIRE_APIKEY_2026_0001
#
# NOTE:
# - Le widget WordPress est public => une API key ne peut pas rester secrète côté client.
# - Par défaut, FFD_RESA_REQUIRE_API_KEY = false.

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List, Literal, Optional

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response

MenuType = Literal["BOISSON", "PLAT"]

router = APIRouter(prefix="/ffd/resa", tags=["ffd_resa"])

# ----------------------------
# ENV / CONFIG
# ----------------------------
FFD_RESA_MENU_PATH = os.getenv("FFD_RESA_MENU_PATH", "app/data/menu.json").strip()

# CORS spécifique FFD (pas de main.py)
# Exemple : "https://ffdebat.org,https://www.ffdebat.org"
CORS_FFD = os.getenv("CORS_FFD", "https://ffdebat.org").strip()
ALLOWED_ORIGINS = [o.strip() for o in CORS_FFD.split(",") if o.strip()]

# API key optionnelle
API_KEY = os.getenv("API_KEY", "").strip()  # ex: NAVIRE_APIKEY_2026_0001
FFD_RESA_REQUIRE_API_KEY = os.getenv("FFD_RESA_REQUIRE_API_KEY", "false").strip().lower() in ("1", "true", "yes")

# Headers autorisés (si tu ajoutes x-api-key plus tard)
CORS_ALLOW_HEADERS = "Content-Type, Authorization, x-api-key"
CORS_ALLOW_METHODS = "GET, POST, OPTIONS"


# ----------------------------
# CORS helper (router-level)
# ----------------------------
def _cors_origin_for_request(request: Request) -> Optional[str]:
    """
    Retourne l'origin autorisé correspondant.
    Si Origin absent => None (appel server-to-server / curl)
    """
    origin = request.headers.get("origin")
    if not origin:
        return None
    if "*" in ALLOWED_ORIGINS:
        return "*"  # peu recommandé, mais support
    if origin in ALLOWED_ORIGINS:
        return origin
    return None


def _with_cors(request: Request, response: Response) -> Response:
    """
    Ajoute les headers CORS à une Response.
    """
    origin = _cors_origin_for_request(request)
    if origin:
        response.headers["Access-Control-Allow-Origin"] = origin
        # important si tu autorises plusieurs origins: le navigateur exige Vary: Origin
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Methods"] = CORS_ALLOW_METHODS
        response.headers["Access-Control-Allow-Headers"] = CORS_ALLOW_HEADERS
        # cache preflight (1h)
        response.headers["Access-Control-Max-Age"] = "3600"
    return response


@router.options("/{path:path}")
def cors_preflight(path: str, request: Request):
    """
    Répond aux preflight OPTIONS sans toucher main.py.
    """
    resp = Response(status_code=204)
    return _with_cors(request, resp)


# ----------------------------
# Utils
# ----------------------------
def _require_key(x_api_key: Optional[str]) -> None:
    if not FFD_RESA_REQUIRE_API_KEY:
        return
    if not API_KEY:
        raise HTTPException(status_code=500, detail="API_KEY manquante côté serveur (env).")
    if (x_api_key or "") != API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized (x-api-key)")


def _load_menu() -> Dict[str, List[dict]]:
    p = Path(FFD_RESA_MENU_PATH)
    if not p.exists():
        raise HTTPException(status_code=500, detail=f"Menu introuvable: {FFD_RESA_MENU_PATH}")

    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Menu illisible (JSON): {e}")

    if not isinstance(raw, dict) or "BOISSON" not in raw or "PLAT" not in raw:
        raise HTTPException(status_code=500, detail="menu.json invalide: doit contenir BOISSON et PLAT.")

    if not isinstance(raw["BOISSON"], list) or not isinstance(raw["PLAT"], list):
        raise HTTPException(status_code=500, detail="menu.json invalide: BOISSON/PLAT doivent être des listes.")

    # Nettoyage/validation minimale
    def clean(items: List[dict]) -> List[dict]:
        out: List[dict] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            _id = str(it.get("id", "")).strip()
            label = str(it.get("label", "")).strip()
            price = it.get("price_cents", None)
            if not _id or not label:
                continue
            try:
                price_cents = int(price)
            except Exception:
                continue
            if price_cents < 0:
                continue
            out.append(
                {
                    "id": _id,
                    "label": label,
                    "price_cents": price_cents,
                    "category": str(it.get("category", "")).strip(),
                }
            )
        return out

    return {"BOISSON": clean(raw["BOISSON"]), "PLAT": clean(raw["PLAT"])}


# ----------------------------
# Endpoints (avec CORS)
# ----------------------------
@router.get("/health")
def health(request: Request):
    payload = {
        "ok": True,
        "module": "ffd_resa_menu_bridge",
        "menu_path": FFD_RESA_MENU_PATH,
        "allowed_origins": ALLOWED_ORIGINS,
        "require_api_key": FFD_RESA_REQUIRE_API_KEY,
    }
    return _with_cors(request, JSONResponse(payload))


@router.get("/menu")
def menu(
    request: Request,
    type: Optional[MenuType] = Query(default=None, description="BOISSON ou PLAT. Si absent: renvoie tout."),
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
):
    _require_key(x_api_key)
    data = _load_menu()

    if type is None:
        payload = {
            "items": data,
            "count": {"BOISSON": len(data["BOISSON"]), "PLAT": len(data["PLAT"])},
        }
        return _with_cors(request, JSONResponse(payload))

    payload = {"type": type, "items": data[type], "count": len(data[type])}
    return _with_cors(request, JSONResponse(payload))