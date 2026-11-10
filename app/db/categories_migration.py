# app/db/categories_migration.py
#
# Catégories de profil gérées depuis la console (meta_options), demandes de
# statut vérifié (status_verifications) et fin du parcours d'accueil.
# Les deux tables sont créées par Base.metadata.create_all ; ici, la seule
# colonne ajoutée à une table existante. SQL idempotent joué au démarrage.

from sqlalchemy import text
from app.db.database import engine

_MIGRATIONS = [
    # Les comptes existants ont déjà découvert NAVIRE : ils sont marqués
    # « accueil terminé » une seule fois, à l'ajout de la colonne (DEFAULT NOW()
    # remplit les lignes existantes), puis le DEFAULT est retiré pour que les
    # nouveaux inscrits partent sans.
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS onboarding_completed_at TIMESTAMPTZ DEFAULT NOW()",
    "ALTER TABLE users ALTER COLUMN onboarding_completed_at DROP DEFAULT",
]


def run_categories_migration() -> None:
    with engine.begin() as conn:
        for sql in _MIGRATIONS:
            try:
                conn.execute(text(sql))
            except Exception as e:
                print(f"⚠️  migrate_categories [{e.__class__.__name__}]: {sql.strip()[:70]}")

    print("✅ categories_migration : OK")
