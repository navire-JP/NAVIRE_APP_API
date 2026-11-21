"""
app/routers/mcp.py
==================
Serveur MCP de NAVIRE : l'élève abonné branche NAVIRE dans son propre Claude
ou ChatGPT, qui interroge alors la base NAVIRE avec SON abonnement (le
modèle ne coûte rien à NAVIRE ; seule la recherche fait un petit appel
d'embedding).

Transport « Streamable HTTP » du protocole MCP, en mode sans état : chaque
POST /mcp porte un message JSON-RPC et reçoit sa réponse en JSON (pas de flux
SSE, pas de session côté serveur). Écrit à la main plutôt qu'avec le SDK
`mcp`, dont les dépendances (Starlette récent) ne sont pas compatibles avec la
version de FastAPI du projet ; le protocole utile ici tient en peu de lignes.

Authentification : jeton OAuth obtenu via app/routers/mcp_oauth.py. Sans
jeton valable → 401 avec l'en-tête WWW-Authenticate qui indique à
l'application où se connecter.

Outils : les mêmes que NAVIRE AI (app/services/navire_chat.py), en lecture
seule et cloisonnés au compte de l'élève. Réservés aux abonnés, vérifié à
chaque appel.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from app.core.config import APP_VERSION
from app.db.database import get_db
from app.db.models import User
from app.services import mcp_oauth as oauth

logger = logging.getLogger(__name__)

router = APIRouter(tags=["connecteur-mcp"])

# Du plus récent au plus ancien : on reprend la version demandée par
# l'application si on la connaît, sinon on propose la plus récente.
PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")

SERVER_INFO = {
    "name": "navire",
    "title": "NAVIRE",
    "version": APP_VERSION,
    "websiteUrl": "https://navire-ai.com",
}

INSTRUCTIONS = (
    "NAVIRE est une plateforme pour les étudiants en droit français. Pour toute question de droit "
    "(notion, régime, jurisprudence, méthodologie, contenu d'un cours ou d'un PDF de l'étudiant), "
    "appelle search_legal_base AVANT de répondre, éventuellement plusieurs fois avec des formulations "
    "différentes. Les extraits NAVIRE priment : cite-les précisément (cours NAVIRE Lib, article, actu, "
    "PDF) avec leur lien quand il y en a un. Si la base ne couvre pas tout, complète avec tes "
    "connaissances en le signalant. N'invente jamais un numéro d'article ou une référence d'arrêt. "
    "Pour les stats, le classement ou les fichiers de l'étudiant, utilise get_user_stats et "
    "list_user_files ; pour l'actualité, get_latest_actus. Réponds en français."
)

READ_ONLY = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}

TOOLS: list[dict] = [
    {
        "name": "search_legal_base",
        "title": "Rechercher dans NAVIRE",
        "description": (
            "Recherche dans la base juridique NAVIRE : cours NAVIRE Lib, articles des doctorants et de "
            "l'équipe, actualités juridiques, et PDF personnels de l'étudiant. Retrouve aussi les "
            "références exactes (article 1240, n° de pourvoi). À utiliser pour TOUTE question de droit, "
            "éventuellement plusieurs fois. Renvoie des extraits avec leur source et leur lien."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "La question ou les termes juridiques à rechercher."},
            },
            "required": ["query"],
        },
        "annotations": {"title": "Rechercher dans NAVIRE", **READ_ONLY},
    },
    {
        "name": "get_user_stats",
        "title": "Mes statistiques NAVIRE",
        "description": "Statistiques réelles de l'étudiant sur NAVIRE : ELO, rang au classement, taux de réussite QCM, nombre de sessions.",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {"title": "Mes statistiques NAVIRE", **READ_ONLY},
    },
    {
        "name": "list_user_files",
        "title": "Mes fichiers NAVIRE",
        "description": "Liste les fichiers PDF que l'étudiant a déposés sur NAVIRE (leur contenu est cherchable avec search_legal_base).",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {"title": "Mes fichiers NAVIRE", **READ_ONLY},
    },
    {
        "name": "get_latest_actus",
        "title": "Actualités NAVIRE",
        "description": "Dernières actualités juridiques ou derniers articles NAVIRE (rédigés par des doctorants et l'équipe). Peut filtrer par catégorie.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": ["civil", "penal", "public", "affaires", "general"],
                    "description": "Catégorie (optionnel).",
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 10, "description": "Nombre d'éléments (défaut 5, max 10)."},
                "kind": {
                    "type": "string",
                    "enum": ["actu", "article"],
                    "description": "« article » pour les articles NAVIRE, « actu » pour les actualités (optionnel : les deux).",
                },
            },
        },
        "annotations": {"title": "Actualités NAVIRE", **READ_ONLY},
    },
]
TOOL_NAMES = {t["name"] for t in TOOLS}

NOT_SUBSCRIBED = (
    "Le connecteur NAVIRE est réservé aux abonnés NAVIRE AI, NAVIRE AI+ et AdJuris. "
    "L'abonnement de ce compte n'est plus actif : abonne-toi sur https://navire-ai.com/subscribe "
    "pour retrouver l'accès."
)

# Codes d'erreur JSON-RPC
PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS = -32700, -32600, -32601, -32602


# ============================================================
# Outils
# ============================================================

def _call_tool(db: Session, user: User, name: str, args: dict) -> dict:
    from app.services import navire_chat as chat

    if name == "search_legal_base":
        query = str(args.get("query") or "").strip()
        if not query:
            return _tool_error("Le paramètre « query » est obligatoire.")
        data = chat._tool_search_legal_base(db, user, query[:1000])
    elif name == "get_user_stats":
        data = chat._tool_get_user_stats(db, user)
    elif name == "list_user_files":
        data = chat._tool_list_user_files(db, user)
    elif name == "get_latest_actus":
        try:
            limit = max(1, min(int(args.get("limit") or 5), 10))
        except (TypeError, ValueError):
            limit = 5
        data = chat._tool_get_latest_actus(db, args.get("category"), limit, args.get("kind"))
    else:
        raise KeyError(name)

    return {
        "content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, default=str)}],
        "isError": bool(isinstance(data, dict) and data.get("error")),
    }


def _tool_error(message: str) -> dict:
    return {"content": [{"type": "text", "text": message}], "isError": True}


# ============================================================
# JSON-RPC
# ============================================================

def _ok(msg_id: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _err(msg_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _handle(db: Session, user: User, msg: Any) -> dict | None:
    """Une requête → sa réponse ; une notification ou une réponse → None."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        return _err(None, INVALID_REQUEST, "Message JSON-RPC 2.0 attendu.")
    method = msg.get("method")
    if "id" not in msg or method is None:
        return None  # notification (notifications/initialized…) ou réponse du client
    msg_id = msg["id"]
    params = msg.get("params") or {}
    if not isinstance(params, dict):
        return _err(msg_id, INVALID_PARAMS, "params doit être un objet.")

    if method == "initialize":
        asked = params.get("protocolVersion")
        version = asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
        return _ok(msg_id, {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
            "instructions": INSTRUCTIONS,
        })

    if method == "ping":
        return _ok(msg_id, {})

    if method == "tools/list":
        return _ok(msg_id, {"tools": TOOLS})

    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        if name not in TOOL_NAMES:
            return _err(msg_id, INVALID_PARAMS, f"Outil inconnu : {name}")
        if not isinstance(args, dict):
            return _err(msg_id, INVALID_PARAMS, "arguments doit être un objet.")
        if not oauth.is_subscriber(db, user):
            return _ok(msg_id, _tool_error(NOT_SUBSCRIBED))
        try:
            return _ok(msg_id, _call_tool(db, user, name, args))
        except Exception:
            logger.exception("Connecteur NAVIRE : outil %s en erreur (user %s)", name, user.id)
            db.rollback()
            return _ok(msg_id, _tool_error(f"Erreur interne pendant {name}. Réessaie dans un instant."))

    # Listes vides plutôt qu'une erreur : certaines applications les demandent
    # même quand la capacité n'est pas annoncée.
    if method == "resources/list":
        return _ok(msg_id, {"resources": []})
    if method == "resources/templates/list":
        return _ok(msg_id, {"resourceTemplates": []})
    if method == "prompts/list":
        return _ok(msg_id, {"prompts": []})

    return _err(msg_id, METHOD_NOT_FOUND, f"Méthode inconnue : {method}")


