import os
from pathlib import Path

def _split_csv(value: str) -> list[str]:
    return [x.strip() for x in value.split(",") if x.strip()]

# ============================================================
# App
# ============================================================
APP_ENV = os.getenv("APP_ENV", "dev")
APP_NAME = os.getenv("APP_NAME", "NAVIRE APP API")
APP_VERSION = os.getenv("APP_VERSION", "0.1.0")

API_BASE_URL = os.getenv("API_BASE_URL", "")

# URL publique de l'API, utilisée partout où un lien absolu est nécessaire —
# en pratique le logo affiché en en-tête des emails, qui ne peut pas être servi
# par un chemin relatif. API_BASE_URL prime ; à défaut, l'URL Render de prod.
PUBLIC_API_URL = (API_BASE_URL or "https://navire-app-api.onrender.com").rstrip("/")

# Logo NAVIRE des emails. Servi par app/routers/assets.py depuis le dossier
# assets/ du dépôt ; surchargeable par EMAIL_LOGO_URL si un jour l'image est
# hébergée ailleurs (CDN, Cloudinary…).
EMAIL_LOGO_URL = os.getenv("EMAIL_LOGO_URL", f"{PUBLIC_API_URL}/assets/logo-navire.png")

CORS_ORIGINS = _split_csv(os.getenv("CORS_ORIGINS", ""))

# ============================================================
# Auth / JWT
# ============================================================
JWT_SECRET = os.getenv("JWT_SECRET", "")
JWT_ALGO = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "10080"))

if not JWT_SECRET:
    # En prod tu peux rendre ça "fatal" (raise) ; en dev tu peux tolérer
    JWT_SECRET = "DEV_ONLY_CHANGE_ME"

# ============================================================
# Bot Discord
# ============================================================
BOT_SECRET = os.getenv("BOT_SECRET", "DEV_BOT_SECRET_CHANGE_ME")

# ============================================================
# Storage (NAVIRE - step 0)
# ============================================================
# En prod Render: mets STORAGE_PATH sur le disque persistant (ex: /var/data/storage)
# En local: fallback sur ./storage
STORAGE_PATH = os.getenv("STORAGE_PATH", str(Path("./storage").resolve()))

# Racine des fichiers utilisateurs
USER_FILES_DIR = Path(STORAGE_PATH) / "UserFiles"

# Taille max upload (en bytes) - par défaut 20MB
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(20 * 1024 * 1024)))

# ============================================================
# Stripe
# ============================================================
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")

# Price IDs : un par combinaison plan × cycle
STRIPE_PRICES: dict[str, dict[str, str]] = {
    "membre": {
        "monthly": os.getenv("STRIPE_PRICE_MEMBRE_MONTHLY", ""),
        "annual":  os.getenv("STRIPE_PRICE_MEMBRE_ANNUAL", ""),
    },
    "membre+": {
        "monthly": os.getenv("STRIPE_PRICE_MEMBRE_PLUS_MONTHLY", ""),
        "annual":  os.getenv("STRIPE_PRICE_MEMBRE_PLUS_ANNUAL", ""),
    },
    "beta": {
        "monthly": os.getenv("STRIPE_PRICE_BETA", ""),
    },
    "prepa": {
        # Paiement unique (mode "payment", pas "subscription").
        # Price ID Stripe : price_1Tif3lLeRHpDiZMshDohbgZq
        "onetime": os.getenv("STRIPE_PRICE_PREPA_ONETIME", "price_1Tif3lLeRHpDiZMshDohbgZq"),
    },
}

# URL du front, utilisée par les redirections Stripe et les liens des emails.
# Définie ici (et non plus bas) parce que les URLs de retour Stripe en dépendent.
FRONTEND_URL = os.getenv("FRONTEND_URL", "https://navire-ai.com")

# Retour après un checkout Stripe. Les valeurs par défaut pointent sur le vrai
# site : un défaut factice (ancien "https://ton-app.fr/...") envoyait l'élève
# sur un domaine mort juste après avoir payé, si la variable manquait sur Render.
#   succès   → /login : l'élève se reconnecte et voit son grade tout de suite,
#              comme le checkout Prép'AdJuris.
#   annulé   → retour sur la page des offres.
STRIPE_SUCCESS_URL = os.getenv(
    "STRIPE_SUCCESS_URL",
    f"{FRONTEND_URL}/login?subscription=success",
)
STRIPE_CANCEL_URL = os.getenv(
    "STRIPE_CANCEL_URL",
    f"{FRONTEND_URL}/offres?subscription=cancelled",
)


