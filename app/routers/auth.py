from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from sqlalchemy import select
from datetime import datetime, timezone

from app.db.database import get_db
from app.db.models import User, Subscription, PendingSubscription
from app.schemas.auth import (
    RegisterIn,
    LoginIn,
    AuthOut,
    UserOut,
    EmailCodeRequestIn,
    EmailCodeVerifyIn,
    OAuthIn,
    validate_password,
)
from app.core.security import hash_password, verify_password, create_access_token, decode_token
from app.core.config import REQUIRE_EMAIL_VERIFICATION
from app.core.limits import get_limits
from app.core.cloudinary_client import resolve_avatar_url
from app.services.email_verification import (
    request_code,
    check_code,
    email_token_is_valid,
    purge_verification,
    VerificationError,
)
from app.services.oauth import (
    verify_provider_token,
    OAuthError,
    OAuthNotConfigured,
)

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


def activate_pending_prepa_adjuris(db: Session, user: User) -> int:
    """
    Appelé à l'inscription. Rattache les paiements Prép'AdJuris effectués avec
    cet email avant la création du compte (le formulaire public paie sans
    authentification), puis ouvre l'accès Discord.
    Retourne le nombre de matières rattachées.

    Imports différés : subscriptions.py importe ce module, un import en tête de
    fichier créerait un cycle.
    """
    from sqlalchemy import select as _select
    from app.db.models import PrepaAdjurisEnrollment
    from app.core.prepa_adjuris_config import matiere_niveau
    from app.routers.subscriptions import send_adjuris_discord_invite

    orphelines = db.execute(
        _select(PrepaAdjurisEnrollment).where(
            PrepaAdjurisEnrollment.user_id.is_(None),
            PrepaAdjurisEnrollment.email == user.email.lower(),
        )
    ).scalars().all()

    if not orphelines:
        return 0

    for enrollment in orphelines:
        enrollment.user_id = user.id
    db.commit()

    actives = [e.matiere_key for e in orphelines if e.status == "active"]

    # Même grade que le webhook (subscriptions.py::_handle_prepa_adjuris_checkout) :
    # ce chemin ne fait que rattraper un paiement fait avant la création du
    # compte, le résultat doit être identique.
    if actives:
        today = datetime.now(timezone.utc)
        expiry = datetime(today.year, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
        user.plan = "prepa"
        user.prepa_annee = matiere_niveau(actives[0])
        user.prepa_expires_at = expiry
        db.commit()

    try:
        send_adjuris_discord_invite(db, user, actives)
    except Exception:
        # L'accès Discord ne doit jamais faire échouer une inscription.
        pass

    return len(orphelines)


@router.get("/email-exists")
def email_exists(email: str, db: Session = Depends(get_db)):
    """
    Un compte existe-t-il déjà avec cet email ? Utilisé par les formulaires
    d'inscription pour basculer entre écran de connexion et écran de
    création de compte avant même la soumission (ex : Prép'AdJuris). Ne
    révèle rien de plus que ce que /auth/register révèle déjà via son 400
    "Email already registered" — même comparaison, non insensible à la casse,
    pour rester cohérent avec register()/login().
    """
    user = db.execute(
        select(User).where(User.email == email)
    ).scalar_one_or_none()
    return {"exists": user is not None}


@router.post("/register", response_model=AuthOut)
def register(payload: RegisterIn, db: Session = Depends(get_db)):
    # 0) validation password
    try:
        validate_password(payload.password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # 1) vérifier email unique
    existing = db.execute(
        select(User).where(User.email == payload.email)
    ).scalar_one_or_none()

    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    # 1 bis) l'adresse a-t-elle été prouvée ?
    # Le jeton vient de /auth/verify-code : il atteste que le code envoyé à
    # cette adresse a bien été rendu, donc que la boîte appartient à la
    # personne qui s'inscrit. Sans lui, le compte est créé non vérifié (ou
    # refusé si REQUIRE_EMAIL_VERIFICATION est actif).
    verified = email_token_is_valid(payload.email_token or "", payload.email)

    if REQUIRE_EMAIL_VERIFICATION and not verified:
        raise HTTPException(
            status_code=400,
            detail="Adresse email non vérifiée. Demande un code puis valide-le avant de créer ton compte.",
        )

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
        email_verified=verified,
        email_verified_at=datetime.now(timezone.utc) if verified else None,
    )


    db.add(user)
    db.commit()
    db.refresh(user)

    # Activer un abonnement en attente si l'email correspond à un paiement Stripe
    activate_pending_subscription(db, user)

    # Rattacher les paiements Prép'AdJuris faits avant la création du compte
    activate_pending_prepa_adjuris(db, user)

    # Le code de vérification a rempli son office
    if verified:
        purge_verification(db, user.email)

    # 4) création du token (même logique que login)
    token = create_access_token(str(user.id))

    # 5) retour token + user
    return AuthOut(access_token=token, user=user)


@router.post("/login", response_model=AuthOut)
def login(payload: LoginIn, db: Session = Depends(get_db)):
    user = db.execute(
        select(User).where(User.email == payload.email)
    ).scalar_one_or_none()

    # Un compte créé via Google/Facebook n'a pas de mot de passe : verify_password
    # planterait sur un hash absent, et surtout le seul moyen d'y entrer doit
    # rester le fournisseur.
    if user and not user.password_hash:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Ce compte se connecte avec {user.oauth_provider or 'Google ou Facebook'}.",
        )

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

        "email_verified": current_user.email_verified,
        "oauth_provider": current_user.oauth_provider,

        "newsletter_opt_in": current_user.newsletter_opt_in,
        "university": current_user.university,
        "study_level": current_user.study_level,
        "avatar_url": resolve_avatar_url(current_user.avatar_url),

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

