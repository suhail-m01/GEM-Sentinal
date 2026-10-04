from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..core.db import Base, utcnow
from .user import new_id


class VerificationReport(Base):
    __tablename__ = "verification_reports"
    __table_args__ = (Index("ix_reports_app_run", "application_id", "run_no"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    run_no: Mapped[int] = mapped_column(Integer, default=1)
    triggered_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(16), default="COMPLETED")
    compliance_score: Mapped[int] = mapped_column(Integer, default=0)
    risk_level: Mapped[str] = mapped_column(String(16), index=True)  # LOW, MEDIUM, HIGH, CRITICAL
    eligibility_result: Mapped[str] = mapped_column(String(16))
    # JSON: list of rule evaluation results
    rule_results: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # JSON: cross-verification field comparison
    cross_verification: Mapped[list | None] = mapped_column(JSON, nullable=True)
    mismatches: Mapped[list | None] = mapped_column(JSON, nullable=True)
    missing_documents: Mapped[list | None] = mapped_column(JSON, nullable=True)
    expired_documents: Mapped[list | None] = mapped_column(JSON, nullable=True)
    blacklist_findings: Mapped[list | None] = mapped_column(JSON, nullable=True)
    low_confidence_fields: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # JSON: per source: {status, retrieved_at, message}
    source_results: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # JSON: per-document authenticity findings
    authenticity: Mapped[list | None] = mapped_column(JSON, nullable=True)
    ai_explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_explanation_language: Mapped[str] = mapped_column(String(8), default="en")
    # XAI structured explanation (v2.0): sectioned, RAG-grounded, citation-
    # bearing object rendered in the review UI and the PDF.
    explanation_detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Rules version of the tender at run time. A later-published corrigendum
    # bumps the version → this report is corrigendum-stale and must be re-run.
    rules_version_at_run: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model_identifier: Mapped[str | None] = mapped_column(String(80), nullable=True)
    report_version: Mapped[str] = mapped_column(String(16), default="1.0")
    recommendation: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_refs: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Evidence strength distribution, e.g. {"S3": 7, "S2": 2, "S1": 3}
    strength_distribution: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # % of applicable requirements actually verified (UNABLE_TO_VERIFY excluded)
    coverage_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    # SHA-256 of the canonical deterministic results — replay asserts identity
    determinism_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Sentinel Watch: set when this report is a drift-generated revision
    created_from_report_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    generated_at: Mapped[object] = mapped_column(DateTime, default=utcnow)

    application: Mapped["Application"] = relationship(lazy="joined")


class OfficerDecision(Base):
    __tablename__ = "officer_decisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    officer_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    level: Mapped[str] = mapped_column(String(4))  # L1, L2
    action: Mapped[str] = mapped_column(String(48))
    remarks: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_overridden: Mapped[bool] = mapped_column(Boolean, default=False)
    override_record_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime, default=utcnow)


class OverrideRecord(Base):
    __tablename__ = "override_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    officer_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    level: Mapped[str] = mapped_column(String(4))  # L1, L2
    # MINOR, MEDIUM, HIGH, CRITICAL, FRAUD
    severity: Mapped[str] = mapped_column(String(16), index=True)
    overridden_finding: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    reason_category: Mapped[str] = mapped_column(String(64))
    justification: Mapped[str] = mapped_column(Text)
    supporting_evidence: Mapped[list | None] = mapped_column(JSON, nullable=True)
    reauth_at: Mapped[object] = mapped_column(DateTime, default=utcnow)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(300), nullable=True)
    admin_notified: Mapped[bool] = mapped_column(Boolean, default=False)
    admin_cosign_required: Mapped[bool] = mapped_column(Boolean, default=False)
    admin_cosigned_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    admin_cosigned_at: Mapped[object] = mapped_column(DateTime, nullable=True)
    l2_review_required: Mapped[bool] = mapped_column(Boolean, default=True)
    vigilance_referral_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # PENDING_REVIEW, PENDING_COSIGN, REVIEWED, REJECTED, ESCALATED
    status: Mapped[str] = mapped_column(String(20), default="PENDING_REVIEW", index=True)
    application: Mapped["Application"] = relationship(lazy="joined")
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    reviewed_at: Mapped[object] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime, default=utcnow)


from .application import Application  # noqa: E402,F401
from .user import User  # noqa: E402,F401
