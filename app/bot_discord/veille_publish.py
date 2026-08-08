# app/bot_discord/veille_publish.py
"""
Diffusion Discord d'une actu (veille) publiée depuis la console admin.

`POST /admin/actus/add` tourne dans le thread FastAPI ; le bot Discord tourne
dans son propre thread daemon (voir app/main.py). Comme pour role_sync.py, on
planifie la coroutine de publication sur la boucle du bot via
run_coroutine_threadsafe.

Best-effort : si le bot n'est pas prêt ou pas connecté, on log et on ne
bloque jamais la création de l'actu côté API.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def publish_veille_item_sync(
    *,
    item_id: int,
    title: str,
    essentiel: str,
    impact: str,
    category: str,
    source_url: str,
    source_name: str,
    published_at: datetime | None,
) -> None:
    from app.bot_discord.bot import bot

    async def _action() -> None:
        cog = bot.get_cog("VeilleCog")
        if not cog:
            logger.warning("VeilleCog absent — actu #%s non diffusée sur Discord.", item_id)
            return
        await cog.publish_item(
            item_id=item_id,
            title=title,
            essentiel=essentiel,
            impact=impact,
            category=category,
            source_url=source_url,
            source_name=source_name,
            published_at=published_at,
        )

    try:
        asyncio.run_coroutine_threadsafe(_action(), bot.loop)
    except Exception:
        logger.warning("Bot Discord indisponible — actu #%s non diffusée.", item_id, exc_info=True)
