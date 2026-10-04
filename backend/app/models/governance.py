from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..core.db import Base, utcnow
from .user import new_id


class AuditLog(Base):
    """Append-only, hash-chained audit record (V2.1: IP-aware).

    The canonical hash payload includes the normalized source IP and the
    request correlation id, so the full security context is covered by the
    chain. Full IP values are access-controlled: ordinary views are masked.
    """

    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    actor_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    actor_role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    action: Mapped[str] = mapped_column(String(80), index=True)
    entity_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    entity_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    tender_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    application_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    source_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)  # normalized
    ip_source: Mapped[str | None] = mapped_column(String(16), nullable=True)  # DIRECT | TRUSTED_PROXY | MALFORMED
    request_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(300), nullable=True)  # sanitized summary
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    prev_hash: Mapped[str] = mapped_column(String(64))
    record_hash: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[object] = mapped_column(DateTime, default=utcnow, index=True)


class BlacklistEntry(Base):
    __tablename__ = "blacklist_entries"
    __table_args__ = (Index("ix_blacklist_pan", "pan"), Index("ix_blacklist_gstin", "gstin"))

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    entity_name: Mapped[str] = mapped_column(String(300), index=True)
    pan: Mapped[str | None] = mapped_column(String(20), nullable=True)
    gstin: Mapped[str | None] = mapped_column(String(20), nullable=True)
    reference_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    reason: Mapped[str] = mapped_column(Text)
    source_authority: Mapped[str] = mapped_column(String(200))
    official_order_reference: Mapped[str | None] = mapped_column(String(200), nullable=True)
    valid_from: Mapped[object] = mapped_column(Date, nullable=True)
    valid_until: Mapped[object] = mapped_column(Date, nullable=True)
    # ACTIVE, EXPIRED, APPEAL_PENDING, REMOVED (status transitions only; no hard delete)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE", index=True)
    review_status: Mapped[str | None] = mapped_column(String(60), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[object] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    recipient_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(48), index=True)
    title: Mapped[str] = mapped_column(String(200))
    message: Mapped[str] = mapped_column(Text)
    entity_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    entity_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[object] = mapped_column(DateTime, default=utcnow, index=True)


class VigilanceReferral(Base):
    __tablename__ = "vigilance_referrals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    referral_no: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    override_record_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    # DRAFT, REVIEWED, READY_FOR_CVC_FILING, FILED_MANUALLY
    status: Mapped[str] = mapped_column(String(24), default="DRAFT", index=True)
    report: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    notice_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    reviewed_at: Mapped[object] = mapped_column(DateTime, nullable=True)
    filed_at: Mapped[object] = mapped_column(DateTime, nullable=True)
    filed_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime, default=utcnow)
    application: Mapped["Application"] = relationship(lazy="joined")


class SourceHealth(Base):
    __tablename__ = "source_health"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(16), index=True)  # UP, DOWN
    message: Mapped[str | None] = mapped_column(String(300), nullable=True)
    checked_at: Mapped[object] = mapped_column(DateTime, default=utcnow)


class TrustedReference(Base):
    __tablename__ = "trusted_references"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    issuer_name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(16))  # SEAL, SIGNATURE, LOGO
    file_path: Mapped[str] = mapped_column(String(300))
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime, default=utcnow)
