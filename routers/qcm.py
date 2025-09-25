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

router = APIRouter(prefix="/qcm", tags=["qcm"])

STORAGE_DIR = Path(os.getenv("STORAGE_DIR", "storage")).resolve()

MAX_QUESTIONS = 12
MIN_QUESTIONS = 1
ALLOWED_DIFFICULTIES = {"facile", "intermediaire", "difficile"}

POINTS = {
    "facile":        {"good": 2, "bad": -1},
    "intermediaire": {"good": 4, "bad": -1},
    "difficile":     {"good": 5, "bad": -2},
}

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
    choices: List[str] = Field(min_length=4, max_length=4)
    correctIndex: int = Field(ge=0, le=3)
    explanation: str

class GenerateRequest(BaseModel):
    file_id: str
    topic: Optional[str] = None
    num_questions: int = Field(default=6, ge=MIN_QUESTIONS, le=MAX_QUESTIONS)
    pages: Optional[str] = None  # "1-3,5,8-9"
    difficulty: Optional[str] = Field(default="intermediaire")

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

def _load_pages_text_locally(file_id: str) -> list[str] | None:
    p = STORAGE_DIR / file_id / "pages.json"
    if not p.exists():
        return None
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("pages") or []

def _try_index_from_storage(file_id: str) -> Dict[str, Any]:
    candidates = list(STORAGE_DIR.glob(f"{file_id}_*"))
    candidates += list(STORAGE_DIR.glob(f"{file_id}.*"))

    pdf_path: Optional[Path] = None
    for p in candidates:
        if p.is_file() and p.suffix.lower() == ".pdf":
            pdf_path = p
            break
    if not pdf_path or not pdf_path.exists():
        raise HTTPException(status_code=400, detail="PDF introuvable sur disque pour indexation.")

    try:
        with open(pdf_path, "rb") as f:
            data = f.read()
        pages_text = extract_pages_text_from_bytes(data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Extraction PDF échouée: {e}")

    meta = {"name": pdf_path.name, "pages_text": pages_text, "page_count": len(pages_text)}
    DATABASE["files"][file_id] = meta
    return meta

def _load_pages_or_fail(file_id: str, pages_filter: Optional[str]) -> List[str]:
    # 1) RAM
    meta = DATABASE["files"].get(file_id)
    pages_text: List[str] = (meta or {}).get("pages_text") or []

    # 2) JSON persistant
    if not pages_text:
        cached = _load_pages_text_locally(file_id)
        if cached:
            pages_text = cached
            DATABASE["files"][file_id] = {
                "name": (meta or {}).get("name", ""),
                "pages_text": pages_text,
                "page_count": len(pages_text),
            }

    # 3) Fallback: indexer depuis le PDF
    if not pages_text:
        meta = _try_index_from_storage(file_id)
        pages_text = meta.get("pages_text") or []

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

@router.post("/generate", response_model=GenerateResponse, summary="Génère un QCM.")
def generate_qcm(req: GenerateRequest):
    difficulty = _normalize_difficulty(req.difficulty)
    n = _normalize_num_questions(req.num_questions)

    selected_pages_text = _load_pages_or_fail(req.file_id, req.pages)

    joined = "\n".join(selected_pages_text)
    if not joined.strip():
        raise HTTPException(status_code=400, detail="Source vide après sélection des pages.")

    try:
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.2,
            response_format={"type": "json_object"},
            messages=_mk_prompt(joined[:20000], req.topic, difficulty, n),
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"LLM error: {e}")

    try:
        content = completion.choices[0].message.content or ""
        data = json.loads(content)
        raw_qs = data["questions"]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"JSON invalide: {e}")

    try:
        cleaned = [QuestionQCM(**q) for q in raw_qs]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Questions invalides: {e}")

    session_id = str(uuid.uuid4())
    DATABASE["sessions"][session_id] = {
        "file_id": req.file_id,
        "difficulty": difficulty,
        "pages": req.pages or "",
        "questions": [c.model_dump() for c in cleaned],
        "score": {"good": 0, "bad": 0},
    }
    return GenerateResponse(session_id=session_id, questions=cleaned)

@router.get("/{session_id}", response_model=GenerateResponse, summary="Récupère une session.")
def get_qcm(session_id: str):
    sess = DATABASE["sessions"].get(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session introuvable.")
    return {"session_id": session_id, "questions": sess["questions"]}
