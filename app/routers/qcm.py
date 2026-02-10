# app/routers/qcm.py
from __future__ import annotations

import os
import random
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Tuple, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from openai import OpenAI

from app.db.database import get_db, SessionLocal
from app.db.models import User, File, QcmSession, QcmQuestion
from app.routers.auth import get_current_user

router = APIRouter(prefix="/qcm", tags=["qcm"])

# =========================================================
# OpenAI client (timeouts)
# =========================================================
OPENAI_MODEL = os.getenv("OPENAI_MODEL_QCM", os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
OPENAI_TIMEOUT_SEC = float(os.getenv("OPENAI_TIMEOUT_SEC", "25"))
OPENAI_MAX_TOKENS = int(os.getenv("OPENAI_MAX_TOKENS", "650"))
QCM_TEMPERATURE = float(os.getenv("QCM_TEMPERATURE", "0.5"))

client = OpenAI(timeout=OPENAI_TIMEOUT_SEC)

# =========================================================
# Session policy
# =========================================================
QCM_COUNT = int(os.getenv("QCM_COUNT", "5"))
SESSION_TTL_MIN = int(os.getenv("QCM_SESSION_TTL_MIN", "30"))

# =========================================================
# Validation policy
# =========================================================
MIN_TEXT_LEN = int(os.getenv("QCM_MIN_TEXT_LEN", "700"))
MIN_QUESTION_LEN = int(os.getenv("QCM_MIN_QUESTION_LEN", "25"))
MIN_CHOICE_LEN = int(os.getenv("QCM_MIN_CHOICE_LEN", "8"))
MIN_EXPLANATION_LEN = int(os.getenv("QCM_MIN_EXPLANATION_LEN", "25"))

DUPLICATE_SIMILARITY_GUARD = os.getenv("QCM_DUP_GUARD", "1") == "1"

# Retry policy
MAX_TRIES_PER_QUESTION = int(os.getenv("QCM_MAX_TRIES_PER_QUESTION", "6"))
MAX_TOTAL_TRIES = int(os.getenv("QCM_MAX_TOTAL_TRIES", "30"))

# Watchdog (soft)
SERVER_GENERATION_WATCHDOG_SEC = int(os.getenv("QCM_WATCHDOG_SEC", "240"))

# Chunking
CHUNK_WORDS = int(os.getenv("QCM_CHUNK_WORDS", "380"))

# =========================================================
# In-process generation locks (per session + target_index)
# =========================================================
GEN_LOCKS: Dict[Tuple[str, int], float] = {}
GEN_LOCK_TTL_SEC = float(os.getenv("QCM_GEN_LOCK_TTL_SEC", "180.0"))
GEN_LOCKS_MUTEX = threading.Lock()

# =========================================================
# Cache PDF extracted text
# =========================================================
CACHE_DIR = Path(os.getenv("NAVIRE_QCM_CACHE_DIR", "./storage/QcmCache")).resolve()
CACHE_DIR.mkdir(parents=True, exist_ok=True)

QCM_TYPES = [
    "QCM de définition",
    "QCM de distinction",
    "QCM d'exception",
    "QCM de qualification",
    "QCM de procédure",
]

# =========================================================
# Time helpers
# =========================================================
def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def ensure_aware_utc(dt: datetime | None) -> datetime | None:
    # SQLite/SQLAlchemy peut renvoyer des datetime naive.
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


# =========================================================
# Locks helpers
# =========================================================
def _lock_key(session_id: str, target_index: int) -> Tuple[str, int]:
    return (session_id, target_index)


def acquire_gen_lock(session_id: str, target_index: int) -> bool:
    now = time.monotonic()
    with GEN_LOCKS_MUTEX:
        expired = [k for k, ts in GEN_LOCKS.items() if (now - ts) > GEN_LOCK_TTL_SEC]
        for k in expired:
            GEN_LOCKS.pop(k, None)

        key = _lock_key(session_id, target_index)
        if key in GEN_LOCKS:
            return False
        GEN_LOCKS[key] = now
        return True


def release_gen_lock(session_id: str, target_index: int) -> None:
    with GEN_LOCKS_MUTEX:
        GEN_LOCKS.pop(_lock_key(session_id, target_index), None)


def lock_age_sec(session_id: str, target_index: int) -> float | None:
    key = _lock_key(session_id, target_index)
    ts = GEN_LOCKS.get(key)
    if ts is None:
        return None
    return max(0.0, time.monotonic() - ts)


# =========================================================
# Text helpers
# =========================================================
def _norm(s: str) -> str:
    return " ".join((s or "").strip().lower().split())


def parse_pages_str(pages_str: str, total_pages: int) -> list[int]:
    """
    ""            -> all
    "1"           -> [1]
    "1-3"         -> [1,2,3]
    "1,3,5-7"     -> [1,3,5,6,7]
    """
    pages_str = (pages_str or "").strip()
    if not pages_str:
        return []
    pages: set[int] = set()
    for part in pages_str.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            try:
                start, end = int(a.strip()), int(b.strip())
            except Exception:
                continue
            if start > end:
                start, end = end, start
            for p in range(start, end + 1):
                if 1 <= p <= total_pages:
                    pages.add(p)
        else:
            try:
                p = int(part)
            except Exception:
                continue
            if 1 <= p <= total_pages:
                pages.add(p)
    return sorted(pages)


def extract_text_pdf(path: str, pages_str: str) -> str:
    import fitz  # PyMuPDF

    text_parts: list[str] = []
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


def get_seen_questions_for_session(db: Session, session_id: str) -> set[str]:
    qs = db.execute(select(QcmQuestion).where(QcmQuestion.session_id == session_id)).scalars().all()
    return {_norm(q.question) for q in qs if q.question}


# =========================================================
# Thread spawn
# =========================================================
def spawn_generation(session_id: str, target_index: int) -> None:
    """
    IMPORTANT: génération via thread daemon (pour ne pas bloquer /current).
    """
    t = threading.Thread(
        target=ensure_question_generated,
        args=(session_id, target_index),
        daemon=True,
    )
    t.start()


# =========================================================
# Ownership + expiry
# =========================================================
def get_owned_session(db: Session, user_id: int, session_id: str) -> QcmSession:
    s = db.execute(select(QcmSession).where(QcmSession.id == session_id)).scalar_one_or_none()
    if not s:
        raise HTTPException(404, detail="Session introuvable")
    if s.user_id != user_id:
        raise HTTPException(403, detail="Session non autorisée")

    now = utcnow()
    expires_at = ensure_aware_utc(s.expires_at)
    if expires_at is None:
        raise HTTPException(500, detail="Session invalide (expires_at manquant)")
    if now > expires_at:
        raise HTTPException(410, detail="Session expirée")

    return s


def get_active_session_for_user(db: Session, user_id: int) -> Optional[QcmSession]:
    now = utcnow()
    sessions = db.execute(
        select(QcmSession)
        .where(QcmSession.user_id == user_id)
        .order_by(QcmSession.created_at.desc())
    ).scalars().all()

    for s in sessions:
        if s.status == "done":
            continue
        exp = ensure_aware_utc(s.expires_at)
        if exp and now <= exp:
            return s
    return None


# =========================================================
# Generation core: ensure ONE question exists (index 0-based)
# =========================================================
def ensure_question_generated(session_id: str, target_index: int) -> None:
    """
    Génère UNE question (target_index) si elle n'existe pas.
    - index 0-based en DB (0..QCM_COUNT-1)
    - Idempotent + lock anti-concurrence
    - En cas d'échec: status reste generating + error_message (soft)
    """
    if target_index < 0 or target_index >= QCM_COUNT:
        return

    if not acquire_gen_lock(session_id, target_index):
        return

    db = SessionLocal()
    try:
        session = db.execute(select(QcmSession).where(QcmSession.id == session_id)).scalar_one_or_none()
        if not session:
            return
        if session.status in ("done", "error"):
            return

        # already exists?
        existing = db.execute(
            select(QcmQuestion).where(
                QcmQuestion.session_id == session.id,
                QcmQuestion.index == target_index,
            )
        ).scalar_one_or_none()
        if existing:
            # if current target and session was generating -> ready
            if session.status != "done" and target_index == int(session.current_index or 0):
                session.status = "ready"
                session.error_message = ""
                db.commit()
            return

        # mark generating
        session.status = "generating"
        session.error_message = ""
        db.commit()

        # PDF words (hard failure if impossible)
        try:
            words = get_or_build_source_words(db, session)
        except Exception as e:
            session.status = "error"
            session.error_message = f"Source PDF invalide: {str(e)[:240]}"
            db.commit()
            return

        # dynamic-ish chunk size but bounded
        chunk_size = CHUNK_WORDS

        seen_questions = get_seen_questions_for_session(db, session.id)

        total_tries = 0
        last_err = ""

        for _ in range(MAX_TRIES_PER_QUESTION):
            total_tries += 1
            if total_tries > MAX_TOTAL_TRIES:
                last_err = f"Échec génération: trop de tentatives ({MAX_TOTAL_TRIES})."
                break

            # session may have become done
            db.refresh(session)
            if session.status in ("done", "error"):
                return

            chunk = pick_chunk(words, chunk_size)
            if not chunk:
                last_err = "Texte source vide après découpage"
                time.sleep(0.25)
                continue

            prompt = build_prompt(chunk, session.difficulty)

            try:
                rep = client.chat.completions.create(
                    model=OPENAI_MODEL,
                    messages=[
                        {"role": "system", "content": "Tu respectes strictement le format demandé."},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=QCM_TEMPERATURE,
                    max_tokens=OPENAI_MAX_TOKENS,
                )
                txt = rep.choices[0].message.content or ""
                data = parse_qcm_answer(txt)
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
                    answered=False,
                    user_letter="",
                )
                db.add(q)
                db.commit()

                # if it's the current requested question -> ready
                db.refresh(session)
                if session.status != "done" and target_index == int(session.current_index or 0):
                    session.status = "ready"
                    session.error_message = ""
                    db.commit()

                return

            except Exception as e:
                last_err = str(e)[:250]
                # soft retry
                session.status = "generating"
                session.error_message = f"Retrying: {last_err}"
                db.commit()
                time.sleep(0.35)
                continue

        # If we exit loop: soft failure
        session = db.execute(select(QcmSession).where(QcmSession.id == session_id)).scalar_one_or_none()
        if session and session.status not in ("done", "error"):
            session.status = "generating"
            session.error_message = f"Retrying: {last_err}" if last_err else "Retrying generation..."
            db.commit()

    finally:
        release_gen_lock(session_id, target_index)
        db.close()


# =========================================================
# ROUTES
# =========================================================
@router.post("/start")
def start_qcm(
    payload: dict,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    file_id = int(payload.get("file_id", 0) or 0)
    difficulty = (payload.get("difficulty") or "medium").strip()
    pages = (payload.get("pages") or "").strip()

    if difficulty not in ("easy", "medium", "hard"):
        raise HTTPException(400, detail="difficulty invalide")

    if not file_id:
        raise HTTPException(status_code=400, detail="file_id manquant")

    file = db.execute(select(File).where(File.id == file_id)).scalar_one_or_none()
    if not file:
        raise HTTPException(status_code=404, detail="Fichier introuvable")
    if file.user_id != user.id:
        raise HTTPException(status_code=403, detail="Fichier non autorisé")

    # ✅ Anti double session (renvoie l'actuelle plutôt que créer une nouvelle)
    active = get_active_session_for_user(db, user.id)
    if active is not None:
        return {
            "session_id": active.id,
            "status": active.status,
            "expires_at": ensure_aware_utc(active.expires_at).isoformat() if active.expires_at else None,
            "reuse": True,
        }

    expires_at = utcnow() + timedelta(minutes=SESSION_TTL_MIN)

    session = QcmSession(
        user_id=user.id,
        file_id=file_id,
        difficulty=difficulty,
        pages=pages,
        status="generating",
        current_index=0,  # 0-based
        expires_at=expires_at,
        error_message="",
    )
    db.add(session)
    db.commit()
    db.refresh(session)

    # start generating the current question (index 0)
    spawn_generation(session.id, 0)

    return {
        "session_id": session.id,
        "status": session.status,
        "expires_at": ensure_aware_utc(session.expires_at).isoformat() if session.expires_at else None,
        "reuse": False,
    }


@router.get("/{session_id}/current")
def current(
    session_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        s = get_owned_session(db, user.id, session_id)
    except HTTPException as e:
        return {"status": "error", "detail": e.detail, "code": e.status_code}

    # total questions
    total = QCM_COUNT

    # question index (0-based in DB)
    idx = int(s.current_index or 0)

    q = db.execute(
        select(QcmQuestion).where(
            QcmQuestion.session_id == s.id,
            QcmQuestion.index == idx,
        )
    ).scalar_one_or_none()

    generated_count = db.execute(
        select(QcmQuestion).where(QcmQuestion.session_id == s.id)
    ).scalars().all()
    generated_count = len(generated_count)

    created_at = ensure_aware_utc(s.created_at)
    session_age_sec = (utcnow() - created_at).total_seconds() if created_at else 0.0
    lk_age = lock_age_sec(s.id, idx)

    if s.status == "done":
        return {"status": "done", "total": total}

    if s.status == "error":
        return {"status": "error", "detail": s.error_message or "Erreur génération", "total": total}

    if q:
        if s.status != "done":
            s.status = "ready"
            s.error_message = ""
            db.commit()

        return {
            "status": "ready",
            "index": idx + 1,  # UI 1-based
            "total": total,
            "generated_count": generated_count,
            "question": q.question,
            "choices": [q.choice_a, q.choice_b, q.choice_c, q.choice_d],
            "lock_age_sec": lk_age,
            "session_age_sec": session_age_sec,
        }

    # not ready -> spawn generation
    spawn_generation(s.id, idx)

    # watchdog message if too long and nothing generated
    if session_age_sec > SERVER_GENERATION_WATCHDOG_SEC and generated_count == 0:
        if s.status != "generating":
            s.status = "generating"
        s.error_message = "Génération lente… (retry automatique)"
        db.commit()

    if s.status != "generating":
        s.status = "generating"
        db.commit()

    return {
        "status": "generating",
        "generated_count": generated_count,
        "total": total,
        "detail": s.error_message or "",
        "lock_age_sec": lk_age,
        "session_age_sec": session_age_sec,
    }


@router.get("/{session_id}/next_status")
def next_status(
    session_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    s = get_owned_session(db, user.id, session_id)

    total = QCM_COUNT
    if s.status == "done" or int(s.current_index or 0) >= total - 1:
        return {"status": "done", "total": total}

    next_index = int(s.current_index or 0) + 1

    qnext = db.execute(
        select(QcmQuestion).where(
            QcmQuestion.session_id == s.id,
            QcmQuestion.index == next_index,
        )
    ).scalar_one_or_none()

    generated_count = db.execute(
        select(QcmQuestion).where(QcmQuestion.session_id == s.id)
    ).scalars().all()
    generated_count = len(generated_count)

    if qnext:
        return {
            "status": "ready",
            "next_index": next_index + 1,  # UI 1-based
            "total": total,
            "generated_count": generated_count,
        }

    spawn_generation(s.id, next_index)

    return {
        "status": "generating",
        "next_index": next_index + 1,
        "total": total,
        "generated_count": generated_count,
        "detail": s.error_message or "",
    }


@router.post("/{session_id}/answer")
def answer(
    session_id: str,
    payload: dict,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    s = get_owned_session(db, user.id, session_id)
    if s.status == "done":
        raise HTTPException(400, detail="Session terminée")

    choice_index = int(payload.get("choice_index", -1))
    if choice_index not in (0, 1, 2, 3):
        raise HTTPException(400, detail="choice_index invalide (0..3)")

    idx = int(s.current_index or 0)

    q = db.execute(
        select(QcmQuestion).where(
            QcmQuestion.session_id == s.id,
            QcmQuestion.index == idx,
        )
    ).scalar_one_or_none()

    if not q:
        # not ready -> ensure generation
        spawn_generation(s.id, idx)
        raise HTTPException(409, detail="Question en cours de génération, réessaie.")

    letter = ["A", "B", "C", "D"][choice_index]
    q.answered = True
    q.user_letter = letter
    db.commit()

    correct_index = ["A", "B", "C", "D"].index(q.correct_letter)
    is_correct = (letter == q.correct_letter)

    done = (idx >= QCM_COUNT - 1)

    # keep session status updated
    if done:
        s.status = "done"
        s.error_message = ""
        db.commit()
    else:
        # prefetch next
        spawn_generation(s.id, idx + 1)

    return {
        "status": "answered",
        "index": idx + 1,
        "total": QCM_COUNT,
        "correct_index": correct_index,
        "is_correct": is_correct,
        "explanation": q.explanation,
        "done": done,
    }


@router.post("/{session_id}/next")
def next_q(
    session_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    s = get_owned_session(db, user.id, session_id)

    if s.status == "done":
        return {"status": "done"}

    cur = int(s.current_index or 0)

    # If already last question => done
    if cur >= QCM_COUNT - 1:
        s.status = "done"
        db.commit()
        return {"status": "done"}

    # advance
    s.current_index = cur + 1
    s.status = "generating"
    db.commit()

    # ensure question exists
    spawn_generation(s.id, int(s.current_index))

    # try return immediately if already present
    q = db.execute(
        select(QcmQuestion).where(
            QcmQuestion.session_id == s.id,
            QcmQuestion.index == int(s.current_index),
        )
    ).scalar_one_or_none()

    if q:
        s.status = "ready"
        s.error_message = ""
        db.commit()
        return {
            "status": "ready",
            "index": int(s.current_index) + 1,
            "total": QCM_COUNT,
            "question": q.question,
            "choices": [q.choice_a, q.choice_b, q.choice_c, q.choice_d],
        }

    return {
        "status": "generating",
        "index": int(s.current_index) + 1,
        "total": QCM_COUNT,
        "detail": s.error_message or "",
    }


@router.post("/{session_id}/close")
def close_qcm(
    session_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    s = get_owned_session(db, user.id, session_id)
    s.status = "done"
    db.commit()
    return {"status": "done"}
