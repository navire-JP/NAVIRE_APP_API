from __future__ import annotations

import json
import random
import re
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select, func
import openai
import pdfplumber

from app.db.database import get_db
from app.db.models import User, File, FlashDeck, FlashCard, FlashStudySession
from app.routers.auth import get_current_user
from app.core.limits import check_flashcard_limit
from app.core.config import OPENAI_API_KEY
from app.schemas.flash import (
    DeckCreateIn, DeckUpdateIn, DeckOut,
    CardCreateIn, CardUpdateIn, CardOut,
    StudyStartOut, StudyGradeIn, StudyNextOut,
    GenerateFromPdfIn
)

router = APIRouter(prefix="/flash", tags=["flash"])


# =========================================================
# Helpers
# =========================================================
def utcnow():
    return datetime.now(timezone.utc)


def require_membre(user: User):
    """Réservé aux membres (membre ou membre+). Les admins passent toujours."""
    if user.is_admin:
        return
    if (user.plan or "free") == "free":
        raise HTTPException(403, detail="Réservé aux abonnés.")


def deck_owned(db: Session, user: User, deck_id: int) -> FlashDeck:
    d = db.execute(select(FlashDeck).where(FlashDeck.id == deck_id)).scalar_one_or_none()
    if not d:
        raise HTTPException(404, detail="Deck introuvable.")
    if d.user_id != user.id:
        raise HTTPException(403, detail="Forbidden")
    return d


def card_owned(db: Session, user: User, card_id: int) -> FlashCard:
    c = db.execute(select(FlashCard).where(FlashCard.id == card_id)).scalar_one_or_none()
    if not c:
        raise HTTPException(404, detail="Carte introuvable.")
    d = db.execute(select(FlashDeck).where(FlashDeck.id == c.deck_id)).scalar_one_or_none()
    if not d or d.user_id != user.id:
        raise HTTPException(403, detail="Forbidden")
    return c


def card_out(c: FlashCard) -> CardOut:
    return CardOut(
        id=c.id,
        deck_id=c.deck_id,
        front=c.front,
        back=c.back,
        tags=c.tags,
        source_type=c.source_type,
        source_file_id=c.source_file_id,
        source_pages=c.source_pages,
    )


