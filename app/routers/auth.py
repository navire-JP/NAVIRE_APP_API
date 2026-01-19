from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from sqlalchemy import select
from datetime import datetime, timezone

from app.db.database import get_db
from app.db.models import User
from app.schemas.auth import RegisterIn, LoginIn, AuthOut, UserOut, validate_password
from app.core.security import hash_password, verify_password, create_access_token, decode_token

router = APIRouter(prefix="/auth", tags=["auth"])
bearer = HTTPBearer(auto_error=False)

def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    if not creds:
        raise HTTPException(status_code=401, detail="Token manquant.")

    try:
        payload = decode_token(creds.credentials)
        user_id = int(payload["sub"])
    except Exception:
        raise HTTPException(status_code=401, detail="Token invalide.")

    user = db.execute(
        select(User).where(User.id == user_id)
    ).scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=401, detail="Utilisateur introuvable.")

    return user

# ============================================================
# Entitlements (source de vérité des droits)
# ============================================================

def compute_file_entitlements(user: User) -> dict:
    """
    Retourne les droits fichiers selon le plan utilisateur.
    """

    # Admin ou AI+
    if user.is_admin or user.plan == "navire_ai_plus":
        return {
            "files_limit": 10,
            "files_ttl_hours": None,
        }

    # Abonné standard
    if user.plan == "navire_ai":
        return {
            "files_limit": 3,
            "files_ttl_hours": None,
        }

    # Free
    return {
        "files_limit": 1,
        "files_ttl_hours": 24,
    }


@router.post("/register", response_model=AuthOut)
def register(payload: RegisterIn, db: Session = Depends(get_db)):
    # 0) validation password
    validate_password(payload.password)

    # 1) vérifier email unique
    existing = db.execute(
        select(User).where(User.email == payload.email)
    ).scalar_one_or_none()

    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    # 2) hash password
    pwd_hash = hash_password(payload.password)

    # 3) create user (+ defaults temporaires)
    user = User(
        email=payload.email,
        username=payload.username,
        password_hash=pwd_hash,
        score=100,
        grade="Primo",
        newsletter_opt_in=payload.newsletter_opt_in,
        university=payload.university,
        study_level=payload.study_level,
    )


    db.add(user)
    db.commit()
    db.refresh(user)

    # 4) création du token (même logique que login)
    token = create_access_token(str(user.id))

    # 5) retour token + user
    return AuthOut(access_token=token, user=user)


@router.post("/login", response_model=AuthOut)
def login(payload: LoginIn, db: Session = Depends(get_db)):
    user = db.execute(
        select(User).where(User.email == payload.email)
    ).scalar_one_or_none()

    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Identifiants invalides.",
        )

    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)

    token = create_access_token(str(user.id))
    return AuthOut(access_token=token, user=user)


@router.get("/me")
def me(current_user: User = Depends(get_current_user)):
    entitlements = compute_file_entitlements(current_user)

    return {
        "id": current_user.id,
        "email": current_user.email,
        "username": current_user.username,

        "newsletter_opt_in": current_user.newsletter_opt_in,
        "university": current_user.university,
        "study_level": current_user.study_level,

        "score": current_user.score,
        "grade": current_user.grade,

        "plan": current_user.plan,
        "is_admin": current_user.is_admin,

        "files_limit": entitlements["files_limit"],
        "files_ttl_hours": entitlements["files_ttl_hours"],
    }


