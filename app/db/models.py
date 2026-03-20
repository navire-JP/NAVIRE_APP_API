from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    String,
    DateTime,
    Integer,
    Boolean,
    ForeignKey,
    Text,
    JSON,
    Float,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base




class QcmSession(Base):
    __tablename__ = "qcm_sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)

    file_id: Mapped[int] = mapped_column(Integer, nullable=False)
    difficulty: Mapped[str] = mapped_column(String, nullable=False)  # easy|medium|hard
    pages: Mapped[str] = mapped_column(String, default="", nullable=False)

    status: Mapped[str] = mapped_column(String, default="generating", nullable=False)  # generating|ready|done|error
    current_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    error_message: Mapped[str] = mapped_column(Text, default="", nullable=False)

    questions = relationship("QcmQuestion", back_populates="session", cascade="all, delete-orphan")


class QcmQuestion(Base):
    __tablename__ = "qcm_questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("qcm_sessions.id"), index=True, nullable=False)

    index: Mapped[int] = mapped_column(Integer, nullable=False)  # 0..4
    question: Mapped[str] = mapped_column(Text, nullable=False)

    choice_a: Mapped[str] = mapped_column(Text, nullable=False)
    choice_b: Mapped[str] = mapped_column(Text, nullable=False)
    choice_c: Mapped[str] = mapped_column(Text, nullable=False)
    choice_d: Mapped[str] = mapped_column(Text, nullable=False)

    correct_letter: Mapped[str] = mapped_column(String, nullable=False)  # A|B|C|D
    explanation: Mapped[str] = mapped_column(Text, nullable=False)

    answered: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    user_letter: Mapped[str] = mapped_column(String, default="", nullable=False)

    session = relationship("QcmSession", back_populates="questions")


# ============================================================
# QCM STATS HISTORY
# ============================================================

class QcmSessionHistory(Base):
    """
    Enregistre chaque session QCM complétée (ou abandonnée) pour le calcul
    des métriques utilisateur : streaks, taux de réussite, fichiers les plus
    utilisés, etc.

    TTL : supprimée automatiquement 6 mois après last_activity_at (APScheduler).
    """
    __tablename__ = "qcm_session_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    # Référence à la session QCM active (nullable : si session expirée et purgée)
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    # Fichier source du QCM
    file_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    file_name: Mapped[str] = mapped_column(String(255), default="", nullable=False)  # snapshot du nom au moment de la session

    # Résultats bruts
    total_questions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    correct_answers: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    wrong_answers: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Taux de réussite pré-calculé (0.0 → 1.0) pour éviter divisions répétées
    success_rate: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    # Une session compte pour le streak uniquement si elle est complète (5/5 questions répondues)
    is_complete: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Difficulté choisie (snapshot)
    difficulty: Mapped[str] = mapped_column(String(16), default="", nullable=False)  # easy|medium|hard

    # Timestamps — timezone-aware UTC
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Dernière activité sur cette entrée — sert de référence pour le TTL 6 mois
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )

    # Relation
    user: Mapped["User"] = relationship("User", back_populates="qcm_history")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)

    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    newsletter_opt_in: Mapped[bool] = mapped_column(Boolean, default=False)
    university: Mapped[str | None] = mapped_column(String(120), nullable=True)
    study_level: Mapped[str | None] = mapped_column(String(120), nullable=True)

    score: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    grade: Mapped[str] = mapped_column(String(64), default="Primo", nullable=False)

    # ✅ Plans: free | navire_ai | navire_ai_plus
    plan: Mapped[str] = mapped_column(String(32), default="free", nullable=False)

    # ✅ Admin: 10 fichiers persistants
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # elos:
    elo: Mapped[int] = mapped_column(Integer, default=0, nullable=False, index=True)

    # dans class User(...) ajoute la relation :
    flash_decks: Mapped[list["FlashDeck"]] = relationship(
        "FlashDeck",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    # ============================================================
    # Relations
    # ============================================================
    files: Mapped[list["File"]] = relationship(
        "File",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    qcm_history: Mapped[list["QcmSessionHistory"]] = relationship(
        "QcmSessionHistory",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class File(Base):
    __tablename__ = "files"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    filename_original: Mapped[str] = mapped_column(String(255), nullable=False)
    filename_stored: Mapped[str] = mapped_column(String(255), nullable=False)

    # chemin "complet" (au sens STORAGE_PATH), ex: /var/data/storage/UserFiles/1/uuid.pdf
    path: Mapped[str] = mapped_column(String(500), nullable=False)

    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    # ✅ NEW: TTL (free = now + 24h). Abonnés: NULL
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # ============================================================
    # Relations
    # ============================================================
    user: Mapped["User"] = relationship("User", back_populates="files")

# ============================================================
# FLASHCARDS
# ============================================================


class FlashDeck(Base):
    __tablename__ = "flash_decks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    title: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)
    
    # relations
    user: Mapped["User"] = relationship("User", back_populates="flash_decks")
    cards: Mapped[list["FlashCard"]] = relationship(
        "FlashCard",
        back_populates="deck",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class FlashCard(Base):
    __tablename__ = "flash_cards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    deck_id: Mapped[int] = mapped_column(
        ForeignKey("flash_decks.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    front: Mapped[str] = mapped_column(Text, nullable=False)
    back: Mapped[str] = mapped_column(Text, nullable=False)

    # tags simple (string "tag1,tag2") pour V1 (on normalisera plus tard si besoin)
    tags: Mapped[str] = mapped_column(String(255), default="", nullable=False)

    # source tracking (V1)
    # source_type: manual | pdf
    source_type: Mapped[str] = mapped_column(String(16), default="manual", nullable=False)
    source_file_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_pages: Mapped[str] = mapped_column(String(64), default="", nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    deck: Mapped["FlashDeck"] = relationship("FlashDeck", back_populates="cards")


class FlashStudySession(Base):
    __tablename__ = "flash_study_sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    deck_id: Mapped[int] = mapped_column(ForeignKey("flash_decks.id", ondelete="CASCADE"), index=True, nullable=False)

    # mode: classic | random | exam (V1: classic)
    mode: Mapped[str] = mapped_column(String(32), default="classic", nullable=False)

    # state minimal
    total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    current_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    order_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)  # {"card_ids":[...]}
    stats_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)  # {"correct":0,"wrong":0,...}

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# ============================================================
# SCORING ELOS
# ============================================================

class EloEvent(Base):
    __tablename__ = "elo_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)

    source: Mapped[str] = mapped_column(String(32), nullable=False)      # "qcm" | "flashcards" | ...
    delta: Mapped[int] = mapped_column(Integer, nullable=False)          # +1 / -2 / ...
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    # idempotence / traçabilité
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    question_index: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)

    meta: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)