from pydantic import BaseModel, EmailStr, Field
import re

PWD_REGEX = re.compile(r"^(?=.*[A-Z])(?=.*(\d|[^\w\s])).{8,128}$")

class RegisterIn(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    newsletter_opt_in: bool = False
    university: str | None = Field(default=None, max_length=120)
    study_level: str | None = Field(default=None, max_length=120)

class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)

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

def validate_password(pw: str) -> None:
    # min 8 + 1 majuscule + (1 chiffre OU 1 symbole)
    if not PWD_REGEX.match(pw):
        raise ValueError("Mot de passe: min 8, 1 majuscule, et 1 chiffre ou symbole.")
