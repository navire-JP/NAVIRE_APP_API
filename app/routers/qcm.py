from __future__ import annotations

import os
import random
import time
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Tuple, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select

from openai import OpenAI

from app.db.database import get_db, SessionLocal
from app.db.models import User, File, QcmSession, QcmQuestion
from app.routers.auth import get_current_user


router = APIRouter(prefix="/qcm", tags=["qcm"])

# OpenAI client (sync)
client = OpenAI()

# -------------------------
# Session policy
# -------------------------
QCM_COUNT = int(os.getenv("QCM_COUNT", "5"))
SESSION_TTL_MIN = int(os.getenv("QCM_SESSION_TTL_MIN", "30"))

# -------------------------
# Validation policy
# -------------------------
MIN_TEXT_LEN = int(os.getenv("QCM_MIN_TEXT_LEN", "700"))
MIN_QUESTION_LEN = int(os.getenv("QCM_MIN_QUESTION_LEN", "25"))
MIN_CHOICE_LEN = int(os.getenv("QCM_MIN_CHOICE_LEN", "8"))
MIN_EXPLANATION_LEN = int(os.getenv("QCM_MIN_EXPLANATION_LEN", "40"))

DUPLICATE_SIMILARITY_GUARD = os.getenv("QCM_DUP_GUARD", "1") == "1"

# Retry policy
MAX_TRIES_PER_QUESTION = int(os.getenv("QCM_MAX_TRIES_PER_QUESTION", "6"))
MAX_TOTAL_TRIES = int(os.getenv("QCM_MAX_TOTAL_TRIES", "30"))

# -------------------------
# Watchdog (server-side)
# IMPORTANT: on ne passe plus en "error" juste parce que c'est long.
# On relance si un lock est trop vieux (worker mort / exception silencieuse).
# -------------------------
SERVER_GENERATION_WATCHDOG_SEC = int(os.getenv("QCM_WATCHDOG_SEC", "240"))

# -------------------------
# In-process generation locks (anti-concurrent)
# key = (session_id, target_index) -> started_at_monotonic
# -------------------------
GEN_LOCKS: Dict[Tuple[str, int], float] = {}
GEN_LOCK_TTL_SEC = float(os.getenv("QCM_GEN_LOCK_TTL_SEC", "180.0"))


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


def lock_age_sec(session_id: str, target_index: int) -> Optional[float]:
    ts = GEN_LOCKS.get(_lock_key(session_id, target_index))
    if ts is None:
        return None
    return time.monotonic() - ts


def force_release_lock_if_stale(session_id: str, target_index: int) -> bool:
    """
    Si un lock traîne trop longtemps (thread mort, crash, etc.),
    on le libère pour permettre une relance.
    """
    age = lock_age_sec(session_id, target_index)
    if age is None:
        return False
    if age > SERVER_GENERATION_WATCHDOG_SEC:
        release_gen_lock(session_id, target_index)
        return True
    return False


def _norm(s: str) -> str:
    return " ".join((s or "").strip().lower().split())


# -------------------------
# Cache PDF extracted text
# -------------------------
CACHE_DIR = Path(os.getenv("NAVIRE_QCM_CACHE_DIR", "/tmp/navire_qcm_cache"))
CACHE_DIR.mkdir(parents=True, exist_ok=True)

QCM_TYPES = [
    "K1 - Définition stricte",
    "K2 - Fondement juridique",
    "C1 - Portée d'une règle",
    "R1 - Mini cas pratique",
    "D2 - Exception à une règle",
    "T1 - Vocabulaire juridique",
]


def parse_pages_str(pages_str: str, total_pages: int) -> list[int]:
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
            except Exception:
                continue
        else:
            try:
                p = int(part)
                if 1 <= p <= total_pages:
                    pages.add(p)
            except Exception:
                continue
    return sorted(pages)


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
    return " ".join(words[start : start + chunk_size])


def get_seen_questions_for_session(db: Session, session_id: str) -> set[str]:
    qs = db.execute(select(QcmQuestion).where(QcmQuestion.session_id == session_id)).scalars().all()
    return {_norm(q.question) for q in qs if q.question}


