"""
app/services/navire_ingest.py
==============================
Découpe et vectorise les sources NAVIRE dans NavireChunk.

Sources ingérées :
  - LibDocument publié (cours NAVIRE Lib)       -> "lib" / "lib_plus" (réservé Lib+), global
  - VeilleItem actu                             -> "actu", global
  - VeilleItem article (doctorants / équipe)    -> "article", global
  - PrepaCourseFile (anciens cours Prépasserelle) -> "cours", global
  - File (PDF perso utilisateur)                -> "pdf_user", cloisonné (owner_user_id=user.id)

Indexation automatique : une actu, un article ou un cours Lib est (ré)indexé
à sa publication, sa modification, et retiré à sa suppression ou dépublication
(reindex_in_background). Au démarrage, backfill_missing() rattrape en tâche de
fond ce qui n'a pas encore de chunks.

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

import threading

from app.db.models import File, LibDocument, NavireChunk, PrepaCourseFile, VeilleItem
from app.routers.qcm import extract_text_pdf  # réutilisation directe

OPENAI_EMBEDDING_MODEL = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
CHUNK_SIZE_WORDS = int(os.getenv("NAVIRE_CHUNK_WORDS", "300"))
CHUNK_OVERLAP_WORDS = int(os.getenv("NAVIRE_CHUNK_OVERLAP", "50"))

_client = OpenAI()


def embed_text(text: str) -> list[float]:
    resp = _client.embeddings.create(model=OPENAI_EMBEDDING_MODEL, input=text)
    return resp.data[0].embedding


def embed_texts(texts: list[str], batch: int = 64) -> list[list[float]]:
    """Vectorise plusieurs textes par appels groupés (bien plus rapide qu'un par un)."""
    out: list[list[float]] = []
    for i in range(0, len(texts), batch):
        part = [t[:8000] for t in texts[i:i + batch]]
        resp = _client.embeddings.create(model=OPENAI_EMBEDDING_MODEL, input=part)
        out.extend(d.embedding for d in sorted(resp.data, key=lambda d: d.index))
    return out


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
    Actu : un seul chunk (titre + essentiel + impact).
    Article : le texte entier, découpé en morceaux précédés du titre.
    """
    _delete_existing_chunks(db, "actu", item.id)
    _delete_existing_chunks(db, "article", item.id)

    author = item.author.username if getattr(item, "author", None) else ""
    extra = {
        "titre": item.title,
        "source_url": item.source_url,
        "source_name": item.source_name,
        "category": item.category,
    }

    if item.kind == "article" and item.body_html:
        from app.services.articles import article_url, html_to_text

        body = html_to_text(item.body_html)
        pieces = _split_into_chunks(body, size=280, overlap=40) or [body]
        texts = [f"Article NAVIRE : {item.title}" + (f" (par {author})" if author else "") + f"\n\n{p}" for p in pieces]
        extra.update({"author": author, "url": article_url(item.id)})
        source_type = "article"
    else:
        content = f"{item.title}\n\n{item.essentiel}\n\n{item.impact}".strip()
        if not content:
            db.commit()
            return 0
        texts = [content]
        source_type = "actu"

    vectors = embed_texts(texts)
    for text, vec in zip(texts, vectors):
        db.add(NavireChunk(
            source_type=source_type, source_id=item.id, owner_user_id=None, annee=None,
            content=text, embedding=vec, extra=extra,
        ))
    db.commit()
    return len(texts)


# ============================================================
# Ingestion : cours NAVIRE Lib (LibDocument)
# ============================================================

LIB_CHUNK_WORDS = int(os.getenv("NAVIRE_LIB_CHUNK_WORDS", "320"))
_HEADINGS = ("h1", "h2", "h3", "h4")


def _lib_indexable(doc: LibDocument) -> bool:
    """Cours de la bibliothèque commune seulement : ni PDF perso, ni Prépa."""
    return bool(
        doc.is_published and doc.owner_id is None and doc.kind not in ("perso", "prepa")
        and (doc.content or {}).get("blocks")
    )


def lib_sections(doc: LibDocument) -> list[tuple[str, str]]:
    """
    Découpe un cours selon ses titres : [(chemin « Partie › Sous-partie », texte)].
    Une section trop longue est coupée en plusieurs morceaux ; chaque morceau
    garde son chemin de titres, pour être compris hors contexte.
    """
    path: dict[str, str] = {}
    sections: list[tuple[str, list[str]]] = []
    current: list[str] = []

    def flush():
        if current:
            trail = " › ".join(path[h] for h in _HEADINGS if path.get(h))
            sections.append((trail, current[:]))
            current.clear()

    for b in (doc.content or {}).get("blocks", []):
        t = b.get("t")
        text = " ".join((b.get("text") or "").split())
        if not text:
            continue
        if t in _HEADINGS:
            flush()
            level = _HEADINGS.index(t)
            path[t] = text[:140]
            for deeper in _HEADINGS[level + 1:]:
                path.pop(deeper, None)
        elif t != "pdp":
            current.append(text)
    flush()

    out: list[tuple[str, str]] = []
    for trail, paras in sections:
        buf: list[str] = []
        words = 0
        for para in paras:
            n = len(para.split())
            if buf and words + n > LIB_CHUNK_WORDS:
                out.append((trail, "\n".join(buf)))
                buf, words = [], 0
            if n > LIB_CHUNK_WORDS * 1.5:   # paragraphe énorme : découpe brute
                out.extend((trail, piece) for piece in _split_into_chunks(para, LIB_CHUNK_WORDS, 40))
                continue
            buf.append(para)
            words += n
        if buf:
            out.append((trail, "\n".join(buf)))
    return out


def ingest_lib_document(db: Session, doc: LibDocument) -> int:
    """(Ré)indexe un cours Lib publié ; le retire de l'index sinon."""
    _delete_existing_chunks(db, "lib", doc.id)
    _delete_existing_chunks(db, "lib_plus", doc.id)
    if not _lib_indexable(doc):
        db.commit()
        return 0

    sections = lib_sections(doc)
    if not sections:
        db.commit()
        return 0
    head = " · ".join(x for x in (doc.titre, doc.matiere, doc.niveau) if x)
    texts = [f"Cours NAVIRE Lib : {head}" + (f"\nSection : {trail}" if trail else "") + f"\n\n{body}" for trail, body in sections]
    vectors = embed_texts(texts)
    source_type = "lib_plus" if doc.is_plus else "lib"
    for (trail, _), text, vec in zip(sections, texts, vectors):
        db.add(NavireChunk(
            source_type=source_type, source_id=doc.id, owner_user_id=None, annee=None,
            content=text, embedding=vec,
            extra={
                "titre": doc.titre, "matiere": doc.matiere, "niveau": doc.niveau,
                "kind": doc.kind, "section": trail, "plus": bool(doc.is_plus),
            },
        ))
    db.commit()
    return len(texts)


def remove_source(db: Session, source_types: tuple[str, ...], source_id: int) -> None:
    for st in source_types:
        _delete_existing_chunks(db, st, source_id)
    db.commit()


# ============================================================
# Indexation en tâche de fond (après publication / modification)
# ============================================================
# Les embeddings prennent quelques secondes pour un long cours : on ne fait
# pas attendre la console ni l'auteur d'un article. Chaque tâche ouvre sa
# propre session ; une erreur est journalisée sans rien casser.

def _run_reindex(kind: str, source_id: int) -> None:
    from app.db.database import SessionLocal

    db = SessionLocal()
    try:
        if kind == "lib":
            doc = db.get(LibDocument, source_id)
            if doc is None:
                remove_source(db, ("lib", "lib_plus"), source_id)
            else:
                ingest_lib_document(db, doc)
        elif kind == "veille":
            item = db.get(VeilleItem, source_id)
            if item is None:
                remove_source(db, ("actu", "article"), source_id)
            else:
                ingest_veille_item(db, item)
    except Exception as e:  # pragma: no cover - journalisé, jamais bloquant
        db.rollback()
        print(f"⚠️ NAVIRE reindex {kind}#{source_id} : {e}")
    finally:
        db.close()


def reindex_in_background(kind: str, source_id: int) -> None:
    """kind : "lib" (LibDocument) | "veille" (actu ou article)."""
    if os.getenv("NAVIRE_INDEX_DISABLED") == "1":   # tests : pas d'appel OpenAI
        return
    if os.getenv("NAVIRE_INDEX_SYNC") == "1":   # tests : exécution immédiate
        _run_reindex(kind, source_id)
        return
    threading.Thread(target=_run_reindex, args=(kind, source_id), daemon=True).start()


def backfill_missing(db: Session, limit_actus: int = 500) -> dict:
    """
    Indexe ce qui n'a encore aucun chunk : cours Lib publiés, articles, actus
    récentes. Idempotent ; appelé au démarrage (tâche de fond) et par la console.
    """
    from sqlalchemy import select

    done = {"lib": 0, "article": 0, "actu": 0, "chunks": 0}

    def indexed(types):
        return set(db.execute(
            select(NavireChunk.source_id).where(NavireChunk.source_type.in_(types)).distinct()
        ).scalars().all())

    have_lib = indexed(("lib", "lib_plus"))
    for doc in db.query(LibDocument).filter(LibDocument.is_published.is_(True), LibDocument.owner_id.is_(None)).all():
        if doc.id not in have_lib and _lib_indexable(doc):
            done["chunks"] += ingest_lib_document(db, doc)
            done["lib"] += 1

    have_veille = indexed(("actu", "article"))
    items = db.query(VeilleItem).order_by(VeilleItem.created_at.desc()).limit(limit_actus).all()
    for item in items:
        if item.id not in have_veille:
            done["chunks"] += ingest_veille_item(db, item)
            done["article" if item.kind == "article" else "actu"] += 1
    return done


def backfill_in_background() -> None:
    def run():
        from app.db.database import SessionLocal

        db = SessionLocal()
        try:
            r = backfill_missing(db)
            print(f"✅ NAVIRE index : rattrapage {r}")
        except Exception as e:  # pragma: no cover
            db.rollback()
            print(f"⚠️ NAVIRE index : rattrapage impossible ({e})")
        finally:
            db.close()

    threading.Thread(target=run, daemon=True).start()


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


def ingest_all_lib_documents(db: Session) -> dict:
    docs = db.query(LibDocument).filter(LibDocument.owner_id.is_(None)).all()
    total = 0
    for d in docs:
        total += ingest_lib_document(db, d)
    return {"documents_processed": len(docs), "chunks_created": total}