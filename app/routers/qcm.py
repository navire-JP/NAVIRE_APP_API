from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import select
from datetime import datetime, timezone, timedelta
import os, random, time
from typing import Dict, Tuple

from app.db.database import get_db
from app.db.database import SessionLocal
from app.db.models import User, File, QcmSession, QcmQuestion
from app.routers.auth import get_current_user  # réutilise ton helper
from openai import OpenAI


router = APIRouter(prefix="/qcm", tags=["qcm"])
client = OpenAI()

QCM_COUNT = 5
SESSION_TTL_MIN = 30


# -------------------------
# ✅ Anti-freeze watchdog
# -------------------------
SERVER_GENERATION_WATCHDOG_SEC = int(os.getenv("QCM_WATCHDOG_SEC", "90"))

# -------------------------
# ✅ In-process generation locks (anti-concurrent)
# key = (session_id, target_index) -> started_at_monotonic
# -------------------------
GEN_LOCKS: Dict[Tuple[str, int], float] = {}
GEN_LOCK_TTL_SEC = 180.0


def _lock_key(session_id: str, target_index: int) -> Tuple[str, int]:
    return (session_id, target_index)


def acquire_gen_lock(session_id: str, target_index: int) -> bool:
    now = time.monotonic()
    expired = [k for k, ts in GEN_LOCKS.items() if (now - ts) > GEN_LOCK_TTL_SEC]
    for k in expired:
        GEN_LOCKS.pop(k, None)
    key = _lock_key(session_id, target_index)
    if key in GEN_LOCKS:
        return False
    GEN_LOCKS[key] = now
    return True


def release_gen_lock(session_id: str, target_index: int) -> None:
    GEN_LOCKS.pop(_lock_key(session_id, target_index), None)


def lock_age_seconds(session_id: str, target_index: int) -> float | None:
    ts = GEN_LOCKS.get(_lock_key(session_id, target_index))
    if ts is None:
        return None
    return time.monotonic() - ts


# -------------------------
# QCM generation policy
# -------------------------
MIN_TEXT_LEN = 700
MAX_TRIES_PER_QUESTION = 6
MAX_TOTAL_TRIES = 30


def _norm(s: str) -> str:
    return " ".join((s or "").lower().strip().split())


def pick_chunk(words: list[str], chunk_size: int) -> str:
    if not words:
        return ""
    if len(words) <= chunk_size:
        return " ".join(words)
    start = random.randint(0, max(0, len(words) - chunk_size))
    return " ".join(words[start : start + chunk_size])


def build_prompt(chunk: str, difficulty: str) -> str:
    return f"""
Tu es un générateur de QCM pour étudiants en droit.

Contraintes STRICTES :
- 1 question de QCM en français.
- 4 choix (A,B,C,D), 1 seule bonne réponse.
- Explication courte (2-5 lignes).
- Le contenu DOIT être fondé sur le texte fourni.
- Pas de "je ne sais pas". Pas de placeholders.
- La question DOIT être claire, les réponses distinctes.

Format de sortie STRICT (JSON sur une seule ligne) :
{{
  "question": "...",
  "a": "...",
  "b": "...",
  "c": "...",
  "d": "...",
  "good": "A|B|C|D",
  "exp": "..."
}}

Difficulté: {difficulty}

Texte source:
\"\"\"{chunk}\"\"\"
""".strip()


def parse_qcm_answer(raw: str) -> dict:
    # parsing simple: on s'attend à du JSON
    import json

    raw = (raw or "").strip()
    # fallback: essayer de couper si modèle entoure avec ```json
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw.replace("json", "").strip()
    return json.loads(raw)


def validate_qcm_data(data: dict, seen_questions: set[str]) -> None:
    # validations minimales
    required = ["question", "a", "b", "c", "d", "good", "exp"]
    for k in required:
        if k not in data:
            raise ValueError(f"Champ manquant: {k}")

    q = _norm(str(data["question"]))
    if len(q) < 10:
        raise ValueError("Question trop courte")
    if q in seen_questions:
        raise ValueError("Question dupliquée")

    good = str(data["good"]).strip().upper()
    if good not in ["A", "B", "C", "D"]:
        raise ValueError("good invalide")

    # réponses non vides et distinctes
    answers = [str(data["a"]).strip(), str(data["b"]).strip(), str(data["c"]).strip(), str(data["d"]).strip()]
    if any(len(_norm(x)) < 2 for x in answers):
        raise ValueError("Réponse vide")
    if len({_norm(x) for x in answers}) < 4:
        raise ValueError("Réponses non distinctes")

    exp = _norm(str(data["exp"]))
    if len(exp) < 10:
        raise ValueError("Explication trop courte")


