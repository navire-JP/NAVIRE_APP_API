from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import User, QcmSession, QcmQuestion
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

    session_ids = [
        s.id for s in db.query(QcmSession.id).filter(
            QcmSession.user_id == user.id,
            QcmSession.status == "done",
        ).all()
    ]

    total_sessions = len(session_ids)

    if session_ids:
        questions = db.query(QcmQuestion).filter(
            QcmQuestion.session_id.in_(session_ids),
            QcmQuestion.answered == True,
        ).all()
        total_answered = len(questions)
        total_correct = sum(1 for q in questions if q.user_letter == q.correct_letter)
        success_rate = round((total_correct / total_answered * 100) if total_answered > 0 else 0, 1)
    else:
        success_rate = 0.0

    return {
        "username": user.username,
        "university": user.university or "Non renseignée",
        "elo": user.elo or 0,
        "total_sessions": total_sessions,
        "success_rate": success_rate,
    }