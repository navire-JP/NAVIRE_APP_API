# app/bot_discord/cogs/classement.py
# - //setup_classement  : poste l'embed dans le channel (admin seulement)
# - Mise à jour auto de l'embed à chaque gain/perte ELO (appelé depuis participation.py)
# - Format identique à l'embed existant + streak 🔥

import discord
from discord.ext import commands, tasks

from app.bot_discord.config import (
    CLASSEMENT_CHANNEL_ID,
    LEADERBOARD_REFRESH_SECONDS,
    LEADERBOARD_LIMIT,
    ADMIN_ROLE_ID,
)
from app.bot_discord.utils.api_client import get_leaderboard

# Grades NAVIRE (copie de elo service)
_GRADE_THRESHOLDS = [
    (9231, "🌑 KRONOS"),
    (1200, "🌟 Polaris"),
    (800,  "✸ Majorant"),
    (300,  "✸ Paladin"),
    (100,  "✸ Forcené"),
    (1,    "✸ Page"),
    (0,    "Esseulé"),
]

def _grade_from_elo(elo: int) -> str:
    for threshold, name in _GRADE_THRESHOLDS:
        if elo >= threshold:
            return name
    return "Esseulé"

_MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}


def _build_embed(rows: list[dict]) -> discord.Embed:
    embed = discord.Embed(
        title="🏆 Classement des Elo",
        color=0xE87722,
    )

    if not rows:
        embed.description = "Aucun classement disponible."
        embed.set_footer(text="Mise à jour automatiquement à chaque changement de score")
        return embed

    lines = []
    for row in rows:
        rank    = row["rank"]
        elo     = row["elo"] or 0
        grade   = _grade_from_elo(elo)
        streak  = row.get("discord_streak", 0)
        username = row.get("username", "?")

        # Préfixe rang
        if rank <= 3:
            prefix = _MEDALS[rank]
        else:
            prefix = f"#{rank}"

        # Streak
        streak_str = f" 🔥 : **{streak}**" if streak and streak > 0 else ""

        lines.append(f"{prefix} – **{username}** – {grade} – {elo} Elo{streak_str}")

    embed.description = "\n".join(lines)
    embed.set_footer(text="Mise à jour automatiquement à chaque changement de score")
    return embed


def _is_admin(ctx: commands.Context) -> bool:
    role = discord.utils.get(ctx.guild.roles, id=ADMIN_ROLE_ID)
    return role in ctx.author.roles if role else False


class ClassementCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot         = bot
        self._message_id: int | None = None
        self.refresh.change_interval(seconds=LEADERBOARD_REFRESH_SECONDS)
        self.refresh.start()

    def cog_unload(self):
        self.refresh.cancel()

    # ── Mise à jour de l'embed (appelable depuis l'extérieur) ────────────────

    async def update_embed(self) -> None:
        """Rafraîchit l'embed classement. Appelé après chaque gain ELO."""
        channel = self.bot.get_channel(CLASSEMENT_CHANNEL_ID)
        if not channel:
            return
        try:
            rows  = await get_leaderboard(limit=LEADERBOARD_LIMIT)
            embed = _build_embed(rows)
        except Exception as e:
            print(f"[classement] Erreur API : {e}")
            return

        if self._message_id:
            try:
                msg = await channel.fetch_message(self._message_id)
                await msg.edit(embed=embed)
                return
            except (discord.NotFound, discord.HTTPException):
                self._message_id = None

        # Pas de message existant → on en crée un
        msg = await channel.send(embed=embed)
        self._message_id = msg.id

    # ── Task de refresh périodique (filet de sécurité) ───────────────────────

    @tasks.loop(seconds=300)
    async def refresh(self):
        await self.update_embed()

    @refresh.before_loop
    async def before_refresh(self):
        await self.bot.wait_until_ready()

    # ── Commande //setup_classement (admin) ──────────────────────────────────

    @commands.command(name="setup_classement")
    async def setup_classement(self, ctx: commands.Context):
        """//setup_classement — Installe l'embed classement dans #classement (admin)."""
        if not _is_admin(ctx):
            return await ctx.send("❌ Commande réservée aux admins.", delete_after=5)

        await ctx.message.delete(delay=2)

        channel = self.bot.get_channel(CLASSEMENT_CHANNEL_ID)
        if not channel:
            return await ctx.send(
                f"❌ Channel #classement introuvable (ID {CLASSEMENT_CHANNEL_ID}).",
                delete_after=10,
            )

        # Purger les anciens messages du bot dans ce channel
        await channel.purge(limit=10, check=lambda m: m.author == self.bot.user)
        self._message_id = None

        try:
            rows  = await get_leaderboard(limit=LEADERBOARD_LIMIT)
            embed = _build_embed(rows)
        except Exception as e:
            return await ctx.send(f"❌ Erreur API : {e}", delete_after=10)

        msg = await channel.send(embed=embed)
        self._message_id = msg.id

        await ctx.send(
            f"✅ Embed classement installé dans <#{CLASSEMENT_CHANNEL_ID}>.",
            delete_after=5,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(ClassementCog(bot))