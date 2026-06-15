"""
app/db/prepa_migration.py
=========================
Migration légère PREPASSERELLE, exécutée AU DÉMARRAGE de l'app (lifespan),
à côté de ensure_storage_dirs().

Pourquoi ici plutôt qu'un SQL manuel : le contenu est 100 % idempotent
(IF NOT EXISTS partout), donc rejouable à chaque boot sans aucun risque.
Tu pushes sur GitHub → Render redéploie → les tables/colonnes sont créées.
Aucune commande à lancer à la main.

⚠️ Postgres uniquement. En dev SQLite, la migration est ignorée (en local tu
crées le schéma via Base.metadata.create_all(), qui gère les nouvelles tables).
SQLite ne supporte de toute façon pas "ADD COLUMN IF NOT EXISTS".
"""

from __future__ import annotations

import logging

from sqlalchemy import text

from app.db.database import engine, is_sqlite

logger = logging.getLogger("prepa_migration")


# Pas de BEGIN/COMMIT ici : la transaction est gérée par engine.begin().
# Chaque entrée = un seul statement SQL.
_STATEMENTS: list[str] = [
    # ── Colonnes sur users ───────────────────────────────────
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS prepa_annee VARCHAR(4)",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS prepa_expires_at TIMESTAMPTZ",

    # ── prepa_courses ────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS prepa_courses (
        id            SERIAL       PRIMARY KEY,
        annee         VARCHAR(4)   NOT NULL,
        titre         VARCHAR(200) NOT NULL,
        description   TEXT         NOT NULL DEFAULT '',
        ordre         INTEGER      NOT NULL DEFAULT 0,
        is_published  BOOLEAN      NOT NULL DEFAULT FALSE,
        created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
        updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_prepa_courses_annee ON prepa_courses (annee)",

    # ── prepa_course_files ───────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS prepa_course_files (
        id                 SERIAL       PRIMARY KEY,
        course_id          INTEGER      NOT NULL
                           REFERENCES prepa_courses (id) ON DELETE CASCADE,
        titre              VARCHAR(200) NOT NULL,
        filename_original  VARCHAR(255) NOT NULL,
        filename_stored    VARCHAR(255) NOT NULL,
        path               VARCHAR(500) NOT NULL,
        size_bytes         INTEGER      NOT NULL,
        ordre              INTEGER      NOT NULL DEFAULT 0,
        created_at         TIMESTAMPTZ  NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_prepa_course_files_course_id ON prepa_course_files (course_id)",

    # ── prepa_exercises ──────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS prepa_exercises (
        id                          SERIAL       PRIMARY KEY,
        annee                       VARCHAR(4)   NOT NULL,
        week_number                 INTEGER      NOT NULL,
        titre                       VARCHAR(200) NOT NULL,
        consigne                    TEXT         NOT NULL DEFAULT '',
        subject_filename_original   VARCHAR(255),
        subject_filename_stored     VARCHAR(255),
        subject_path                VARCHAR(500),
        subject_size_bytes          INTEGER,
        corrige_filename_original   VARCHAR(255),
        corrige_filename_stored     VARCHAR(255),
        corrige_path                VARCHAR(500),
        corrige_size_bytes          INTEGER,
        corrige_published           BOOLEAN      NOT NULL DEFAULT FALSE,
        due_date                    TIMESTAMPTZ,
        is_published                BOOLEAN      NOT NULL DEFAULT FALSE,
        created_at                  TIMESTAMPTZ  NOT NULL DEFAULT now(),
        updated_at                  TIMESTAMPTZ  NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_prepa_exercises_annee ON prepa_exercises (annee)",

    # ── prepa_submissions ────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS prepa_submissions (
        id                 SERIAL       PRIMARY KEY,
        user_id            INTEGER      NOT NULL
                           REFERENCES users (id) ON DELETE CASCADE,
        exercise_id        INTEGER      NOT NULL
                           REFERENCES prepa_exercises (id) ON DELETE CASCADE,
        filename_original  VARCHAR(255) NOT NULL,
        filename_stored    VARCHAR(255) NOT NULL,
        path               VARCHAR(500) NOT NULL,
        size_bytes         INTEGER      NOT NULL,
        submitted_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
        status             VARCHAR(20)  NOT NULL DEFAULT 'submitted',
        note               DOUBLE PRECISION,
        feedback           TEXT         NOT NULL DEFAULT '',
        corrected_at       TIMESTAMPTZ
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_prepa_submissions_user_id ON prepa_submissions (user_id)",
    "CREATE INDEX IF NOT EXISTS ix_prepa_submissions_exercise_id ON prepa_submissions (exercise_id)",
]


def run_prepa_migration() -> None:
    """
    Applique la migration PREPASSERELLE. Idempotent : sans effet si déjà appliqué.
    À appeler une fois au démarrage (lifespan), après ensure_storage_dirs().
    Ne lève jamais : en cas d'échec, log l'erreur sans empêcher le boot.
    """
    if is_sqlite:
        logger.info("SQLite détecté : migration Postgres ignorée (dev via create_all).")
        return

    try:
        with engine.begin() as conn:
            for stmt in _STATEMENTS:
                conn.execute(text(stmt))
        logger.info("Migration PREPASSERELLE appliquée (ou déjà à jour).")
    except Exception as exc:  # noqa: BLE001 — on ne veut pas bloquer le démarrage
        logger.exception("Échec de la migration PREPASSERELLE : %s", exc)