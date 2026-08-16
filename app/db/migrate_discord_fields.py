# app/db/migrate_discord_fields.py

from sqlalchemy import text
from app.db.database import engine

_MIGRATIONS = [
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS discord_id               VARCHAR(32) UNIQUE",
    "CREATE INDEX  IF NOT EXISTS ix_users_discord_id ON users (discord_id)",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS discord_streak           INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS discord_last_active      DATE",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS discord_messages_pending INTEGER NOT NULL DEFAULT 0",

    # Deep work — la table elle-même est créée par Base.metadata.create_all au
    # démarrage ; on ajoute ici les index de lecture des statistiques, et les
    # colonnes ajoutées après coup pour les bases déjà en place.
    "ALTER TABLE deep_work_sessions ADD COLUMN IF NOT EXISTS dm_message_id VARCHAR(32) NOT NULL DEFAULT ''",
    "CREATE INDEX IF NOT EXISTS ix_deep_work_sessions_user_status "
    "ON deep_work_sessions (user_id, status)",
    "CREATE INDEX IF NOT EXISTS ix_deep_work_sessions_started_at "
    "ON deep_work_sessions (started_at)",
]


def run_discord_migrations() -> None:
    with engine.begin() as conn:
        for sql in _MIGRATIONS:
            try:
                conn.execute(text(sql))
            except Exception as e:
                print(f"⚠️  migrate_discord [{e.__class__.__name__}]: {sql[:70]}")
    print("✅ migrate_discord_fields : OK")