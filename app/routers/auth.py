from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from sqlalchemy import select, or_
from datetime import datetime, timezone

from app.db.database import get_db
from app.db.models import User
from app.schemas.auth import RegisterIn, LoginIn, AuthOut, UserOut, validate_password
from app.core.security import hash_password, verify_password, create_access_token, decode_token

router = APIRouter(prefix="/auth", tags=["auth"])
bearer = HTTPBearer(auto_error=False)

@router.post("/register", response_model=AuthOut)
def register(payload: RegisterIn, db: Session = Depends(get_db)):
    try:
        validate_password(payload.password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    exists = db.execute(
        select(User).where(or_(User.email == payload.email, User.username == payload.username))
    ).scalar_one_or_none()
    if exists:
        raise HTTPException(status_code=409, detail="Email ou username déjà utilisé.")

    user = User(
        email=payload.email,
        username=payload.username,
        password_hash=hash_password(payload.password),
        newsletter_opt_in=payload.newsletter_opt_in,
        university=payload.university,
        study_level=payload.study_level,
        score=0,
        grade="Cadet",
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token(str(user.id))
    return AuthOut(access_token=token, user=user)

@router.post("/login", response_model=AuthOut)
def login(payload: LoginIn, db: Session = Depends(get_db)):
    user = db.execute(select(User).where(User.email == payload.email)).scalar_one_or_none()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Identifiants invalides.")

    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)

    token = create_access_token(str(user.id))
    return AuthOut(access_token=token, user=user)

@router.get("/me", response_model=UserOut)
def me(creds: HTTPAuthorizationCredentials | None = Depends(bearer), db: Session = Depends(get_db)):
    if not creds:
        raise HTTPException(status_code=401, detail="Token manquant.")
    try:
        payload = decode_token(creds.credentials)
        user_id = int(payload["sub"])
    except Exception:
        raise HTTPException(status_code=401, detail="Token invalide.")

    user = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="Utilisateur introuvable.")
    return user
