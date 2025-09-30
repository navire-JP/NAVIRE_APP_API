from typing import List, Optional
from fastapi import APIRouter, Query
from app.models.flashcards import Flashcard, FlashcardListResponse

router = APIRouter(prefix="/v1/flashcards", tags=["flashcards"])

# V1: petit dataset de démo (remplaçable par une source JSON/DB)
_DEMO_FLASHCARDS: List[Flashcard] = [
    Flashcard(id="fc1", front="Principe du contradictoire ?", back="Chaque partie doit pouvoir présenter ses arguments.", tags=["procédure"]),
    Flashcard(id="fc2", front="Force obligatoire du contrat ?", back="Les contrats tiennent lieu de loi entre les parties.", tags=["obligations"]),
    Flashcard(id="fc3", front="Abus de position dominante ?", back="Sanctions + mesures correctrices.", tags=["concurrence"]),
    Flashcard(id="fc4", front="Responsabilité des dirigeants ?", back="Engagée en cas de faute de gestion.", tags=["sociétés"]),
]

@router.get("", response_model=FlashcardListResponse)
def list_flashcards(tags: Optional[str] = Query(default=None, description="Liste séparée par des virgules")):
    if not tags:
        return FlashcardListResponse(items=_DEMO_FLASHCARDS)

    wanted = {t.strip().lower() for t in tags.split(",") if t.strip()}
    items = [
        fc for fc in _DEMO_FLASHCARDS
        if wanted.intersection({t.lower() for t in fc.tags})
    ]
    return FlashcardListResponse(items=items)
