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


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)

class ProfileUpdateIn(BaseModel):
    university: str = Field(min_length=1, max_length=120)
    study_level: str = Field(min_length=1, max_length=120)


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
