# app/bot_discord/cogs/participation.py
# +1 ELO toutes les 3 messages Discord + streak
# → log dans #log
# → mise à jour immédiate de l'embed classement

import discord
from discord.ext import commands

from app.bot_discord.config import LOG_CHANNEL_ID
from app.bot_discord.utils.api_client import record_participation


class ParticipationCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Ignorer bots et DMs
        if message.author.bot or message.guild is None:
            return

        try:
            result = await record_participation(str(message.author.id), message_count=1)
        except Exception as e:
            print(f"[participation] Erreur API {message.author.id}: {e}")
            return

        if not result.get("ok"):
            return  # user non lié → silencieux

        elo_gained = result.get("elo_gained", 0)
        if elo_gained <= 0:
            return

        new_elo      = result.get("new_elo", 0)
        streak       = result.get("streak", 0)
        streak_bonus = result.get("streak_bonus", 0)

        # ── Log dans #log ────────────────────────────────────────────────────
        log_channel = self.bot.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            desc = (
                f"**+{elo_gained} ELO** | {message.author.mention} "
                f"→ **{new_elo} ELO** total"
            )
            if streak_bonus:
                desc += f"\n🔥 Bonus streak {streak} jours (+{streak_bonus} ELO)"

            embed = discord.Embed(
                description=desc,
                color=0xE87722,
            )
            embed.set_author(
                name=message.author.display_name,
                icon_url=message.author.display_avatar.url,
            )
            embed.set_footer(text=f"#{message.channel.name}")
            try:
                await log_channel.send(embed=embed)
            except discord.Forbidden:
                pass

        # ── Mise à jour immédiate du classement ──────────────────────────────
        classement_cog = self.bot.get_cog("ClassementCog")
        if classement_cog:
            try:
                await classement_cog.update_embed()
            except Exception as e:
                print(f"[participation] Erreur update classement : {e}")


async def setup(bot: commands.Bot):
    await bot.add_cog(ParticipationCog(bot))