# app/routers/qcm.py
from __future__ import annotations

import os
import random
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from openai import OpenAI

from app.db.database import get_db, SessionLocal
from app.db.models import User, File, QcmSession, QcmQuestion
from app.routers.auth import get_current_user

router = APIRouter(prefix="/qcm", tags=["qcm"])
client = OpenAI()

# =========================================================
# Session policy
# =========================================================
QCM_COUNT = int(os.getenv("QCM_COUNT", "5"))
SESSION_TTL_MIN = int(os.getenv("QCM_SESSION_TTL_MIN", "30"))

# =========================================================
# Validation policy (IMPORTANT)
# =========================================================
MIN_TEXT_LEN = int(os.getenv("QCM_MIN_TEXT_LEN", "700"))
MIN_QUESTION_LEN = int(os.getenv("QCM_MIN_QUESTION_LEN", "25"))
MIN_CHOICE_LEN = int(os.getenv("QCM_MIN_CHOICE_LEN", "8"))
MIN_EXPLANATION_LEN = int(os.getenv("QCM_MIN_EXPLANATION_LEN", "25"))

DUPLICATE_SIMILARITY_GUARD = os.getenv("QCM_DUP_GUARD", "1") == "1"
CHUNK_WORDS = int(os.getenv("QCM_CHUNK_WORDS", "380"))

CACHE_DIR = Path(os.getenv("QCM_CACHE_DIR", "./storage/QcmCache")).resolve()
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# =========================================================
# Internal: per-session generation locks
# =========================================================
_GEN_LOCKS: Dict[str, threading.Lock] = {}
_PREFETCH_LOCKS: Dict[str, threading.Lock] = {}

QCM_TYPES = [
    "QCM de définition",
    "QCM de distinction",
    "QCM d'exception",
    "QCM de qualification",
    "QCM de procédure",
]

# =========================================================
# Helpers
# =========================================================
def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def ttl_deadline() -> datetime:
    return now_utc() - timedelta(minutes=SESSION_TTL_MIN)


def _lock_for(session_id: str) -> threading.Lock:
    lock = _GEN_LOCKS.get(session_id)
    if lock is None:
        lock = threading.Lock()
        _GEN_LOCKS[session_id] = lock
    return lock


def _prefetch_lock_for(session_id: str) -> threading.Lock:
    lock = _PREFETCH_LOCKS.get(session_id)
    if lock is None:
        lock = threading.Lock()
        _PREFETCH_LOCKS[session_id] = lock
    return lock


def _norm(s: str) -> str:
    return " ".join((s or "").lower().split())


def parse_pages_str(pages: str, max_pages: int) -> list[int]:
    """
    pages can be:
      ""            -> empty means ALL
      "1"           -> [1]
      "1-3"         -> [1,2,3]
      "1,3,5-7"     -> [1,3,5,6,7]
    indexes are 1-based in UI.
    """
    pages = (pages or "").strip()
    if not pages:
        return []
    out: set[int] = set()
    for part in pages.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            try:
                a_i = int(a.strip())
                b_i = int(b.strip())
            except Exception:
                continue
            if a_i > b_i:
                a_i, b_i = b_i, a_i
            for x in range(a_i, b_i + 1):
                if 1 <= x <= max_pages:
                    out.add(x)
        else:
            try:
                x = int(part)
            except Exception:
                continue
            if 1 <= x <= max_pages:
                out.add(x)
    return sorted(out)


def extract_text_pdf(path: str, pages_str: str) -> str:
    import fitz  # PyMuPDF

    text_parts = []
    with fitz.open(path) as doc:
        targets = parse_pages_str(pages_str, doc.page_count)
        indices = [p - 1 for p in targets] if targets else list(range(doc.page_count))

        for i in indices:
            try:
                t = doc[i].get_text("text") or ""
            except Exception:
                t = ""
            if t.strip():
                text_parts.append(t)

    return "\n".join(text_parts).strip()


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

