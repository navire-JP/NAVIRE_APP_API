import os
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app


@pytest.fixture(scope="session")
def test_client(tmp_path_factory, monkeypatch):
    """
    Crée un TestClient avec un STORAGE_PATH temporaire (isolé),
    et force quelques variables d'env pour les tests.
    """
    tmp_storage = tmp_path_factory.mktemp("storage")
    # Variables d'env pour les settings
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("APP_NAME", "NAVIRE APP API (tests)")
    monkeypatch.setenv("STORAGE_PATH", str(tmp_storage))
    monkeypatch.setenv("MAX_UPLOAD_MB", "2")  # limite faible pour tests
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost")
    monkeypatch.setenv("API_KEY", "change_me")

    # IMPORTANT: vider le cache des settings pour prendre en compte les env
    try:
        get_settings.cache_clear()  # type: ignore[attr-defined]
    except Exception:
        pass

    app = create_app()
    client = TestClient(app)
    return client
