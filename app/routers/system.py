from fastapi import APIRouter
from app.core.config import get_settings

router = APIRouter(tags=["system"])

@router.get("/health")
def health():
    s = get_settings()
    return {"status": "ok", "version": s.APP_VERSION}

@router.get("/version")
def version():
    s = get_settings()
    return {"name": s.APP_NAME, "version": s.APP_VERSION, "env": s.APP_ENV}