Contraintes:
- 1 seule réponse correcte (A/B/C/D)
- Les 3 autres sont crédibles mais fausses
- Explication: justifie la bonne et pourquoi les autres sont fausses
- Pas d'ambiguïté
- Ne renvoie rien d'autre que le format demandé.

Format STRICT:
Question: ...
Réponse A: ...
Réponse B: ...
Réponse C: ...
Réponse D: ...
Bonne Réponse: A|B|C|D
Explication: ...

EXTRAIT:
{source_text}
""".strip()


def parse_qcm_answer(txt: str) -> dict:
    lines = [l.strip() for l in (txt or "").split("\n") if l.strip()]

    def pick(prefix: str) -> str:
        for l in lines:
            if l.lower().startswith(prefix.lower()):
                return l.split(":", 1)[1].strip()
        return ""

    q = pick("Question")
    a = pick("Réponse A")
    b = pick("Réponse B")
    c = pick("Réponse C")
    d = pick("Réponse D")
    good = pick("Bonne Réponse").upper()[:1]
    exp = pick("Explication")

    if not (q and a and b and c and d and good in ["A", "B", "C", "D"] and exp):
        raise ValueError("Format OpenAI invalide")
    return {"question": q, "a": a, "b": b, "c": c, "d": d, "good": good, "exp": exp}


def validate_qcm_data(data: dict, seen_questions: set[str]) -> None:
    q = (data.get("question") or "").strip()
    a = (data.get("a") or "").strip()
    b = (data.get("b") or "").strip()
    c = (data.get("c") or "").strip()
    d = (data.get("d") or "").strip()
    good = (data.get("good") or "").strip().upper()[:1]
    exp = (data.get("exp") or "").strip()

    if len(q) < MIN_QUESTION_LEN:
        raise ValueError("Question trop courte")
    if min(len(a), len(b), len(c), len(d)) < MIN_CHOICE_LEN:
        raise ValueError("Réponses incomplètes")
    if good not in ["A", "B", "C", "D"]:
        raise ValueError("Bonne réponse invalide")
    if len(exp) < MIN_EXPLANATION_LEN:
        raise ValueError("Explication trop courte")

    if DUPLICATE_SIMILARITY_GUARD:
        nq = _norm(q)
        if nq in seen_questions:
            raise ValueError("Question dupliquée")


def cache_path_for_session(session_id: str) -> Path:
    return CACHE_DIR / f"{session_id}.txt"


def get_or_build_source_words(db: Session, session: QcmSession) -> list[str]:
    p = cache_path_for_session(session.id)
    if p.exists():
        txt = p.read_text(encoding="utf-8", errors="ignore").strip()
        return txt.split()

    file = db.execute(select(File).where(File.id == session.file_id)).scalar_one_or_none()
    if not file:
        raise ValueError("Fichier introuvable")

    txt = extract_text_pdf(file.path, session.pages)
    if len(txt) < MIN_TEXT_LEN:
        raise ValueError("PDF trop vide ou texte insuffisant")

    p.write_text(txt, encoding="utf-8")
    return txt.split()


def pick_chunk(words: list[str], chunk_size: int) -> str:
    if not words:
        return ""
    start = random.randint(0, max(0, len(words) - chunk_size))
    chunk = words[start : start + chunk_size]
    return " ".join(chunk).strip()


def call_openai_one(prompt: str) -> str:
    # ⚠️ adapte le modèle si besoin dans ton projet
    model = os.getenv("OPENAI_MODEL_QCM", "gpt-4o-mini")
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "Tu respectes strictement le format demandé."},
            {"role": "user", "content": prompt},
        ],
        temperature=float(os.getenv("QCM_TEMPERATURE", "0.5")),
        max_tokens=int(os.getenv("QCM_MAX_TOKENS", "700")),
    )
    return (resp.choices[0].message.content or "").strip()


def ensure_session_owner(session: QcmSession, user: User) -> None:
    if session.user_id != user.id:
        raise HTTPException(status_code=403, detail="Forbidden")


def ensure_not_expired(session: QcmSession) -> None:
    if session.created_at < ttl_deadline():
        raise HTTPException(status_code=410, detail="Session expirée")


def _session_status(db: Session, session: QcmSession) -> dict:
    total = session.total or QCM_COUNT
    gen_count = db.execute(
        select(QcmQuestion).where(QcmQuestion.session_id == session.id)
    ).scalars().all()
    generated = len(gen_count)
    return {"generated_count": generated, "total": total}


def _get_question_by_index(db: Session, session_id: str, index1: int) -> Optional[QcmQuestion]:
    return db.execute(
        select(QcmQuestion).where(
            QcmQuestion.session_id == session_id,
            QcmQuestion.index == index1,
        )
    ).scalar_one_or_none()


def _next_unanswered_index(db: Session, session: QcmSession) -> int:
    # answered stored on session.current_index (1-based) + session.answered_count optional
    cur = int(session.current_index or 0)
    return max(1, cur + 1)


# =========================================================
# Generation core (runs in thread)
# =========================================================
def generate_questions_for_session(session_id: str) -> None:
    lock = _lock_for(session_id)
    if not lock.acquire(blocking=False):
        return  # already generating

    try:
        db = SessionLocal()
        try:
            session = db.execute(select(QcmSession).where(QcmSession.id == session_id)).scalar_one_or_none()
            if not session:
                return

            # If already has enough, mark ready/done accordingly
            total = int(session.total or QCM_COUNT)
            st = _session_status(db, session)
            if st["generated_count"] >= total:
                session.status = "ready"
                session.detail = "Questions prêtes."
                db.commit()
                return

            session.status = "generating"
            session.detail = "Génération en cours…"
            db.commit()

            words = get_or_build_source_words(db, session)
            seen: set[str] = set()

            # build seen from existing questions
            existing = db.execute(
                select(QcmQuestion).where(QcmQuestion.session_id == session.id)
            ).scalars().all()
            for q in existing:
                seen.add(_norm(q.question))

            # generate until total
            tries = 0
            max_tries = int(os.getenv("QCM_MAX_TRIES", "30"))
            while True:
                session = db.execute(select(QcmSession).where(QcmSession.id == session_id)).scalar_one_or_none()
                if not session:
                    return

                # stop if closed/done/error
                if session.status in ("done", "closed", "error"):
                    return

                st = _session_status(db, session)
                generated = st["generated_count"]
                total = int(session.total or QCM_COUNT)

                if generated >= total:
                    session.status = "ready"
                    session.detail = "Questions prêtes."
                    db.commit()
                    return

                if tries >= max_tries:
                    session.status = "error"
                    session.detail = "Génération impossible (trop d'échecs)."
                    db.commit()
                    return

                tries += 1

                chunk = pick_chunk(words, CHUNK_WORDS)
                prompt = build_prompt(chunk, session.difficulty or "medium")

                try:
                    raw = call_openai_one(prompt)
                    data = parse_qcm_answer(raw)
                    validate_qcm_data(data, seen)
                except Exception as e:
                    session.detail = f"Retry… ({generated}/{total})"
                    db.commit()
                    time.sleep(0.35)
                    continue

                # insert question
                q_obj = QcmQuestion(
                    session_id=session.id,
                    index=generated + 1,  # 1-based
                    question=data["question"],
                    choice_a=data["a"],
                    choice_b=data["b"],
                    choice_c=data["c"],
                    choice_d=data["d"],
                    correct_letter=data["good"],
                    explanation=data["exp"],
                )
                db.add(q_obj)
                db.flush()

                seen.add(_norm(data["question"]))

                session.detail = f"Génération… ({generated+1}/{total})"
                db.commit()

                time.sleep(0.12)

        finally:
            try:
                db.close()
            except Exception:
                pass
    finally:
        try:
            lock.release()
        except Exception:
            pass


def spawn_generation(session_id: str) -> None:
    t = threading.Thread(target=generate_questions_for_session, args=(session_id,), daemon=True)
    t.start()


# =========================================================
# Prefetch (make sure next question exists)
# =========================================================
def prefetch_next_if_needed(session_id: str) -> None:
    lock = _prefetch_lock_for(session_id)
    if not lock.acquire(blocking=False):
        return
    try:
        db = SessionLocal()
        try:
            session = db.execute(select(QcmSession).where(QcmSession.id == session_id)).scalar_one_or_none()
            if not session:
                return

            if session.status in ("done", "closed", "error"):
                return

            total = int(session.total or QCM_COUNT)
            next_idx = _next_unanswered_index(db, session)

            if next_idx > total:
                return

            q = _get_question_by_index(db, session.id, next_idx)
            if q:
                return  # already ready

            # not generated yet -> ensure generator running
            spawn_generation(session.id)
        finally:
            db.close()
    finally:
        lock.release()


# =========================================================
# ROUTES
# =========================================================
@router.post("/start")
def start_qcm(
    payload: dict,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    file_id = int(payload.get("file_id") or 0)
    difficulty = (payload.get("difficulty") or "medium").strip()
    pages = (payload.get("pages") or "").strip()

    if not file_id:
        raise HTTPException(status_code=400, detail="file_id manquant")

    file = db.execute(select(File).where(File.id == file_id)).scalar_one_or_none()
    if not file:
        raise HTTPException(status_code=404, detail="Fichier introuvable")

    # Optional: ensure ownership (if your File has user_id)
    if getattr(file, "user_id", None) is not None and file.user_id != user.id:
        raise HTTPException(status_code=403, detail="Forbidden")

    # prevent multiple active sessions for same user/file (optional)
    active = db.execute(
        select(QcmSession)
        .where(QcmSession.user_id == user.id)
        .where(QcmSession.status.in_(["generating", "ready"]))
        .order_by(QcmSession.created_at.desc())
    ).scalar_one_or_none()
    if active and active.created_at >= ttl_deadline():
        # return existing active session id
        return {"session_id": active.id, "status": active.status}

    session = QcmSession(
        user_id=user.id,
        file_id=file.id,
        difficulty=difficulty,
        pages=pages,
        status="generating",
        total=QCM_COUNT,
        current_index=0,
        created_at=now_utc(),
        detail="Démarrage…",
    )
    db.add(session)
    db.commit()

    # start background generation
    spawn_generation(session.id)

    return {"session_id": session.id, "status": session.status}


@router.get("/{session_id}/current")
def current(
    session_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    session = db.execute(select(QcmSession).where(QcmSession.id == session_id)).scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session introuvable")
    ensure_session_owner(session, user)
    ensure_not_expired(session)

    total = int(session.total or QCM_COUNT)

    # done?
    if session.status in ("done", "closed"):
        return {"status": "done", "detail": "Terminé.", "total": total}

    # hard error?
    if session.status == "error":
        return {"status": "error", "detail": session.detail or "Erreur génération", "total": total}

    # compute how many already generated
    st = _session_status(db, session)
    generated = st["generated_count"]

    # if not enough generated yet -> generating
    next_idx = _next_unanswered_index(db, session)
    q = _get_question_by_index(db, session.id, next_idx)

    if not q:
        # keep generation running
        spawn_generation(session.id)
        return {
            "status": "generating",
            "detail": session.detail or "Génération…",
            "generated_count": generated,
            "total": total,
        }

    # ready
    session.status = "ready"
    session.detail = "Questions prêtes."
    db.commit()

    return {
        "status": "ready",
        "index": int(q.index),
        "total": total,
        "question": q.question,
        "choices": [q.choice_a, q.choice_b, q.choice_c, q.choice_d],
        "generated_count": generated,
    }


@router.post("/{session_id}/answer")
def answer(
    session_id: str,
    payload: dict,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    choice_index = int(payload.get("choice_index") if payload.get("choice_index") is not None else -1)
    if choice_index not in (0, 1, 2, 3):
        raise HTTPException(status_code=400, detail="choice_index invalide (0..3)")

    session = db.execute(select(QcmSession).where(QcmSession.id == session_id)).scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session introuvable")
    ensure_session_owner(session, user)
    ensure_not_expired(session)

    total = int(session.total or QCM_COUNT)

    if session.status in ("done", "closed"):
        return {"status": "done", "detail": "Terminé.", "total": total}

    next_idx = _next_unanswered_index(db, session)
    q = _get_question_by_index(db, session.id, next_idx)
    if not q:
        # not ready -> generating
        spawn_generation(session.id)
        raise HTTPException(status_code=409, detail="Question pas prête (generating)")

    correct_letter = (q.correct_letter or "").strip().upper()
    correct_index = {"A": 0, "B": 1, "C": 2, "D": 3}.get(correct_letter, -1)
    is_correct = (choice_index == correct_index)

    # mark session progress
    session.current_index = int(q.index)
    if int(session.current_index or 0) >= total:
        session.status = "done"
        session.detail = "Terminé."
    else:
        session.status = "ready"
    db.commit()

    # prefetch next in background
    prefetch_next_if_needed(session.id)

    return {
        "status": "answered",
        "index": int(q.index),
        "total": total,
        "correct_index": correct_index,
        "is_correct": is_correct,
        "explanation": q.explanation or "",
    }


@router.get("/{session_id}/next_status")
def next_status(
    session_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    session = db.execute(select(QcmSession).where(QcmSession.id == session_id)).scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session introuvable")
    ensure_session_owner(session, user)
    ensure_not_expired(session)

    total = int(session.total or QCM_COUNT)

    if session.status in ("done", "closed"):
        return {"status": "done", "total": total}

    next_idx = _next_unanswered_index(db, session)
    if next_idx > total:
        return {"status": "done", "total": total}

    q = _get_question_by_index(db, session.id, next_idx)
    if q:
        return {"status": "ready", "index": int(q.index), "total": total}

    # not ready yet -> start prefetch/generation
    prefetch_next_if_needed(session.id)
    st = _session_status(db, session)
    return {
        "status": "generating",
        "generated_count": st["generated_count"],
        "total": total,
        "detail": session.detail or "Génération…",
    }


@router.post("/{session_id}/next")
def next_question(
    session_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    session = db.execute(select(QcmSession).where(QcmSession.id == session_id)).scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session introuvable")
    ensure_session_owner(session, user)
    ensure_not_expired(session)

    total = int(session.total or QCM_COUNT)

    if session.status in ("done", "closed"):
        return {"status": "done", "detail": "Terminé.", "total": total}

    next_idx = _next_unanswered_index(db, session)
    if next_idx > total:
        session.status = "done"
        session.detail = "Terminé."
        db.commit()
        return {"status": "done", "detail": "Terminé.", "total": total}

    q = _get_question_by_index(db, session.id, next_idx)
    if not q:
        # not ready -> generating
        spawn_generation(session.id)
        st = _session_status(db, session)
        return {
            "status": "generating",
            "detail": session.detail or "Génération…",
            "generated_count": st["generated_count"],
            "total": total,
        }

    session.status = "ready"
    session.detail = "Question prête."
    db.commit()

    # prefetch next
    prefetch_next_if_needed(session.id)

    return {
        "status": "ready",
        "index": int(q.index),
        "total": total,
        "question": q.question,
        "choices": [q.choice_a, q.choice_b, q.choice_c, q.choice_d],
    }


@router.post("/{session_id}/close")
def close_session(
    session_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    session = db.execute(select(QcmSession).where(QcmSession.id == session_id)).scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session introuvable")
    ensure_session_owner(session, user)

    session.status = "closed"
    session.detail = "Fermée."
    db.commit()

    return {"status": "closed", "detail": "Fermée."}
