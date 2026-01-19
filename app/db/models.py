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
