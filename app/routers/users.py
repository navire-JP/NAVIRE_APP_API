from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import User, QCMSession
from app.schemas.auth import ProfileUpdateIn, UserOut
from app.routers.auth import get_current_user


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


@router.get("/{username}/public")
def get_user_public(username: str, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == username).first()
    if not user:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable")

    sessions = db.query(QCMSession).filter(
        QCMSession.user_id == user.id,
        QCMSession.status == "completed"
    ).all()

    total_sessions = len(sessions)
    total_questions = sum(s.total_questions or 0 for s in sessions)
    total_correct = sum(s.correct_answers or 0 for s in sessions)
    success_rate = round(
        (total_correct / total_questions * 100) if total_questions > 0 else 0, 1
    )

    return {
        "username": user.username,
        "university": user.university or "Non renseignée",
        "elo": user.elo or 0,
        "total_sessions": total_sessions,
        "success_rate": success_rate,
    }