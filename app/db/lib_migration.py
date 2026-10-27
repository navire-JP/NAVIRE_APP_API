# app/db/lib_migration.py
"""
NAVIRE Lib : colonnes ajoutées après la création des tables (create_all ne
modifie pas une table existante). Idempotent, Postgres comme SQLite.
"""

from sqlalchemy import inspect, text

from app.db.database import engine

_COLONNES = {
    ("lib_documents", "is_plus"): "BOOLEAN NOT NULL DEFAULT FALSE",
    ("lib_progress", "skipped"): "BOOLEAN NOT NULL DEFAULT FALSE",
}


def run_lib_migration() -> None:
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    with engine.begin() as conn:
        for (table, col), ddl in _COLONNES.items():
            if table not in tables or col in {c["name"] for c in insp.get_columns(table)}:
                continue
            try:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))
            except Exception as e:
                print(f"⚠️  lib_migration [{e.__class__.__name__}]: {table}.{col}")
    print("✅ lib_migration : OK")
