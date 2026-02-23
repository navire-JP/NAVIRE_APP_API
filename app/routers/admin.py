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

@router.post("/make-admin")
def make_admin(
    email: str,
    x_api_key: str | None = Header(default=None),
):
    expected = os.getenv("API_KEY")
    if not expected or x_api_key != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")

    from app.db.database import SessionLocal
    from app.db.models import User

    db = SessionLocal()
    try:
        u = db.query(User).filter(User.email == email).first()
        if not u:
            raise HTTPException(status_code=404, detail="User not found")
        u.is_admin = True
        db.commit()
        return {"ok": True, "email": email, "is_admin": True}
    finally:
        db.close()