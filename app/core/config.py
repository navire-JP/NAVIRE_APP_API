import os

def _split_csv(value: str) -> list[str]:
    return [x.strip() for x in value.split(",") if x.strip()]

APP_ENV = os.getenv("APP_ENV", "dev")
APP_NAME = os.getenv("APP_NAME", "NAVIRE APP API")
APP_VERSION = os.getenv("APP_VERSION", "0.1.0")

API_BASE_URL = os.getenv("API_BASE_URL", "")

CORS_ORIGINS = _split_csv(os.getenv("CORS_ORIGINS", ""))

JWT_SECRET = os.getenv("JWT_SECRET", "")
JWT_ALGO = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "10080"))

if not JWT_SECRET:
    # En prod tu peux rendre ça "fatal" (raise) ; en dev tu peux tolérer
    JWT_SECRET = "DEV_ONLY_CHANGE_ME"
