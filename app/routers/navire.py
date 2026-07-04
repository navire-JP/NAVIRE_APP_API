"""
app/routers/navire.py
======================
Endpoints NAVIRE : chat, historique des 10 dernières conversations, et
ingestion (admin) des sources.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import User, NavireConversation, NavireChunk, File
from app.routers.auth import get_current_user
from app.core.limits import check_navire_daily_limit
from app.services.navire_chat import send_message
from app.services.navire_ingest import ingest_all_prepa_courses, ingest_all_veille_items, ingest_user_file

router = APIRouter(prefix="/navire", tags=["navire"])

ADMIN_CODE = "THORKISHERE"  # même convention que le reste de l'admin console


# ============================================================
# Schémas
# ============================================================

class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    conversation_id: int | None = None
    mode: str = "chat"  # chat | cas_pratique | note_synthese


class ChatResponse(BaseModel):
    conversation_id: int
    reply: str
    sources: list[dict]


# ============================================================
# Chat
# ============================================================

@router.post("/chat", response_model=ChatResponse)
def chat(
    payload: ChatRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    check_navire_daily_limit(user, db)

    if payload.mode not in ("chat", "cas_pratique", "note_synthese"):
        raise HTTPException(status_code=400, detail="Mode invalide.")

    result = send_message(
        db=db,
        user=user,
        conversation_id=payload.conversation_id,
        user_message=payload.message,
        mode=payload.mode,
    )
    return result


# ============================================================
# Historique — 10 dernières conversations
# ============================================================

@router.get("/conversations")
def list_conversations(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    conversations = (
        db.query(NavireConversation)
        .filter(NavireConversation.user_id == user.id)  # cloisonnement
        .order_by(NavireConversation.updated_at.desc())
        .limit(10)
        .all()
    )
    return [
        {
            "id": c.id,
            "title": c.title,
            "mode": c.mode,
            "updated_at": c.updated_at,
            "message_count": len(c.messages or []),
        }
        for c in conversations
    ]


@router.get("/conversations/{conversation_id}")
def get_conversation(
    conversation_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    conv = (
        db.query(NavireConversation)
        .filter(
            NavireConversation.id == conversation_id,
            NavireConversation.user_id == user.id,  # cloisonnement — jamais d'accès croisé
        )
        .first()
    )
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation introuvable.")

    return {
        "id": conv.id,
        "title": conv.title,
        "mode": conv.mode,
        "messages": conv.messages,
        "created_at": conv.created_at,
        "updated_at": conv.updated_at,
    }


@router.delete("/conversations/{conversation_id}")
def delete_conversation(
    conversation_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    conv = (
        db.query(NavireConversation)
        .filter(
            NavireConversation.id == conversation_id,
            NavireConversation.user_id == user.id,
        )
        .first()
    )
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation introuvable.")
    db.delete(conv)
    db.commit()
    return {"ok": True}


# ============================================================
# Admin — ingestion des sources (à appeler manuellement après ajout
# de contenu, ou brancher sur un cron plus tard)
# ============================================================

@router.post("/admin/ingest/courses")
def admin_ingest_courses(
    db: Session = Depends(get_db),
    x_admin_code: str = Header(...),
):
    if x_admin_code != ADMIN_CODE:
        raise HTTPException(status_code=403, detail="Accès refusé.")
    return ingest_all_prepa_courses(db)


@router.post("/admin/ingest/actus")
def admin_ingest_actus(
    db: Session = Depends(get_db),
    x_admin_code: str = Header(...),
    limit: int | None = None,
):
    if x_admin_code != ADMIN_CODE:
        raise HTTPException(status_code=403, detail="Accès refusé.")
    return ingest_all_veille_items(db, limit=limit)


@router.post("/admin/ingest/user-files")
def admin_ingest_user_files(
    db: Session = Depends(get_db),
    x_admin_code: str = Header(...),
):
    """
    RATTRAPAGE : ingère tous les PDF users déjà présents en base qui n'ont
    pas encore de chunks dans l'index (uploadés avant le déploiement du hook).
    Idempotent : les fichiers déjà indexés sont réingérés proprement
    (suppression des anciens chunks avant réinsertion).
    """
    if x_admin_code != ADMIN_CODE:
        raise HTTPException(status_code=403, detail="Accès refusé.")

    files = db.query(File).all()
    processed = 0
    chunks_total = 0
    errors: list[dict] = []
    for f in files:
        try:
            created = ingest_user_file(db, f)
            chunks_total += created
            processed += 1
        except Exception as e:
            errors.append({"file_id": f.id, "filename": f.filename_original, "error": str(e)})

    return {
        "files_processed": processed,
        "chunks_created": chunks_total,
        "errors": errors,
    }


@router.get("/admin/index-status")
def admin_index_status(
    db: Session = Depends(get_db),
    x_admin_code: str = Header(...),
):
    """
    DIAGNOSTIC : état réel de l'index vectoriel, par type de source.
    Permet de vérifier en un coup d'œil si les ingestions ont bien tourné.
    """
    if x_admin_code != ADMIN_CODE:
        raise HTTPException(status_code=403, detail="Accès refusé.")

    from sqlalchemy import func as sa_func

    rows = (
        db.query(NavireChunk.source_type, sa_func.count(NavireChunk.id))
        .group_by(NavireChunk.source_type)
        .all()
    )
    counts = {source_type: count for source_type, count in rows}
    return {
        "total_chunks": sum(counts.values()),
        "by_source": {
            "cours": counts.get("cours", 0),
            "actu": counts.get("actu", 0),
            "pdf_user": counts.get("pdf_user", 0),
        },
    }