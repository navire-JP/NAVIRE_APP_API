# app/routers/cab.py
"""
Router NavireCab — Simulation cabinet d'avocat

Endpoints :
- POST /cab/start     → Créer une session et générer le dossier
- GET  /cab/{sid}     → Récupérer la session et le dossier
- POST /cab/{sid}/answer → Soumettre une réponse
- POST /cab/{sid}/finish → Terminer et obtenir la note /20
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import User, CabSession, CabResult
from app.routers.auth import get_current_user
from app.services.cab_service import (
    generate_dossier,
    calculate_phase_score,
    calculate_final_score,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cab", tags=["NavireCab"])


# ============================================================
# SCHEMAS
# ============================================================

class CabStartRequest(BaseModel):
    difficulty: str = Field(default="medium", pattern="^(easy|medium|hard)$")
    support_type: int = Field(default=2, ge=1, le=2)
    theme: Optional[str] = None


class CabStartResponse(BaseModel):
    session_id: str
    status: str
    support_type: int
    message: str


class CabDossierMail(BaseModel):
    subject: str
    from_: str = Field(alias="from")
    body: str

    class Config:
        populate_by_name = True


class CabPhasePublic(BaseModel):
    """Phase sans la réponse correcte (pour le front)."""
    question: str
    choices: list[str]


class CabDossierPublic(BaseModel):
    """Dossier sans les réponses (envoyé au front)."""
    mail: CabDossierMail
    attachment: str
    phases: list[CabPhasePublic]
    meta: dict = {}


class CabSessionResponse(BaseModel):
    session_id: str
    status: str
    difficulty: str
    support_type: int
    current_phase: int
    total_phases: int
    dossier: Optional[CabDossierPublic] = None
    answers: list = []


class CabAnswerRequest(BaseModel):
    phase_index: int = Field(ge=0, le=4)
    choice: int = Field(ge=0, le=3)
    ref_given: Optional[str] = None


class CabAnswerResponse(BaseModel):
    phase_index: int
    correct: bool
    points: int
    ref_bonus: bool
    debrief: str
    expected_refs: list[str]
    current_phase: int
    is_finished: bool


class CabFinishResponse(BaseModel):
    session_id: str
    score_20: float
    mention: str
    raw_score: int
    max_possible: int
    correct_count: int
    ref_bonus_count: int
    answers_detail: list
    duration_seconds: Optional[int] = None


# ============================================================
# HELPERS
# ============================================================

def _get_user_session(
    db: Session,
    session_id: str,
    user: User
) -> CabSession:
    """Récupère une session appartenant à l'utilisateur."""
    session = db.query(CabSession).filter(
        CabSession.id == session_id,
        CabSession.user_id == user.id
    ).first()

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session non trouvée"
        )
    return session


def _mask_dossier(dossier: dict) -> dict:
    """Masque les réponses correctes du dossier."""
    safe_phases = []
    for phase in dossier.get("phases", []):
        safe_phases.append({
            "question": phase["question"],
            "choices": phase["choices"],
        })

    return {
        "mail": dossier.get("mail"),
        "attachment": dossier.get("attachment"),
        "phases": safe_phases,
        "meta": dossier.get("meta", {})
    }


# ============================================================
# ENDPOINTS
# ============================================================

@router.post("/start", response_model=CabStartResponse)
def start_session(
    request: CabStartRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Crée une nouvelle session NavireCab et génère le dossier.

    - Support 1 : IA (peut prendre 10-30s)
    - Support 2 : Template DB (instantané)

    Fallback automatique Support 1 → Support 2 si IA échoue.
    """
    try:
        # Génération du dossier
        dossier, actual_support = generate_dossier(
            db=db,
            support_type=request.support_type,
            difficulty=request.difficulty,
            theme=request.theme,
        )

        # Créer la session
        session = CabSession(
            user_id=current_user.id,
            difficulty=request.difficulty,
            support_type=actual_support,
            dossier_json=dossier,
            status="ready",
            answers_json=[],
        )
        db.add(session)
        db.commit()
        db.refresh(session)

        logger.info(
            f"[CAB] Session created: sid={session.id}, user={current_user.id}, "
            f"support={actual_support}, difficulty={request.difficulty}"
        )

        fallback_msg = ""
        if request.support_type == 1 and actual_support == 2:
            fallback_msg = " (fallback IA → template)"

        return CabStartResponse(
            session_id=session.id,
            status="ready",
            support_type=actual_support,
            message=f"Dossier prêt{fallback_msg}"
        )

    except Exception as e:
        logger.error(f"[CAB] start_session failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur création session: {str(e)}"
        )


@router.get("/{session_id}", response_model=CabSessionResponse)
def get_session(
    session_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Récupère une session avec le dossier (sans les réponses correctes).
    """
    session = _get_user_session(db, session_id, current_user)

    # Marquer comme "running" si c'est le premier accès
    if session.status == "ready":
        session.status = "running"
        session.started_at = datetime.now(timezone.utc)
        db.commit()

    dossier = session.dossier_json
    if not dossier:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Dossier non généré"
        )

    safe_dossier = _mask_dossier(dossier)

    return CabSessionResponse(
        session_id=session.id,
        status=session.status,
        difficulty=session.difficulty,
        support_type=session.support_type,
        current_phase=session.current_phase,
        total_phases=len(dossier.get("phases", [])),
        dossier=safe_dossier,
        answers=session.answers_json or []
    )


