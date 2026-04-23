# app/bot_discord/cogs/classement.py

import discord
from discord.ext import commands, tasks

from app.bot_discord.config import CLASSEMENT_CHANNEL_ID, LEADERBOARD_REFRESH_SECONDS
from app.bot_discord.utils.api_client import get_leaderboard

_MEDALS     = {1: "🥇", 2: "🥈", 3: "🥉"}
_PLAN_BADGE = {"membre+": "⭐", "membre": "✦", "free": ""}


def _build_embed(rows: list[dict]) -> discord.Embed:
    embed = discord.Embed(
        title="🏛️ Classement NAVIRE — ELO",
        description="ELO gagné sur navire-ai.com",
        color=0xE87722,
    )
    if not rows:
        embed.add_field(name="—", value="Aucun classement disponible.")
        return embed

    lines = []
    for row in rows:
        rank    = row["rank"]
        medal   = _MEDALS.get(rank, f"`#{rank}`")
        badge   = _PLAN_BADGE.get(row.get("plan", "free"), "")
        did     = row.get("discord_id")
        mention = f"<@{did}>" if did else f"**{row['username']}**"
        lines.append(f"{medal} {mention} {badge} — **{row['elo']} ELO**")

    embed.add_field(name="\u200b", value="\n".join(lines), inline=False)
    embed.set_footer(text="Mis à jour automatiquement • //link pour apparaître")
    return embed


class ClassementCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot         = bot
        self._message_id: int | None = None
        self.refresh.change_interval(seconds=LEADERBOARD_REFRESH_SECONDS)
        self.refresh.start()

    def cog_unload(self):
        self.refresh.cancel()

    @tasks.loop(seconds=300)
    async def refresh(self):
        channel = self.bot.get_channel(CLASSEMENT_CHANNEL_ID)
        if not channel:
            return
        try:
            embed = _build_embed(await get_leaderboard(limit=10))
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

        await channel.purge(limit=5, check=lambda m: m.author == self.bot.user)
        msg = await channel.send(embed=embed)
        self._message_id = msg.id

    @refresh.before_loop
    async def before_refresh(self):
        await self.bot.wait_until_ready()

    @commands.command(name="classement")
    async def classement_cmd(self, ctx: commands.Context):
        """//classement — Affiche le classement ELO NAVIRE."""
        try:
            embed = _build_embed(await get_leaderboard(limit=10))
        except Exception as e:
            return await ctx.send(f"❌ Erreur : {e}")
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(ClassementCog(bot))