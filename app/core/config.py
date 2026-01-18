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

def ensure_storage_dirs() -> None:
    """
    Crée les dossiers de stockage si absents.
    Appelé au démarrage (lifespan) pour éviter les erreurs runtime.
    """
    Path(STORAGE_PATH).mkdir(parents=True, exist_ok=True)
    USER_FILES_DIR.mkdir(parents=True, exist_ok=True)
