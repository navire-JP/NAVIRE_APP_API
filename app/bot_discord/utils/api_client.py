# app/bot_discord/utils/api_client.py

import httpx
from app.bot_discord.config import API_BASE_URL, BOT_SECRET, LEADERBOARD_LIMIT

_HEADERS = {"x-bot-secret": BOT_SECRET, "Content-Type": "application/json"}
_TIMEOUT = 10.0


async def _get(path: str, **params) -> dict | list | None:
    async with httpx.AsyncClient(base_url=API_BASE_URL, timeout=_TIMEOUT) as client:
        r = await client.get(path, headers=_HEADERS, params=params)
        r.raise_for_status()
        return r.json()


async def _post(path: str, body: dict) -> dict | None:
    async with httpx.AsyncClient(base_url=API_BASE_URL, timeout=_TIMEOUT) as client:
        r = await client.post(path, headers=_HEADERS, json=body)
        r.raise_for_status()
        return r.json()


async def link_discord(user_id: int, discord_id: str) -> dict:
    return await _post("/discord/link", {"user_id": user_id, "discord_id": discord_id})


async def get_navire_user(discord_id: str) -> dict | None:
    try:
        return await _get(f"/discord/user/{discord_id}")
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return None
        raise


async def record_participation(discord_id: str, message_count: int = 1) -> dict:
    return await _post("/discord/participation", {
        "discord_id":    discord_id,
        "message_count": message_count,
    })


async def verify_discord_link(
    discord_id: str, discord_name: str, user_id: int, email: str, code: str
) -> dict:
    """
    Liaison authentifiée par le triplet (identifiant, email, code).
    Retourne toujours {"ok": bool, "message"?: str, ...} en 200 : les échecs
    attendus (code expiré, triplet incohérent) ne lèvent pas d'exception,
    seules les vraies pannes réseau/serveur le font.
    """
    return await _post("/discord/link-verify", {
        "discord_id":   discord_id,
        "discord_name": discord_name,
        "user_id":      user_id,
        "email":        email,
        "code":         code,
    })


async def get_sync_state() -> list[dict]:
    """
    Plan d'abonnement de tous les comptes liés : [{discord_id, user_id,
    username, plan}, …]. Une requête par cycle de surveillance.
    """
    data = await _get("/discord/sync-state")
    return data if isinstance(data, list) else []


async def get_leaderboard(limit: int = LEADERBOARD_LIMIT) -> list[dict]:
    data = await _get("/discord/leaderboard", limit=limit)
    return data if isinstance(data, list) else []


async def navire_ai_chat(discord_id: str, message: str, conversation_id: int | None = None) -> dict:
    """
    Relaie une question posée depuis Discord à NAVIRE AI (RAG cours + actus).
    Retourne toujours {"ok": bool, ...} en 200 — {"ok": false, "message": ...}
    pour les cas attendus (compte non lié, limite quotidienne atteinte).
    """
    return await _post("/discord/navire/chat", {
        "discord_id":      discord_id,
        "message":         message,
        "conversation_id": conversation_id,
    })


async def link_adjuris_discord(discord_id: str, email: str, code: str) -> dict:
    """
    Valide un code de liaison Prép'AdJuris et lie discord_id au compte NAVIRE
    correspondant à `email`. Retourne toujours {"ok": bool, "message"?: str,
    "matieres"?: [...]} avec un statut 200 — jamais d'exception pour un code
    invalide/expiré, seulement pour une vraie panne réseau/serveur.
    """
    return await _post("/prepa/adjuris/link-discord", {
        "discord_id": discord_id,
        "email": email,
        "code": code,
    })