# app/bot_discord/cogs/logs_cog.py
"""
Logs génériques : départs du serveur et erreurs de commandes préfixées.
Les logs spécifiques (veille, vocal, modération, ELO) vivent dans leur
propre cog — celui-ci couvre ce qui reste.
"""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from app.bot_discord.config import COLOR_ADMIN, NOTIFICATOR_CHANNEL_ID

logger = logging.getLogger(__name__)


class LogsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        channel = self.bot.get_channel(NOTIFICATOR_CHANNEL_ID)
        if not channel:
            return
        embed = discord.Embed(description=f"👋 **{member}** a quitté le serveur.", color=COLOR_ADMIN)
        embed.timestamp = discord.utils.utcnow()
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        if isinstance(error, (commands.CommandNotFound, commands.CheckFailure)):
            return
        logger.warning("Erreur commande %s : %s", ctx.command, error, exc_info=error)


async def setup(bot: commands.Bot):
    await bot.add_cog(LogsCog(bot))
