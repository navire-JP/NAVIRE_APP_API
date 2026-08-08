# app/bot_discord/config.py

import os

DISCORD_TOKEN               = os.getenv("DISCORD_TOKEN", "")
BOT_SECRET                  = os.getenv("BOT_SECRET", "DEV_BOT_SECRET_CHANGE_ME")
API_BASE_URL                = os.getenv("API_BASE_URL", "https://navire-app-api.onrender.com")
GUILD_ID                    = int(os.getenv("DISCORD_GUILD_ID", "1131156852265201715"))
CLASSEMENT_CHANNEL_ID       = int(os.getenv("DISCORD_CLASSEMENT_CHANNEL_ID", "0"))
LOG_CHANNEL_ID              = int(os.getenv("DISCORD_LOG_CHANNEL_ID", "1351521002068119593"))
LEADERBOARD_REFRESH_SECONDS = int(os.getenv("LEADERBOARD_REFRESH_SECONDS", "300"))
LEADERBOARD_LIMIT           = int(os.getenv("LEADERBOARD_LIMIT", "20"))

# Rôle admin Discord — seuls les membres avec ce rôle peuvent utiliser les commandes admin
ADMIN_ROLE_ID = int(os.getenv("DISCORD_ADMIN_ROLE_ID", "1132339702159118346"))

# ============================================================
# Salons — accueil, veille, logs
# ============================================================
NOTIFICATOR_CHANNEL_ID     = int(os.getenv("DISCORD_NOTIFICATOR_CHANNEL_ID",     "1355177276005679247"))
HELPMENU_CHANNEL_ID        = int(os.getenv("DISCORD_HELPMENU_CHANNEL_ID",        "1424454231607349258"))
VEILLE_CHANNEL_ID          = int(os.getenv("DISCORD_VEILLE_CHANNEL_ID",          "1368599844511416391"))
LOGS_NEWSLETTER_CHANNEL_ID = int(os.getenv("DISCORD_LOGS_NEWSLETTER_CHANNEL_ID", "1368661001049739335"))
VOICE_LOGS_CHANNEL_ID      = int(os.getenv("DISCORD_VOICE_LOGS_CHANNEL_ID",      "1535543052750299278"))
# Pas d'ID fourni pour le journal de modération — reste désactivé (aucun envoi,
# le reste de la modération fonctionne quand même) tant que la variable n'est
# pas positionnée.
MODLOG_CHANNEL_ID          = int(os.getenv("DISCORD_MODLOG_CHANNEL_ID", "0"))

# ============================================================
# Charte visuelle des embeds
# ============================================================
# Blanc/bleu pour tout ce qui est visible des étudiants, noir pour le
# côté admin (logs, panneaux de modération/aide réservés au staff).
COLOR_PRIMARY = 0x2E86DE  # bleu
COLOR_WHITE   = 0xFFFFFF
COLOR_ADMIN   = 0x000000  # noir

# ============================================================
# Modération
# ============================================================
MOD_MAX_WARNS       = int(os.getenv("DISCORD_MOD_MAX_WARNS", "3"))
MOD_TIMEOUT_MINUTES = int(os.getenv("DISCORD_MOD_TIMEOUT_MINUTES", "10"))

# Plan NAVIRE → nom du rôle Discord correspondant.
# C'est la seule source de vérité des rôles "d'abonnement" : sync_member_role
# retire tous les rôles listés ici avant de poser celui du plan courant.
# Le plan "free" n'a volontairement pas de rôle (aucun rôle posé).
#
# Pas d'entrée pour le plan "prepa" (PREPASSERELLE) : ce programme n'a pas de
# rôle Discord. Prép'AdJuris non plus n'apparaît pas ici — ses accès passent par
# les rôles par matière ci-dessous, attribués sur un chemin séparé.
PLAN_TO_ROLE: dict[str, str] = {
    "membre":  os.getenv("DISCORD_ROLE_MEMBRE",      "navire_ai"),
    "membre+": os.getenv("DISCORD_ROLE_MEMBRE_PLUS", "navire_ai+"),
}

# Surveillance des changements d'abonnement (cogs/sync_account.py) : intervalle
# entre deux relectures de l'état des comptes liés côté API.
SYNC_POLL_SECONDS = int(os.getenv("DISCORD_SYNC_POLL_SECONDS", "180"))

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