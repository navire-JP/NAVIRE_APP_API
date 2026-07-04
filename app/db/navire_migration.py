"""
app/db/navire_migration.py
============================
Migration pgvector pour NAVIRE, en DEUX temps à cause de l'ordre de démarrage :

  1. ensure_vector_extension()  -> CREATE EXTENSION vector
     DOIT tourner AVANT Base.metadata.create_all(), car la table
     navire_chunks déclare une colonne de type `vector` : sans l'extension
     activée, create_all échoue avec "type vector does not exist".

  2. run_navire_migration()     -> CREATE INDEX ivfflat
     DOIT tourner APRÈS create_all(), car l'index porte sur une table qui
     doit déjà exister.

Les deux sont idempotentes (IF NOT EXISTS) — sans risque à chaque redémarrage.
"""

from __future__ import annotations

from sqlalchemy import text

from app.db.database import engine


def ensure_vector_extension() -> None:
    """
    Active l'extension pgvector. À appeler AVANT Base.metadata.create_all().
    """
    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
        conn.commit()
        print("✅ Extension pgvector activée (ou déjà présente)")


def run_navire_migration() -> None:
    """
    Crée l'index ivfflat de similarité cosinus sur navire_chunks.embedding.
    À appeler APRÈS Base.metadata.create_all() (la table doit exister).

    lists=100 : réglage raisonnable pour quelques dizaines de milliers de
    chunks ; à revoir si le volume explose.
    """
    with engine.connect() as conn:
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
            print(f"⚠️ Index ivfflat non créé (table vide ou absente) : {e}")