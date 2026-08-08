# app/bot_discord/cogs/navire_ai.py
"""
NAVIRE AI dans Discord — relaie les questions vers l'assistant de la
plateforme (function-calling + RAG sur les cours), sous la limite quotidienne
partagée avec l'appli (voir POST /discord/navire/chat côté API).
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from app.bot_discord.config import COLOR_PRIMARY
from app.bot_discord.utils.api_client import navire_ai_chat

MAX_REPLY_LEN = 4000  # marge sous la limite Discord de 4096 sur la description d'un embed


class NavireAICog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # discord_id -> conversation_id, mémoire courte pour garder le fil
        # d'une même conversation entre deux appels (perdue au redémarrage).
        self._conversations: dict[str, int] = {}

    @app_commands.command(name="navire", description="Pose une question à NAVIRE AI (cours, méthodo, droit).")
    @app_commands.describe(question="Ta question")
    @app_commands.checks.cooldown(1, 20.0, key=lambda i: i.user.id)
    async def navire(self, interaction: discord.Interaction, question: str) -> None:
        await interaction.response.defer(thinking=True)

        discord_id = str(interaction.user.id)
        try:
            result = await navire_ai_chat(discord_id, question, self._conversations.get(discord_id))
        except Exception:
            await interaction.followup.send(
                "❌ NAVIRE AI est momentanément indisponible. Réessaie dans un instant."
            )
            return

        if not result.get("ok"):
            await interaction.followup.send(f"❌ {result.get('message', 'Question refusée.')}")
            return

        if result.get("conversation_id"):
            self._conversations[discord_id] = result["conversation_id"]

        reply = (result.get("reply") or "")[:MAX_REPLY_LEN]
        embed = discord.Embed(title="🤖 NAVIRE AI", description=reply, color=COLOR_PRIMARY)

        sources = result.get("sources") or []
        if sources:
            embed.add_field(
                name="Sources",
                value="\n".join(f"• {s.get('label', '')}" for s in sources[:5]),
                inline=False,
            )
        embed.set_footer(text=f"Demandé par {interaction.user.display_name}")
        await interaction.followup.send(embed=embed)

    @navire.error
    async def navire_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.CommandOnCooldown):
            message = f"⏳ Laisse {round(error.retry_after)}s avant de reposer une question."
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
            return
        raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(NavireAICog(bot))
