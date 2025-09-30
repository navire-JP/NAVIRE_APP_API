from fastapi import Depends, HTTPException, Security
from fastapi.security.api_key import APIKeyHeader
from starlette.status import HTTP_401_UNAUTHORIZED

from app.core.config import get_settings

settings = get_settings()

api_key_header = APIKeyHeader(name="x-api-key", auto_error=False)

def get_api_key(api_key: str = Security(api_key_header)) -> str:
    """
    Vérifie que la clé API envoyée dans l'en-tête est correcte.
    """
    if api_key == settings.API_KEY:
        return api_key
    raise HTTPException(
        status_code=HTTP_401_UNAUTHORIZED,
        detail="API Key invalide",
    )
