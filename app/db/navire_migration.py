"""
app/db/navire_migration.py
============================
Active l'extension pgvector sur le Render PG et crée l'index de similarité
sur navire_chunks.embedding.

Suit le même pattern que migrate_discord_fields.py / prepa_migration.py :
une fonction idempotente appelée au démarrage (lifespan de main.py).
"""

from __future__ import annotations

from sqlalchemy import text

from app.db.database import engine


def run_navire_migration() -> None:
    with engine.connect() as conn:
        # 1. Activer l'extension (idempotent — CREATE EXTENSION IF NOT EXISTS)
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
        conn.commit()
        print("✅ Extension pgvector activée (ou déjà présente)")

        # 2. Index ivfflat pour la recherche par similarité cosinus.
        #    Nécessite que la table existe déjà (create_all tourne avant
        #    cet appel dans le lifespan — voir INTEGRATION_NOTES.md).
        #    lists=100 est un réglage raisonnable pour un volume de quelques
        #    dizaines de milliers de chunks ; à revoir si le volume explose.
        try:
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS navire_chunks_embedding_idx
                ON navire_chunks
                USING ivfflat (embedding vector_cosine_ops)
                WITH (lists = 100);
            """))
            conn.commit()
            print("✅ Index ivfflat créé (ou déjà présent) sur navire_chunks.embedding")
        except Exception as e:
            conn.rollback()
            print(f"⚠️ Index ivfflat non créé (normal si table vide ou pas encore migrée) : {e}")