"""
app/services/navire_ingest.py
==============================
Découpe et vectorise les sources NAVIRE dans NavireChunk.

Sources ingérées (Phase 1) :
  - PrepaCourseFile (cours L1/L2/L3 + méthodo)  -> global (owner_user_id=NULL)
  - VeilleItem (actus)                          -> global (owner_user_id=NULL)
  - File (PDF perso utilisateur)                -> cloisonné (owner_user_id=user.id)

Volontairement exclus (voir conversation) :
  - PrepaSubmission (copies notées d'élèves) — pas de la doctrine, confidentiel
  - Scores/prix/stats — pas encore stabilisés, à traiter en Phase 2 via
    function-calling SQL direct, pas via vecteurs.

Réutilise client OpenAI + extract_text_pdf du router qcm pour rester cohérent
avec le reste du projet (même modèle, même parsing PyMuPDF).
"""

from __future__ import annotations

import os
from typing import Optional

from openai import OpenAI
from sqlalchemy.orm import Session

from app.db.models import NavireChunk, PrepaCourseFile, VeilleItem, File
from app.routers.qcm import extract_text_pdf  # réutilisation directe

OPENAI_EMBEDDING_MODEL = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
CHUNK_SIZE_WORDS = int(os.getenv("NAVIRE_CHUNK_WORDS", "300"))
CHUNK_OVERLAP_WORDS = int(os.getenv("NAVIRE_CHUNK_OVERLAP", "50"))

_client = OpenAI()


def embed_text(text: str) -> list[float]:
    resp = _client.embeddings.create(model=OPENAI_EMBEDDING_MODEL, input=text)
    return resp.data[0].embedding


def _split_into_chunks(text: str, size: int = CHUNK_SIZE_WORDS, overlap: int = CHUNK_OVERLAP_WORDS) -> list[str]:
    words = text.split()
    if not words:
        return []
    chunks = []
    step = max(size - overlap, 1)
    for start in range(0, len(words), step):
        chunk_words = words[start:start + size]
        if chunk_words:
            chunks.append(" ".join(chunk_words))
        if start + size >= len(words):
            break
    return chunks


def _delete_existing_chunks(db: Session, source_type: str, source_id: int) -> None:
    """Évite les doublons lors d'une réingestion (mise à jour d'un cours/actu)."""
    db.query(NavireChunk).filter(
        NavireChunk.source_type == source_type,
        NavireChunk.source_id == source_id,
    ).delete()


# ============================================================
# Ingestion : cours PREPA
# ============================================================

def ingest_prepa_course_file(db: Session, course_file: PrepaCourseFile) -> int:
    """
    Extrait le texte du PDF de leçon, découpe, vectorise, stocke.
    Retourne le nombre de chunks créés.
    """
    _delete_existing_chunks(db, "cours", course_file.id)

    text = extract_text_pdf(course_file.path, pages_str="")
    if not text.strip():
        return 0

    course = course_file.course  # relation -> annee, titre
    chunks_text = _split_into_chunks(text)

    created = 0
    for chunk_text in chunks_text:
        embedding = embed_text(chunk_text)
        chunk = NavireChunk(
            source_type="cours",
            source_id=course_file.id,
            owner_user_id=None,
            annee=course.annee if course else None,
            content=chunk_text,
            embedding=embedding,
            extra={
                "cours_titre": course.titre if course else "",
                "lecon_titre": course_file.titre,
            },
        )
        db.add(chunk)
        created += 1

    db.commit()
    return created


# ============================================================
# Ingestion : actus (VeilleItem)
# ============================================================

def ingest_veille_item(db: Session, item: VeilleItem) -> int:
    """
    Une actu = un seul chunk (contenu déjà court : title + essentiel + impact).
    """
    _delete_existing_chunks(db, "actu", item.id)

    content = f"{item.title}\n\n{item.essentiel}\n\n{item.impact}"
    if not content.strip():
        return 0

    embedding = embed_text(content)
    chunk = NavireChunk(
        source_type="actu",
        source_id=item.id,
        owner_user_id=None,
        annee=None,
        content=content,
        embedding=embedding,
        extra={
            "titre": item.title,
            "source_url": item.source_url,
            "source_name": item.source_name,
            "category": item.category,
        },
    )
    db.add(chunk)
    db.commit()
    return 1


# ============================================================
# Ingestion : PDF utilisateur (CLOISONNÉ — owner_user_id obligatoire)
# ============================================================

def ingest_user_file(db: Session, file: File) -> int:
    """
    Vectorise un PDF perso. owner_user_id = file.user_id TOUJOURS.
    Ne jamais appeler avec owner_user_id=None pour cette fonction.
    """
    _delete_existing_chunks(db, "pdf_user", file.id)

    text = extract_text_pdf(file.path, pages_str="")
    if not text.strip():
        return 0

    chunks_text = _split_into_chunks(text)
    created = 0
    for chunk_text in chunks_text:
        embedding = embed_text(chunk_text)
        chunk = NavireChunk(
            source_type="pdf_user",
            source_id=file.id,
            owner_user_id=file.user_id,  # <-- cloisonnement, jamais NULL ici
            annee=None,
            content=chunk_text,
            embedding=embedding,
            extra={"filename": file.filename_original},
        )
        db.add(chunk)
        created += 1

    db.commit()
    return created


# ============================================================
# Ingestion en masse (à appeler depuis un endpoint admin)
# ============================================================

def ingest_all_prepa_courses(db: Session) -> dict:
    files = db.query(PrepaCourseFile).all()
    total = 0
    for f in files:
        total += ingest_prepa_course_file(db, f)
    return {"course_files_processed": len(files), "chunks_created": total}


def ingest_all_veille_items(db: Session, limit: Optional[int] = None) -> dict:
    query = db.query(VeilleItem).order_by(VeilleItem.created_at.desc())
    if limit:
        query = query.limit(limit)
    items = query.all()
    total = 0
    for item in items:
        total += ingest_veille_item(db, item)
    return {"items_processed": len(items), "chunks_created": total}