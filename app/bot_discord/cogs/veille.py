# app/bot_discord/cogs/veille.py
"""
Diffusion Discord des actus (veille) publiées depuis la console admin.

Déclenché par app/bot_discord/veille_publish.py, planifié depuis
POST /admin/actus/add (voir app/routers/admin_console.py). Deux salons :
  - LOGS_NEWSLETTER_CHANNEL_ID : trace, pour le staff, ce qui va être publié
    et à quel moment (avant/au moment de l'envoi public).
  - VEILLE_CHANNEL_ID : l'embed public, visible des étudiants.
"""

from __future__ import annotations

from datetime import datetime

import discord
from discord.ext import commands

from app.bot_discord.config import COLOR_ADMIN, COLOR_PRIMARY, LOGS_NEWSLETTER_CHANNEL_ID, VEILLE_CHANNEL_ID

CATEGORY_LABELS = {
    "civil":    "⚖️ Droit civil",
    "penal":    "🔒 Droit pénal",
    "public":   "🏛️ Droit public",
    "affaires": "💼 Droit des affaires",
    "general":  "📌 Général",
}


def _build_public_embed(
    *, title: str, essentiel: str, impact: str, category: str,
    source_url: str, source_name: str, published_at: datetime | None,
) -> discord.Embed:
    embed = discord.Embed(
        title=f"📰 {title}",
        url=source_url or None,
        description=essentiel,
        color=COLOR_PRIMARY,
    )
    if impact:
        embed.add_field(name="Pourquoi c'est important", value=impact, inline=False)
    embed.add_field(name="Catégorie", value=CATEGORY_LABELS.get(category, category), inline=True)
    footer = f"Source : {source_name}" if source_name else "NAVIRE — Veille juridique"
    embed.set_footer(text=footer)
    embed.timestamp = published_at or discord.utils.utcnow()
    return embed


def _build_log_embed(
    *, item_id: int, title: str, category: str, published_at: datetime | None,
) -> discord.Embed:
    embed = discord.Embed(title="🗞️ Actu en cours de publication", color=COLOR_ADMIN)
    embed.add_field(name="Titre", value=title[:250], inline=False)
    embed.add_field(name="ID", value=str(item_id), inline=True)
    embed.add_field(name="Catégorie", value=CATEGORY_LABELS.get(category, category), inline=True)
    when = f"<t:{int(published_at.timestamp())}:F>" if published_at else "à l'instant"
    embed.add_field(name="Publication", value=when, inline=True)
    return embed


class VeilleCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def publish_item(
        self,
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
        log_channel = self.bot.get_channel(LOGS_NEWSLETTER_CHANNEL_ID)
        if log_channel:
            try:
                await log_channel.send(
                    embed=_build_log_embed(
                        item_id=item_id, title=title, category=category, published_at=published_at
                    )
                )
            except discord.HTTPException as e:
                print(f"[veille] Erreur log #logsnewsletter : {e}")

        channel = self.bot.get_channel(VEILLE_CHANNEL_ID)
        if not channel:
            print(f"[veille] Salon #veille introuvable (ID {VEILLE_CHANNEL_ID}).")
            return

        try:
            await channel.send(
                embed=_build_public_embed(
                    title=title,
                    essentiel=essentiel,
                    impact=impact,
                    category=category,
                    source_url=source_url,
                    source_name=source_name,
                    published_at=published_at,
                )
            )
        except discord.HTTPException as e:
            print(f"[veille] Erreur publication #veille : {e}")


async def setup(bot: commands.Bot):
    await bot.add_cog(VeilleCog(bot))
