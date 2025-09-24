# routers/qcm.py
from __future__ import annotations

import os
import uuid
import json
import random
from pathlib import Path
from typing import List, Optional, Dict, Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from core.config import client
from core.db import DATABASE
from utils.pdf_tools import parse_pages_str, extract_pages_text_from_bytes

# =========================
#         CONFIG
# =========================

router = APIRouter(prefix="/qcm", tags=["qcm"])

# Emplacement par défaut des PDFs sur disque (aligné avec routers/files.py)
STORAGE_DIR = Path(os.getenv("STORAGE_DIR", "storage")).resolve()

# Garde-fous
MAX_QUESTIONS = 12
MIN_QUESTIONS = 1
ALLOWED_DIFFICULTIES = {"facile", "intermediaire", "difficile"}

# Poids (réutilisables plus tard si on ajoute un endpoint de scoring)
POINTS = {
    "facile":        {"good": 2, "bad": -1},
    "intermediaire": {"good": 4, "bad": -1},
    "difficile":     {"good": 5, "bad": -2},
}

# Types de QCM (un est tiré au sort par génération pour varier)
QCM_TYPES = [
    "K1 - Définition stricte", "K2 - Fondement juridique", "K3 - Classement typologique",
    "K4 - Liste complète", "C1 - Portée d'une règle", "C2 - Lien entre notions",
    "R1 - Mini cas pratique", "R2 - Qualification juridique", "R3 - Choix procédural",
    "D1 - Faux amis juridiques", "D2 - Exception à une règle", "D3 - Distinction conceptuelle fine",
    "J1 - Arrêt célèbre", "J2 - Jurisprudence récente", "P1 - Calcul de délai",
    "P2 - Conséquence procédurale", "T1 - Vocabulaire juridique", "T2 - Règle de méthode",
]


# =========================
#        SCHEMAS
# =========================

class QuestionQCM(BaseModel):
    title: str
    choices: List[str] = Field(min_length=4, max_length=4)
    correctIndex: int = Field(ge=0, le=3)
    explanation: str

class GenerateRequest(BaseModel):
    file_id: str
    topic: Optional[str] = None
    num_questions: int = Field(default=6, ge=MIN_QUESTIONS, le=MAX_QUESTIONS)
    pages: Optional[str] = None  # ex: "1-3, 5, 8-9"
    difficulty: Optional[str] = Field(default="intermediaire")

class GenerateResponse(BaseModel):
    session_id: str
    questions: List[QuestionQCM]


# =========================
#        HELPERS
# =========================

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
        "la bonne et invalide les 3 autres. Réponds en JSON avec "
        "{\"questions\":[{title,choices[4],correctIndex,explanation}]}."
    )

    user = (
        f"{type_prompt}\n"
        f"Niveau: {diff_prompt}\n"
        f"Contexte/Source (extraits du PDF){' sur le thème: '+topic if topic else ''}:\n"
        f"{source_text}\n\n"
        f"Génère {n} questions."
    )

    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _try_index_from_storage(file_id: str) -> Dict[str, Any]:
    """
    Si DATABASE['files'][file_id] n'a pas pages_text, on tente de retrouver le PDF
    sur disque dans STORAGE_DIR avec un nom '<uuid>_<originalname>.pdf' ou '<uuid>.pdf'.
    Si trouvé, on extrait le texte de chaque page et on met à jour DATABASE.
    Retourne le meta (avec pages_text/page_count).
    """
    # Cherche fichiers commençant par <file_id>_
    candidates = list(STORAGE_DIR.glob(f"{file_id}_*"))
    # Fallback: <file_id>.* (au cas où)
    candidates += list(STORAGE_DIR.glob(f"{file_id}.*"))

    pdf_path: Optional[Path] = None
    for p in candidates:
        if p.is_file() and p.suffix.lower() == ".pdf":
            pdf_path = p
            break

    if not pdf_path or not pdf_path.exists():
        # Rien à indexer
        raise HTTPException(status_code=400, detail="PDF introuvable sur disque pour indexation.")

    try:
        with open(pdf_path, "rb") as f:
            data = f.read()
        pages_text = extract_pages_text_from_bytes(data)  # List[str]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Extraction PDF échouée: {e}")

    meta = {
        "name": pdf_path.name,
        "pages_text": pages_text,
        "page_count": len(pages_text),
    }
    # on met en cache pour appels suivants
    DATABASE["files"][file_id] = meta
    return meta