def extract_text_pdf(pdf_path: str, pages: str) -> str:
    # ⚠️ Garde ta logique existante ici si tu as déjà pdfplumber / pypdf etc.
    # Ici placeholder minimal: à remplacer par ton implémentation actuelle.
    import pdfplumber

    text_parts: list[str] = []
    page_nums: list[int] = []
    if pages:
        # pages = "1,2,3" ou "1-5" etc. (selon ton implémentation actuelle)
        # On fait simple: si "1-5"
        if "-" in pages:
            a, b = pages.split("-", 1)
            a = int(a.strip())
            b = int(b.strip())
            page_nums = list(range(a, b + 1))
        else:
            page_nums = [int(x.strip()) for x in pages.split(",") if x.strip().isdigit()]

    with pdfplumber.open(pdf_path) as pdf:
        if not page_nums:
            # par défaut : premières pages
            page_nums = list(range(1, min(len(pdf.pages), 6) + 1))

        for pn in page_nums:
            idx = pn - 1
            if 0 <= idx < len(pdf.pages):
                page = pdf.pages[idx]
                text_parts.append(page.extract_text() or "")

    return "\n".join(text_parts).strip()


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

    # ✅ génération par question : on prépare la question 0 seulement
    bg.add_task(generate_one_question, session.id, 0)

    return {
        "session_id": session.id,
        "status": session.status,
        "expires_at": session.expires_at.isoformat(),
    }


def get_owned_session(db: Session, user_id: int, session_id: str) -> QcmSession:
    s = db.execute(select(QcmSession).where(QcmSession.id == session_id)).scalar_one_or_none()
    if not s:
        raise HTTPException(404, detail="Session introuvable")
    if s.user_id != user_id:
        raise HTTPException(403, detail="Session non autorisée")
    return s


def get_seen_questions(db: Session, session_id: str) -> set[str]:
    qs = db.execute(select(QcmQuestion).where(QcmQuestion.session_id == session_id)).scalars().all()
    return {_norm(q.question) for q in qs if q.question}


