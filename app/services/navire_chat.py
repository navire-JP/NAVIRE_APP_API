"""
app/services/navire_chat.py
=============================
Logique de conversation NAVIRE : retrieval -> prompt strict -> génération ->
mémorisation (rétention 10 conversations / user).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from openai import OpenAI
from sqlalchemy.orm import Session

from app.db.models import NavireConversation, User
from app.services.navire_ingest import embed_text
from app.services.navire_retrieval import search_chunks

OPENAI_MODEL_NAVIRE = os.getenv("OPENAI_MODEL_NAVIRE", os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
MAX_CONVERSATIONS_PER_USER = 10
TOP_K_CHUNKS = 8

_client = OpenAI()


# ============================================================
# Garde-fou : ne répondre QUE depuis les sources retrouvées
# ============================================================

SYSTEM_PROMPT_BASE = """Tu es NAVIRE, l'assistant juridique de l'application MAJOR, destiné aux étudiants en droit français.

RÈGLE ABSOLUE : tu réponds UNIQUEMENT à partir des extraits de sources fournis ci-dessous (cours MAJOR PREPA, actualités juridiques, documents personnels de l'utilisateur). Tu n'utilises AUCUNE connaissance extérieure à ces extraits.

Si les extraits fournis ne permettent pas de répondre à la question, tu le dis explicitement et clairement : tu n'inventes rien, tu ne complètes pas avec tes connaissances générales. Propose à l'utilisateur de reformuler ou d'importer un document pertinent.

Cite toujours la source de ta réponse (ex: "D'après le cours de Droit des obligations, leçon 3" ou "D'après l'actualité du [date]").

Style : direct, structuré, pas de blabla. Tu es un outil de travail, pas un chatbot bavard."""


def _build_context_block(chunks: list) -> str:
    if not chunks:
        return "(Aucun extrait pertinent trouvé dans la base.)"

    parts = []
    for c in chunks:
        label = {
            "cours": f"[COURS — {c.extra.get('cours_titre', '')} / {c.extra.get('lecon_titre', '')}]",
            "actu": f"[ACTUALITÉ — {c.extra.get('titre', '')} ({c.extra.get('source_name', '')})]",
            "pdf_user": f"[VOTRE DOCUMENT — {c.extra.get('filename', '')}]",
        }.get(c.source_type, "[SOURCE]")
        parts.append(f"{label}\n{c.content}")

    return "\n\n---\n\n".join(parts)


def _enforce_conversation_retention(db: Session, user_id: int) -> None:
    """Ne garde que les 10 conversations les plus récentes par utilisateur."""
    conversations = (
        db.query(NavireConversation)
        .filter(NavireConversation.user_id == user_id)
        .order_by(NavireConversation.updated_at.desc())
        .all()
    )
    if len(conversations) > MAX_CONVERSATIONS_PER_USER:
        for old_conv in conversations[MAX_CONVERSATIONS_PER_USER:]:
            db.delete(old_conv)
        db.commit()


def get_or_create_conversation(
    db: Session, user: User, conversation_id: int | None, mode: str = "chat"
) -> NavireConversation:
    if conversation_id:
        conv = (
            db.query(NavireConversation)
            .filter(
                NavireConversation.id == conversation_id,
                NavireConversation.user_id == user.id,  # cloisonnement
            )
            .first()
        )
        if conv:
            return conv

    conv = NavireConversation(user_id=user.id, messages=[], mode=mode, title="")
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return conv


def send_message(
    db: Session,
    user: User,
    conversation_id: int | None,
    user_message: str,
    mode: str = "chat",
) -> dict:
    """
    Point d'entrée principal : reçoit un message, fait le retrieval cloisonné,
    appelle le modèle, sauvegarde, applique la rétention.
    """
    conv = get_or_create_conversation(db, user, conversation_id, mode)

    now_iso = datetime.now(timezone.utc).isoformat()

    # 1. Retrieval cloisonné par user
    query_embedding = embed_text(user_message)
    annee_filter = getattr(user, "prepa_annee", None)
    chunks = search_chunks(
        db,
        query_embedding=query_embedding,
        user_id=user.id,
        top_k=TOP_K_CHUNKS,
        annee_filter=annee_filter,
    )
    context_block = _build_context_block(chunks)

    # 2. Historique récent de CETTE conversation (mémoire courte)
    history_messages = [
        {"role": m["role"], "content": m["content"]}
        for m in (conv.messages or [])[-10:]  # 10 derniers tours de CETTE conv
    ]

    system_prompt = f"{SYSTEM_PROMPT_BASE}\n\n=== EXTRAITS DE SOURCES DISPONIBLES ===\n\n{context_block}"

    messages = [{"role": "system", "content": system_prompt}] + history_messages + [
        {"role": "user", "content": user_message}
    ]

    # 3. Génération
    rep = _client.chat.completions.create(
        model=OPENAI_MODEL_NAVIRE,
        messages=messages,
        temperature=0.3,
        max_tokens=1000,
    )
    assistant_reply = (rep.choices[0].message.content or "").strip()

    # 4. Sauvegarde
    conv.messages = (conv.messages or []) + [
        {"role": "user", "content": user_message, "at": now_iso},
        {"role": "assistant", "content": assistant_reply, "at": datetime.now(timezone.utc).isoformat()},
    ]
    if not conv.title:
        conv.title = user_message[:80]
    db.add(conv)
    db.commit()
    db.refresh(conv)

    # 5. Rétention (purge au-delà de 10 conversations)
    _enforce_conversation_retention(db, user.id)

    sources_cited = [
        {
            "type": c.source_type,
            "label": c.extra.get("titre") or c.extra.get("lecon_titre") or c.extra.get("filename", ""),
        }
        for c in chunks
    ]

    return {
        "conversation_id": conv.id,
        "reply": assistant_reply,
        "sources": sources_cited,
    }