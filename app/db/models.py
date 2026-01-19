from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    String,
    DateTime,
    Integer,
    func,
    Boolean,
    ForeignKey,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base

# ajoute ces imports
from sqlalchemy import String, Integer, DateTime, ForeignKey, Text, Boolean
from sqlalchemy.orm import relationship, Mapped, mapped_column
from datetime import datetime, timezone
import uuid


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

    # ============================================================
    # Relations
    # ============================================================
    files: Mapped[list["File"]] = relationship(
        "File",
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