# ============================================================
# Brevo (emails transactionnels)
# ============================================================
BREVO_API_KEY     = os.getenv("BREVO_API_KEY", "")
BREVO_SENDER_EMAIL = os.getenv("BREVO_SENDER_EMAIL", "no-reply@navire.fr")
BREVO_SENDER_NAME  = os.getenv("BREVO_SENDER_NAME", "NAVIRE")


# ============================================================
# Connexion Google / Facebook
# ============================================================
# GOOGLE_CLIENT_ID : Client ID OAuth "Web" (console.cloud.google.com).
#   La même valeur est utilisée par le formulaire côté navigateur ET par la
#   vérification côté serveur : c'est ce qui garantit que le jeton présenté a
#   bien été émis pour NAVIRE et pas pour une autre application.
# FACEBOOK_APP_ID / FACEBOOK_APP_SECRET : developers.facebook.com.
#   Le secret ne quitte jamais le serveur, il sert à valider le jeton reçu.
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
# Nécessaire au flux par fenêtre : l'échange du code contre un jeton se fait
# de serveur à serveur. Ce secret ne doit jamais figurer dans l'embed.
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
FACEBOOK_APP_ID = os.getenv("FACEBOOK_APP_ID", "")
FACEBOOK_APP_SECRET = os.getenv("FACEBOOK_APP_SECRET", "")

# ============================================================
# Vérification d'adresse email
# ============================================================
# Durée de validité du code à 6 chiffres envoyé par email.
EMAIL_CODE_TTL_MINUTES = int(os.getenv("EMAIL_CODE_TTL_MINUTES", "15"))
# Nombre d'essais autorisés avant invalidation du code.
EMAIL_CODE_MAX_ATTEMPTS = int(os.getenv("EMAIL_CODE_MAX_ATTEMPTS", "5"))
# Délai minimum entre deux envois pour une même adresse (secondes).
EMAIL_CODE_RESEND_COOLDOWN = int(os.getenv("EMAIL_CODE_RESEND_COOLDOWN", "60"))

# Exiger une adresse vérifiée pour TOUTE inscription par mot de passe.
# Laisser à "false" tant que d'anciens formulaires appellent /auth/register
# sans jeton de vérification : ils continueraient à fonctionner, mais leurs
# comptes seraient créés non vérifiés. Passer à "true" une fois tous les
# formulaires migrés.
REQUIRE_EMAIL_VERIFICATION = os.getenv("REQUIRE_EMAIL_VERIFICATION", "false").lower() == "true"

# ============================================================
# Discord (côté backend : construction de liens dans les emails)
# ============================================================
# Mêmes variables d'env que app/bot_discord/config.py (GUILD_ID, etc.) : le
# bot et le backend tournent dans le même process mais lisent chacun leur
# propre copie de config, comme BOT_SECRET ci-dessus.
DISCORD_GUILD_ID = int(os.getenv("DISCORD_GUILD_ID", "1131156852265201715"))
DISCORD_PREPA_ADJURIS_CHANNEL_ID = int(os.getenv("DISCORD_PREPA_ADJURIS_CHANNEL_ID", "0"))
# Salon #🔹connecter-mon-compte-navire, sert à construire le lien de
# redirection donné au bouton « Connecter mes comptes » de la page profil.
DISCORD_SYNC_CHANNEL_ID = int(os.getenv("DISCORD_SYNC_CHANNEL_ID", "1535091433768489061"))
# Invitation publique du serveur, utilisée si l'élève n'a pas encore rejoint.
DISCORD_INVITE_URL = os.getenv("DISCORD_INVITE_URL", "https://discord.gg/gW6FX8kVNB")


def ensure_storage_dirs() -> None:
    """
    Crée les dossiers de stockage si absents.
    Appelé au démarrage (lifespan) pour éviter les erreurs runtime.
    """
    Path(STORAGE_PATH).mkdir(parents=True, exist_ok=True)
    USER_FILES_DIR.mkdir(parents=True, exist_ok=True)