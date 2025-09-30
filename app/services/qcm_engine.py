import time
import uuid
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

from fastapi import HTTPException
from starlette.status import HTTP_404_NOT_FOUND, HTTP_400_BAD_REQUEST

from app.models.qcm import (
    Difficulty,
    Question,
    StartQcmRequest,
    StartQcmResponse,
    AnswerRequest,
    AnswerResponse,
    ResultItem,
    ResultResponse,
)
from app.core.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class _QuestionInternal:
    id: str
    stem: str
    choices: List[str]
    correct_index: int
    explanation: str


@dataclass
class _Session:
    id: str
    file_id: str
    difficulty: Difficulty
    total: int
    index: int
    score: int
    created_at: float
    questions: List[_QuestionInternal]
    answers: Dict[str, int]  # questionId -> chosenIndex


class QcmEngine:
    """
    Moteur QCM minimaliste en mémoire (V1).
    Génère 'total' questions depuis le PDF (ou fallback si extract indispo).
    """

    def __init__(self) -> None:
        self._sessions: Dict[str, _Session] = {}
        self._ttl_seconds: int = 60 * 60  # 1h

    # ---------- public API ----------

    def start(self, req: StartQcmRequest) -> StartQcmResponse:
        """
        Crée une session et retourne la 1ère question.
        """
        text_corpus = self._extract_corpus(file_id=req.fileId, pages=req.pages)
        questions = self._generate_questions(text_corpus, req.difficulty, req.total)

        if not questions:
            raise HTTPException(
                status_code=HTTP_400_BAD_REQUEST,
                detail="Impossible de générer des questions depuis ce fichier.",
            )

        sess_id = f"sess_{uuid.uuid4().hex[:12]}"
        sess = _Session(
            id=sess_id,
            file_id=req.fileId,
            difficulty=req.difficulty,
            total=len(questions),
            index=0,
            score=0,
            created_at=time.time(),
            questions=questions,
            answers={},
        )
        self._sessions[sess_id] = sess

        q = self._to_public_question(questions[0])
        return StartQcmResponse(
            sessionId=sess.id,
            total=sess.total,
            index=sess.index,
            question=q,
        )

    def answer(self, session_id: str, body: AnswerRequest) -> AnswerResponse:
        sess = self._get_session(session_id)
        self._ensure_not_finished(sess)

        current = sess.questions[sess.index]
        if body.questionId != current.id:
            raise HTTPException(
                status_code=HTTP_400_BAD_REQUEST,
                detail="questionId ne correspond pas à la question actuelle.",
            )
        if body.choiceIndex < 0 or body.choiceIndex >= len(current.choices):
            raise HTTPException(
                status_code=HTTP_400_BAD_REQUEST,
                detail="choiceIndex invalide.",
            )

        is_correct = (body.choiceIndex == current.correct_index)
        if is_correct:
            sess.score += 1
        sess.answers[current.id] = body.choiceIndex

        sess.index += 1  # avancer le curseur

        if sess.index < sess.total:
            next_q = self._to_public_question(sess.questions[sess.index])
            return AnswerResponse(
                isCorrect=is_correct,
                explanation=current.explanation,
                nextIndex=sess.index,
                nextQuestion=next_q,
            )
        else:
            # plus de questions
            return AnswerResponse(
                isCorrect=is_correct,
                explanation=current.explanation,
                nextIndex=sess.index,
                nextQuestion=None,
            )

    def result(self, session_id: str) -> ResultResponse:
        sess = self._get_session(session_id)
        details: List[ResultItem] = []
        for q in sess.questions:
            chosen = sess.answers.get(q.id, -1)
            details.append(
                ResultItem(
                    questionId=q.id,
                    correctIndex=q.correct_index,
                    chosenIndex=chosen,
                )
            )
        return ResultResponse(score=sess.score, total=sess.total, details=details)

    # ---------- internals ----------

    def _get_session(self, session_id: str) -> _Session:
        sess = self._sessions.get(session_id)
        if not sess:
            raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Session introuvable.")
        # TTL
        if time.time() - sess.created_at > self._ttl_seconds:
            try:
                del self._sessions[session_id]
            except KeyError:
                pass
            raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Session expirée.")
        return sess

    def _ensure_not_finished(self, sess: _Session) -> None:
        if sess.index >= sess.total:
            raise HTTPException(
                status_code=HTTP_400_BAD_REQUEST,
                detail="La session est déjà terminée.",
            )

    def _to_public_question(self, q: _QuestionInternal) -> Question:
        return Question(
            id=q.id,
            stem=q.stem,
            choices=q.choices,
            explanation=None,  # renvoyée uniquement après réponse
        )

    def _extract_corpus(self, file_id: str, pages: Optional[str]) -> str:
        """
        Essaie d'extraire le texte du PDF via utils.pdf_extract.
        Fallback sur un corpus synthétique si indispo (pour ne pas bloquer la V1).
        """
        try:
            from app.utils.pdf_extract import extract_text
            text = extract_text(file_id=file_id, pages=pages)
            if text and text.strip():
                return text
        except Exception as e:
            logger.warning("extract_text failed: %s", e)

        # Fallback : petit corpus bidon pour générer des questions génériques
        return (
            "Droit des obligations : principe de la force obligatoire du contrat. "
            "Procédure civile : principe du contradictoire. "
            "Droit des sociétés : responsabilité des dirigeants. "
            "Droit de la concurrence : abus de position dominante."
        )

    def _generate_questions(self, corpus: str, difficulty: Difficulty, total: int) -> List[_QuestionInternal]:
        """
        Essaie d'utiliser utils.qcm_gen, sinon fallback sur un générateur simple.
        """
        try:
            from app.utils.qcm_gen import generate_questions  # -> List[dict]
            raw_items = generate_questions(corpus=corpus, difficulty=difficulty, total=total)
            questions: List[_QuestionInternal] = []
            for it in raw_items:
                questions.append(
                    _QuestionInternal(
                        id=it["id"],
                        stem=it["stem"],
                        choices=it["choices"],
                        correct_index=it["correctIndex"],
                        explanation=it.get("explanation", "Voir le support pour l'explication."),
                    )
                )
            if questions:
                return questions
        except Exception as e:
            logger.warning("qcm_gen.generate_questions failed: %s", e)

        # --- Fallback très simple : distracteurs + bonne réponse en index 1 ---
        base_pairs = [
            ("Quel est le principe du contradictoire ?", "Chaque partie doit pouvoir faire valoir ses arguments."),
            ("La force obligatoire du contrat signifie ?", "Les contrats tiennent lieu de loi entre les parties."),
            ("Quel est l'effet d'un abus de position dominante ?", "Sanctions et mesures correctrices possibles."),
            ("Responsabilité des dirigeants ?", "Engagée en cas de faute de gestion."),
            ("But de la procédure civile ?", "Garantir un procès équitable."),
        ]

        items = (base_pairs * ((total // len(base_pairs)) + 1))[:total]
        out: List[_QuestionInternal] = []
        for i, (stem, correct) in enumerate(items, start=1):
            choices = [
                "Réponse A générique",
                correct,
                "Réponse C générique",
                "Réponse D générique",
            ]
            out.append(
                _QuestionInternal(
                    id=f"q{i}",
                    stem=stem,
                    choices=choices,
                    correct_index=1,
                    explanation="Référence : principes généraux — réponse attendue explicitée.",
                )
            )
        return out
