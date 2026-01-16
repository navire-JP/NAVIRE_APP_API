from sqlalchemy import String, DateTime, func, Boolean, Integer
from sqlalchemy.orm import Mapped, mapped_column
from app.db.database import Base

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)

    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    newsletter_opt_in: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    university: Mapped[str | None] = mapped_column(String(120), nullable=True)
    study_level: Mapped[str | None] = mapped_column(String(120), nullable=True)

    score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    grade: Mapped[str] = mapped_column(String(64), default="Cadet", nullable=False)

    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_login_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
