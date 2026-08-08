# app/bot_discord/cogs/moderation.py
"""
Modération basique : filtre de mots interdits + avertissements progressifs
jusqu'à une mise en sourdine temporaire (timeout Discord).

Compteur d'avertissements en mémoire (remis à zéro au redémarrage du bot) —
suffisant pour une V1, pas besoin de table dédiée pour l'instant.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import timedelta

import discord
from discord.ext import commands

from app.bot_discord.config import COLOR_ADMIN, MODLOG_CHANNEL_ID, MOD_MAX_WARNS, MOD_TIMEOUT_MINUTES

# Liste volontairement courte pour la V1 — à compléter à la sauce NAVIRE.
BAD_WORDS = {
    "connard", "connasse", "salope", "pute", "putain de merde", "enculé",
    "batard", "encule", "fdp", "nique ta mère", "nique sa mère",
    "fuck you", "bitch", "asshole",
}

_WORD_RE = re.compile(
    r"(" + "|".join(re.escape(w) for w in BAD_WORDS) + r")",
    re.IGNORECASE,
)


class ModerationCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._warns: dict[int, int] = defaultdict(int)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        if not _WORD_RE.search(message.content):
            return

        try:
            await message.delete()
        except (discord.Forbidden, discord.NotFound):
            pass

        self._warns[message.author.id] += 1
        warns = self._warns[message.author.id]

        try:
            await message.channel.send(
                f"⚠️ {message.author.mention}, message supprimé — reste courtois ici "
                f"({warns}/{MOD_MAX_WARNS}).",
                delete_after=8,
            )
        except discord.HTTPException:
            pass

        timed_out = False
        if warns >= MOD_MAX_WARNS and isinstance(message.author, discord.Member):
            try:
                await message.author.timeout(
                    timedelta(minutes=MOD_TIMEOUT_MINUTES),
                    reason="Modération automatique NAVIRE — propos répétés",
                )
                timed_out = True
                self._warns[message.author.id] = 0
            except discord.Forbidden:
                pass

        await self._log(message, warns, timed_out)

    async def _log(self, message: discord.Message, warns: int, timed_out: bool) -> None:
        channel = self.bot.get_channel(MODLOG_CHANNEL_ID)
        if not channel:
            return

        embed = discord.Embed(title="🚫 Message modéré", color=COLOR_ADMIN)
        embed.add_field(name="Auteur", value=message.author.mention, inline=True)
        embed.add_field(name="Salon", value=message.channel.mention, inline=True)
        embed.add_field(name="Avertissements", value=f"{warns}/{MOD_MAX_WARNS}", inline=True)
        if message.content:
            embed.add_field(name="Contenu", value=message.content[:1000], inline=False)
        if timed_out:
            embed.add_field(
                name="Action", value=f"Mis en sourdine {MOD_TIMEOUT_MINUTES} min", inline=False
            )
        embed.set_footer(text=f"ID : {message.author.id}")
        embed.timestamp = discord.utils.utcnow()

        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(ModerationCog(bot))
