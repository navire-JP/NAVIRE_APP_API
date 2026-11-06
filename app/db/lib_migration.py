# app/db/lib_migration.py
"""
NAVIRE Lib (et classement) : colonnes ajoutées après la création des tables (create_all ne
modifie pas une table existante). Idempotent, Postgres comme SQLite.
"""

import os

from sqlalchemy import inspect, text

from app.db.database import engine

_COLONNES = {
    ("lib_documents", "is_plus"): "BOOLEAN NOT NULL DEFAULT FALSE",
    ("lib_progress", "skipped"): "BOOLEAN NOT NULL DEFAULT FALSE",
    ("lib_documents", "owner_id"): "INTEGER REFERENCES users(id) ON DELETE CASCADE",
    ("lib_documents", "source_file_id"): "INTEGER REFERENCES files(id) ON DELETE CASCADE",
    ("lib_documents", "prepa_ressource_id"): "INTEGER REFERENCES prepa_adjuris_ressources(id) ON DELETE CASCADE",
    # Classement (leaderboard.py) : stats visibles par les autres, oui par défaut
    ("users", "stats_public"): "BOOLEAN NOT NULL DEFAULT TRUE",
    # Page profil : bannière, bio, LinkedIn
    ("users", "banner_url"): "VARCHAR(500)",
    ("users", "bio"): "VARCHAR(160)",
    ("users", "linkedin_url"): "VARCHAR(300)",
    # Veille : qui a posté l'actu
    ("veille_items", "author_id"): "INTEGER REFERENCES users(id) ON DELETE SET NULL",
}

_INDEX = [
    "CREATE INDEX IF NOT EXISTS ix_lib_documents_owner_id ON lib_documents (owner_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_lib_documents_source_file_id ON lib_documents (source_file_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_lib_documents_prepa_ressource_id ON lib_documents (prepa_ressource_id)",
]


def _run(sql: str) -> None:
    # Une transaction par instruction : en Postgres, un échec annule la transaction.
    try:
        with engine.begin() as conn:
            conn.execute(text(sql))
    except Exception as e:
        print(f"⚠️  lib_migration [{e.__class__.__name__}]: {sql[:70]}")


def run_lib_migration() -> None:
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    for (table, col), ddl in _COLONNES.items():
        if table in tables and col not in {c["name"] for c in insp.get_columns(table)}:
            _run(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
    if "lib_documents" in tables:
        for sql in _INDEX:
            _run(sql)
    if "veille_items" in tables and "users" in tables:
        _auteur_des_anciennes_actus()
    print("✅ lib_migration : OK")


def _auteur_des_anciennes_actus() -> None:
    """Actus postées à la main avant le champ author_id : elles ont forcément été
    publiées par un admin (seules routes de création). On les rattache au compte
    VEILLE_DEFAULT_AUTHOR_EMAIL s'il est défini, sinon au premier admin créé.
    Idempotent : ne touche que les actus encore sans auteur."""
    email = os.getenv("VEILLE_DEFAULT_AUTHOR_EMAIL", "").strip().lower()
    try:
        with engine.begin() as conn:
            if email:
                uid = conn.execute(
                    text("SELECT id FROM users WHERE lower(email) = :e"), {"e": email}
                ).scalar()
            else:
                uid = conn.execute(
                    text("SELECT id FROM users WHERE is_admin = :t ORDER BY id LIMIT 1"), {"t": True}
                ).scalar()
            if uid is None:
                return
            n = conn.execute(
                text(
                    "UPDATE veille_items SET author_id = :uid "
                    "WHERE author_id IS NULL AND external_id LIKE 'manual:%'"
                ),
                {"uid": uid},
            ).rowcount
            if n:
                print(f"✅ lib_migration : {n} actu(s) rattachée(s) à l'auteur #{uid}")
    except Exception as e:
        print(f"⚠️  lib_migration [auteur des actus] {e.__class__.__name__}: {e}")
