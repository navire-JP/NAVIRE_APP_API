from enum import Enum
from typing import Optional, List
from pydantic import BaseModel, Field


class Difficulty(str, Enum):
    easy = "easy"
    medium = "medium"
    hard = "hard"


class Question(BaseModel):
    id: str
    stem: str = Field(..., description="Énoncé de la question")
    choices: List[str] = Field(..., min_items=2, description="Liste des propositions")
    # On ne renvoie pas correctIndex côté client tant que non répondu
    explanation: Optional[str] = Field(None, description="Explication (retournée après réponse)")


class StartQcmRequest(BaseModel):
    fileId: str = Field(..., description="ID du fichier")
    difficulty: Difficulty = Field(default=Difficulty.medium)
    pages: Optional[str] = Field(
        None,
        description="Plage(s) de pages ex: '12-24,30'. Vide = tout le doc."
    )
    total: int = Field(default=5, ge=1, le=50, description="Nombre de questions")


class StartQcmResponse(BaseModel):
    sessionId: str
    total: int
    index: int
    question: Question


class AnswerRequest(BaseModel):
    questionId: str
    choiceIndex: int = Field(..., ge=0)


class AnswerResponse(BaseModel):
    isCorrect: bool
    explanation: str
    nextIndex: int
    nextQuestion: Optional[Question] = None


class ResultItem(BaseModel):
    questionId: str
    correctIndex: int
    chosenIndex: int


class ResultResponse(BaseModel):
    score: int
    total: int
    details: list[ResultItem]
