from typing import List
from pydantic import BaseModel, Field


class Flashcard(BaseModel):
    id: str
    front: str = Field(..., description="Question / recto")
    back: str = Field(..., description="Réponse / verso")
    tags: List[str] = Field(default_factory=list)


class FlashcardListResponse(BaseModel):
    items: List[Flashcard]