def _openai_timeout_sec() -> float:
    # timeout “dur” par call OpenAI (évite thread bloqué indéfiniment)
    return float(os.getenv("OPENAI_TIMEOUT_SEC", "25"))


def ensure_question_generated(session_id: str, target_index: int) -> None:
    """
    Génère UNE question (target_index) si elle n'existe pas.
    Idempotent + lock anti-concurrent.

    IMPORTANT:
    - Ne met PAS la session en "error" pour des erreurs transitoires OpenAI.
    - Ne met en "error" que si on a réellement épuisé les tentatives.
    """
    if not acquire_gen_lock(session_id, target_index):
        return

    db = SessionLocal()
    last_err: str = ""
    try:
        session = db.execute(select(QcmSession).where(QcmSession.id == session_id)).scalar_one_or_none()
        if not session:
            return
        if session.status == "done":
            return

        existing = db.execute(
            select(QcmQuestion).where(
                QcmQuestion.session_id == session.id,
                QcmQuestion.index == target_index,
            )
        ).scalar_one_or_none()

        if existing:
            if session.status != "done":
                session.status = "ready"
                session.error_message = ""
                db.commit()
            return

        # On marque generating le temps de produire
        session.status = "generating"
        session.error_message = ""
        db.commit()

        words = get_or_build_source_words(db, session)
        chunk_size = max(220, min(520, max(220, (len(words) // 5) if len(words) else 220)))
        seen_questions = get_seen_questions_for_session(db, session.id)

        total_tries = 0
        for local_try in range(MAX_TRIES_PER_QUESTION):
            total_tries += 1
            if total_tries > MAX_TOTAL_TRIES:
                last_err = f"Échec génération: trop de tentatives ({MAX_TOTAL_TRIES})."
                break

            db.refresh(session)
            if session.status == "done":
                return

            chunk = pick_chunk(words, chunk_size)
            if not chunk:
                last_err = "Texte source vide après découpage"
                time.sleep(0.35)
                continue

            prompt = build_prompt(chunk, session.difficulty)

            try:
                rep = client.chat.completions.create(
                    model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                    messages=[{"role": "user", "content": prompt}],
                    # un peu de stabilité
                    temperature=float(os.getenv("QCM_TEMPERATURE", "0.2")),
                    # timeout dur
                    timeout=_openai_timeout_sec(),
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
                )
                db.add(q)
                db.commit()

                db.refresh(session)
                if session.status != "done":
                    session.status = "ready"
                    session.error_message = ""
                    db.commit()
                return

            except Exception as e:
                # erreurs transitoires => on réessaie
                last_err = str(e)[:300] if str(e) else "Erreur OpenAI/parse/validation"
                time.sleep(0.45)
                continue

        # Si on arrive ici => échec définitif pour cette question
        if session.status != "done":
            session.status = "error"
            session.error_message = (
                f"Impossible de générer une question valide (index={target_index}). "
                f"Dernière erreur: {last_err or 'inconnue'}"
            )
            db.commit()

    except Exception as e:
        try:
            session = db.execute(select(QcmSession).where(QcmSession.id == session_id)).scalar_one_or_none()
            if session and session.status != "done":
                session.status = "error"
                session.error_message = str(e)[:300] if str(e) else "Erreur interne"
                db.commit()
        except Exception:
            pass
    finally:
        release_gen_lock(session_id, target_index)
        db.close()


def spawn_generation(session_id: str, target_index: int) -> None:
    """
    Lance la génération dans un thread daemon (non bloquant),
    et ne dépend plus de BackgroundTasks (évite freeze / fetch pending).
    """
    t = threading.Thread(
        target=ensure_question_generated,
        args=(session_id, target_index),
        daemon=True,
    )
    t.start()


def get_owned_session(db: Session, user_id: int, session_id: str) -> QcmSession:
    s = db.execute(select(QcmSession).where(QcmSession.id == session_id)).scalar_one_or_none()
    if not s:
        raise HTTPException(404, detail="Session introuvable")
    if s.user_id != user_id:
        raise HTTPException(403, detail="Session non autorisée")

    now = datetime.now(timezone.utc)
    if now > s.expires_at:
        raise HTTPException(410, detail="Session expirée")
    return s


@router.post("/start")
def start_qcm(
    payload: dict,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    file_id = int(payload.get("file_id", 0))
    difficulty = (payload.get("difficulty") or "medium").strip()
    pages = (payload.get("pages") or "").strip()

    if difficulty not in ["easy", "medium", "hard"]:
        raise HTTPException(400, detail="difficulty invalide")

    file = db.execute(select(File).where(File.id == file_id)).scalar_one_or_none()
    if not file or file.user_id != user.id:
        raise HTTPException(403, detail="Fichier non autorisé")

    expires_at = datetime.now(timezone.utc) + timedelta(minutes=SESSION_TTL_MIN)

    session = QcmSession(
        user_id=user.id,
        file_id=file_id,
        difficulty=difficulty,
        pages=pages,
        status="generating",
        current_index=0,
        expires_at=expires_at,
        error_message="",
    )
    db.add(session)
    db.commit()
    db.refresh(session)

    # ✅ génération question 0 => thread daemon
    spawn_generation(session.id, 0)

    return {
        "session_id": session.id,
        "status": session.status,
        "expires_at": session.expires_at.isoformat(),
    }


@router.get("/{session_id}/current")
def current(
    session_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    s = get_owned_session(db, user.id, session_id)

    if s.status == "error":
        return {"status": "error", "detail": s.error_message}

    if s.status == "done":
        return {"status": "done"}

    # question courante
    q = db.execute(
        select(QcmQuestion).where(
            QcmQuestion.session_id == s.id,
            QcmQuestion.index == s.current_index,
        )
    ).scalar_one_or_none()

    generated_count = db.execute(
        select(QcmQuestion).where(QcmQuestion.session_id == s.id)
    ).scalars().all()
    generated_count = len(generated_count)

    if q:
        if s.status != "done":
            s.status = "ready"
            s.error_message = ""
            db.commit()

        return {
            "status": "ready",
            "index": s.current_index + 1,
            "total": QCM_COUNT,
            "generated_count": generated_count,
            "question": q.question,
            "choices": [q.choice_a, q.choice_b, q.choice_c, q.choice_d],
        }

    # pas de question => génération on-demand
    # ✅ Si lock vieux => on le libère et on relance (évite "bloquée")
    force_release_lock_if_stale(s.id, s.current_index)
    spawn_generation(s.id, s.current_index)

    # on ne force plus "error" juste parce que c'est long :
    # on renvoie generating + info lock age pour debug
    age = lock_age_sec(s.id, s.current_index)

    if s.status != "generating":
        s.status = "generating"
        s.error_message = ""
        db.commit()

    return {
        "status": "generating",
        "generated_count": generated_count,
        "total": QCM_COUNT,
        "lock_age_sec": age,
    }


@router.post("/{session_id}/answer")
def answer(
    session_id: str,
    payload: dict,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    s = get_owned_session(db, user.id, session_id)
    if s.status == "error":
        raise HTTPException(400, detail=s.error_message or "Erreur génération")
    if s.status == "done":
        raise HTTPException(400, detail="Session terminée")

    choice_index = int(payload.get("choice_index", -1))
    if choice_index not in [0, 1, 2, 3]:
        raise HTTPException(400, detail="choice_index invalide")

    q = db.execute(
        select(QcmQuestion).where(
            QcmQuestion.session_id == s.id,
            QcmQuestion.index == s.current_index,
        )
    ).scalar_one_or_none()

    if not q:
        # pas encore prête -> client doit repoll /current
        raise HTTPException(409, detail="Question en cours de génération")

    letter = ["A", "B", "C", "D"][choice_index]
    q.answered = True
    q.user_letter = letter
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
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    s = get_owned_session(db, user.id, session_id)

    if s.status == "error":
        return {"status": "error", "detail": s.error_message}
    if s.status == "done":
        return {"status": "done"}

    if s.current_index >= QCM_COUNT - 1:
        s.status = "done"
        db.commit()
        return {"status": "done"}

    # avance l'index
    s.current_index += 1
    s.status = "generating"
    s.error_message = ""
    db.commit()

    # ✅ génération de la prochaine question => thread daemon
    spawn_generation(s.id, s.current_index)

    return {"status": "generating"}


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
