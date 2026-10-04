import uuid

from sqlalchemy import Boolean, DateTime, Integer, String, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..core.db import Base, utcnow


def new_id() -> str:
    return str(uuid.uuid4())


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    mobile: Mapped[str | None] = mapped_column(String(20), nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32), default="BIDDER", index=True)
    # PENDING_OTP, PENDING_APPROVAL, ACTIVE, SUSPENDED, DISABLED
    status: Mapped[str] = mapped_column(String(32), default="PENDING_OTP", index=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[object] = mapped_column(DateTime, nullable=True)
    last_login_at: Mapped[object] = mapped_column(DateTime, nullable=True)
    otp_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # Rotating session id (correlates audit events; new value per login/refresh)
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[object] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    bidder_profile: Mapped["BidderProfile | None"] = relationship(
        back_populates="user", uselist=False, lazy="joined"
    )


from .bidder import BidderProfile, ConsentRecord, Document  # noqa: E402,F401
