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
    "app.bot_discord.cogs.prepa_adjuris",
    "app.bot_discord.cogs.sync_account",
]


@bot.event
async def on_ready():
    print(f"✅ NAVIRE Bot connecté : {bot.user} (ID {bot.user.id})")

    # Enregistre les slash commands auprès de Discord (/setup_sync_embed…).
    try:
        synced = await bot.tree.sync()
        print(f"[bot_discord] ✅ {len(synced)} slash command(s) synchronisée(s)")
    except Exception as e:
        print(f"[bot_discord] ❌ sync slash commands : {e}")


async def run_bot():
    """Appelé depuis app/main.py dans un thread daemon."""
    async with bot:
        for cog in _COGS:
            try:
                await bot.load_extension(cog)
                print(f"[bot_discord] ✅ {cog}")
            except Exception as e:
                print(f"[bot_discord] ❌ {cog} : {e}")

        # Vue persistante (bouton "Lier mon compte NAVIRE") — enregistrée une
        # fois pour survivre aux redémarrages du bot.
        from app.bot_discord.cogs.prepa_adjuris import LinkAccountView
        from app.bot_discord.cogs.sync_account import SyncAccountView
        bot.add_view(LinkAccountView())
        bot.add_view(SyncAccountView())

        await bot.start(DISCORD_TOKEN)