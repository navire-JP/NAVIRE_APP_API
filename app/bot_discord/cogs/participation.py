# app/bot_discord/cogs/participation.py

import discord
from discord.ext import commands

from app.bot_discord.utils.api_client import record_participation


class ParticipationCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return

        try:
            result = await record_participation(str(message.author.id), message_count=1)
        except Exception as e:
            print(f"[participation] Erreur API {message.author.id}: {e}")
            return

        if not result.get("ok"):
            return

        elo_gained = result.get("elo_gained", 0)
        if elo_gained <= 0:
            return

        streak       = result.get("streak", 0)
        new_elo      = result.get("new_elo", 0)
        streak_bonus = result.get("streak_bonus", 0)

        lines = [f"**+{elo_gained} ELO** sur NAVIRE ! (total : **{new_elo} ELO**)"]
        if streak_bonus:
            lines.append(f"🔥 Bonus streak {streak} jours consécutifs (+{streak_bonus} ELO)")

        try:
            await message.author.send("\n".join(lines))
        except discord.Forbidden:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(ParticipationCog(bot))