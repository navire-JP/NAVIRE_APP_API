from pydantic import BaseModel, Field
from typing import Optional, List, Dict

# -------------------
# Decks
# -------------------
class DeckCreateIn(BaseModel):
    title: str = Field(min_length=2, max_length=120)
    description: str = Field(default="", max_length=2000)

class DeckUpdateIn(BaseModel):
    title: Optional[str] = Field(default=None, min_length=2, max_length=120)
    description: Optional[str] = Field(default=None, max_length=2000)

class DeckOut(BaseModel):
    id: int
    title: str
    description: str
    cards_count: int

# -------------------
# Cards
# -------------------
class CardCreateIn(BaseModel):
    front: str = Field(min_length=1, max_length=8000)
    back: str = Field(min_length=1, max_length=8000)
    tags: str = Field(default="", max_length=255)

class CardUpdateIn(BaseModel):
    front: Optional[str] = Field(default=None, max_length=8000)
    back: Optional[str] = Field(default=None, max_length=8000)
    tags: Optional[str] = Field(default=None, max_length=255)

class CardOut(BaseModel):
    id: int
    deck_id: int
    front: str
    back: str
    tags: str
    source_type: str
    source_file_id: Optional[int] = None
    source_pages: str

# -------------------
# Study
# -------------------
class StudyStartOut(BaseModel):
    session_id: str
    total: int
    index: int
    card: CardOut

class StudyGradeIn(BaseModel):
    # V1: on grade simple (true/false)
    is_correct: bool

class StudyNextOut(BaseModel):
    status: str  # "ready" | "done"
    total: int
    index: int
    card: Optional[CardOut] = None
    stats: Dict = {}

# -------------------
# Generate from PDF (abonné)
# -------------------
class GenerateFromPdfIn(BaseModel):
    file_id: int
    pages: str = ""  # "1-3,7"
    count: int = 15  # 10-30 conseillé