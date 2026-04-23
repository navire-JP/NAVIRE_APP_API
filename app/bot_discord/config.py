# app/bot_discord/config.py

import os

DISCORD_TOKEN               = os.getenv("DISCORD_TOKEN", "")
BOT_SECRET                  = os.getenv("BOT_SECRET", "DEV_BOT_SECRET_CHANGE_ME")
API_BASE_URL                = os.getenv("API_BASE_URL", "https://navire-app-api.onrender.com")
GUILD_ID                    = int(os.getenv("DISCORD_GUILD_ID", "0"))
CLASSEMENT_CHANNEL_ID       = int(os.getenv("DISCORD_CLASSEMENT_CHANNEL_ID", "0"))
LOG_CHANNEL_ID              = int(os.getenv("DISCORD_LOG_CHANNEL_ID", "1351521002068119593"))
LEADERBOARD_REFRESH_SECONDS = int(os.getenv("LEADERBOARD_REFRESH_SECONDS", "300"))
LEADERBOARD_LIMIT           = int(os.getenv("LEADERBOARD_LIMIT", "20"))

# Rôle admin Discord — seuls les membres avec ce rôle peuvent utiliser les commandes admin
ADMIN_ROLE_ID = int(os.getenv("DISCORD_ADMIN_ROLE_ID", "1132339702159118346"))

PLAN_TO_ROLE: dict[str, str] = {
    "membre":  os.getenv("DISCORD_ROLE_MEMBRE",      "navire_ai"),
    "membre+": os.getenv("DISCORD_ROLE_MEMBRE_PLUS", "navire_ai+"),
}