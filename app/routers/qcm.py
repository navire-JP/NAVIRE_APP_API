from fastapi import APIRouter, Depends
from app.models.qcm import (
    StartQcmRequest, StartQcmResponse,
    AnswerRequest, AnswerResponse,
    ResultResponse,
)
from app.services.qcm_engine import QcmEngine

router = APIRouter(prefix="/v1/qcm", tags=["qcm"])

# Singleton simple en module-scope (V1)
_engine = QcmEngine()

@router.post("/start", response_model=StartQcmResponse)
def start_qcm(body: StartQcmRequest):
    return _engine.start(body)

@router.post("/{session_id}/answer", response_model=AnswerResponse)
def answer_qcm(session_id: str, body: AnswerRequest):
    return _engine.answer(session_id, body)

@router.get("/{session_id}/result", response_model=ResultResponse)
def result_qcm(session_id: str):
    return _engine.result(session_id)
