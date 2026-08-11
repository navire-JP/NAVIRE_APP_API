from pydantic import BaseModel, EmailStr, Field
import re

# Password policy:
# - 8 à 72 caractères (bcrypt limit)
# - au moins 1 majuscule
# - au moins 1 chiffre OU 1 symbole
PWD_REGEX = re.compile(r"^(?=.*[A-Z])(?=.*(\d|[^\w\s])).{8,72}$")


# =========================
# INPUT SCHEMAS
# =========================

class RegisterIn(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)

    newsletter_opt_in: bool = False
    university: str | None = Field(default=None, max_length=120)
    study_level: str | None = Field(default=None, max_length=120)

    # Jeton rendu par /auth/verify-code : il prouve que l'adresse a bien reçu
    # son code, donc qu'elle appartient à la personne qui s'inscrit. Optionnel
    # pour ne pas casser les formulaires existants ; rendu obligatoire par la
    # variable REQUIRE_EMAIL_VERIFICATION.
    email_token: str | None = None


class EmailCodeRequestIn(BaseModel):
    """Demande d'envoi du code à 6 chiffres."""
    email: EmailStr
    username: str | None = Field(default=None, max_length=64)


class EmailCodeVerifyIn(BaseModel):
    """Vérification du code reçu par email."""
    email: EmailStr
    code: str = Field(min_length=4, max_length=10)


class OAuthIn(BaseModel):
    """
    Connexion ou inscription via un fournisseur.
    access_token est le jeton rendu par le SDK Google ou Facebook dans le
    navigateur ; il est revérifié côté serveur avant toute création de compte.
    """
    provider: str = Field(pattern="^(google|facebook)$")

    # Deux chemins possibles :
    #  - ticket : rendu par /auth/{provider}/callback, profil déjà vérifié ;
    #  - access_token : jeton du SDK, revérifié auprès du fournisseur.
    ticket: str | None = Field(default=None, max_length=4096)
    access_token: str | None = Field(default=None, max_length=4096)

    university: str | None = Field(default=None, max_length=120)
    study_level: str | None = Field(default=None, max_length=120)
    newsletter_opt_in: bool = False


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)


class ProfileUpdateIn(BaseModel):
    
    username: str | None = Field(default=None, min_length=1, max_length=64)
    university: str | None = Field(default=None, min_length=1, max_length=120)
    study_level: str | None = Field(default=None, min_length=1, max_length=120)


# =========================
# OUTPUT SCHEMAS
# =========================

class UserOut(BaseModel):
    id: int
    email: EmailStr
    username: str

    newsletter_opt_in: bool
    university: str | None
    study_level: str | None
    avatar_url: str | None = None

    email_verified: bool = False

    score: int
    grade: str

    class Config:
        from_attributes = True


class AuthOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


# =========================
# VALIDATION HELPERS
# =========================

def validate_password(pw: str) -> None:
    """
    Valide la politique de mot de passe.
    Lève ValueError si invalide.
    """
    if not PWD_REGEX.match(pw):
        raise ValueError(
            "Mot de passe invalide : "
            "8 à 72 caractères, au moins 1 majuscule, "
            "et au moins 1 chiffre ou 1 symbole."
        )