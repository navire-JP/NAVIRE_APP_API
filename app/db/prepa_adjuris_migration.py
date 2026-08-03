"""
app/db/prepa_adjuris_migration.py
==================================
Migration légère Prép'AdJuris, exécutée AU DÉMARRAGE de l'app (lifespan),
même pattern que app/db/prepa_migration.py : statements idempotents
(IF NOT EXISTS partout), rejouables à chaque boot sans risque.

⚠️ Postgres uniquement. En dev SQLite, ignorée (create_all() gère les
nouvelles tables).
"""

from __future__ import annotations

import logging

from sqlalchemy import text

from app.db.database import engine, is_sqlite

logger = logging.getLogger("prepa_adjuris_migration")


_STATEMENTS: list[str] = [
    # ── prepa_adjuris_enrollments ────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS prepa_adjuris_enrollments (
        id                       SERIAL       PRIMARY KEY,
        user_id                  INTEGER      NOT NULL
                                 REFERENCES users (id) ON DELETE CASCADE,
        matiere_key              VARCHAR(64)  NOT NULL,
        stripe_subscription_id   VARCHAR(100) UNIQUE,
        stripe_customer_id       VARCHAR(100),
        stripe_schedule_id       VARCHAR(100),
        status                   VARCHAR(20)  NOT NULL DEFAULT 'active',
        created_at               TIMESTAMPTZ  NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_prepa_adjuris_enrollments_user_id ON prepa_adjuris_enrollments (user_id)",
    "CREATE INDEX IF NOT EXISTS ix_prepa_adjuris_enrollments_matiere_key ON prepa_adjuris_enrollments (matiere_key)",

    # ── discord_link_codes ───────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS discord_link_codes (
        id            SERIAL       PRIMARY KEY,
        user_id       INTEGER      NOT NULL
                      REFERENCES users (id) ON DELETE CASCADE,
        code          VARCHAR(16)  NOT NULL UNIQUE,
        created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
        expires_at    TIMESTAMPTZ  NOT NULL,
        used_at       TIMESTAMPTZ
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_discord_link_codes_user_id ON discord_link_codes (user_id)",
    "CREATE INDEX IF NOT EXISTS ix_discord_link_codes_code ON discord_link_codes (code)",
]


def run_prepa_adjuris_migration() -> None:
    """
    Applique la migration Prép'AdJuris. Idempotent : sans effet si déjà appliqué.
    À appeler une fois au démarrage (lifespan). Ne lève jamais : en cas
    d'échec, log l'erreur sans empêcher le boot.
    """
    if is_sqlite:
        logger.info("SQLite détecté : migration Postgres ignorée (dev via create_all).")
        return

    try:
        with engine.begin() as conn:
            for stmt in _STATEMENTS:
                conn.execute(text(stmt))
        logger.info("Migration Prép'AdJuris appliquée (ou déjà à jour).")
    except Exception as exc:  # noqa: BLE001 — on ne veut pas bloquer le démarrage
        logger.exception("Échec de la migration Prép'AdJuris : %s", exc)
