from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from sqlalchemy import select
from datetime import datetime, timezone

from app.db.database import get_db
from app.db.models import User, Subscription, PendingSubscription
from app.schemas.auth import RegisterIn, LoginIn, AuthOut, UserOut, validate_password
from app.core.security import hash_password, verify_password, create_access_token, decode_token
from app.core.limits import get_limits

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


def get_current_user_optional(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User | None:
    """Comme get_current_user mais retourne None si pas de token (pour les endpoints publics)."""
    if not creds:
        return None

    try:
        payload = decode_token(creds.credentials)
        user_id = int(payload["sub"])
    except Exception:
        return None

    user = db.execute(
        select(User).where(User.id == user_id)
    ).scalar_one_or_none()

    return user


# compute_file_entitlements supprimé — remplacé par app.core.limits


def activate_pending_subscription(db: Session, user: User) -> bool:
    """
    Appelé à l'inscription. Si l'email de l'user correspond à un paiement
    Stripe en attente (PendingSubscription), applique le plan immédiatement
    et supprime la ligne pending.
    Retourne True si un plan a été activé.
    """
    from sqlalchemy import select as _select
    pending = db.execute(
        _select(PendingSubscription).where(PendingSubscription.email == user.email.lower())
    ).scalar_one_or_none()

    if not pending:
        return False

    # Récupérer ou créer la subscription
    sub = db.execute(
        _select(Subscription).where(Subscription.user_id == user.id)
    ).scalar_one_or_none()
    if not sub:
        sub = Subscription(user_id=user.id)
        db.add(sub)

    sub.plan = pending.plan
    sub.billing_cycle = pending.billing_cycle
    sub.status = "active"
    sub.stripe_subscription_id = pending.stripe_subscription_id
    sub.stripe_customer_id = pending.stripe_customer_id
    sub.current_period_start = pending.current_period_start
    sub.current_period_end = pending.current_period_end
    sub.cancelled_at = None
    db.commit()

    user.plan = pending.plan
    db.commit()

    db.delete(pending)
    db.commit()

    return True


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

    # Activer un abonnement en attente si l'email correspond à un paiement Stripe
    activate_pending_subscription(db, user)

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
    limits = get_limits(current_user.plan)

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

        # Limites du plan courant (source : limits.py)
        "files_limit": limits["files_total"],
        "files_ttl_hours": limits["file_ttl_hours"],
        "flashcards_limit": limits["flashcards_total"],
        "qcm_per_day": limits["qcm_per_day"],
    }