from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..core.db import Base, utcnow
from ..core.security import decrypt_field
from .user import new_id


def _safe_decrypt(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return decrypt_field(value)
    except Exception:
        return None


class BidderProfile(Base):
    __tablename__ = "bidder_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    company_name: Mapped[str] = mapped_column(String(300))
    trade_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # Sensitive identifiers encrypted at rest (AES-256)
    pan_encrypted: Mapped[str | None] = mapped_column(String(512), nullable=True)
    gstin_encrypted: Mapped[str | None] = mapped_column(String(512), nullable=True)
    udyam_encrypted: Mapped[str | None] = mapped_column(String(512), nullable=True)
    pan_masked: Mapped[str | None] = mapped_column(String(32), nullable=True)
    gstin_masked: Mapped[str | None] = mapped_column(String(32), nullable=True)
    udyam_masked: Mapped[str | None] = mapped_column(String(64), nullable=True)
    business_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    business_category: Mapped[str | None] = mapped_column(String(120), nullable=True)
    registered_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    district: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    state: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    pincode: Mapped[str | None] = mapped_column(String(10), nullable=True)
    authorized_rep_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    authorized_rep_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    authorized_rep_phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    annual_turnover: Mapped[int | None] = mapped_column(Integer, nullable=True)
    established_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    registration_date: Mapped[object] = mapped_column(Date, nullable=True)
    filing_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    local_content_percent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    profile_completion: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[object] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[object] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    user: Mapped["User"] = relationship(back_populates="bidder_profile")

    @property
    def pan_plain(self) -> str | None:
        return _safe_decrypt(self.pan_encrypted)

    @property
    def gstin_plain(self) -> str | None:
        return _safe_decrypt(self.gstin_encrypted)

    @property
    def udyam_plain(self) -> str | None:
        return _safe_decrypt(self.udyam_encrypted)


class ConsentRecord(Base):
    __tablename__ = "consent_records"
    __table_args__ = (Index("ix_consent_user_source", "user_id", "source_category", unique=True),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    source_category: Mapped[str] = mapped_column(String(40))  # GST, UDYAM, PAN, ALL, ...
    purpose: Mapped[str] = mapped_column(
        Text, default="Compliance verification for tender eligibility"
    )
    status: Mapped[str] = mapped_column(String(16), default="GRANTED")  # GRANTED, REVOKED
    granted_at: Mapped[object] = mapped_column(DateTime, default=utcnow)
    revoked_at: Mapped[object] = mapped_column(DateTime, nullable=True)

    @property
    def is_granted(self) -> bool:
        return self.status == "GRANTED"


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (Index("ix_documents_user_type", "user_id", "doc_type"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(300))
    doc_type: Mapped[str] = mapped_column(String(48), index=True)
    # source system: UDYAM, GST, PAN, DIGILOCKER, MCA21, EPFO, ESIC, STARTUP_INDIA, NSIC, BIS, LOCAL_CONTENT, BLACKLIST, MANUAL
    source: Mapped[str] = mapped_column(String(40), index=True)
    source_classification: Mapped[str] = mapped_column(String(32), default="GOVERNMENT_SOURCE")
    # VERIFIED, PENDING, EXPIRED, MISMATCH, MANUAL_UPLOADED, UNABLE_TO_VERIFY, MANUAL_REVIEW_REQUIRED
    verification_status: Mapped[str] = mapped_column(String(32), default="PENDING", index=True)
    issue_date: Mapped[object] = mapped_column(Date, nullable=True)
    expiry_date: Mapped[object] = mapped_column(Date, nullable=True)
    last_synced_at: Mapped[object] = mapped_column(DateTime, nullable=True)
    reference_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    document_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)  # SHA-256
    masked_identifier: Mapped[str | None] = mapped_column(String(64), nullable=True)
    file_path: Mapped[str | None] = mapped_column(String(300), nullable=True)
    file_type: Mapped[str | None] = mapped_column(String(10), nullable=True)
    ocr_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ocr_confidence: Mapped[float | None] = mapped_column(nullable=True)
    authenticity_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    authenticity_findings: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[object] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