def generate_one_question(session_id: str, target_index: int) -> None:
    """Génère UNE question (index target_index) avec retry/validation (idempotent + lock)."""
    if not acquire_gen_lock(session_id, target_index):
        return

    db = SessionLocal()
    try:
        session = db.execute(select(QcmSession).where(QcmSession.id == session_id)).scalar_one_or_none()
        if not session:
            return
        if session.status in ["done", "error"]:
            return

        existing = db.execute(
            select(QcmQuestion).where(QcmQuestion.session_id == session.id, QcmQuestion.index == target_index)
        ).scalar_one_or_none()
        if existing:
            session.status = "ready"
            session.error_message = None
            db.commit()
            return

        file = db.execute(select(File).where(File.id == session.file_id)).scalar_one_or_none()
        if not file:
            session.status = "error"
            session.error_message = "Fichier introuvable"
            db.commit()
            return

        text = extract_text_pdf(file.path, session.pages)
        if len(text) < MIN_TEXT_LEN:
            session.status = "error"
            session.error_message = "PDF trop vide ou texte insuffisant"
            db.commit()
            return

        words = text.split()
        chunk_size = max(200, min(450, len(words) // max(1, QCM_COUNT) if len(words) else 200))
        seen_questions = get_seen_questions(db, session.id)

        session.status = "generating"
        session.error_message = None
        db.commit()

        local_try = 0
        total_tries = 0

        while local_try < MAX_TRIES_PER_QUESTION:
            local_try += 1
            total_tries += 1
            if total_tries > MAX_TOTAL_TRIES:
                raise ValueError(f"Échec génération: trop de tentatives ({MAX_TOTAL_TRIES}).")

            db.refresh(session)
            if session.status == "done":
                return

            chunk = pick_chunk(words, chunk_size)
            if not chunk:
                raise ValueError("Texte source vide après découpage")

            prompt = build_prompt(chunk, session.difficulty)

            try:
                rep = client.chat.completions.create(
                    model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                    messages=[{"role": "user", "content": prompt}],
                )
                raw = rep.choices[0].message.content or ""
                data = parse_qcm_answer(raw)
                validate_qcm_data(data, seen_questions)
                seen_questions.add(_norm(data["question"]))

                q = QcmQuestion(
                    session_id=session.id,
                    index=target_index,
                    question=data["question"],
                    choice_a=data["a"],
                    choice_b=data["b"],
                    choice_c=data["c"],
                    choice_d=data["d"],
                    correct_letter=data["good"],
                    explanation=data["exp"],
                )
                db.add(q)
                db.commit()

                db.refresh(session)
                if session.status != "done":
                    session.status = "ready"
                    session.error_message = None
                    db.commit()
                return

            except Exception:
                time.sleep(0.25)
                continue

        raise ValueError(f"Échec génération question index={target_index} après {MAX_TRIES_PER_QUESTION} tentatives.")

    except Exception as e:
        try:
            session = db.execute(select(QcmSession).where(QcmSession.id == session_id)).scalar_one_or_none()
            if session and session.status != "done":
                session.status = "error"
                session.error_message = str(e)
                db.commit()
        except:
            pass
    finally:
        release_gen_lock(session_id, target_index)
        db.close()


# (on garde ta fonction historique si tu veux, mais elle n'est plus utilisée par défaut)
def generate_session_questions(session_id: str):
    """
    Ancienne génération full-session (background).
    Non utilisée désormais (on génère question par question).
    """
    db = SessionLocal()
    try:
        session = db.execute(select(QcmSession).where(QcmSession.id == session_id)).scalar_one_or_none()
        if not session:
            return
        if session.status in ["done", "error"]:
            return
        # optionnel: tu peux décider de pré-générer en batch ici si tu veux plus tard.
        return
    finally:
        db.close()


@router.get("/{session_id}/current")
def current(
    session_id: str,
    bg: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    s = get_owned_session(db, user.id, session_id)

    if s.status == "error":
        return {"status": "error", "detail": s.error_message}

    if s.status == "done":
        return {"status": "done"}

    q = db.execute(
        select(QcmQuestion).where(QcmQuestion.session_id == s.id, QcmQuestion.index == s.current_index)
    ).scalar_one_or_none()

    if q:
        if s.status != "ready":
            s.status = "ready"
            s.error_message = None
            db.commit()

        return {
            "status": "ready",
            "index": s.current_index + 1,
            "total": QCM_COUNT,
            "question": q.question,
            "choices": [q.choice_a, q.choice_b, q.choice_c, q.choice_d],
        }

    # ✅ Pas de question -> auto-heal + watchdog lock-based
    age = lock_age_seconds(s.id, s.current_index)

    if age is not None:
        if age > SERVER_GENERATION_WATCHDOG_SEC:
            s.status = "error"
            s.error_message = "Génération bloquée (timeout serveur). Relance le QCM."
            db.commit()
            return {"status": "error", "detail": s.error_message}

        if s.status != "generating":
            s.status = "generating"
            s.error_message = None
            db.commit()
        return {"status": "generating"}

    # ✅ Inline (fiable sur Render) + fallback background
    try:
        generate_one_question(s.id, s.current_index)
    except Exception:
        pass

    q2 = db.execute(
        select(QcmQuestion).where(QcmQuestion.session_id == s.id, QcmQuestion.index == s.current_index)
    ).scalar_one_or_none()

    if q2:
        s.status = "ready"
        s.error_message = None
        db.commit()
        return {
            "status": "ready",
            "index": s.current_index + 1,
            "total": QCM_COUNT,
            "question": q2.question,
            "choices": [q2.choice_a, q2.choice_b, q2.choice_c, q2.choice_d],
        }

    if s.status != "generating":
        s.status = "generating"
        s.error_message = None
        db.commit()

    bg.add_task(generate_one_question, s.id, s.current_index)
    return {"status": "generating"}


@router.post("/{session_id}/answer")
def answer(
    session_id: str,
    payload: dict,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    s = get_owned_session(db, user.id, session_id)
    if s.status in ["error", "done"]:
        raise HTTPException(400, detail="Session terminée ou en erreur")

    choice_index = int(payload.get("choice_index", -1))
    if choice_index not in [0, 1, 2, 3]:
        raise HTTPException(400, detail="choice_index invalide")

    q = db.execute(
        select(QcmQuestion).where(QcmQuestion.session_id == s.id, QcmQuestion.index == s.current_index)
    ).scalar_one_or_none()

    if not q:
        raise HTTPException(400, detail="Question introuvable (encore en génération)")

    letter = ["A", "B", "C", "D"][choice_index]
    q.answered = True
    q.user_letter = letter

    # quand l'utilisateur répond, la session est ready/answered côté front
    if s.status != "ready":
        s.status = "ready"

    db.commit()

    correct_index = ["A", "B", "C", "D"].index(q.correct_letter)
    is_correct = letter == q.correct_letter

    return {
        "correct_index": correct_index,
        "is_correct": is_correct,
        "explanation": q.explanation,
        "done": (s.current_index >= QCM_COUNT - 1),
    }


@router.post("/{session_id}/next")
def next_q(
    session_id: str,
    bg: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    s = get_owned_session(db, user.id, session_id)

    if s.status == "error":
        return {"status": "error", "detail": s.error_message}
    if s.status == "done":
        return {"status": "done"}

    qcur = db.execute(
        select(QcmQuestion).where(QcmQuestion.session_id == s.id, QcmQuestion.index == s.current_index)
    ).scalar_one_or_none()
    if not qcur or not getattr(qcur, "answered", False):
        raise HTTPException(400, detail="Réponds d'abord à la question")

    if s.current_index >= QCM_COUNT - 1:
        s.status = "done"
        db.commit()
        return {"status": "done"}

    s.current_index += 1
    s.status = "generating"
    s.error_message = None
    db.commit()

    qnext = db.execute(
        select(QcmQuestion).where(QcmQuestion.session_id == s.id, QcmQuestion.index == s.current_index)
    ).scalar_one_or_none()

    if qnext:
        s.status = "ready"
        db.commit()
        return {"status": "ready", "index": s.current_index + 1}

    bg.add_task(generate_one_question, s.id, s.current_index)
    return {"status": "generating", "index": s.current_index + 1}


@router.post("/{session_id}/close")
def close_session(
    session_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    s = get_owned_session(db, user.id, session_id)
    s.status = "done"
    db.commit()
    return {"status": "ok"}