@router.post("/{session_id}/answer", response_model=CabAnswerResponse)
def submit_answer(
    session_id: str,
    request: CabAnswerRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Soumet la réponse à une phase.

    Retourne immédiatement :
    - Si correct ou non
    - Points obtenus
    - Debrief explicatif
    - Références attendues
    """
    session = _get_user_session(db, session_id, current_user)

    if session.status not in ("ready", "running"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Session non modifiable (status={session.status})"
        )

    # Vérifier qu'on répond à la bonne phase
    if request.phase_index != session.current_phase:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Phase attendue: {session.current_phase}, reçue: {request.phase_index}"
        )

    dossier = session.dossier_json
    phases = dossier.get("phases", [])

    if request.phase_index >= len(phases):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Phase invalide"
        )

    phase = phases[request.phase_index]

    # Calcul du score
    result = calculate_phase_score(phase, request.choice, request.ref_given)

    # Enregistrer la réponse
    answer_record = {
        "phase": request.phase_index,
        "choice": request.choice,
        "ref_given": request.ref_given,
        **result
    }

    answers = session.answers_json or []
    answers.append(answer_record)
    session.answers_json = answers
    session.current_phase = request.phase_index + 1

    # Marquer running si pas encore fait
    if session.status == "ready":
        session.status = "running"
        session.started_at = datetime.now(timezone.utc)

    db.commit()

    is_finished = session.current_phase >= len(phases)

    logger.info(
        f"[CAB] Answer: sid={session_id}, phase={request.phase_index}, "
        f"correct={result['correct']}, points={result['points']}"
    )

    return CabAnswerResponse(
        phase_index=request.phase_index,
        correct=result["correct"],
        points=result["points"],
        ref_bonus=result["ref_bonus"],
        debrief=phase.get("debrief", ""),
        expected_refs=phase.get("refs", []),
        current_phase=session.current_phase,
        is_finished=is_finished
    )


@router.post("/{session_id}/finish", response_model=CabFinishResponse)
def finish_session(
    session_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Termine la session et calcule la note finale /20.

    Enregistre le résultat dans cab_results pour l'historique.
    """
    session = _get_user_session(db, session_id, current_user)

    if session.status == "finished":
        # Retourner le résultat existant
        result = db.query(CabResult).filter(
            CabResult.session_id == session_id
        ).first()

        if result:
            return CabFinishResponse(
                session_id=session_id,
                score_20=result.score_20,
                mention=result.mention,
                raw_score=result.score_raw,
                max_possible=25,
                correct_count=result.correct_count,
                ref_bonus_count=result.ref_bonus_count,
                answers_detail=result.answers_json or [],
                duration_seconds=result.duration_seconds
            )

    answers = session.answers_json or []
    dossier = session.dossier_json or {}
    num_phases = len(dossier.get("phases", []))

    # Calcul final
    final = calculate_final_score(answers, num_phases)

    # Durée
    duration = None
    if session.started_at:
        delta = datetime.now(timezone.utc) - session.started_at
        duration = int(delta.total_seconds())

    # Créer CabResult
    meta = dossier.get("meta", {})
    cab_result = CabResult(
        user_id=current_user.id,
        session_id=session_id,
        difficulty=session.difficulty,
        support_type=session.support_type,
        theme=meta.get("theme", ""),
        template_code=meta.get("template_code"),
        score_raw=final["raw_score"],
        score_20=final["score_20"],
        mention=final["mention"],
        correct_count=final["correct_count"],
        ref_bonus_count=final["ref_bonus_count"],
        answers_json=answers,
        duration_seconds=duration,
    )
    db.add(cab_result)

    # Mettre à jour la session
    session.status = "finished"
    session.finished_at = datetime.now(timezone.utc)

    db.commit()

    logger.info(
        f"[CAB] Session finished: sid={session_id}, score={final['score_20']}/20, "
        f"mention={final['mention']}"
    )

    return CabFinishResponse(
        session_id=session_id,
        score_20=final["score_20"],
        mention=final["mention"],
        raw_score=final["raw_score"],
        max_possible=final["max_possible"],
        correct_count=final["correct_count"],
        ref_bonus_count=final["ref_bonus_count"],
        answers_detail=answers,
        duration_seconds=duration
    )


# ============================================================
# ADMIN / DEBUG
# ============================================================

@router.get("/admin/templates")
def list_templates(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Liste les templates DB (admin only)."""
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")

    from app.db.models import CabDossierTemplate

    templates = db.query(CabDossierTemplate).all()
    return [
        {
            "id": t.id,
            "code": t.code,
            "title": t.title,
            "theme": t.theme,
            "difficulty": t.difficulty,
            "is_active": t.is_active,
            "times_used": t.times_used,
            "avg_score": t.avg_score,
        }
        for t in templates
    ]


@router.get("/history")
def get_history(
    limit: int = 10,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Historique des sessions terminées de l'utilisateur."""
    results = db.query(CabResult).filter(
        CabResult.user_id == current_user.id
    ).order_by(CabResult.created_at.desc()).limit(limit).all()

    return [
        {
            "id": r.id,
            "session_id": r.session_id,
            "score_20": r.score_20,
            "mention": r.mention,
            "difficulty": r.difficulty,
            "theme": r.theme,
            "correct_count": r.correct_count,
            "duration_seconds": r.duration_seconds,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in results
    ]