# =========================================================
# Decks
# =========================================================
@router.post("/decks", response_model=DeckOut)
def create_deck(
    payload: DeckCreateIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    d = FlashDeck(user_id=user.id, title=payload.title, description=payload.description or "")
    db.add(d)
    db.commit()
    db.refresh(d)
    return DeckOut(id=d.id, title=d.title, description=d.description, cards_count=0)


@router.get("/decks")
def list_decks(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    decks = db.execute(
        select(FlashDeck).where(FlashDeck.user_id == user.id).order_by(FlashDeck.created_at.desc())
    ).scalars().all()

    deck_ids = [d.id for d in decks]
    counts = {}
    if deck_ids:
        rows = db.execute(
            select(FlashCard.deck_id, func.count(FlashCard.id))
            .where(FlashCard.deck_id.in_(deck_ids))
            .group_by(FlashCard.deck_id)
        ).all()
        counts = {deck_id: int(c) for deck_id, c in rows}

    return {
        "items": [
            {
                "id": d.id,
                "title": d.title,
                "description": d.description,
                "cards_count": counts.get(d.id, 0),
            }
            for d in decks
        ]
    }


@router.get("/decks/{deck_id}")
def get_deck(
    deck_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    d = deck_owned(db, user, deck_id)
    cnt = db.execute(select(func.count(FlashCard.id)).where(FlashCard.deck_id == d.id)).scalar_one()
    return {"id": d.id, "title": d.title, "description": d.description, "cards_count": int(cnt)}


@router.patch("/decks/{deck_id}")
def update_deck(
    deck_id: int,
    payload: DeckUpdateIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    d = deck_owned(db, user, deck_id)
    if payload.title is not None:
        d.title = payload.title
    if payload.description is not None:
        d.description = payload.description
    db.commit()
    return {"ok": True}


@router.delete("/decks/{deck_id}")
def delete_deck(
    deck_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    d = deck_owned(db, user, deck_id)
    db.delete(d)
    db.commit()
    return {"ok": True}


# =========================================================
# Cards
# =========================================================
@router.post("/decks/{deck_id}/cards", response_model=CardOut)
def create_card(
    deck_id: int,
    payload: CardCreateIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    d = deck_owned(db, user, deck_id)

    # 🔒 Quota total de flashcards — délègue à limits.py (lève 403 si dépassé)
    check_flashcard_limit(user, db)

    c = FlashCard(
        deck_id=d.id,
        front=payload.front,
        back=payload.back,
        tags=payload.tags or "",
        source_type="manual",
        source_file_id=None,
        source_pages="",
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return card_out(c)


@router.get("/decks/{deck_id}/cards")
def list_cards(
    deck_id: int,
    q: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    d = deck_owned(db, user, deck_id)

    stmt = select(FlashCard).where(FlashCard.deck_id == d.id).order_by(FlashCard.created_at.desc())
    cards = db.execute(stmt).scalars().all()

    if q:
        qn = q.lower().strip()
        cards = [c for c in cards if qn in (c.front or "").lower() or qn in (c.back or "").lower()]

    return {
        "items": [
            {
                "id": c.id,
                "deck_id": c.deck_id,
                "front": c.front,
                "back": c.back,
                "tags": c.tags,
                "source_type": c.source_type,
                "source_file_id": c.source_file_id,
                "source_pages": c.source_pages,
            }
            for c in cards
        ]
    }


# ---------------------------------------------------------
# routes attendues par le TSX :
#   PATCH /flash/decks/{deck_id}/cards/{card_id}
#   DELETE /flash/decks/{deck_id}/cards/{card_id}
# ---------------------------------------------------------
@router.patch("/decks/{deck_id}/cards/{card_id}", response_model=CardOut)
def update_card_in_deck(
    deck_id: int,
    card_id: int,
    payload: CardUpdateIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    d = deck_owned(db, user, deck_id)
    c = card_owned(db, user, card_id)
    if c.deck_id != d.id:
        raise HTTPException(404, detail="Carte introuvable dans ce deck.")

    if payload.front is not None:
        c.front = payload.front
    if payload.back is not None:
        c.back = payload.back
    if payload.tags is not None:
        c.tags = payload.tags

    db.commit()
    db.refresh(c)
    return card_out(c)


@router.delete("/decks/{deck_id}/cards/{card_id}")
def delete_card_in_deck(
    deck_id: int,
    card_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    d = deck_owned(db, user, deck_id)
    c = card_owned(db, user, card_id)
    if c.deck_id != d.id:
        raise HTTPException(404, detail="Carte introuvable dans ce deck.")

    db.delete(c)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------
# BACKWARD COMPAT : routes existantes conservées
# ---------------------------------------------------------
@router.patch("/cards/{card_id}", response_model=CardOut)
def update_card(
    card_id: int,
    payload: CardUpdateIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    c = card_owned(db, user, card_id)
    if payload.front is not None:
        c.front = payload.front
    if payload.back is not None:
        c.back = payload.back
    if payload.tags is not None:
        c.tags = payload.tags
    db.commit()
    db.refresh(c)
    return card_out(c)


@router.delete("/cards/{card_id}")
def delete_card(
    card_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    c = card_owned(db, user, card_id)
    db.delete(c)
    db.commit()
    return {"ok": True}


# =========================================================
# Study (V1 simple)
# =========================================================
@router.post("/decks/{deck_id}/study/start", response_model=StudyStartOut)
def study_start(
    deck_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    d = deck_owned(db, user, deck_id)

    cards = db.execute(select(FlashCard).where(FlashCard.deck_id == d.id)).scalars().all()
    if not cards:
        raise HTTPException(400, detail="Deck vide.")

    ids = [c.id for c in cards]
    random.shuffle(ids)

    sess = FlashStudySession(
        user_id=user.id,
        deck_id=d.id,
        mode="classic",
        total=len(ids),
        current_index=0,
        order_json={"card_ids": ids},
        stats_json={"correct": 0, "wrong": 0},
        created_at=utcnow(),
        ended_at=None,
    )
    db.add(sess)
    db.commit()
    db.refresh(sess)

    first = db.execute(select(FlashCard).where(FlashCard.id == ids[0])).scalar_one()
    return StudyStartOut(
        session_id=sess.id,
        total=sess.total,
        index=1,
        card=card_out(first),
    )


@router.post("/study/{session_id}/grade", response_model=StudyNextOut)
def study_grade_next(
    session_id: str,
    payload: StudyGradeIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    sess = db.execute(select(FlashStudySession).where(FlashStudySession.id == session_id)).scalar_one_or_none()
    if not sess:
        raise HTTPException(404, detail="Session introuvable.")
    if sess.user_id != user.id:
        raise HTTPException(403, detail="Forbidden")
    if sess.ended_at is not None:
        return StudyNextOut(status="done", total=sess.total, index=sess.total, card=None, stats=sess.stats_json)

    stats = dict(sess.stats_json or {})
    if payload.is_correct:
        stats["correct"] = int(stats.get("correct", 0)) + 1
    else:
        stats["wrong"] = int(stats.get("wrong", 0)) + 1
    sess.stats_json = stats

    ids = (sess.order_json or {}).get("card_ids") or []
    nxt = int(sess.current_index or 0) + 1

    if nxt >= len(ids):
        sess.current_index = len(ids)
        sess.ended_at = utcnow()
        db.commit()
        return StudyNextOut(status="done", total=sess.total, index=sess.total, card=None, stats=stats)

    sess.current_index = nxt
    db.commit()

    c = db.execute(select(FlashCard).where(FlashCard.id == ids[nxt])).scalar_one()
    return StudyNextOut(
        status="ready",
        total=sess.total,
        index=nxt + 1,
        card=card_out(c),
        stats=stats,
    )


# =========================================================
# Generate from PDF — réservé membre/membre+
# =========================================================

def parse_page_range(pages_str: str, max_page: int) -> list[int]:
    """
    Parse une chaîne de pages comme "1-5" ou "1,3,5-7" en liste d'indices (0-based).
    Si vide, retourne toutes les pages.
    """
    if not pages_str or not pages_str.strip():
        return list(range(max_page))
    
    result = set()
    parts = pages_str.replace(" ", "").split(",")
    
    for part in parts:
        if "-" in part:
            try:
                start, end = part.split("-", 1)
                start = int(start) - 1  # 0-based
                end = int(end) - 1
                for i in range(max(0, start), min(end + 1, max_page)):
                    result.add(i)
            except ValueError:
                continue
        else:
            try:
                p = int(part) - 1  # 0-based
                if 0 <= p < max_page:
                    result.add(p)
            except ValueError:
                continue
    
    return sorted(result) if result else list(range(max_page))


def extract_pdf_text(file_path: str, pages: list[int], max_chars: int = 30000) -> str:
    """Extrait le texte des pages spécifiées d'un PDF."""
    text_parts = []
    total_chars = 0
    
    try:
        with pdfplumber.open(file_path) as pdf:
            for page_idx in pages:
                if page_idx >= len(pdf.pages):
                    continue
                page = pdf.pages[page_idx]
                page_text = page.extract_text() or ""
                
                if total_chars + len(page_text) > max_chars:
                    remaining = max_chars - total_chars
                    if remaining > 0:
                        text_parts.append(page_text[:remaining])
                    break
                
                text_parts.append(page_text)
                total_chars += len(page_text)
    except Exception as e:
        raise HTTPException(500, detail=f"Erreur lecture PDF: {str(e)}")
    
    return "\n\n".join(text_parts)


def generate_flashcards_with_ai(text: str, count: int, deck_title: str) -> list[dict]:
    """Appelle GPT pour générer des flashcards à partir du texte."""
    
    if not OPENAI_API_KEY:
        raise HTTPException(500, detail="Clé OpenAI non configurée.")
    
    client = openai.OpenAI(api_key=OPENAI_API_KEY)
    
    prompt = f"""Tu es un assistant pédagogique spécialisé en droit français. 
À partir du texte juridique suivant, génère exactement {count} flashcards de révision.

Contexte du deck : {deck_title}

Règles :
- Chaque carte doit avoir une question (front) claire et précise
- Chaque réponse (back) doit être complète mais concise (2-4 phrases max)
- Privilégie les définitions, articles de loi, principes juridiques, exceptions importantes
- Varie les types de questions : définitions, conditions, effets, exceptions, jurisprudence
- Ajoute 1-3 tags pertinents par carte (mots-clés séparés par des virgules)

Format de réponse STRICTEMENT en JSON (rien d'autre) :
[
  {{"front": "Question 1 ?", "back": "Réponse 1", "tags": "tag1, tag2"}},
  {{"front": "Question 2 ?", "back": "Réponse 2", "tags": "tag1, tag3"}}
]

TEXTE SOURCE :
{text[:25000]}
"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Tu génères des flashcards juridiques en JSON. Réponds UNIQUEMENT avec le JSON, sans commentaire ni markdown."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=3000,
            temperature=0.7,
        )
        
        content = response.choices[0].message.content.strip()
        
        # Nettoyer le JSON (enlever ```json si présent)
        content = re.sub(r'^```json\s*', '', content)
        content = re.sub(r'\s*```$', '', content)
        content = content.strip()
        
        cards = json.loads(content)
        
        if not isinstance(cards, list):
            raise ValueError("La réponse n'est pas une liste")
        
        # Valider et nettoyer chaque carte
        valid_cards = []
        for card in cards:
            if isinstance(card, dict) and "front" in card and "back" in card:
                valid_cards.append({
                    "front": str(card.get("front", "")).strip(),
                    "back": str(card.get("back", "")).strip(),
                    "tags": str(card.get("tags", "")).strip(),
                })
        
        return valid_cards[:count]  # Limiter au nombre demandé
        
    except json.JSONDecodeError as e:
        raise HTTPException(500, detail=f"Erreur parsing réponse IA: {str(e)}")
    except openai.APIError as e:
        raise HTTPException(500, detail=f"Erreur API OpenAI: {str(e)}")
    except Exception as e:
        raise HTTPException(500, detail=f"Erreur génération: {str(e)}")


@router.post("/decks/{deck_id}/generate-from-pdf")
def generate_from_pdf(
    deck_id: int,
    payload: GenerateFromPdfIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # 🔒 Génération IA réservée aux abonnés
    require_membre(user)

    d = deck_owned(db, user, deck_id)

    f = db.execute(select(File).where(File.id == payload.file_id)).scalar_one_or_none()
    if not f:
        raise HTTPException(404, detail="Fichier introuvable.")
    if f.user_id != user.id:
        raise HTTPException(403, detail="Forbidden")

    # Vérification expiration timezone-aware
    if f.expires_at is not None:
        try:
            exp = f.expires_at
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if exp < utcnow():
                raise HTTPException(410, detail="Fichier expiré.")
        except TypeError:
            if f.expires_at < datetime.utcnow():
                raise HTTPException(410, detail="Fichier expiré.")

    # Vérifier que le fichier existe sur le disque
    file_path = Path(f.path)
    if not file_path.exists():
        raise HTTPException(404, detail="Fichier non trouvé sur le serveur.")

    # Extraire le texte du PDF
    with pdfplumber.open(str(file_path)) as pdf:
        total_pages = len(pdf.pages)
    
    page_indices = parse_page_range(payload.pages or "", total_pages)
    
    if not page_indices:
        raise HTTPException(400, detail="Aucune page valide sélectionnée.")
    
    text = extract_pdf_text(str(file_path), page_indices)
    
    if len(text.strip()) < 100:
        raise HTTPException(400, detail="Pas assez de texte extrait du PDF.")

    # Générer les flashcards avec l'IA
    count = min(payload.count or 10, 30)  # Max 30 cartes par génération
    generated = generate_flashcards_with_ai(text, count, d.title)
    
    if not generated:
        raise HTTPException(500, detail="Aucune carte générée par l'IA.")

    # Insérer les cartes en DB
    created_cards = []
    for card_data in generated:
        # Vérifier quota avant chaque insertion
        try:
            check_flashcard_limit(user, db)
        except HTTPException:
            # Quota atteint, on arrête d'insérer
            break
        
        c = FlashCard(
            deck_id=d.id,
            front=card_data["front"],
            back=card_data["back"],
            tags=card_data["tags"],
            source_type="pdf",
            source_file_id=f.id,
            source_pages=payload.pages or "all",
        )
        db.add(c)
        created_cards.append(c)
    
    db.commit()
    
    # Refresh pour avoir les IDs
    for c in created_cards:
        db.refresh(c)

    return {
        "status": "completed",
        "deck_id": d.id,
        "file_id": f.id,
        "pages_processed": len(page_indices),
        "cards_created": len(created_cards),
        "cards": [
            {
                "id": c.id,
                "front": c.front,
                "back": c.back,
                "tags": c.tags,
            }
            for c in created_cards
        ],
    }