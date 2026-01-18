from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from database import get_db
from models import User
from auth import ProfileUpdateIn, UserOut
from security import get_current_user  # <-- adapte si ton fichier s'appelle autrement

router = APIRouter(prefix="/users", tags=["users"])


@router.post("/profile", response_model=UserOut)
def update_profile(
    payload: ProfileUpdateIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    current_user.university = payload.university
    current_user.study_level = payload.study_level

    db.commit()
    db.refresh(current_user)
    return current_user
