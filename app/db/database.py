import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

# ─────────────────────────────────────────────────────────────
# DATABASE_URL
# En production (Render) : variable d'environnement PostgreSQL
# En dev local           : SQLite fallback
# ─────────────────────────────────────────────────────────────
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./app.db")

# Render fournit parfois l'URL avec le préfixe "postgres://" (ancien format)
# SQLAlchemy 2.x exige "postgresql://"
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Les connect_args check_same_thread sont spécifiques à SQLite
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

is_sqlite = DATABASE_URL.startswith("sqlite")

engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
    # pool_pre_ping : teste la connexion avant chaque requête
    # évite les erreurs "connexion expirée" après inactivité sur Render/PostgreSQL
    pool_pre_ping=not is_sqlite,
    # pool_recycle : force le renouvellement des connexions toutes les 10 minutes
    # PostgreSQL coupe les connexions idle après ~10min par défaut
    pool_recycle=600 if not is_sqlite else -1,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()