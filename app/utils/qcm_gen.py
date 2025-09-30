import uuid
from typing import List, Dict
from app.models.qcm import Difficulty


def generate_questions(corpus: str, difficulty: Difficulty, total: int) -> List[Dict]:
    """
    Générateur très basique de QCM à partir d'un corpus de texte.
    V1 : repère quelques phrases et crée des questions factices.
    """
    sentences = [s.strip() for s in corpus.split(".") if len(s.strip()) > 8]
    if not sentences:
        sentences = ["Principe du contradictoire", "Force obligatoire du contrat"]

    items: List[Dict] = []
    for i, s in enumerate(sentences[:total], start=1):
        q_id = f"q_{uuid.uuid4().hex[:8]}"
        stem = f"Que signifie : {s} ?"
        correct = f"Définition correcte de « {s} »"
        choices = [
            correct,
            f"Mauvaise interprétation de {s}",
            "Réponse générique C",
            "Réponse générique D",
        ]
        items.append(
            {
                "id": q_id,
                "stem": stem,
                "choices": choices,
                "correctIndex": 0,
                "explanation": f"La bonne réponse est : {correct}",
            }
        )

    return items
