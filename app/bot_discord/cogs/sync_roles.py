# app/bot_discord/role_sync.py
"""
Attribution/retrait des rôles Discord Prép'AdJuris depuis l'extérieur du
thread du bot (typiquement : le webhook Stripe, qui tourne dans le thread
FastAPI). Le bot tourne dans un thread daemon séparé (voir app/main.py) —
on planifie la coroutine sur sa boucle via run_coroutine_threadsafe, comme
documenté dans la spec.

Best-effort : si le bot n'est pas prêt (token absent en dev, pas encore
connecté), on log et on ne bloque jamais l'appelant (le webhook Stripe doit
répondre 200 à Stripe quoi qu'il arrive).
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


def assign_adjuris_role_sync(discord_id: str, matiere_key: str) -> None:
    _schedule_role_action(discord_id, matiere_key, add=True)


def remove_adjuris_role_sync(discord_id: str, matiere_key: str) -> None:
    _schedule_role_action(discord_id, matiere_key, add=False)


def _schedule_role_action(discord_id: str, matiere_key: str, add: bool) -> None:
    import discord

    from app.bot_discord.bot import bot
    from app.bot_discord.config import GUILD_ID, PREPA_ADJURIS_ROLE_IDS

    role_id = PREPA_ADJURIS_ROLE_IDS.get(matiere_key)
    if not role_id or not GUILD_ID:
        logger.warning("Rôle/guild Adjuris non configuré pour %s — action ignorée.", matiere_key)
        return

    async def _action() -> None:
        guild = bot.get_guild(GUILD_ID)
        if not guild:
            return
        member = guild.get_member(int(discord_id))
        role = guild.get_role(role_id)
        if not member or not role:
            return
        try:
            if add:
                await member.add_roles(role, reason=f"Inscription Prép'AdJuris : {matiere_key}")
            else:
                await member.remove_roles(role, reason=f"Fin d'accès Prép'AdJuris : {matiere_key}")
        except discord.Forbidden:
            pass

    try:
        asyncio.run_coroutine_threadsafe(_action(), bot.loop)
    except Exception:
        logger.warning(
            "Bot Discord indisponible — rôle Adjuris (%s) non %s pour %s",
            matiere_key, "assigné" if add else "retiré", discord_id,
        )
