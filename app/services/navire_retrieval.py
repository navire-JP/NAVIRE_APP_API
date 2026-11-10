"""
app/services/navire_retrieval.py
=================================
Recherche pour le RAG NAVIRE — pgvector natif (v0.8.1 confirmé disponible
sur le Render PG).

Deux recherches combinées :
  - par le sens : distance cosinus en SQL via l'opérateur `<=>` (index
    ivfflat créé par app/db/navire_migration.py) ;
  - par mots exacts : les références qu'un embedding retrouve mal
    (« article 1240 », un numéro de pourvoi « 21-12.345 », une loi
    « 2016-131 ») sont cherchées telles quelles dans le texte.
Les extraits trouvés par mots exacts passent en tête, sans doublon.
"""

from __future__ import annotations

import re
from typing import Optional

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.db.models import NavireChunk


def _visible(
    db: Session,
    user_id: int,
    source_types: Optional[list[str]],
    annee_filter: Optional[str],
    user_file_ids: Optional[list[int]],
):
    """
    Requête de base, cloisonnée par utilisateur.

    GARDE-FOU CONFIDENTIALITE (ne jamais retirer) :
    un chunk owner_user_id != NULL n'est JAMAIS retourné pour un autre
    utilisateur que son propriétaire.
    """
    query = db.query(NavireChunk).filter(
        or_(
            NavireChunk.owner_user_id.is_(None),
            NavireChunk.owner_user_id == user_id,
        )
    )
    if source_types:
        query = query.filter(NavireChunk.source_type.in_(source_types))
    if user_file_ids is not None:
        query = query.filter(
            or_(
                NavireChunk.source_type != "pdf_user",
                NavireChunk.source_id.in_(user_file_ids or [-1]),
            )
        )
    if annee_filter:
        query = query.filter(
            or_(NavireChunk.annee.is_(None), NavireChunk.annee == annee_filter)
        )
    return query


# Références à chercher mot pour mot : numéros d'articles, de pourvois, de lois.
_REF_RE = re.compile(
    r"\b(?:art(?:icle|\.)?\s*)?([LRD]\.?\s?\d{2,4}(?:-\d+)*|\d{2,4}(?:-\d+)+(?:\.\d+)?|\d{3,5})\b",
    re.IGNORECASE,
)


def exact_terms(query: str) -> list[str]:
    """Termes de la question qu'un embedding retrouve mal : on les cherche tels quels."""
    terms: list[str] = []
    for m in _REF_RE.finditer(query or ""):
        t = m.group(1).replace(" ", "")
        if t and t not in terms:
            terms.append(t)
    return terms[:4]


def search_chunks(
    db: Session,
    query_embedding: list[float],
    user_id: int,
    top_k: int = 8,
    source_types: Optional[list[str]] = None,
    annee_filter: Optional[str] = None,
    user_file_ids: Optional[list[int]] = None,
    query_text: str = "",
) -> list[NavireChunk]:
    """
    Retourne jusqu'à top_k extraits : d'abord ceux qui contiennent une
    référence exacte de la question, puis les plus proches par le sens.
    """
    base = lambda: _visible(db, user_id, source_types, annee_filter, user_file_ids)  # noqa: E731

    exact: list[NavireChunk] = []
    terms = exact_terms(query_text)
    if terms:
        conds = [
            # Le terme entouré d'une frontière simple, pour que « 1240 » ne
            # remonte pas « 12400 ».
            and_(NavireChunk.content.ilike(f"%{t}%"))
            for t in terms
        ]
        candidates = base().filter(or_(*conds)).limit(40).all()
        pattern = re.compile(r"(?<![\d\w])(" + "|".join(re.escape(t) for t in terms) + r")(?![\d])", re.IGNORECASE)
        exact = [c for c in candidates if pattern.search(c.content.replace(" ", " "))][: max(2, top_k // 2)]

    semantic = (
        base()
        .order_by(NavireChunk.embedding.cosine_distance(query_embedding))
        .limit(top_k)
        .all()
    )

    seen: set[int] = set()
    results: list[NavireChunk] = []
    for c in exact + semantic:
        if c.id in seen:
            continue
        seen.add(c.id)
        results.append(c)
        if len(results) >= top_k:
            break
    return results