def _load_pages_or_fail(file_id: str, pages_filter: Optional[str]) -> List[str]:
    """
    Charge les pages (texte) d'un fichier:
    - depuis DATABASE si présent
    - sinon tente une indexation depuis le disque (STORAGE_DIR)
    Applique le filtre de pages 'pages_filter' si fourni.
    """
    meta = DATABASE["files"].get(file_id)

    if not meta or not meta.get("pages_text"):
        # Essayer d'indexer à la volée depuis disque
        meta = _try_index_from_storage(file_id)

    pages_text: List[str] = meta.get("pages_text") or []
    if not pages_text:
        raise HTTPException(status_code=400, detail="PDF inexploitable (aucun texte).")

    indices_1based = parse_pages_str(pages_filter, len(pages_text))
    selected = [pages_text[i - 1] for i in indices_1based if 1 <= i <= len(pages_text)]
    if not any(s.strip() for s in selected):
        raise HTTPException(status_code=400, detail="Pages sélectionnées vides.")
    return selected


def _normalize_difficulty(diff: Optional[str]) -> str:
    d = (diff or "intermediaire").lower().strip()
    if d not in ALLOWED_DIFFICULTIES:
        d = "intermediaire"
    return d


def _normalize_num_questions(n: int) -> int:
    if n < MIN_QUESTIONS:
        return MIN_QUESTIONS
    if n > MAX_QUESTIONS:
        return MAX_QUESTIONS
    return n


# =========================
#         ROUTES
# =========================

@router.post("/generate", response_model=GenerateResponse, summary="Génère un QCM à partir d'un PDF indexé (ou indexe à la volée).")
def generate_qcm(req: GenerateRequest):
    # Normalisations
    difficulty = _normalize_difficulty(req.difficulty)
    n = _normalize_num_questions(req.num_questions)

    # Charge/Indexe les pages
    selected_pages_text = _load_pages_or_fail(req.file_id, req.pages)

    # Concatène les pages choisies et coupe pour rester safe côté token
    joined = "\n".join(selected_pages_text)
    if not joined.strip():
        raise HTTPException(status_code=400, detail="Source vide après sélection des pages.")

    # Prompt
    messages = _mk_prompt(joined[:20000], req.topic, difficulty, n)

    # Appel OpenAI
    try:
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.2,
            response_format={"type": "json_object"},
            messages=messages,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"LLM error: {e}")

    # Parsing JSON
    try:
        content = completion.choices[0].message.content or ""
        data = json.loads(content)
        raw_qs = data["questions"]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"JSON invalide: {e}")

    # Validation des questions
    try:
        cleaned = [QuestionQCM(**q) for q in raw_qs]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Questions invalides: {e}")

    # Enregistrement session
    session_id = str(uuid.uuid4())
    DATABASE["sessions"][session_id] = {
        "file_id": req.file_id,
        "difficulty": difficulty,
        "pages": req.pages or "",
        "questions": [c.model_dump() for c in cleaned],
        "score": {"good": 0, "bad": 0},
    }

    return GenerateResponse(session_id=session_id, questions=cleaned)


@router.get("/{session_id}", response_model=GenerateResponse, summary="Récupère les questions d'une session.")
def get_qcm(session_id: str):
    sess = DATABASE["sessions"].get(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session introuvable.")
    return {"session_id": session_id, "questions": sess["questions"]}