# ============================================================
# Vérification d'adresse email
# ============================================================

@router.post("/request-code")
def request_email_code(payload: EmailCodeRequestIn, db: Session = Depends(get_db)):
    """
    Envoie un code à 6 chiffres à l'adresse indiquée.

    C'est la seule preuve possible qu'une adresse appartient à la personne qui
    s'inscrit : le code n'arrive que dans la boîte concernée. Tant qu'il n'est
    pas rendu, aucun compte n'est créé avec cette adresse.
    """
    email = payload.email.strip().lower()

    existing = db.execute(
        select(User).where(User.email == email)
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail="Un compte existe déjà avec cet email.")

    try:
        cooldown = request_code(db, email, payload.username or "")
    except VerificationError as e:
        # 429 quand c'est un problème de cadence, 400 sinon.
        status_code = 429 if e.retry_after else 400
        raise HTTPException(status_code=status_code, detail=e.message)

    return {"sent": True, "cooldown": cooldown}


@router.post("/verify-code")
def verify_email_code(payload: EmailCodeVerifyIn, db: Session = Depends(get_db)):
    """
    Valide le code reçu et retourne le jeton à joindre à /auth/register.
    Le jeton vaut 30 minutes et ne concerne que cette adresse.
    """
    try:
        email_token = check_code(db, payload.email, payload.code)
    except VerificationError as e:
        raise HTTPException(status_code=400, detail=e.message)

    return {"verified": True, "email_token": email_token}


# ============================================================
# Connexion Google / Facebook
# ============================================================

@router.post("/oauth", response_model=AuthOut)
def oauth_login(payload: OAuthIn, db: Session = Depends(get_db)):
    """
    Inscription ou connexion via Google/Facebook.

    L'email n'est jamais lu depuis la requête : il est récupéré auprès du
    fournisseur à partir du jeton, une fois vérifié que ce jeton a bien été
    émis pour NAVIRE. Impossible, donc, de se déclarer propriétaire d'une
    adresse qu'on ne possède pas.

    Trois cas :
      - compte déjà lié au fournisseur, on connecte ;
      - compte existant avec la même adresse, on rattache le fournisseur ;
      - aucun compte, on en crée un, sans mot de passe et déjà vérifié.
    """
    try:
        profile = verify_provider_token(payload.provider, payload.access_token)
    except OAuthNotConfigured as e:
        # Configuration serveur incomplète : c'est une erreur d'exploitation,
        # pas une faute de l'utilisateur.
        raise HTTPException(status_code=503, detail=str(e))
    except OAuthError as e:
        raise HTTPException(status_code=401, detail=str(e))

    email = profile.get("email")
    if not email:
        raise HTTPException(
            status_code=400,
            detail="Ton compte Facebook n'a pas d'adresse email. Inscris-toi avec ton email.",
        )

    provider = profile["provider"]
    sub = profile["sub"]

    # 1) déjà connu par ce fournisseur
    user = db.execute(
        select(User).where(User.oauth_provider == provider, User.oauth_sub == sub)
    ).scalar_one_or_none()

    # 2) sinon, compte existant portant la même adresse
    if user is None:
        user = db.execute(
            select(User).where(User.email == email)
        ).scalar_one_or_none()

        if user is not None:
            # Rattachement : l'adresse est prouvée par le fournisseur, le
            # propriétaire du compte est donc bien celui qui se présente.
            user.oauth_provider = provider
            user.oauth_sub = sub
            if not user.email_verified:
                user.email_verified = True
                user.email_verified_at = datetime.now(timezone.utc)
            db.commit()

    # 3) sinon, création
    if user is None:
        username = (profile.get("given_name") or profile.get("name") or email.split("@")[0]).strip()
        username = (username or "Navigateur")[:64]
        if len(username) < 3:
            username = "Navigateur"

        user = User(
            email=email,
            username=username,
            password_hash=None,
            score=100,
            grade="Primo",
            newsletter_opt_in=payload.newsletter_opt_in,
            university=payload.university,
            study_level=payload.study_level,
            email_verified=True,
            email_verified_at=datetime.now(timezone.utc),
            oauth_provider=provider,
            oauth_sub=sub,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        # Mêmes rattrapages que l'inscription classique : un paiement fait
        # avant la création du compte doit être appliqué ici aussi.
        activate_pending_subscription(db, user)
        activate_pending_prepa_adjuris(db, user)

    else:
        # Connexion d'un compte déjà existant
        if payload.university and not user.university:
            user.university = payload.university
        if payload.study_level and not user.study_level:
            user.study_level = payload.study_level
        user.last_login_at = datetime.now(timezone.utc)
        db.commit()

    db.refresh(user)
    token = create_access_token(str(user.id))

    return AuthOut(access_token=token, user=user)
