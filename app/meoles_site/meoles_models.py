from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import String, DateTime, Integer, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


class CartSession(Base):
    __tablename__ = "meoles_cart_sessions"

    session_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    # active | converted | abandoned
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)

    items: Mapped[list["CartItem"]] = relationship(
        "CartItem",
        back_populates="session",
        cascade="all, delete-orphan",
    )


class CartItem(Base):
    __tablename__ = "meoles_cart_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("meoles_cart_sessions.session_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    product_key: Mapped[str] = mapped_column(String(64), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    session: Mapped["CartSession"] = relationship("CartSession", back_populates="items")