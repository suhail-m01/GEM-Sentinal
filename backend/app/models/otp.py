"""Real OTP delivery + trusted-device models.

OTP codes are NEVER stored in plaintext — only a SHA-256 hash, with expiry,
attempt counters and issuance bookkeeping (cooldown / per-window limits).
TrustedDevice binds a browser (client-generated device ID, stored hashed) to a
user for N days so routine logins don't re-prompt for OTP.
"""
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..core.db import Base, utcnow
from .user import new_id


class OtpRecord(Base):
    __tablename__ = "otp_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(255), index=True)
    purpose: Mapped[str] = mapped_column(String(32), index=True)  # LOGIN / REGISTER / CHANGE_MOBILE
    code_hash: Mapped[str] = mapped_column(String(64))  # SHA-256 hex of the 6-digit code
    mobile_masked: Mapped[str | None] = mapped_column(String(20), nullable=True)  # +91 •••••4567
    expires_at: Mapped[object] = mapped_column(DateTime, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    consumed_at: Mapped[object] = mapped_column(DateTime, nullable=True)
    fail_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)  # EXPIRED / MAX_ATTEMPTS / BAD_CODE
    source_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    device_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime, default=utcnow)


class TrustedDevice(Base):
    __tablename__ = "trusted_devices"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    device_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # SHA-256 of client device ID
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)  # browser hint (UA snippet)
    source_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime, default=utcnow)
    last_used_at: Mapped[object] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[object] = mapped_column(DateTime)
    revoked_at: Mapped[object] = mapped_column(DateTime, nullable=True)


class GovEmployee(Base):
    """System-Admin-maintained master of government employees.

    Government-portal sign-in is Government Unique ID + OTP to the registered
    phone — email/password is NOT used on the gov portal. An officer can log in
    only when their record exists here and is ACTIVE (first-time logins need
    admin approval).
    """
    __tablename__ = "gov_employees"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    gov_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # govt unique id (uppercase)
    full_name: Mapped[str] = mapped_column(String(255))
    phone: Mapped[str] = mapped_column(String(16))  # E.164 +91… — OTP destination
    role: Mapped[str] = mapped_column(String(32), index=True)  # one of the 17 roles
    department: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="PENDING_APPROVAL", index=True)
    user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime, default=utcnow)
    approved_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    approved_at: Mapped[object] = mapped_column(DateTime, nullable=True)
    last_login_at: Mapped[object] = mapped_column(DateTime, nullable=True)
