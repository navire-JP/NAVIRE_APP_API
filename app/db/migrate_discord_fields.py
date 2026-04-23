# app/db/migrate_discord_fields.py

from sqlalchemy import text
from app.db.database import engine

_MIGRATIONS = [
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS discord_id               VARCHAR(32) UNIQUE",
    "CREATE INDEX  IF NOT EXISTS ix_users_discord_id ON users (discord_id)",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS discord_streak           INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS discord_last_active      DATE",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS discord_messages_pending INTEGER NOT NULL DEFAULT 0",
]


def run_discord_migrations() -> None:
    with engine.begin() as conn:
        for sql in _MIGRATIONS:
            try:
                conn.execute(text(sql))
            except Exception as e:
                print(f"⚠️  migrate_discord [{e.__class__.__name__}]: {sql[:70]}")
    print("✅ migrate_discord_fields : OK")