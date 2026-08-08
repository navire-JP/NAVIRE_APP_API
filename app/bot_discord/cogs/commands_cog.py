# app/bot_discord/cogs/commands_cog.py
"""
Cog "libre" : commandes additionnelles à la sauce NAVIRE.

Contrairement aux autres cogs, chacun porté par une responsabilité précise
(modération, veille, onboarding…), celui-ci est pensé pour être modifié au
fil de l'eau — ajoute tes commandes perso à la suite des exemples ci-dessous.
"""

from __future__ import annotations

import time

import discord
from discord import app_commands
from discord.ext import commands

from app.bot_discord.config import COLOR_PRIMARY

_START_TIME = time.monotonic()


class CommandsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="ping", description="Latence du bot NAVIRE.")
    async def ping(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            f"🏓 Pong — {round(self.bot.latency * 1000)} ms", ephemeral=True
        )

    @app_commands.command(name="avatar", description="Affiche l'avatar d'un membre.")
    @app_commands.describe(member="Le membre visé (toi par défaut)")
    async def avatar(
        self, interaction: discord.Interaction, member: discord.Member | None = None
    ) -> None:
        target = member or interaction.user
        embed = discord.Embed(title=f"Avatar de {target.display_name}", color=COLOR_PRIMARY)
        embed.set_image(url=target.display_avatar.url)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="uptime", description="Depuis combien de temps le bot tourne.")
    async def uptime(self, interaction: discord.Interaction) -> None:
        seconds = int(time.monotonic() - _START_TIME)
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        await interaction.response.send_message(
            f"⏱️ En ligne depuis **{h}h{m:02d}m{s:02d}s**.", ephemeral=True
        )

    # ── Ajoute tes commandes perso ci-dessous ────────────────────────────────


async def setup(bot: commands.Bot):
    await bot.add_cog(CommandsCog(bot))
