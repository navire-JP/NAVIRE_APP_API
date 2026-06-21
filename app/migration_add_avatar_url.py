"""
Migration manuelle (si tu n'utilises pas Alembic, exécute ce script une fois
en ligne de commande sur Render via le Shell, ou ajoute la colonne à la main
en SQL — les deux options sont données ci-dessous).

OPTION A — via SQL direct (le plus simple, copie-colle dans le Shell Render
ou un client Postgres connecté à ta base) :

    ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar_url VARCHAR(500);

OPTION B — via ce script Python (utilise SQLAlchemy, lit la même DATABASE_URL
que ton backend) :

    python migration_add_avatar_url.py

Exécute UNE SEULE des deux options, pas les deux.
"""
from __future__ import annotations

from sqlalchemy import text
from app.db.database import engine


def run():
    with engine.connect() as conn:
        conn.execute(text(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar_url VARCHAR(500);"
        ))
        conn.commit()
    print("OK — colonne avatar_url ajoutée (ou déjà présente).")


if __name__ == "__main__":
    run()