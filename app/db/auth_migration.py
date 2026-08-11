# app/db/auth_migration.py
#
# Colonnes ajoutées pour la vérification d'adresse email et la connexion
# Google/Facebook. Même principe que les autres migrations maison : du SQL
# idempotent joué au démarrage, sans Alembic.

from sqlalchemy import text
from app.db.database import engine

_MIGRATIONS = [
    # ── Vérification d'adresse ────────────────────────────────────────────
    # La colonne est créée avec DEFAULT TRUE : les comptes existants, antérieurs
    # à la vérification, sont donc marqués vérifiés une seule fois, au moment de
    # l'ajout de la colonne. Le DEFAULT repasse ensuite à FALSE pour que toute
    # nouvelle ligne parte non vérifiée. Faire ce rattrapage avec un UPDATE
    # rejouerait le backfill à chaque démarrage et validerait d'office les
    # comptes en attente de leur code.
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified    BOOLEAN NOT NULL DEFAULT TRUE",
    "ALTER TABLE users ALTER COLUMN email_verified SET DEFAULT FALSE",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified_at TIMESTAMP",

    # ── Connexion via un fournisseur ──────────────────────────────────────
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS oauth_provider VARCHAR(20)",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS oauth_sub      VARCHAR(64)",
    "CREATE INDEX IF NOT EXISTS ix_users_oauth_sub ON users (oauth_sub)",

    # Un compte Google/Facebook n'a pas de mot de passe.
    "ALTER TABLE users ALTER COLUMN password_hash DROP NOT NULL",

    # ── Codes de vérification ─────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS email_verifications (
        id           SERIAL PRIMARY KEY,
        email        VARCHAR(320) NOT NULL UNIQUE,
        code_hash    VARCHAR(255) NOT NULL,
        expires_at   TIMESTAMPTZ  NOT NULL,
        attempts     INTEGER      NOT NULL DEFAULT 0,
        last_sent_at TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
        verified_at  TIMESTAMPTZ,
        created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_email_verifications_email ON email_verifications (email)",
]

def run_auth_migration() -> None:
    with engine.begin() as conn:
        for sql in _MIGRATIONS:
            try:
                conn.execute(text(sql))
            except Exception as e:
                print(f"⚠️  migrate_auth [{e.__class__.__name__}]: {sql.strip()[:70]}")

    print("✅ auth_migration : OK")
