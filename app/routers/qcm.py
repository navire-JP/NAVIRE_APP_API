from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import select
from datetime import datetime, timezone, timedelta
import os, random
import json
import logging

from app.db.database import get_db
from app.db.database import SessionLocal
from app.db.models import User, File, QcmSession, QcmQuestion
from app.routers.auth import get_current_user  # réutilise ton helper
from openai import OpenAI


router = APIRouter(prefix="/qcm", tags=["qcm"])
client = OpenAI()

logger = logging.getLogger("navire.qcm")

QCM_COUNT = 5
SESSION_TTL_MIN = 30

# Reprend l'esprit de ta liste QCM_TYPES (Discord)
QCM_TYPES = [
    "K1 - Définition stricte",
    "K2 - Fondement juridique",
    "C1 - Portée d'une règle",
    "R1 - Mini cas pratique",
    "D2 - Exception à une règle",
    "T1 - Vocabulaire juridique",
]


def parse_pages_str(pages_str: str, total_pages: int) -> list[int]:
    # "5-7,9" -> [5,6,7,9]
    if not pages_str:
        return []
    pages = set()
    for part in pages_str.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            try:
                start, end = int(a), int(b)
                if start <= end:
                    for p in range(start, end + 1):
                        if 1 <= p <= total_pages:
                            pages.add(p)
            except:
                continue
        else:
            try:
                p = int(part)
                if 1 <= p <= total_pages:
                    pages.add(p)
            except:
                continue
    return sorted(pages)


def extract_text_pdf(path: str, pages_str: str) -> str:
    import fitz
    text = ""
    with fitz.open(path) as doc:
        targets = parse_pages_str(pages_str, doc.page_count)
        indices = [p - 1 for p in targets] if targets else list(range(doc.page_count))
        for i in indices:
            text += doc[i].get_text("text") + "\n"
    return text.strip()


def difficulty_block(difficulty: str) -> str:
    if difficulty == "easy":
        return "Niveau: FACILE (notions fondamentales, formulations directes)."
    if difficulty == "hard":
        return "Niveau: DIFFICILE (distinctions fines, exceptions, pièges)."
    return "Niveau: INTERMÉDIAIRE (CRFPA standard)."


def build_prompt(source_text: str, difficulty: str) -> str:
    qcm_type = random.choice(QCM_TYPES)
    return f"""
Tu es un examinateur CRFPA. Génère UN QCM à réponse unique à partir de l'extrait.

TYPE: {qcm_type}
{difficulty_block(difficulty)}

Tu DOIS répondre en JSON strict (pas de texte autour).
Schéma attendu :
{{
  "question": "string",
  "choices": ["string", "string", "string", "string"],
  "correct_index": 0,
  "explanation": "string"
}}

Contraintes :
- exact 4 choix
- correct_index ∈ [0,1,2,3]
- Les 3 mauvaises réponses sont crédibles mais fausses
- L'explication justifie la bonne réponse et explique pourquoi les autres sont fausses
- Pas d'ambiguïté

EXTRAIT :
{source_text}
""".strip()


def parse_qcm_json(txt: str) -> dict:
    """
    Parse robuste : on attend un JSON.
    Si le JSON n'est pas valide ou incomplet -> ValueError explicite.
    """
    try:
        data = json.loads(txt)
    except Exception:
        raise ValueError("OpenAI n'a pas renvoyé un JSON valide")

    q = (data.get("question") or "").strip()
    choices = data.get("choices")
    ci = data.get("correct_index")
    exp = (data.get("explanation") or "").strip()

    if not q:
        raise ValueError("JSON invalide: question manquante")
    if not isinstance(choices, list) or len(choices) != 4:
        raise ValueError("JSON invalide: choices doit contenir exactement 4 éléments")
    if not isinstance(ci, int) or ci not in [0, 1, 2, 3]:
        raise ValueError("JSON invalide: correct_index doit être 0..3")
    if not exp:
        raise ValueError("JSON invalide: explanation manquante")

    choices = [str(c).strip() for c in choices]
    if any(not c for c in choices):
        raise ValueError("JSON invalide: choices contient un élément vide")

    good_letter = ["A", "B", "C", "D"][ci]
    return {
        "question": q,
        "a": choices[0],
        "b": choices[1],
        "c": choices[2],
        "d": choices[3],
        "good": good_letter,
        "exp": exp,
    }


