# app/bot_discord/utils/api_client.py

import httpx
from app.bot_discord.config import API_BASE_URL, BOT_SECRET

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


async def get_leaderboard(limit: int = 10) -> list[dict]:
    data = await _get("/discord/leaderboard", limit=limit)
    return data if isinstance(data, list) else []