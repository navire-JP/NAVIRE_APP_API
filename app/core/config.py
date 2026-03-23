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

# Price IDs — un par combinaison plan × cycle
STRIPE_PRICES: dict[str, dict[str, str]] = {
    "membre": {
        "monthly": os.getenv("STRIPE_PRICE_MEMBRE_MONTHLY", ""),
        "annual":  os.getenv("STRIPE_PRICE_MEMBRE_ANNUAL", ""),
    },
    "membre+": {
        "monthly": os.getenv("STRIPE_PRICE_MEMBRE_PLUS_MONTHLY", ""),
        "annual":  os.getenv("STRIPE_PRICE_MEMBRE_PLUS_ANNUAL", ""),
    },
}

# URL de redirection après checkout Stripe (à setter sur Render)
STRIPE_SUCCESS_URL = os.getenv("STRIPE_SUCCESS_URL", "https://ton-app.fr/subscribe?success=true")
STRIPE_CANCEL_URL  = os.getenv("STRIPE_CANCEL_URL",  "https://ton-app.fr/subscribe?cancelled=true")


# ============================================================
# Brevo (emails transactionnels)
# ============================================================
BREVO_API_KEY     = os.getenv("BREVO_API_KEY", "")
BREVO_SENDER_EMAIL = os.getenv("BREVO_SENDER_EMAIL", "no-reply@navire.fr")
BREVO_SENDER_NAME  = os.getenv("BREVO_SENDER_NAME", "NAVIRE")

# URL du front — utilisée dans les liens des emails (ex: lien d'inscription)
FRONTEND_URL = os.getenv("FRONTEND_URL", "https://navire.framer.website")


def ensure_storage_dirs() -> None:
    """
    Crée les dossiers de stockage si absents.
    Appelé au démarrage (lifespan) pour éviter les erreurs runtime.
    """
    Path(STORAGE_PATH).mkdir(parents=True, exist_ok=True)
    USER_FILES_DIR.mkdir(parents=True, exist_ok=True)