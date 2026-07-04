"""
app/services/navire_retrieval.py
=================================
Recherche de similarité pour le RAG NAVIRE — pgvector natif (v0.8.1 confirmé
disponible sur le Render PG).

La distance cosinus est calculée directement en SQL via l'opérateur `<=>`
(nécessite l'index ivfflat créé par app/db/navire_migration.py). Beaucoup
plus rapide et scalable que le fallback Python (pas de chargement de tous
les chunks en mémoire à chaque requête).
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.db.models import NavireChunk


def search_chunks(
    db: Session,
    query_embedding: list[float],
    user_id: int,
    top_k: int = 8,
    source_types: Optional[list[str]] = None,
    annee_filter: Optional[str] = None,
) -> list[NavireChunk]:
    """
    Retourne les top_k chunks les plus proches semantiquement, cloisonnes
    par utilisateur.

    GARDE-FOU CONFIDENTIALITE (ne jamais retirer) :
    Le filtre SQL ci-dessous garantit qu'un chunk owner_user_id != NULL
    n'est JAMAIS retourne pour un autre utilisateur que son proprietaire.
    """
    query = db.query(NavireChunk).filter(
        or_(
            NavireChunk.owner_user_id.is_(None),
            NavireChunk.owner_user_id == user_id,
        )
    )

    if source_types:
        query = query.filter(NavireChunk.source_type.in_(source_types))

    if annee_filter:
        query = query.filter(
            or_(NavireChunk.annee.is_(None), NavireChunk.annee == annee_filter)
        )

    # Tri par distance cosinus croissante (plus proche = plus pertinent).
    # L'operateur <=> vient de pgvector et s'appuie sur l'index ivfflat.
    results = (
        query.order_by(NavireChunk.embedding.cosine_distance(query_embedding))
        .limit(top_k)
        .all()
    )
    return results