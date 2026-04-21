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
from app.db.models import User, File, QcmSession, QcmQuestion, QcmSessionHistory
from app.routers.auth import get_current_user

from app.services.elo import compute_qcm_delta, apply_elo_delta
from app.core.limits import check_qcm_daily_limit

router = APIRouter(prefix="/qcm", tags=["qcm"])

# =========================================================
# OpenAI client
# =========================================================
OPENAI_MODEL = os.getenv("OPENAI_MODEL_QCM", os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
OPENAI_TIMEOUT_SEC = float(os.getenv("OPENAI_TIMEOUT_SEC", "25"))
OPENAI_MAX_TOKENS = int(os.getenv("OPENAI_MAX_TOKENS", "750"))
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
MIN_EXPLANATION_LEN = int(os.getenv("QCM_MIN_EXPLANATION_LEN", "120"))

DUPLICATE_SIMILARITY_GUARD = os.getenv("QCM_DUP_GUARD", "1") == "1"
CHUNK_WORDS = int(os.getenv("QCM_CHUNK_WORDS", "380"))
MAX_TRIES_PER_QUESTION = int(os.getenv("QCM_MAX_TRIES_PER_QUESTION", "8"))
MAX_TOTAL_TRIES = int(os.getenv("QCM_MAX_TOTAL_TRIES", "40"))

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
# In-process generation locks
# =========================================================
_GEN_LOCKS: Dict[str, threading.Lock] = {}
_GEN_LOCKS_MUTEX = threading.Lock()


def _lock_for_session(session_id: str) -> threading.Lock:
    with _GEN_LOCKS_MUTEX:
        lk = _GEN_LOCKS.get(session_id)
        if lk is None:
            lk = threading.Lock()
            _GEN_LOCKS[session_id] = lk
        return lk


# =========================================================
# Time helpers
# =========================================================
def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def ensure_aware_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def ttl_deadline() -> datetime:
    return utcnow() - timedelta(minutes=SESSION_TTL_MIN)


# =========================================================
# Text helpers
# =========================================================
def _norm(s: str) -> str:
    return " ".join((s or "").lower().split())


def parse_pages_str(pages_str: str, total_pages: int) -> list[int]:
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

    parts: list[str] = []
    with fitz.open(path) as doc:
        targets = parse_pages_str(pages_str, doc.page_count)
        indices = [p - 1 for p in targets] if targets else list(range(doc.page_count))
        for i in indices:
            try:
                t = doc[i].get_text("text") or ""
            except Exception:
                t = ""
            if t.strip():
                parts.append(t)
    return "\n".join(parts).strip()


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
        return "Niveau: FACILE (notions fondamentales, définitions claires, formulations directes, aucun piège)."
    if difficulty == "hard":
        return (
            "Niveau: DIFFICILE — niveau CRFPA écrit national. "
            "La question doit porter sur une subtilité technique, une exception à une règle, une distinction jurisprudentielle fine ou un conflit de normes. "
            "Les 3 mauvaises réponses doivent être très crédibles : reprendre des formulations exactes du cours, des règles réelles mais mal appliquées, ou des confusions classiques en doctrine. "
            "Un étudiant qui n'a pas approfondi le sujet doit hésiter. Aucune réponse ne doit être évidente."
        )
    return "Niveau: INTERMÉDIAIRE — CRFPA standard. Questions de compréhension et d'application, quelques pièges légers."


def build_prompt(source_text: str, difficulty: str, seen_questions: set[str] | None = None) -> str:
    qcm_type = random.choice(QCM_TYPES)

    # FIX 1B : injecter les questions déjà posées dans le prompt pour forcer GPT
    # à couvrir un angle différent du document
    seen_block = ""
    if seen_questions:
        listed = "\n".join(f"- {q}" for q in list(seen_questions)[:10])
        seen_block = f"""
Questions déjà posées (NE PAS répéter ni paraphraser) :
{listed}
"""

    return f"""
Tu es un examinateur CRFPA. Génère UN QCM à réponse unique à partir de l'extrait.

TYPE: {qcm_type}
{difficulty_block(difficulty)}
{seen_block}
Contraintes:
- 1 seule réponse correcte (A/B/C/D)
- Les 3 autres sont crédibles mais fausses
- Explication OBLIGATOIRE : au moins 2 phrases. Justifie pourquoi la bonne réponse est correcte ET pourquoi chaque mauvaise réponse est fausse. Une explication vide ou d'une seule phrase est INTERDITE.
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


# =========================================================
# Shuffle choices — FIX bonne réponse toujours en A
# =========================================================
def _shuffle_choices(data: dict) -> dict:
    """
    GPT place quasi-systématiquement la bonne réponse en position A.
    Cette fonction mélange les 4 choix aléatoirement APRÈS parsing
    et met à jour correct_letter en conséquence.

    Entrée  : {"a": "...", "b": "...", "c": "...", "d": "...", "good": "A", ...}
    Sortie  : même dict, choix mélangés, "good" mis à jour vers la nouvelle position.
    """
    letters = ["A", "B", "C", "D"]
    choices = [data["a"], data["b"], data["c"], data["d"]]

    # FIX 2C : on track l'index (0-3) au lieu du texte pour éviter
    # toute collision si deux choix ont un libellé identique.
    correct_idx = letters.index(data["good"])

    order = list(range(4))
    random.shuffle(order)

    shuffled = [choices[i] for i in order]
    new_good_idx = order.index(correct_idx)
    new_good_letter = letters[new_good_idx]

    return {
        **data,
        "a": shuffled[0],
        "b": shuffled[1],
        "c": shuffled[2],
        "d": shuffled[3],
        "good": new_good_letter,
    }


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
# Ownership + expiry
# =========================================================
def ensure_session_owner(session: QcmSession, user: User) -> None:
    if session.user_id != user.id:
        raise HTTPException(status_code=403, detail="Forbidden")


def ensure_not_expired(session: QcmSession) -> None:
    exp = ensure_aware_utc(session.expires_at)
    if exp is None:
        raise HTTPException(status_code=500, detail="Session invalide (expires_at manquant)")
    if utcnow() > exp:
        raise HTTPException(status_code=410, detail="Session expirée")


def get_owned_session(db: Session, user_id: int, session_id: str) -> QcmSession:
    s = db.execute(select(QcmSession).where(QcmSession.id == session_id)).scalar_one_or_none()
    if not s:
        raise HTTPException(404, detail="Session introuvable")
    if s.user_id != user_id:
        raise HTTPException(403, detail="Session non autorisée")
    ensure_not_expired(s)
    return s


def get_active_session_for_user(db: Session, user_id: int) -> Optional[QcmSession]:
    sessions = db.execute(
        select(QcmSession)
        .where(QcmSession.user_id == user_id)
        .order_by(QcmSession.created_at.desc())
    ).scalars().all()

    now = utcnow()
    for s in sessions:
        if s.status in ("done", "closed"):
            continue
        exp = ensure_aware_utc(s.expires_at)
        if exp and now <= exp:
            return s
    return None


# =========================================================
# Status helpers
# =========================================================
def generated_count(db: Session, session_id: str) -> int:
    return len(
        db.execute(select(QcmQuestion).where(QcmQuestion.session_id == session_id)).scalars().all()
    )


def get_question_by_index0(db: Session, session_id: str, index0: int) -> Optional[QcmQuestion]:
    return db.execute(
        select(QcmQuestion).where(
            QcmQuestion.session_id == session_id,
            QcmQuestion.index == index0,
        )
    ).scalar_one_or_none()


# =========================================================
# Generation core (thread)
# =========================================================
def _set_session_error(db: Session, session: QcmSession, msg: str, hard: bool = False) -> None:
    session.error_message = (msg or "")[:800]
    if hard:
        session.status = "error"
    else:
        if session.status != "done":
            session.status = "generating"
    db.commit()


def generate_all_questions_for_session(session_id: str) -> None:
    lk = _lock_for_session(session_id)
    if not lk.acquire(blocking=False):
        return

    db = SessionLocal()
    try:
        session = db.execute(select(QcmSession).where(QcmSession.id == session_id)).scalar_one_or_none()
        if not session:
            return

        try:
            ensure_not_expired(session)
        except HTTPException:
            session.status = "done"
            db.commit()
            return

        if session.status in ("done", "error", "closed"):
            return

        session.status = "generating"
        session.error_message = ""
        db.commit()

        try:
            words = get_or_build_source_words(db, session)
        except Exception as e:
            _set_session_error(db, session, f"PDF invalide: {str(e)}", hard=True)
            return

        seen = get_seen_questions_for_session(db, session.id)
        tries_total = 0

        while True:
            session = db.execute(select(QcmSession).where(QcmSession.id == session_id)).scalar_one_or_none()
            if not session:
                return
            if session.status in ("done", "error", "closed"):
                return

            gen = generated_count(db, session.id)
            if gen >= QCM_COUNT:
                session.status = "ready"
                session.error_message = "Questions prêtes."
                db.commit()
                return

            target_index0 = gen
            if get_question_by_index0(db, session.id, target_index0):
                time.sleep(0.05)
                continue

            if tries_total >= MAX_TOTAL_TRIES:
                _set_session_error(db, session, "Génération impossible (trop d'échecs).", hard=True)
                return

            tries_total += 1
            session.status = "generating"
            session.error_message = f"Génération… ({gen}/{QCM_COUNT})"
            db.commit()

            chunk = pick_chunk(words, CHUNK_WORDS)
            if not chunk:
                _set_session_error(db, session, "Texte source vide après extraction", hard=True)
                return

            prompt = build_prompt(chunk, session.difficulty or "medium", seen)

            ok = False
            last_err = ""
            for _ in range(MAX_TRIES_PER_QUESTION):
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
                    content = (rep.choices[0].message.content or "").strip()
                    data = parse_qcm_answer(content)
                    validate_qcm_data(data, seen)

                    # ── Shuffle aléatoire des choix avant écriture en base ──
                    data = _shuffle_choices(data)

                    q = QcmQuestion(
                        session_id=session.id,
                        index=target_index0,
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

                    seen.add(_norm(data["question"]))
                    ok = True
                    break

                except Exception as e:
                    last_err = str(e)[:220]
                    session.error_message = f"Retry… ({gen}/{QCM_COUNT})"
                    db.commit()
                    time.sleep(0.35)

            if not ok:
                session.error_message = f"Retrying: {last_err}" if last_err else "Retrying…"
                db.commit()
                time.sleep(0.35)
                continue

            time.sleep(0.12)

    finally:
        try:
            db.close()
        except Exception:
            pass
        try:
            lk.release()
        except Exception:
            pass


def spawn_generation(session_id: str) -> None:
    t = threading.Thread(target=generate_all_questions_for_session, args=(session_id,), daemon=True)
    t.start()


# =========================================================
# History helpers
# =========================================================

def _get_or_create_history(db: Session, session: QcmSession, file: File) -> QcmSessionHistory:
    existing = db.execute(
        select(QcmSessionHistory).where(QcmSessionHistory.session_id == session.id)
    ).scalar_one_or_none()

    if existing:
        return existing

    history = QcmSessionHistory(
        user_id=session.user_id,
        session_id=session.id,
        file_id=file.id,
        file_name=file.filename_original,
        total_questions=QCM_COUNT,
        correct_answers=0,
        wrong_answers=0,
        success_rate=0.0,
        is_complete=False,
        difficulty=session.difficulty or "medium",
        started_at=utcnow(),
        completed_at=None,
        last_activity_at=utcnow(),
    )
    db.add(history)
    db.commit()
    db.refresh(history)
    return history


def _compute_history_stats(db: Session, session: QcmSession) -> tuple[int, int, float, bool]:
    questions = db.execute(
        select(QcmQuestion).where(QcmQuestion.session_id == session.id)
    ).scalars().all()

    answered = [q for q in questions if q.answered]
    correct = sum(
        1 for q in answered
        if (q.user_letter or "").strip().upper() == (q.correct_letter or "").strip().upper()
    )
    wrong = len(answered) - correct
    total_answered = len(answered)
    rate = round(correct / total_answered, 4) if total_answered > 0 else 0.0
    is_complete = total_answered >= QCM_COUNT

    return correct, wrong, rate, is_complete


def _update_history_on_answer(db: Session, session: QcmSession) -> None:
    """
    Appelé après chaque réponse soumise.
    Met à jour les compteurs en temps réel et, si la session est complète
    (toutes les questions répondues), set is_complete=True et completed_at.
    C'est ce qui permet au streak de fonctionner même quand l'user
    ne passe pas par /close après sa dernière question.
    """
    history = db.execute(
        select(QcmSessionHistory).where(QcmSessionHistory.session_id == session.id)
    ).scalar_one_or_none()

    if not history:
        return

    correct, wrong, rate, is_complete = _compute_history_stats(db, session)

    history.correct_answers = correct
    history.wrong_answers = wrong
    history.success_rate = rate
    history.last_activity_at = utcnow()

    # On ne set completed_at qu'une seule fois (quand on atteint la complétion)
    if is_complete and not history.is_complete:
        history.is_complete = True
        history.completed_at = utcnow()

    db.commit()


def _update_history_on_close(db: Session, session: QcmSession) -> None:
    """
    Appelé sur /close (abandon ou fin explicite).
    Filet de sécurité : met à jour les stats et completed_at si pas encore fait.
    """
    history = db.execute(
        select(QcmSessionHistory).where(QcmSessionHistory.session_id == session.id)
    ).scalar_one_or_none()

    if not history:
        return

    correct, wrong, rate, is_complete = _compute_history_stats(db, session)

    history.correct_answers = correct
    history.wrong_answers = wrong
    history.success_rate = rate
    history.last_activity_at = utcnow()

    # Marquer comme complet si applicable et pas encore fait
    if is_complete and not history.is_complete:
        history.is_complete = True

    # Toujours setter completed_at sur close (même si abandon partiel)
    if not history.completed_at:
        history.completed_at = utcnow()

    db.commit()


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

    if difficulty not in ("easy", "medium", "hard"):
        raise HTTPException(400, detail="difficulty invalide")

    if not file_id:
        raise HTTPException(400, detail="file_id manquant")

    file = db.execute(select(File).where(File.id == file_id)).scalar_one_or_none()
    if not file:
        raise HTTPException(404, detail="Fichier introuvable")
    if file.user_id != user.id:
        raise HTTPException(403, detail="Forbidden")

    # 🔒 Limite de sessions QCM par jour selon le plan (lève 403 si dépassé)
    check_qcm_daily_limit(user, db)

    active = get_active_session_for_user(db, user.id)
    if active is not None:
        return {"session_id": active.id, "status": active.status, "reuse": True}

    expires_at = utcnow() + timedelta(minutes=SESSION_TTL_MIN)

    session = QcmSession(
        user_id=user.id,
        file_id=file_id,
        difficulty=difficulty,
        pages=pages,
        status="generating",
        current_index=0,
        expires_at=expires_at,
        error_message="Démarrage…",
    )
    db.add(session)
    db.commit()
    db.refresh(session)

    # ── Créer l'entrée historique dès le démarrage ──
    _get_or_create_history(db, session, file)

    spawn_generation(session.id)

    return {"session_id": session.id, "status": session.status, "reuse": False}


@router.get("/{session_id}/current")
def current(
    session_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    session = get_owned_session(db, user.id, session_id)

    total = QCM_COUNT
    gen = generated_count(db, session.id)

    if session.status in ("done", "closed"):
        return {"status": "done", "detail": "Terminé.", "total": total}

    if session.status == "error":
        return {"status": "error", "detail": session.error_message or "Erreur génération", "total": total}

    idx0 = int(session.current_index or 0)
    q = get_question_by_index0(db, session.id, idx0)

    if q:
        session.status = "ready"
        session.error_message = ""
        db.commit()
        return {
            "status": "ready",
            "index": idx0 + 1,
            "total": total,
            "question": q.question,
            "choices": [q.choice_a, q.choice_b, q.choice_c, q.choice_d],
            "generated_count": gen,
        }

    spawn_generation(session.id)

    return {
        "status": "generating",
        "detail": session.error_message or f"Génération… ({gen}/{total})",
        "generated_count": gen,
        "total": total,
    }


@router.post("/{session_id}/answer")
def answer(
    session_id: str,
    payload: dict,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    session = get_owned_session(db, user.id, session_id)

    choice_index = int(payload.get("choice_index") if payload.get("choice_index") is not None else -1)
    if choice_index not in (0, 1, 2, 3):
        raise HTTPException(400, detail="choice_index invalide (0..3)")

    total = QCM_COUNT

    if session.status in ("done", "closed"):
        return {"status": "done", "detail": "Terminé.", "total": total}

    idx0 = int(session.current_index or 0)
    q = get_question_by_index0(db, session.id, idx0)
    if not q:
        spawn_generation(session.id)
        raise HTTPException(409, detail="Question pas prête (generating)")

    if q.answered:
        correct_letter = (q.correct_letter or "").strip().upper()
        correct_index = {"A": 0, "B": 1, "C": 2, "D": 3}.get(correct_letter, -1)
        # FIX: utiliser choice_index reçu, pas prev_letter — évite le cas où
        # une réponse en retard arrivait avec un choice_index différent de ce
        # que l'user avait cliqué, retournant un is_correct erroné.
        is_correct = (choice_index == correct_index)

        u = db.execute(select(User).where(User.id == user.id)).scalar_one()
        return {
            "status": "already_answered",
            "index": idx0 + 1,
            "total": total,
            "correct_index": correct_index,
            "is_correct": is_correct,
            "explanation": q.explanation or "",
            "elo_delta": 0,
            "new_elo": int(u.elo or 0),
        }

    correct_letter = (q.correct_letter or "").strip().upper()
    correct_index = {"A": 0, "B": 1, "C": 2, "D": 3}.get(correct_letter, -1)
    is_correct = (choice_index == correct_index)

    q.answered = True
    q.user_letter = ["A", "B", "C", "D"][choice_index]
    db.commit()

    delta = compute_qcm_delta(session.difficulty or "medium", is_correct)

    new_elo = apply_elo_delta(
        db,
        user_id=user.id,
        delta=delta,
        source="qcm",
        session_id=session.id,
        question_index=idx0,
        meta={"difficulty": session.difficulty, "is_correct": is_correct},
    )

    # ── Mise à jour historique après chaque réponse ──
    _update_history_on_answer(db, session)

    spawn_generation(session.id)

    return {
        "status": "answered",
        "index": idx0 + 1,
        "total": total,
        "correct_index": correct_index,
        "is_correct": is_correct,
        "explanation": q.explanation or "",
        "elo_delta": delta,
        "new_elo": new_elo,
    }


@router.get("/{session_id}/next_status")
def next_status(
    session_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    session = get_owned_session(db, user.id, session_id)
    total = QCM_COUNT

    if session.status in ("done", "closed"):
        return {"status": "done", "total": total}

    cur = int(session.current_index or 0)
    nxt = cur + 1

    if nxt >= total:
        return {"status": "done", "total": total}

    qnext = get_question_by_index0(db, session.id, nxt)
    if qnext:
        return {"status": "ready", "index": nxt + 1, "total": total}

    spawn_generation(session.id)
    gen = generated_count(db, session.id)
    return {
        "status": "generating",
        "generated_count": gen,
        "total": total,
        "detail": session.error_message or "Génération…",
    }


@router.post("/{session_id}/next")
def next_question(
    session_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    session = get_owned_session(db, user.id, session_id)
    total = QCM_COUNT

    if session.status in ("done", "closed"):
        return {"status": "done", "detail": "Terminé.", "total": total}

    cur = int(session.current_index or 0)
    nxt = cur + 1

    if nxt >= total:
        session.status = "done"
        session.error_message = ""
        db.commit()
        return {"status": "done", "detail": "Terminé.", "total": total}

    session.current_index = nxt
    session.status = "generating"
    db.commit()

    q = get_question_by_index0(db, session.id, nxt)
    if q:
        session.status = "ready"
        session.error_message = ""
        db.commit()
        return {
            "status": "ready",
            "index": nxt + 1,
            "total": total,
            "question": q.question,
            "choices": [q.choice_a, q.choice_b, q.choice_c, q.choice_d],
        }

    spawn_generation(session.id)
    gen = generated_count(db, session.id)
    return {
        "status": "generating",
        "detail": session.error_message or "Génération…",
        "generated_count": gen,
        "total": total,
    }


@router.post("/{session_id}/close")
def close_session(
    session_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    session = db.execute(select(QcmSession).where(QcmSession.id == session_id)).scalar_one_or_none()
    if not session:
        raise HTTPException(404, detail="Session introuvable")
    ensure_session_owner(session, user)

    # ── Finaliser l'historique avant de fermer ──
    _update_history_on_close(db, session)

    session.status = "closed"
    session.error_message = "Fermée."
    db.commit()

    return {"status": "closed", "detail": "Fermée."}