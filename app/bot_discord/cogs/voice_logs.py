# app/bot_discord/cogs/voice_logs.py
"""
Journal des salons vocaux : qui rejoint/quitte quel salon, avec son plan
NAVIRE si le compte est lié — sinon signalé comme non lié.
"""

from __future__ import annotations

import discord
from discord.ext import commands

from app.bot_discord.config import COLOR_ADMIN, VOICE_LOGS_CHANNEL_ID
from app.bot_discord.utils.api_client import get_navire_user


class VoiceLogsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _member_line(self, member: discord.Member) -> str:
        try:
            data = await get_navire_user(str(member.id))
        except Exception:
            data = None
        if data:
            return f"{member.mention} — **{data.get('plan', 'free')}** · {data.get('elo', 0)} ELO"
        return f"{member.mention} — *compte NAVIRE non lié*"

    @commands.Cog.listener()
    async def on_voice_state_update(
        self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
    ) -> None:
        if member.bot or before.channel == after.channel:
            return

        channel = self.bot.get_channel(VOICE_LOGS_CHANNEL_ID)
        if not channel:
            return

        line = await self._member_line(member)

        if before.channel is None and after.channel is not None:
            desc = f"🔊 {line}\nA rejoint **{after.channel.name}**"
        elif before.channel is not None and after.channel is None:
            desc = f"🔇 {line}\nA quitté **{before.channel.name}**"
        else:
            desc = f"🔀 {line}\nA basculé de **{before.channel.name}** vers **{after.channel.name}**"

        embed = discord.Embed(description=desc, color=COLOR_ADMIN)
        embed.set_footer(text=f"ID : {member.id}")
        embed.timestamp = discord.utils.utcnow()

        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(VoiceLogsCog(bot))