def _authenticate(db: Session, authorization: str | None):
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    return token, oauth.resolve_access_token(db, token)


def _unauthorized(with_token: bool) -> JSONResponse:
    challenge = f'Bearer resource_metadata="{oauth.protected_resource_metadata_url()}", scope="{oauth.SCOPE}"'
    if with_token:
        challenge += ', error="invalid_token", error_description="Invalid or expired token"'
    return JSONResponse(
        {"error": "invalid_token" if with_token else "unauthorized",
         "error_description": "Connexion à NAVIRE requise."},
        status_code=401,
        headers={"WWW-Authenticate": challenge},
    )


def _process(db: Session, authorization: str | None, payload: Any):
    token, resolved = _authenticate(db, authorization)
    if resolved is None:
        return "unauthorized", bool(token)
    _, user = resolved
    if isinstance(payload, list):
        if not payload:
            return "ok", _err(None, INVALID_REQUEST, "Lot vide.")
        out = [r for r in (_handle(db, user, m) for m in payload) if r is not None]
        return "ok", out or None
    return "ok", _handle(db, user, payload)


@router.post("/mcp")
async def mcp_post(request: Request, db: Session = Depends(get_db)):
    raw = await request.body()
    try:
        payload = json.loads(raw)
    except ValueError:
        payload = ValueError
    authorization = request.headers.get("authorization")

    if payload is ValueError:
        # L'authentification passe avant l'erreur de syntaxe, pour qu'une
        # application non connectée reçoive bien le 401 qui la fait se connecter.
        _, resolved = await run_in_threadpool(_authenticate, db, authorization)
        if resolved is None:
            return _unauthorized(bool(authorization))
        return JSONResponse(_err(None, PARSE_ERROR, "JSON invalide."), status_code=400)

    status, result = await run_in_threadpool(_process, db, authorization, payload)
    if status == "unauthorized":
        return _unauthorized(result)
    if result is None:
        return Response(status_code=202)
    return JSONResponse(result)


@router.get("/mcp")
@router.delete("/mcp")
def mcp_no_stream():
    """Pas de flux SSE ni de session côté serveur (mode sans état)."""
    return Response(status_code=405, headers={"Allow": "POST"})
