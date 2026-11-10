# app/db/articles_migration.py
#
# Articles (veille_items.kind = "article") : format, texte mis en forme et
# date de modification. SQL idempotent joué au démarrage, comme les autres
# migrations maison. Les lignes existantes restent des actus.

from sqlalchemy import text
from app.db.database import engine

_MIGRATIONS = [
    "ALTER TABLE veille_items ADD COLUMN IF NOT EXISTS kind VARCHAR(16) NOT NULL DEFAULT 'actu'",
    "ALTER TABLE veille_items ADD COLUMN IF NOT EXISTS body_html TEXT",
    "ALTER TABLE veille_items ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ",
    "CREATE INDEX IF NOT EXISTS ix_veille_items_kind ON veille_items (kind)",
]


def run_articles_migration() -> None:
    with engine.begin() as conn:
        for sql in _MIGRATIONS:
            try:
                conn.execute(text(sql))
            except Exception as e:
                print(f"⚠️  migrate_articles [{e.__class__.__name__}]: {sql.strip()[:70]}")

    print("✅ articles_migration : OK")
