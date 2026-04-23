# bot_discord/main.py
# Point d'entrée du bot Discord NAVIRE.
# Lancé comme worker Render séparé : python -m bot_discord.main

import asyncio
import discord
from discord.ext import commands

from bot_discord.config import DISCORD_TOKEN

intents = discord.Intents.default()
intents.members         = True
intents.message_content = True
intents.guilds          = True

bot = commands.Bot(command_prefix="//", intents=intents, help_command=None)

_COGS = [
    "bot_discord.cogs.participation",
    "bot_discord.cogs.sync_roles",
    "bot_discord.cogs.classement",
]


@bot.event
async def on_ready():
    print(f"✅ NAVIRE Bot connecté : {bot.user} (ID {bot.user.id})")


async def main():
    async with bot:
        for cog in _COGS:
            try:
                await bot.load_extension(cog)
                print(f"[cog] {cog} chargé")
            except Exception as e:
                print(f"[cog] ERREUR {cog} : {e}")
        await bot.start(DISCORD_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())