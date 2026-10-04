"""V2.1 tender workflow assignment.

TenderAssignment ties a user to a specific tender with an explicit
workflow role (never a generic officer level). Authority to act in the
evaluation pipeline is (role, tender) scoped — a user without an
assignment on a tender has no rights over that tender's bids.
"""
from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..core.db import Base, utcnow
from .user import new_id

# Buyer-side workflow roles assignable per tender
WORKFLOW_ROLES = (
    "INDENTOR",
    "PROCUREMENT_OFFICER",
    "BID_OPENING_OFFICER",
    "TECHNICAL_EVALUATOR",
    "FINANCE_EVALUATOR",
    "COMMITTEE_CHAIR",
    "COMMITTEE_MEMBER",
    "CFA",
    "CONTRACT_MANAGER",
    "CONTRACT_OFFICER",
    "INSPECTION_OFFICER",
    "PAYMENT_OFFICER",
)

# Roles that may view technical envelope content after TECHNICAL_OPENED
TECHNICAL_VIEW_ROLES = ("PROCUREMENT_OFFICER", "TECHNICAL_EVALUATOR", "BID_OPENING_OFFICER")
# Roles that may view financial envelope content after FINANCIAL_OPENED
FINANCIAL_VIEW_ROLES = ("FINANCE_EVALUATOR", "COMMITTEE_CHAIR", "COMMITTEE_MEMBER", "CFA", "PROCUREMENT_OFFICER")


class TenderAssignment(Base):
    __tablename__ = "tender_assignments"
    __table_args__ = (
        UniqueConstraint("tender_id", "user_id", "workflow_role", name="uq_tenderassign_tender_user_role"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tender_id: Mapped[str] = mapped_column(ForeignKey("tenders.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    workflow_role: Mapped[str] = mapped_column(String(32), index=True)
    assigned_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    assigned_at: Mapped[object] = mapped_column(DateTime, default=utcnow)


class ApplicationStage(Base):
    """One row per (application, stage) in the 11-stage procurement pipeline.

    Status: PENDING (not reached/acted yet), COMPLETED (stage done),
    TERMINATED (pipeline stopped at this stage with a terminal outcome).
    ``payload`` holds the stage outcome data; ``certificate_no`` numbers the
    downloadable certificate of completion for the stage.
    """
    __tablename__ = "application_stages"
    __table_args__ = (UniqueConstraint("application_id", "stage_key", name="uq_apppipeline_stage"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    stage_key: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(16), default="PENDING", index=True)
    acted_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    acted_at: Mapped[object | None] = mapped_column(DateTime, nullable=True)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    certificate_no: Mapped[str | None] = mapped_column(String(40), nullable=True)
