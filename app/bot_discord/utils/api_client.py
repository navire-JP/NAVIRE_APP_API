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

# ============================================================
# Deep work — salon vocal de travail
# ============================================================
# Toutes ces routes répondent 200 avec {"ok": bool, ...} : un compte non lié ou
# une session inconnue ne sont pas des erreurs réseau, le cog les affiche tels
# quels.

async def deepwork_start(discord_id: str, guild_id: str = "", channel_id: str = "") -> dict:
    return await _post("/discord/deepwork/start", {
        "discord_id": discord_id,
        "guild_id":   guild_id,
        "channel_id": channel_id,
    })


async def deepwork_set_goal(session_id: int, discord_id: str, goal_minutes: int | None) -> dict:
    return await _post("/discord/deepwork/goal", {
        "session_id":   session_id,
        "discord_id":   discord_id,
        "goal_minutes": goal_minutes,
    })


async def deepwork_tick(session_id: int, discord_id: str) -> dict:
    return await _post("/discord/deepwork/tick", {
        "session_id": session_id,
        "discord_id": discord_id,
    })


async def deepwork_set_message(session_id: int, discord_id: str, dm_message_id: str) -> dict:
    return await _post("/discord/deepwork/message", {
        "session_id":    session_id,
        "discord_id":    discord_id,
        "dm_message_id": dm_message_id,
    })


async def deepwork_stop(session_id: int, discord_id: str) -> dict:
    return await _post("/discord/deepwork/stop", {
        "session_id": session_id,
        "discord_id": discord_id,
    })


async def deepwork_reset_stale(keep_session_ids: list[int] | None = None) -> dict:
    return await _post("/discord/deepwork/reset-stale", {
        "keep_session_ids": keep_session_ids or [],
    })


async def deepwork_stats(discord_id: str) -> dict:
    return await _get(f"/discord/deepwork/stats/{discord_id}")
