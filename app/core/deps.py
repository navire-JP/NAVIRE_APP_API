from app.core.config import get_settings
from app.services.storage import StorageService


def get_settings_dep():
    return get_settings()


def get_storage_service() -> StorageService:
    """
    Fournit le service de stockage en dépendance (DI).
    """
    settings = get_settings()
    return StorageService(base_path=settings.STORAGE_PATH)
