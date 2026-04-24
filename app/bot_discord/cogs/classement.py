# app/bot_discord/cogs/classement.py
# - //setup_classement  : poste l'embed dans le channel (admin seulement)
# - Mise à jour auto de l'embed à chaque gain/perte ELO (appelé depuis participation.py)
# - Persistance du message_id dans un fichier JSON pour survivre aux redémarrages

import json
import os
import discord
from discord.ext import commands, tasks

from app.bot_discord.config import (
    CLASSEMENT_CHANNEL_ID,
    LEADERBOARD_REFRESH_SECONDS,
    LEADERBOARD_LIMIT,
    ADMIN_ROLE_ID,
)
from app.bot_discord.utils.api_client import get_leaderboard

# ── Persistance du message_id ────────────────────────────────────────────────
_STATE_FILE = os.path.join(os.path.dirname(__file__), ".classement_state.json")


def _load_message_id() -> int | None:
    try:
        with open(_STATE_FILE) as f:
            return json.load(f).get("message_id")
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return None


def _save_message_id(message_id: int | None) -> None:
    with open(_STATE_FILE, "w") as f:
        json.dump({"message_id": message_id}, f)


# ── Grades ───────────────────────────────────────────────────────────────────
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
    embed = discord.Embed(title="🏆 Classement des Elo", color=0xE87722)

    if not rows:
        embed.description = "Aucun classement disponible."
        embed.set_footer(text="Mis à jour automatiquement à chaque changement de score")
        return embed

    lines = []
    for row in rows:
        rank     = row["rank"]
        elo      = row["elo"] or 0
        grade    = _grade_from_elo(elo)
        streak   = row.get("discord_streak", 0)
        username = row.get("username", "?")

        prefix     = _MEDALS[rank] if rank <= 3 else f"#{rank}"
        streak_str = f" 🔥 **{streak}**" if streak and streak > 0 else ""

        lines.append(f"{prefix} – **{username}** – {grade} – {elo} Elo{streak_str}")

    embed.description = "\n".join(lines)
    embed.set_footer(text="Mis à jour automatiquement à chaque changement de score")
    return embed


def _is_admin(ctx: commands.Context) -> bool:
    role = discord.utils.get(ctx.guild.roles, id=ADMIN_ROLE_ID)
    return role in ctx.author.roles if role else False


class ClassementCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot         = bot
        self._message_id: int | None = _load_message_id()
        self.refresh.change_interval(seconds=LEADERBOARD_REFRESH_SECONDS)
        self.refresh.start()

    def cog_unload(self):
        self.refresh.cancel()

    # ── Noyau : obtenir ou créer le message embed ────────────────────────────

    async def _get_or_create_message(
        self,
        channel: discord.TextChannel,
        embed: discord.Embed,
    ) -> discord.Message:
        """
        Tente de récupérer le message existant.
        Si introuvable, purge les anciens messages du bot et en crée un seul.
        """
        if self._message_id:
            try:
                msg = await channel.fetch_message(self._message_id)
                return msg
            except (discord.NotFound, discord.HTTPException):
                # Message supprimé — on repart de zéro
                self._message_id = None
                _save_message_id(None)

        # Purger pour éviter tout doublon avant de poster
        await channel.purge(limit=20, check=lambda m: m.author == self.bot.user)

        msg = await channel.send(embed=embed)
        self._message_id = msg.id
        _save_message_id(msg.id)
        return msg

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

        try:
            msg = await self._get_or_create_message(channel, embed)
            # Si le message existait déjà, on l'édite (sinon il est déjà posté frais)
            if msg.embeds and msg.embeds[0].description != embed.description:
                await msg.edit(embed=embed)
            elif not msg.embeds:
                await msg.edit(embed=embed)
        except Exception as e:
            print(f"[classement] Erreur update_embed : {e}")

    # ── Task de refresh périodique ───────────────────────────────────────────

    @tasks.loop(seconds=300)
    async def refresh(self):
        await self.update_embed()

    @refresh.before_loop
    async def before_refresh(self):
        await self.bot.wait_until_ready()

    # ── //setup_classement (admin) ───────────────────────────────────────────

    @commands.command(name="setup_classement")
    async def setup_classement(self, ctx: commands.Context):
        """//setup_classement — (Re)installe l'embed classement (admin)."""
        if not _is_admin(ctx):
            return await ctx.send("❌ Commande réservée aux admins.", delete_after=5)

        await ctx.message.delete(delay=2)

        channel = self.bot.get_channel(CLASSEMENT_CHANNEL_ID)
        if not channel:
            return await ctx.send(
                f"❌ Channel #classement introuvable (ID {CLASSEMENT_CHANNEL_ID}).",
                delete_after=10,
            )

        # Forcer la recréation propre
        self._message_id = None
        _save_message_id(None)

        try:
            rows  = await get_leaderboard(limit=LEADERBOARD_LIMIT)
            embed = _build_embed(rows)
        except Exception as e:
            return await ctx.send(f"❌ Erreur API : {e}", delete_after=10)

        await self._get_or_create_message(channel, embed)

        await ctx.send(
            f"✅ Embed classement installé dans <#{CLASSEMENT_CHANNEL_ID}>.",
            delete_after=5,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(ClassementCog(bot))