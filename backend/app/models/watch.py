"""SENTINEL WATCH — continuous compliance monitoring models.

A ComplianceWatch is registered on completion of any verification. The
scheduler re-polls the watched sources; drift against the baseline snapshot
produces a DriftEvent (old + new source responses stored verbatim) and a
DriftAlert routed to the CURRENT owner (resolved at notification time).
Historical VerificationReports are NEVER mutated — drift creates a new
report revision linked to the original.
"""
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..core.db import Base, utcnow
from .user import new_id

if TYPE_CHECKING:
    from .application import Application


class ComplianceWatch(Base):
    __tablename__ = "compliance_watches"
    __table_args__ = (Index("ix_watch_app_active", "application_id", "active"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    subject_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    tender_id: Mapped[str | None] = mapped_column(ForeignKey("tenders.id"), nullable=True)
    # Attributes under observation, e.g. ["GST", "PAN", "UDYAM", "MCA", "BLACKLIST"]
    watched_attributes: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Baseline snapshot: attribute -> {value, hash, source, observed_at}
    baseline: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    stage: Mapped[str] = mapped_column(String(40), default="EVALUATION")
    cadence_policy: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_from_report_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    last_polled_at: Mapped[object] = mapped_column(DateTime, nullable=True)
    deactivated_at: Mapped[object] = mapped_column(DateTime, nullable=True)
    deactivation_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[object] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    application: Mapped["Application"] = relationship(lazy="joined")


class DriftEvent(Base):
    __tablename__ = "drift_events"
    __table_args__ = (Index("ix_drift_app_sev", "application_id", "severity"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    watch_id: Mapped[str] = mapped_column(ForeignKey("compliance_watches.id"), index=True)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    attribute: Mapped[str] = mapped_column(String(40), index=True)
    old_value: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    new_value: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    direction: Mapped[str] = mapped_column(String(16))   # IMPROVED | DEGRADED | NEUTRAL
    severity: Mapped[str] = mapped_column(String(16), index=True)  # CRITICAL|HIGH|MEDIUM|LOW
    detected_at: Mapped[object] = mapped_column(DateTime, default=utcnow, index=True)
    source: Mapped[str] = mapped_column(String(40))
    evidence_strength: Mapped[str] = mapped_column(String(2), default="S3")
    score_before: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score_after: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cause_rule_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    cause_summary: Mapped[str | None] = mapped_column(String(400), nullable=True)
    # Both source responses stored verbatim (P2: traceable)
    old_response: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    new_response: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Drift NEVER mutates history — it creates a new report revision
    report_revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    original_report_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class DriftAlert(Base):
    __tablename__ = "drift_alerts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    drift_event_id: Mapped[str] = mapped_column(ForeignKey("drift_events.id"), index=True)
    watch_id: Mapped[str] = mapped_column(ForeignKey("compliance_watches.id"), index=True)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    severity: Mapped[str] = mapped_column(String(16), index=True)
    title: Mapped[str] = mapped_column(String(300))
    message: Mapped[str] = mapped_column(Text)
    # Ownership resolution is recorded: intended vs actually-resolved recipient
    intended_recipient_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    resolved_recipient_id: Mapped[str | None] = mapped_column(String(36), index=True)
    resolution_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    delivery_status: Mapped[str] = mapped_column(String(20), default="DELIVERED")
    # OPEN | ACKNOWLEDGED | SNOOZED | ESCALATED
    status: Mapped[str] = mapped_column(String(20), default="OPEN", index=True)
    snooze_until: Mapped[object] = mapped_column(DateTime, nullable=True)
    snooze_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    acknowledged_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    acknowledged_at: Mapped[object] = mapped_column(DateTime, nullable=True)
    escalation_note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime, default=utcnow, index=True)

    drift_event: Mapped["DriftEvent"] = relationship(lazy="joined")


class SourcePollLog(Base):
    __tablename__ = "source_poll_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    watch_id: Mapped[str] = mapped_column(ForeignKey("compliance_watches.id"), index=True)
    source: Mapped[str] = mapped_column(String(40), index=True)
    identifier: Mapped[str | None] = mapped_column(String(60), nullable=True)
    status: Mapped[str] = mapped_column(String(20))   # UP | DOWN
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    message: Mapped[str | None] = mapped_column(String(300), nullable=True)
    polled_at: Mapped[object] = mapped_column(DateTime, default=utcnow, index=True)