def generate_session_questions(session_id: str):
    db = SessionLocal()
    try:
        session = db.execute(
            select(QcmSession).where(QcmSession.id == session_id)
        ).scalar_one()

        file = db.execute(
            select(File).where(File.id == session.file_id)
        ).scalar_one_or_none()

        if not file:
            session.status = "error"
            session.error_message = "Fichier introuvable"
            db.commit()
            return

        pdf_path = file.path

        try:
            text = extract_text_pdf(pdf_path, session.pages)
            if len(text) < 200:
                raise ValueError("PDF trop vide ou texte insuffisant")

            chunks = []
            words = text.split()
            chunk_size = max(200, min(450, len(words) // QCM_COUNT if len(words) else 200))

            for _ in range(QCM_COUNT):
                start = random.randint(0, max(0, len(words) - chunk_size))
                chunks.append(" ".join(words[start:start + chunk_size]))

            for i, chunk in enumerate(chunks):
                prompt = build_prompt(chunk, session.difficulty)

                rep = client.chat.completions.create(
                    model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                    messages=[
                        {"role": "system", "content": "Tu réponds uniquement en JSON strict, sans texte autour."},
                        {"role": "user", "content": prompt},
                    ],
                    response_format={"type": "json_object"},  # ✅ verrouille le format JSON
                    temperature=0.2,
                    timeout=45,
                )

                txt = rep.choices[0].message.content or ""
                data = parse_qcm_json(txt)

                q = QcmQuestion(
                    session_id=session.id,
                    index=i,
                    question=data["question"],
                    choice_a=data["a"],
                    choice_b=data["b"],
                    choice_c=data["c"],
                    choice_d=data["d"],
                    correct_letter=data["good"],
                    explanation=data["exp"],
                )
                db.add(q)

            session.status = "ready"
            db.commit()

        except Exception as e:
            # IMPORTANT: log dans Render pour avoir le détail de l'erreur
            logger.exception("QCM generation failed")
            session.status = "error"
            session.error_message = str(e)
            db.commit()

    finally:
        db.close()


@router.post("/start")
def start_qcm(
    payload: dict,
    bg: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # payload: {file_id, difficulty, pages}
    file_id = int(payload.get("file_id", 0))
    difficulty = (payload.get("difficulty") or "medium").strip()
    pages = (payload.get("pages") or "").strip()

    if difficulty not in ["easy", "medium", "hard"]:
        raise HTTPException(400, detail="difficulty invalide")

    # vérifier ownership du fichier
    file = db.execute(select(File).where(File.id == file_id)).scalar_one_or_none()
    if not file or file.user_id != user.id:
        raise HTTPException(403, detail="Fichier non autorisé")

    expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=SESSION_TTL_MIN)

    session = QcmSession(
        user_id=user.id,
        file_id=file_id,
        difficulty=difficulty,
        pages=pages,
        status="generating",
        current_index=0,
        expires_at=expires_at,
    )
    db.add(session)
    db.commit()
    db.refresh(session)

    # génération en background
    bg.add_task(generate_session_questions, session.id)

    return {"session_id": session.id, "status": session.status, "expires_at": session.expires_at.isoformat()}


def get_owned_session(db: Session, user_id: int, session_id: str) -> QcmSession:
    s = db.execute(select(QcmSession).where(QcmSession.id == session_id)).scalar_one_or_none()
    if not s:
        raise HTTPException(404, detail="Session introuvable")
    if s.user_id != user_id:
        raise HTTPException(403, detail="Session non autorisée")

    # SQLite renvoie souvent des datetimes naïfs -> comparer en UTC naïf
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    exp = s.expires_at
    # exp peut être naïf ou aware : on force en naïf
    if getattr(exp, "tzinfo", None) is not None:
        exp = exp.replace(tzinfo=None)

    if now > exp:
        raise HTTPException(410, detail="Session expirée")

    return s


@router.get("/{session_id}/current")
def current(
    session_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    s = get_owned_session(db, user.id, session_id)

    if s.status == "generating":
        return {"status": "generating"}

    if s.status == "error":
        return {"status": "error", "detail": s.error_message}

    if s.status == "done":
        return {"status": "done"}

    q = db.execute(
        select(QcmQuestion).where(QcmQuestion.session_id == s.id, QcmQuestion.index == s.current_index)
    ).scalar_one_or_none()

    if not q:
        return {"status": "error", "detail": "Question introuvable"}

    return {
        "status": "ready",
        "index": s.current_index + 1,
        "total": QCM_COUNT,
        "question": q.question,
        "choices": [q.choice_a, q.choice_b, q.choice_c, q.choice_d],
    }


@router.post("/{session_id}/answer")
def answer(
    session_id: str,
    payload: dict,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    s = get_owned_session(db, user.id, session_id)
    if s.status != "ready":
        raise HTTPException(400, detail="Session non prête")

    choice_index = int(payload.get("choice_index", -1))
    if choice_index not in [0, 1, 2, 3]:
        raise HTTPException(400, detail="choice_index invalide")

    q = db.execute(
        select(QcmQuestion).where(QcmQuestion.session_id == s.id, QcmQuestion.index == s.current_index)
    ).scalar_one()

    letter = ["A", "B", "C", "D"][choice_index]
    q.answered = True
    q.user_letter = letter
    db.commit()

    correct_index = ["A", "B", "C", "D"].index(q.correct_letter)
    is_correct = (letter == q.correct_letter)

    return {
        "correct_index": correct_index,
        "is_correct": is_correct,
        "explanation": q.explanation,
        "done": (s.current_index >= QCM_COUNT - 1),
    }


@router.post("/{session_id}/next")
def next_q(
    session_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    s = get_owned_session(db, user.id, session_id)
    if s.status != "ready":
        raise HTTPException(400, detail="Session non prête")

    if s.current_index >= QCM_COUNT - 1:
        s.status = "done"
        db.commit()
        return {"status": "done"}

    s.current_index += 1
    db.commit()
    return {"status": "ok", "index": s.current_index + 1}
