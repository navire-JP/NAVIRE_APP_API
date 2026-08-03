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

# ============================================================
# Prép'AdJuris — rôles additifs, indépendants de PLAN_TO_ROLE
# ============================================================
# Ces rôles ne passent JAMAIS par sync_member_role (SyncRolesCog) : celui-ci
# retire tous les rôles de PLAN_TO_ROLE avant d'en remettre un seul, ce qui
# écraserait un rôle Adjuris déjà attribué. Chemin séparé, voir role_sync.py.
PREPA_ADJURIS_CHANNEL_ID = int(os.getenv("DISCORD_PREPA_ADJURIS_CHANNEL_ID", "0"))

PREPA_ADJURIS_ROLE_IDS: dict[str, int] = {
    "L1_droit_constit":               1533851155321589903,
    "L1_intro_au_droit":              1533851253594132682,
    "L1_droit_ijae":                  1533851341355483297,
    "L2_droit_administratif":         1533851438114144388,
    "L2_droit_des_obligations":       1533851507567493210,
    "L2_droit_penal":                 1533851580921549022,
    "L3_droit_des_suretes":           1533851629277810860,
    "L3_droit_des_societes":          1533851695271116891,
    "L3_droit_des_contrats_speciaux": 1533851742372888727,
}