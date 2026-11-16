"""
Embed 4 — Authentification Kitly (inscription, connexion, mot de passe) et
gestion du compte privé.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.kitly import codes, emails
from app.kitly.config import KITLY_ADMIN_EMAILS, KITLY_ADMIN_SESSION_MINUTES, KITLY_CGU_VERSION, KITLY_PRIVACY_VERSION
from app.kitly.models import (
    KitlyAdminAction,
    KitlyLead,
    KitlyOffer,
    KitlyPortfolioProject,
    KitlyProfile,
    KitlySubscription,
    KitlyUser,
)
from app.kitly.plans import get_plan, plan_out
from app.kitly.schemas import (
    AccountDeleteIn,
    AccountUpdateIn,
    CodeIn,
    ForgotIn,
    LoginIn,
    PasswordChangeIn,
    RegisterIn,
    ResetIn,
)
from app.kitly.security import (
    create_token,
    current_user,
    forbid_impersonation,
    hash_secret,
    is_admin_email,
    verify_secret,
)
from app.kitly.serializers import lead_out, offer_owner, portfolio_owner, profile_owner, user_out
from app.kitly.queries import welcome_until
from app.kitly.utils import client_ip, iso, now, rate_clear, rate_guard, rate_hit

router = APIRouter(tags=["kitly · auth"])

# Limites anti-force brute
LOGIN_MAX_FAILS_PER_EMAIL = 5
LOGIN_MAX_FAILS_PER_IP = 20
LOGIN_WINDOW = 15 * 60
REGISTER_MAX_PER_IP = 10
REGISTER_WINDOW = 60 * 60

# Hash factice : vérifier un mot de passe même quand le compte n'existe pas
# garde un temps de réponse constant (pas d'énumération des adresses).
_DUMMY_HASH = hash_secret("kitly-dummy-password-0")


def _norm(email: str) -> str:
    return email.strip().lower()


def _auth_payload(db: Session, user: KitlyUser) -> dict:
    return {"token": create_token(user), "user": user_out(user), "has_profile": user.profile is not None}


@router.post("/auth/register", status_code=201)
def register(payload: RegisterIn, request: Request, db: Session = Depends(get_db)):
    ip_key = f"register:ip:{client_ip(request)}"
    rate_guard(db, ip_key, REGISTER_MAX_PER_IP, REGISTER_WINDOW, "Trop d'inscriptions depuis cette connexion, réessaie plus tard.")
    rate_hit(db, ip_key)

    email = _norm(payload.email)
    if db.execute(select(KitlyUser.id).where(KitlyUser.email == email)).first():
        raise HTTPException(409, detail="Un compte Kitly existe déjà avec cette adresse. Connecte-toi.")

    user = KitlyUser(
        email=email,
        password_hash=hash_secret(payload.password),
        first_name=payload.first_name,
        last_name=payload.last_name,
        is_admin=email in KITLY_ADMIN_EMAILS,
        cgu_accepted_at=now(),
        cgu_version=KITLY_CGU_VERSION,
        privacy_ack_at=now(),
        privacy_version=KITLY_PRIVACY_VERSION,
        marketing_opt_in=payload.marketing_opt_in,
        last_login_at=now(),
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    # Le compte est utilisable tout de suite (parcours progressif) ; seule la
    # publication de la vitrine exige une adresse vérifiée.
    sent = codes.send_code(db, email, "verify", user.first_name, enforce_cooldown=False)
    return {**_auth_payload(db, user), "verification_email_sent": sent}


@router.post("/auth/login")
def login(payload: LoginIn, request: Request, db: Session = Depends(get_db)):
    email = _norm(payload.email)
    email_key = f"login:email:{email}"
    ip_key = f"login:ip:{client_ip(request)}"
    msg = "Trop de tentatives. Réessaie dans 15 minutes ou réinitialise ton mot de passe."
    rate_guard(db, email_key, LOGIN_MAX_FAILS_PER_EMAIL, LOGIN_WINDOW, msg)
    rate_guard(db, ip_key, LOGIN_MAX_FAILS_PER_IP, LOGIN_WINDOW, msg)

    user = db.execute(select(KitlyUser).where(KitlyUser.email == email)).scalar_one_or_none()
    ok = verify_secret(payload.password, user.password_hash if user else _DUMMY_HASH)
    if not user or not ok or not user.is_active:
        rate_hit(db, email_key)
        rate_hit(db, ip_key)
        raise HTTPException(401, detail="Email ou mot de passe incorrect.")

    rate_clear(db, email_key)
    user.last_login_at = now()
    if email in KITLY_ADMIN_EMAILS and not user.is_admin:
        user.is_admin = True
    db.commit()
    return _auth_payload(db, user)


def _mask(email: str) -> str:
    name, _, domain = email.partition("@")
    return (name[:2] + "***@" + domain) if domain else "***"


@router.post("/auth/admin-login")
def admin_login(payload: LoginIn, request: Request, db: Session = Depends(get_db)):
    """
    Connexion à la console /admin, toujours par mot de passe. Seules les adresses
    de KITLY_ADMIN_EMAILS peuvent l'ouvrir ; pour toute autre, la réponse est la
    même qu'un mauvais mot de passe (rien n'indique qu'une console existe).
    Rend un jeton « admin » de 8 h, seul accepté par /kitly/admin/*.
    """
    email = _norm(payload.email)
    email_key = f"admin-login:email:{email}"
    ip_key = f"admin-login:ip:{client_ip(request)}"
    msg = "Trop de tentatives. Réessaie dans 15 minutes."
    rate_guard(db, email_key, 5, LOGIN_WINDOW, msg)
    rate_guard(db, ip_key, 10, LOGIN_WINDOW, msg)

    user = db.execute(select(KitlyUser).where(KitlyUser.email == email)).scalar_one_or_none()
    ok = verify_secret(payload.password, user.password_hash if user else _DUMMY_HASH)
    if not user or not ok or not user.is_active or not is_admin_email(email):
        # Même réponse pour tous les cas (rien n'indique qui est admin) ; la raison
        # exacte n'est écrite que dans les journaux du serveur (Render → Logs).
        reason = ("adresse absente de KITLY_ADMIN_EMAILS (%d adresse(s) configurée(s))" % len(KITLY_ADMIN_EMAILS)
                  if not is_admin_email(email) else
                  "aucun compte Kitly avec cette adresse (inscription sur /connexion)" if not user else
                  "compte désactivé" if not user.is_active else "mot de passe incorrect")
        print(f"[kitly] ⚠️  Connexion admin refusée pour {_mask(email)} : {reason}.", flush=True)
        rate_hit(db, email_key)
        rate_hit(db, ip_key)
        raise HTTPException(401, detail="Email ou mot de passe incorrect.")

    rate_clear(db, email_key)
    user.last_login_at = now()
    user.is_admin = True
    db.add(KitlyAdminAction(admin_id=user.id, admin_email=user.email, action="admin_login", target_type="system",
                            target_id="", details={"ip": client_ip(request)}))
    db.commit()
    return {"token": create_token(user, admin_session=True), "user": user_out(user),
            "expires_in_minutes": KITLY_ADMIN_SESSION_MINUTES}


@router.post("/auth/logout")
def logout(user: KitlyUser = Depends(current_user)):
    """Le jeton est stocké côté navigateur : le front le supprime. Rien à révoquer ici."""
    return {"ok": True}


@router.post("/auth/logout-all")
def logout_all(user: KitlyUser = Depends(current_user), db: Session = Depends(get_db)):
    """Révoque toutes les sessions (tous appareils) et rend un nouveau jeton pour celle-ci."""
    forbid_impersonation(user)
    user.token_version += 1
    db.commit()
    return {"ok": True, "token": create_token(user)}


@router.post("/auth/verify-email")
def verify_email(payload: CodeIn, user: KitlyUser = Depends(current_user), db: Session = Depends(get_db)):
    if user.email_verified_at:
        return {"ok": True, "user": user_out(user)}
    codes.consume_code(db, user.email, "verify", payload.code)
    user.email_verified_at = now()
    db.commit()
    return {"ok": True, "user": user_out(user)}


@router.post("/auth/resend-code")
def resend_code(user: KitlyUser = Depends(current_user), db: Session = Depends(get_db)):
    if user.email_verified_at:
        return {"ok": True, "already_verified": True}
    sent = codes.send_code(db, user.email, "verify", user.first_name)
    return {"ok": True, "sent": sent}


@router.post("/auth/password/forgot")
def password_forgot(payload: ForgotIn, request: Request, db: Session = Depends(get_db)):
    """Répond toujours pareil, que le compte existe ou non (pas d'énumération)."""
    ip_key = f"forgot:ip:{client_ip(request)}"
    rate_guard(db, ip_key, 10, 3600, "Trop de demandes, réessaie plus tard.")
    rate_hit(db, ip_key)
    email = _norm(payload.email)
    user = db.execute(select(KitlyUser).where(KitlyUser.email == email)).scalar_one_or_none()
    if user and user.is_active:
        try:
            codes.send_code(db, email, "reset", user.first_name)
        except HTTPException as exc:
            if exc.status_code != 429:
                raise
    return {"ok": True, "message": "Si un compte existe pour cette adresse, un code vient d'être envoyé."}


@router.post("/auth/password/reset")
def password_reset(payload: ResetIn, db: Session = Depends(get_db)):
    email = _norm(payload.email)
    user = db.execute(select(KitlyUser).where(KitlyUser.email == email)).scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(400, detail="Code invalide ou expiré.")
    activation = user.cgu_accepted_at is None  # compte créé par l'équipe : jamais accepté les CGU
    if activation and not (payload.accept_cgu and payload.ack_privacy):
        raise HTTPException(422, detail="Pour activer ton compte, accepte les CGU et prends connaissance de la "
                                        "politique de confidentialité.")
    codes.consume_code(db, email, "reset", payload.code)
    if activation:
        user.cgu_accepted_at, user.cgu_version = now(), KITLY_CGU_VERSION
        user.privacy_ack_at, user.privacy_version = now(), KITLY_PRIVACY_VERSION
    user.password_hash = hash_secret(payload.new_password)
    user.token_version += 1
    # Recevoir le code prouve aussi la possession de l'adresse.
    user.email_verified_at = user.email_verified_at or now()
    db.commit()
    rate_clear(db, f"login:email:{email}")
    subject, html = emails.mail_password_changed(user.first_name)
    emails.send(user.email, subject, html)
    return _auth_payload(db, user)


@router.get("/auth/me")
def me(user: KitlyUser = Depends(current_user), db: Session = Depends(get_db)):
    """Compte, formule, limites et progression de l'onboarding."""
    profile = user.profile
    plan = get_plan(db, user.plan_code)
    counts = {"offers": 0, "published_offers": 0, "portfolio": 0}
    has_terms = False
    if profile:
        counts["offers"] = db.execute(
            select(func.count()).select_from(KitlyOffer).where(KitlyOffer.profile_id == profile.id)
        ).scalar_one()
        counts["published_offers"] = db.execute(
            select(func.count()).select_from(KitlyOffer).where(
                KitlyOffer.profile_id == profile.id, KitlyOffer.status == "published"
            )
        ).scalar_one()
        counts["portfolio"] = db.execute(
            select(func.count()).select_from(KitlyPortfolioProject).where(KitlyPortfolioProject.profile_id == profile.id)
        ).scalar_one()
        has_terms = bool(profile.terms and profile.terms.current_version_id)

    onboarding = [
        {"key": "account", "label": "Créer ton compte", "done": True},
        {"key": "verify_email", "label": "Confirmer ton adresse email", "done": user.email_verified_at is not None},
        {"key": "profile", "label": "Créer ta vitrine", "done": profile is not None},
        {"key": "identity", "label": "Présenter ton activité",
         "done": bool(profile and profile.display_name and profile.headline and profile.bio)},
        {"key": "first_offer", "label": "Ajouter ta première offre", "done": counts["offers"] > 0},
        {"key": "terms", "label": "Préparer tes CGV", "done": has_terms},
        {"key": "publish", "label": "Publier ta vitrine", "done": bool(profile and profile.is_published)},
    ]
    return {
        "user": user_out(user),
        "plan": plan_out(plan),
        "profile": profile_owner(profile) if profile else None,
        "counts": counts,
        "onboarding": onboarding,
        "onboarding_progress": round(100 * sum(s["done"] for s in onboarding) / len(onboarding)),
        # Session ouverte par un admin à la place du compte (support) : bandeau dans le builder.
        "impersonated_by": getattr(user, "_kitly_impersonator", None),
        # Offre de bienvenue : référencement marketplace offert pendant le premier mois.
        "marketplace_welcome_until": iso(welcome_until(user)),
    }


# ============================================================
# Compte
# ============================================================

@router.patch("/account")
def update_account(payload: AccountUpdateIn, user: KitlyUser = Depends(current_user), db: Session = Depends(get_db)):
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(user, field, value)
    db.commit()
    return {"user": user_out(user)}


@router.post("/account/password")
def change_password(payload: PasswordChangeIn, user: KitlyUser = Depends(current_user), db: Session = Depends(get_db)):
    forbid_impersonation(user)
    if not verify_secret(payload.current_password, user.password_hash):
        raise HTTPException(400, detail="Mot de passe actuel incorrect.")
    user.password_hash = hash_secret(payload.new_password)
    user.token_version += 1  # déconnecte les autres appareils
    db.commit()
    subject, html = emails.mail_password_changed(user.first_name)
    emails.send(user.email, subject, html)
    return {"ok": True, "token": create_token(user)}


@router.get("/account/export")
def export_account(user: KitlyUser = Depends(current_user), db: Session = Depends(get_db)):
    """Portabilité RGPD : toutes les données du compte en JSON."""
    profile = user.profile
    data: dict = {"account": user_out(user), "profile": None, "offers": [], "portfolio": [], "leads": [], "legal": None}
    data["account"]["consents"] = {
        "cgu_version": user.cgu_version, "cgu_accepted_at": str(user.cgu_accepted_at),
        "privacy_version": user.privacy_version, "privacy_ack_at": str(user.privacy_ack_at),
    }
    if profile:
        data["profile"] = profile_owner(profile)
        data["offers"] = [offer_owner(o) for o in profile.offers]
        data["portfolio"] = [portfolio_owner(p) for p in profile.portfolio]
        leads = db.execute(select(KitlyLead).where(KitlyLead.profile_id == profile.id)).scalars()
        data["leads"] = [lead_out(lead) for lead in leads]
        data["legal"] = _legal_export(db, profile)
    return data


def _legal_export(db: Session, profile: KitlyProfile) -> dict:
    """Identité juridique, réponses au questionnaire et toutes les versions de CGV."""
    from app.kitly.models import KitlyLegalAnswers, KitlySellerLegalProfile
    from app.kitly.serializers import terms_version_out

    lp = profile.legal_profile
    cols = [c.key for c in KitlySellerLegalProfile.__table__.columns if c.key not in ("id", "profile_id")]
    answers = db.execute(select(KitlyLegalAnswers).where(KitlyLegalAnswers.profile_id == profile.id)).scalars()
    return {
        "legal_profile": {k: (str(getattr(lp, k)) if k == "updated_at" else getattr(lp, k)) for k in cols} if lp else None,
        "questionnaire_answers": {a.scope: {"answers": a.answers, "version": a.questionnaire_version} for a in answers},
        "terms_versions": [terms_version_out(v) for v in (profile.terms.versions if profile.terms else [])],
    }


@router.delete("/account")
def delete_account(payload: AccountDeleteIn, user: KitlyUser = Depends(current_user), db: Session = Depends(get_db)):
    """
    Suppression définitive : l'abonnement Stripe est résilié immédiatement,
    puis le compte, la vitrine, les offres et les demandes sont effacés.
    """
    forbid_impersonation(user)
    if not verify_secret(payload.password, user.password_hash):
        raise HTTPException(400, detail="Mot de passe incorrect.")

    active = db.execute(
        select(KitlySubscription).where(
            KitlySubscription.user_id == user.id,
            KitlySubscription.status.in_(("active", "trialing", "past_due", "unpaid", "incomplete")),
        )
    ).scalars().all()
    if active:
        from app.kitly.stripe_client import stripe_client

        client = stripe_client()
        for sub in active:
            try:
                client.subscriptions.cancel(sub.stripe_subscription_id)
            except Exception as exc:
                raise HTTPException(502, detail=f"Résiliation de l'abonnement impossible, réessaie : {exc}")

    # Les commandes (pièces comptables) ne sont pas effacées : la vitrine est
    # détachée par ON DELETE SET NULL et l'instantané de l'offre est conservé.
    db.delete(user)
    db.commit()
    return {"ok": True}

