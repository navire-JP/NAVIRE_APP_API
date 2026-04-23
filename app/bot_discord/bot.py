# app/bot_discord/bot.py

import discord
from discord.ext import commands

from app.bot_discord.config import DISCORD_TOKEN

intents = discord.Intents.default()
intents.members         = True
intents.message_content = True
intents.guilds          = True

bot = commands.Bot(command_prefix="//", intents=intents, help_command=None)

_COGS = [
    "app.bot_discord.cogs.participation",
    "app.bot_discord.cogs.sync_roles",
    "app.bot_discord.cogs.classement",
]


@bot.event
async def on_ready():
    print(f"✅ NAVIRE Bot connecté : {bot.user} (ID {bot.user.id})")


async def run_bot():
    """Appelé depuis app/main.py dans un thread daemon."""
    async with bot:
        for cog in _COGS:
            try:
                await bot.load_extension(cog)
                print(f"[bot_discord] ✅ {cog}")
            except Exception as e:
                print(f"[bot_discord] ❌ {cog} : {e}")
        await bot.start(DISCORD_TOKEN)