from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import select
from datetime import datetime, timezone, timedelta
import os, random, time

from app.db.database import get_db
from app.db.database import SessionLocal
from app.db.models import User, File, QcmSession, QcmQuestion
from app.routers.auth import get_current_user  # réutilise ton helper
from openai import OpenAI


router = APIRouter(prefix="/qcm", tags=["qcm"])
client = OpenAI()

QCM_COUNT = 5
SESSION_TTL_MIN = 30

# ✅ NEW: generation retry policy
MAX_TRIES_PER_QUESTION = 8          # essais max pour une question
MAX_TOTAL_TRIES = QCM_COUNT * 12    # budget global de tentatives
MIN_TEXT_LEN = 200                  # texte mini pour générer
MIN_QUESTION_LEN = 12               # garde-fou
MIN_CHOICE_LEN = 1
MIN_EXPLANATION_LEN = 20
DUPLICATE_SIMILARITY_GUARD = True   # empêche doublons exacts

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


# =========================================================
# ✅ NEW: Validation stricte + anti doublons
# =========================================================
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

    # anti doublons exacts (ou quasi) sur la question
    if DUPLICATE_SIMILARITY_GUARD:
        nq = _norm(q)
        if nq in seen_questions:
            raise ValueError("Question dupliquée")
        # on ajoute seulement si validée
        # (ajout effectué dans la boucle appelante)


def make_chunks(text: str) -> list[str]:
    words = text.split()
    if not words:
        return []
    chunk_size = max(200, min(450, len(words) // QCM_COUNT if len(words) else 200))
    chunks = []
    for _ in range(QCM_COUNT):
        start = random.randint(0, max(0, len(words) - chunk_size))
        chunks.append(" ".join(words[start : start + chunk_size]))
    return chunks


def pick_chunk(words: list[str], chunk_size: int) -> str:
    if not words:
        return ""
    start = random.randint(0, max(0, len(words) - chunk_size))
    return " ".join(words[start : start + chunk_size])


def generate_session_questions(session_id: str):
    """
    ✅ Garantit:
    - on n'enregistre QUE des questions valides,
    - on ne met READY que si QCM_COUNT questions valides existent,
    - retry sur erreur jusqu'à obtention de 5 questions.
    """
    db = SessionLocal()
    try:
        session = db.execute(
            select(QcmSession).where(QcmSession.id == session_id)
        ).scalar_one()

        # ✅ IMPORTANT: si l'utilisateur a clôturé la session (retour/abandon),
        # on ne génère plus et on n'écrase pas le status
        if session.status == "done":
            return

        file = db.execute(select(File).where(File.id == session.file_id)).scalar_one_or_none()

        if not file:
            session.status = "error"
            session.error_message = "Fichier introuvable"
            db.commit()
            return

        pdf_path = file.path

        try:
            text = extract_text_pdf(pdf_path, session.pages)
            if len(text) < MIN_TEXT_LEN:
                raise ValueError("PDF trop vide ou texte insuffisant")

            words = text.split()
            chunk_size = max(200, min(450, len(words) // QCM_COUNT if len(words) else 200))

            # ✅ si relance/bug: supprimer d'éventuelles questions partielles pour repartir clean
            existing = db.execute(
                select(QcmQuestion).where(QcmQuestion.session_id == session.id)
            ).scalars().all()
            if existing:
                for qx in existing:
                    db.delete(qx)
                db.commit()

            seen_questions: set[str] = set()
            created = 0
            total_tries = 0

            while created < QCM_COUNT:
                # Stop si la session a été clôturée pendant la génération
                db.refresh(session)
                if session.status == "done":
                    return

                if total_tries >= MAX_TOTAL_TRIES:
                    raise ValueError(
                        f"Échec génération: trop de tentatives ({MAX_TOTAL_TRIES})."
                    )

                # On tente de générer la question #created, avec un budget local
                local_try = 0
                ok = False

                while local_try < MAX_TRIES_PER_QUESTION and created < QCM_COUNT:
                    local_try += 1
                    total_tries += 1

                    # Stop si clôturée
                    db.refresh(session)
                    if session.status == "done":
                        return

                    # chunk aléatoire à chaque tentative
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

                        # validation stricte + anti doublons
                        validate_qcm_data(data, seen_questions)

                        nq = _norm(data["question"])
                        seen_questions.add(nq)

                        q = QcmQuestion(
                            session_id=session.id,
                            index=created,  # ✅ index = position réelle (0..4)
                            question=data["question"],
                            choice_a=data["a"],
                            choice_b=data["b"],
                            choice_c=data["c"],
                            choice_d=data["d"],
                            correct_letter=data["good"],
                            explanation=data["exp"],
                        )
                        db.add(q)
                        db.commit()  # ✅ on commit question par question
                        created += 1
                        ok = True
                        break

                    except Exception as gen_e:
                        # On retry: format invalide, validation KO, API timeout, etc.
                        # Petit sleep optionnel anti-rate-limit / anti-spam
                        time.sleep(0.15)
                        continue

                if not ok:
                    # On n'a pas réussi à produire une question valide dans le budget local
                    raise ValueError(
                        f"Échec génération question {created+1}/{QCM_COUNT} après {MAX_TRIES_PER_QUESTION} tentatives."
                    )

            # ✅ Vérif finale: on doit avoir exactement QCM_COUNT
            count_db = db.execute(
                select(QcmQuestion).where(QcmQuestion.session_id == session.id)
            ).scalars().all()
            if len(count_db) != QCM_COUNT:
                raise ValueError(
                    f"Génération incomplète: {len(count_db)}/{QCM_COUNT} questions en base."
                )

            # ✅ Ne pas écraser si la session a été clôturée juste avant la fin
            db.refresh(session)
            if session.status != "done":
                session.status = "ready"
                session.error_message = None
                db.commit()

        except Exception as e:
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

    # SQLite renvoie souvent des datetimes naïfs -> comparer en UTC naïf
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    exp = s.expires_at
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
    if s.status != "ready":
        raise HTTPException(400, detail="Session non prête")

    if s.current_index >= QCM_COUNT - 1:
        s.status = "done"
        db.commit()
        return {"status": "done"}

    s.current_index += 1
    db.commit()
    return {"status": "ok", "index": s.current_index + 1}


@router.post("/{session_id}/close")
def close_session(
    session_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Clôture la session (considérée comme terminée), même si l'utilisateur quitte avant la fin.
    """
    s = get_owned_session(db, user.id, session_id)

    s.status = "done"
    # expirer immédiatement pour éviter tout repolling
    s.expires_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()
    return {"status": "done"}
