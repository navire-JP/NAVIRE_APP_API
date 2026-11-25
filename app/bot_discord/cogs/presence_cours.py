# app/bot_discord/cogs/presence_cours.py
"""
Présence aux cours Prép'AdJuris : pendant un cours lancé depuis #cours-start,
le bot relève chaque minute les membres du salon vocal du groupe (renseigné
dans la console, onglet Prof. → Groupes) et les envoie à l'API, qui cumule le
temps de chacun. Présent dès 5 minutes. Règles : app/services/presences.py.

Un relevé par minute plutôt que les seules entrées / sorties : un bot
redémarré ou un événement manqué ne fausse pas la présence (l'API ne
crédite jamais plus de 2 minutes par relevé).
"""

from __future__ import annotations

import logging

import discord
from discord.ext import commands, tasks

from app.bot_discord.utils.api_client import cours_presence, cours_vocaux

logger = logging.getLogger(__name__)

RELEVE_SECONDES = 60


class PresenceCoursCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.relever.start()

    def cog_unload(self) -> None:
        self.relever.cancel()

    @tasks.loop(seconds=RELEVE_SECONDES)
    async def relever(self) -> None:
        try:
            items = await cours_vocaux()
        except Exception as e:  # noqa: BLE001 — l'API peut dormir (Render)
            logger.warning("Présence : liste des cours indisponible (%s)", e)
            return
        for item in items:
            try:
                salon = self.bot.get_channel(int(item["channel_id"]))
            except (KeyError, TypeError, ValueError):
                continue
            if not isinstance(salon, (discord.VoiceChannel, discord.StageChannel)):
                continue
            membres = [
                {"discord_id": str(m.id), "nom": m.display_name[:170]}
                for m in salon.members
                if not m.bot
            ]
            try:
                await cours_presence(int(item["cours_id"]), membres)
            except Exception as e:  # noqa: BLE001
                logger.warning("Présence : relevé du cours %s non envoyé (%s)", item.get("cours_id"), e)

    @relever.before_loop
    async def _attendre(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(PresenceCoursCog(bot))
