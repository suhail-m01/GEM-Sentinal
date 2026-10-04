from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..core.db import Base, utcnow
from .user import new_id


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    application_no: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    tender_id: Mapped[str] = mapped_column(ForeignKey("tenders.id"), index=True)
    bidder_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    # Draft, Submitted, AI Verification Not Run, AI Verification In Progress,
    # Pending L1 Review, Clarification Requested, Resubmitted, L1 Recommended,
    # Pending L2 Approval, Admin Co-sign Required, Approved, Rejected, Withdrawn, Closed
    status: Mapped[str] = mapped_column(String(40), default="Draft", index=True)
    # ELIGIBLE, CONDITIONAL, NOT_ELIGIBLE, BLACKLISTED, MISSING_DOCUMENTS
    eligibility_result: Mapped[str | None] = mapped_column(String(16), nullable=True)
    compliance_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Bidder's quoted amount for the tender (used by financial evaluation & L-ranking)
    quoted_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_level: Mapped[str | None] = mapped_column(String(16), nullable=True)  # LOW, MEDIUM, HIGH, CRITICAL
    assigned_l1_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    assigned_l2_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    routing_method: Mapped[str | None] = mapped_column(String(40), nullable=True)
    routing_fallback: Mapped[bool] = mapped_column(Boolean, default=False)
    declaration_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    declaration_at: Mapped[object] = mapped_column(DateTime, nullable=True)
    submitted_at: Mapped[object] = mapped_column(DateTime, nullable=True)
    # Bid-time snapshot: documents attached at submission are frozen at this
    # moment. Later additions only via clarification responses (purpose=
    # CLARIFICATION_RESPONSE) — never replacements.
    snapshot_frozen_at: Mapped[object] = mapped_column(DateTime, nullable=True)
    last_verified_at: Mapped[object] = mapped_column(DateTime, nullable=True)
    report_stale: Mapped[bool] = mapped_column(Boolean, default=False)
    fraud_flagged: Mapped[bool] = mapped_column(Boolean, default=False)
    sla_due_at: Mapped[object] = mapped_column(DateTime, nullable=True)
    sla_breached: Mapped[bool] = mapped_column(Boolean, default=False)
    closed_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[object] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    tender: Mapped["Tender"] = relationship(lazy="joined")
    bidder_user: Mapped["User"] = relationship(foreign_keys=[bidder_user_id], lazy="joined")
    assigned_l1: Mapped["User | None"] = relationship(foreign_keys=[assigned_l1_id], lazy="joined")
    assigned_l2: Mapped["User | None"] = relationship(foreign_keys=[assigned_l2_id], lazy="joined")
    history: Mapped[list["ApplicationStatusHistory"]] = relationship(
        back_populates="application", cascade="all, delete-orphan"
    )


class ApplicationStatusHistory(Base):
    __tablename__ = "application_status_history"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    status: Mapped[str] = mapped_column(String(40))
    actor_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    actor_role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    remarks: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime, default=utcnow)

    application: Mapped["Application"] = relationship(back_populates="history")


class ApplicationDocument(Base):
    __tablename__ = "application_documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(300))
    doc_type: Mapped[str] = mapped_column(String(48), default="OTHER")
    file_path: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # ORIGINAL, TENDER_SPECIFIC, CLARIFICATION_RESPONSE, OVERRIDE_EVIDENCE
    purpose: Mapped[str] = mapped_column(String(32), default="ORIGINAL")
    uploaded_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[object] = mapped_column(DateTime, default=utcnow)


class Clarification(Base):
    __tablename__ = "clarifications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    requested_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    reason: Mapped[str] = mapped_column(Text)
    documents_needed: Mapped[list | None] = mapped_column(JSON, nullable=True)
    deadline: Mapped[object] = mapped_column(DateTime, nullable=True)
    # OPEN, RESPONDED, CLOSED, EXPIRED
    status: Mapped[str] = mapped_column(String(16), default="OPEN")
    response_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    responded_at: Mapped[object] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime, default=utcnow)


class Grievance(Base):
    __tablename__ = "grievances"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    bidder_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    reason: Mapped[str] = mapped_column(Text)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_paths: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # SUBMITTED, UNDER_REVIEW, RESPONDED, DISMISSED, ALLOWED
    status: Mapped[str] = mapped_column(String(16), default="SUBMITTED", index=True)
    admin_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    responded_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    responded_at: Mapped[object] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime, default=utcnow)


from .tender import Tender  # noqa: E402,F401
from .user import User  # noqa: E402,F401
