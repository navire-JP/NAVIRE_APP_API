# routers/qcm.py
import uuid, json, random
from typing import List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from core.config import client
from core.db import DATABASE
from utils.pdf_tools import parse_pages_str

POINTS = {
    "facile":        {"good": 2, "bad": -1},
    "intermediaire": {"good": 4, "bad": -1},
    "difficile":     {"good": 5, "bad": -2},
}

router = APIRouter()

QCM_TYPES = [
    "K1 - Définition stricte", "K2 - Fondement juridique", "K3 - Classement typologique",
    "K4 - Liste complète", "C1 - Portée d'une règle", "C2 - Lien entre notions",
    "R1 - Mini cas pratique", "R2 - Qualification juridique", "R3 - Choix procédural",
    "D1 - Faux amis juridiques", "D2 - Exception à une règle", "D3 - Distinction conceptuelle fine",
    "J1 - Arrêt célèbre", "J2 - Jurisprudence récente", "P1 - Calcul de délai",
    "P2 - Conséquence procédurale", "T1 - Vocabulaire juridique", "T2 - Règle de méthode",
]

class QuestionQCM(BaseModel):
    title: str
    choices: List[str]
    correctIndex: int
    explanation: str

class GenerateRequest(BaseModel):
    file_id: str
    topic: Optional[str] = None
    num_questions: int = 6
    pages: Optional[str] = None
    difficulty: Optional[str] = "intermediaire"

class GenerateResponse(BaseModel):
    session_id: str
    questions: List[QuestionQCM]

def _mk_prompt(source_text: str, topic: Optional[str], difficulty: str, n: int) -> List[dict]:
    qcm_type = random.choice(QCM_TYPES)
    type_prompt = f"Type de question à respecter STRICTEMENT: {qcm_type}."
    diff_prompt = {
        "facile": "🟢 FACILE: formulation simple, notions de base, pas de pièges.",
        "intermediaire": "🟡 INTERMÉDIAIRE: niveau CRFPA classique, précision juridique attendue.",
        "difficile": "🔴 DIFFICILE: notion piégeuse, distinction fine, exception jurisprudentielle.",
    }.get(difficulty, "🟡 INTERMÉDIAIRE")

    system = (
        "Tu es un assistant de droit qui génère des QCM fiables et non ambigus. "
        "Exige 4 choix, UNE seule bonne réponse (index 'correctIndex'), et une explication qui justifie "
        "la bonne et invalide les 3 autres. Réponds en JSON avec {\"questions\":[{title,choices[4],correctIndex,explanation}]}."
    )

    user = (
        f"{type_prompt}\n"
        f"Niveau: {diff_prompt}\n"
        f"Contexte/Source (extraits du PDF){' sur le thème: '+topic if topic else ''}:\n{source_text}\n\n"
        f"Génère {n} questions."
    )

    return [{"role":"system","content":system},{"role":"user","content":user}]

@router.post("/generate", response_model=GenerateResponse)
def generate_qcm(req: GenerateRequest):
    meta = DATABASE["files"].get(req.file_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Fichier introuvable.")

    pages_text: List[str] = meta.get("pages_text") or []
    if not pages_text:
        raise HTTPException(status_code=400, detail="PDF inexploitable (aucun texte).")

    indices_1based = parse_pages_str(req.pages, len(pages_text))
    # concatène le texte des pages sélectionnées
    joined = "\n".join(pages_text[i-1] for i in indices_1based if 1 <= i <= len(pages_text))
    if not joined.strip():
        raise HTTPException(status_code=400, detail="Pages sélectionnées vides.")

    messages = _mk_prompt(joined[:20000], req.topic, (req.difficulty or "intermediaire").lower(), req.num_questions)

    completion = client.chat.completions.create(
        model="gpt-4o-mini",
        temperature=0.2,
        response_format={"type":"json_object"},
        messages=messages,
    )

    try:
        data = json.loads(completion.choices[0].message.content or "")
        raw_qs = data["questions"]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"JSON invalide: {e}")

    cleaned = [QuestionQCM(**q) for q in raw_qs]
    session_id = str(uuid.uuid4())
    DATABASE["sessions"][session_id] = {
        "file_id": req.file_id,
        "difficulty": (req.difficulty or "intermediaire").lower(),
        "pages": req.pages or "",
        "questions": [c.model_dump() for c in cleaned],
        "score": {"good": 0, "bad": 0},
    }
    return GenerateResponse(session_id=session_id, questions=cleaned)

@router.get("/{session_id}", response_model=GenerateResponse)
def get_qcm(session_id: str):
    sess = DATABASE["sessions"].get(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session introuvable.")
    return {"session_id": session_id, "questions": sess["questions"]}
