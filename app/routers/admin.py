import os
from fastapi import APIRouter, Header, HTTPException
from app.db.database import Base, engine
from app.db import models  # important : charge les modèles

router = APIRouter(prefix="/admin", tags=["admin"])

@router.post("/reset-db")
def reset_db(x_api_key: str | None = Header(default=None)):
    expected = os.getenv("API_KEY")
    if not expected:
        raise HTTPException(status_code=500, detail="API_KEY not set")

    if not x_api_key or x_api_key != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    return {"ok": True, "message": "db reset"}
