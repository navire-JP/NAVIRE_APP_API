from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Base
    APP_ENV: str = "dev"  # dev | staging | prod
    APP_NAME: str = "NAVIRE APP API"
    APP_VERSION: str = "0.1.0"

    # CORS
    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:5173"

    # Security
    API_KEY: str = "change_me"

    # Storage
    STORAGE_PATH: str = "./storage"
    MAX_UPLOAD_MB: int = 25

    # Logging
    LOG_LEVEL: str = "INFO"

    class Config:
        env_file = ".env"
        case_sensitive = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
