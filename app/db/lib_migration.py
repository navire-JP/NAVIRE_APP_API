# app/db/lib_migration.py
"""
NAVIRE Lib (et classement) : colonnes ajoutées après la création des tables (create_all ne
modifie pas une table existante). Idempotent, Postgres comme SQLite.
"""

from sqlalchemy import inspect, text

from app.db.database import engine

_COLONNES = {
    ("lib_documents", "is_plus"): "BOOLEAN NOT NULL DEFAULT FALSE",
    ("lib_progress", "skipped"): "BOOLEAN NOT NULL DEFAULT FALSE",
    ("lib_documents", "owner_id"): "INTEGER REFERENCES users(id) ON DELETE CASCADE",
    ("lib_documents", "source_file_id"): "INTEGER REFERENCES files(id) ON DELETE CASCADE",
    # Classement (leaderboard.py) : stats visibles par les autres, oui par défaut
    ("users", "stats_public"): "BOOLEAN NOT NULL DEFAULT TRUE",
}

_INDEX = [
    "CREATE INDEX IF NOT EXISTS ix_lib_documents_owner_id ON lib_documents (owner_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_lib_documents_source_file_id ON lib_documents (source_file_id)",
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
    print("✅ lib_migration : OK")
