from __future__ import annotations

import random
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select, func

from app.db.database import get_db
from app.db.models import User, File, FlashDeck, FlashCard, FlashStudySession
from app.routers.auth import get_current_user
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

def require_subscription(user: User):
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

    # count cards per deck
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

    return CardOut(
        id=c.id, deck_id=c.deck_id, front=c.front, back=c.back, tags=c.tags,
        source_type=c.source_type, source_file_id=c.source_file_id, source_pages=c.source_pages
    )


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


@router.patch("/cards/{card_id}")
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
    return {"ok": True}


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
        card=CardOut(
            id=first.id, deck_id=first.deck_id, front=first.front, back=first.back, tags=first.tags,
            source_type=first.source_type, source_file_id=first.source_file_id, source_pages=first.source_pages
        )
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

    # stats
    stats = dict(sess.stats_json or {})
    if payload.is_correct:
        stats["correct"] = int(stats.get("correct", 0)) + 1
    else:
        stats["wrong"] = int(stats.get("wrong", 0)) + 1
    sess.stats_json = stats

    # next
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
        card=CardOut(
            id=c.id, deck_id=c.deck_id, front=c.front, back=c.back, tags=c.tags,
            source_type=c.source_type, source_file_id=c.source_file_id, source_pages=c.source_pages
        ),
        stats=stats,
    )


# =========================================================
# Generate from PDF (endpoint V1 stub)
# - on branchera IA + polling juste après (étape suivante)
# =========================================================
@router.post("/decks/{deck_id}/generate-from-pdf")
def generate_from_pdf(
    deck_id: int,
    payload: GenerateFromPdfIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_subscription(user)  # ✅ premium only

    d = deck_owned(db, user, deck_id)

    f = db.execute(select(File).where(File.id == payload.file_id)).scalar_one_or_none()
    if not f:
        raise HTTPException(404, detail="Fichier introuvable.")
    if f.user_id != user.id:
        raise HTTPException(403, detail="Forbidden")
    if f.expires_at is not None:
        # si free: le fichier peut expirer, on empêche si expiré (files.py purge déjà mais safe)
        if f.expires_at < datetime.utcnow():
            raise HTTPException(410, detail="Fichier expiré.")

    # V1: stub -> on renvoie "ok" et on branchera la génération (étape 4)
    # but: tu peux déjà brancher Framer + boutons + droits.
    return {
        "status": "queued",
        "detail": "Job en file (stub). Prochaine étape: génération IA + polling.",
        "deck_id": d.id,
        "file_id": f.id,
        "pages": payload.pages,
        "count": payload.count,
    }