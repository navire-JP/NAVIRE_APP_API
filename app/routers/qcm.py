from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import select
from datetime import datetime, timezone, timedelta
import os, random, time
from pathlib import Path

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
# Retry / Validation policy
# -------------------------
MAX_TRIES_PER_QUESTION = 10         # essais max pour une question (format/qualité)
MAX_TOTAL_TRIES = QCM_COUNT * 20    # budget global
MIN_TEXT_LEN = 200
MIN_QUESTION_LEN = 12
MIN_CHOICE_LEN = 1
MIN_EXPLANATION_LEN = 20
DUPLICATE_SIMILARITY_GUARD = True

# -------------------------
# Cache PDF extracted text
# -------------------------
# (sur Render: /tmp est OK ; si tu veux persister, mets sur disque persistant)
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
    lines = [l.strip() for l in txt.split("\n") if l.strip()]

    def pick(prefix):
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


def _norm(s: str) -> str:
    return " ".join((s or "").strip().lower().split())


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
    if len(a) < MIN_CHOICE_LEN or len(b) < MIN_CHOICE_LEN or len(c) < MIN_CHOICE_LEN or len(d) < MIN_CHOICE_LEN:
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
    """
    Extrait le texte PDF UNE FOIS par session et le met en cache (fichier).
    Retourne les words[].
    """
    p = cache_path_for_session(session.id)
    if p.exists():
        txt = p.read_text(encoding="utf-8", errors="ignore").strip()
        words = txt.split()
        return words

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
    seen = set()
    for q in qs:
        seen.add(_norm(q.question))
    return seen


def ensure_question_generated(session_id: str, target_index: int) -> None:
    """
    Génère ET ENREGISTRE une question (target_index) si elle n'existe pas.
    - Retry sur format/qualité/doublon jusqu'à réussite
    - Ne crée JAMAIS une question invalide
    - Met session.status="ready" si la question courante existe
    - Met session.status="error" si échec définitif
    """
    db = SessionLocal()
    try:
        session = db.execute(select(QcmSession).where(QcmSession.id == session_id)).scalar_one()

        # si l'utilisateur a clos
        if session.status == "done":
            return

        # déjà générée ?
        existing = db.execute(
            select(QcmQuestion).where(QcmQuestion.session_id == session.id, QcmQuestion.index == target_index)
        ).scalar_one_or_none()
        if existing:
            # si on a la question courante, on peut passer ready
            if session.status != "done":
                session.status = "ready"
                session.error_message = None
                db.commit()
            return

        # status generating pendant la fabrication
        session.status = "generating"
        session.error_message = None
        db.commit()

        words = get_or_build_source_words(db, session)
        chunk_size = max(200, min(450, max(200, len(words) // 5 if len(words) else 200)))

        seen_questions = get_seen_questions_for_session(db, session.id)

        total_tries = 0
        local_try = 0
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
                    timeout=45,
                )
                txt = rep.choices[0].message.content or ""
                data = parse_qcm_answer(txt)
                validate_qcm_data(data, seen_questions)

                nq = _norm(data["question"])
                seen_questions.add(nq)

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
                time.sleep(0.15)
                continue

        raise ValueError(
            f"Échec génération question index={target_index} après {MAX_TRIES_PER_QUESTION} tentatives."
        )

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
        db.close()


def get_owned_session(db: Session, user_id: int, session_id: str) -> QcmSession:
    s = db.execute(select(QcmSession).where(QcmSession.id == session_id)).scalar_one_or_none()
    if not s:
        raise HTTPException(404, detail="Session introuvable")
    if s.user_id != user_id:
        raise HTTPException(403, detail="Session non autorisée")

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    exp = s.expires_at
    if getattr(exp, "tzinfo", None) is not None:
        exp = exp.replace(tzinfo=None)
    if now > exp:
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

    # ✅ Génère DIRECTEMENT la 1ère question (index 0) pour "QCM instant"
    ensure_question_generated(session.id, 0)

    # on re-check status en base (peut être ready/error)
    db.refresh(session)

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

    # ✅ si la question courante n'existe pas encore -> generating
    q = db.execute(
        select(QcmQuestion).where(QcmQuestion.session_id == s.id, QcmQuestion.index == s.current_index)
    ).scalar_one_or_none()

    # progression (combien de questions déjà générées)
    generated_count = db.execute(
        select(QcmQuestion).where(QcmQuestion.session_id == s.id)
    ).scalars().all()
    gen_n = len(generated_count)

    if not q:
        return {"status": "generating", "generated_count": gen_n, "total": QCM_COUNT}

    return {
        "status": "ready",
        "index": s.current_index + 1,
        "total": QCM_COUNT,
        "generated_count": gen_n,
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
    if s.status == "error":
        raise HTTPException(400, detail=s.error_message or "Erreur génération")
    if s.status == "done":
        raise HTTPException(400, detail="Session terminée")

    choice_index = int(payload.get("choice_index", -1))
    if choice_index not in [0, 1, 2, 3]:
        raise HTTPException(400, detail="choice_index invalide")

    q = db.execute(
        select(QcmQuestion).where(QcmQuestion.session_id == s.id, QcmQuestion.index == s.current_index)
    ).scalar_one_or_none()

    if not q:
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
    bg: BackgroundTasks,
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
    db.commit()

    # ✅ si la question suivante n'existe pas, on la génère en background
    q = db.execute(
        select(QcmQuestion).where(QcmQuestion.session_id == s.id, QcmQuestion.index == s.current_index)
    ).scalar_one_or_none()

    if not q:
        # on passe en generating et on lance la génération
        s.status = "generating"
        s.error_message = None
        db.commit()
        bg.add_task(ensure_question_generated, s.id, s.current_index)
        return {"status": "generating", "index": s.current_index + 1}

    # sinon prêt direct
    s.status = "ready"
    db.commit()
    return {"status": "ok", "index": s.current_index + 1}


@router.post("/{session_id}/close")
def close_session(
    session_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    s = get_owned_session(db, user.id, session_id)

    s.status = "done"
    s.expires_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()

    # cache cleanup (best effort)
    try:
        p = cache_path_for_session(s.id)
        if p.exists():
            p.unlink()
    except:
        pass

    return {"status": "done"}
