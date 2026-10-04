"""Procuring-entity model.

Petroleum-sector tenders are published by multiple organizations (MoPNG
secretariat, DGH, PNGRB, ONGC, IOC, BPCL, HPCL, GAIL, Oil India, EIL,
refineries/CPSEs). Each organization operates through its own procurement
division, tender committee and delegated financial authority — so approval
titles and value bands are CONFIGURED per organization, not hard-coded as
"L1/L2/L3".
"""
from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..core.db import Base, utcnow
from .user import new_id


class ProcuringOrganization(Base):
    __tablename__ = "procuring_organizations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    short_code: Mapped[str] = mapped_column(String(24), unique=True, index=True)
    # MINISTRY, DEPARTMENT, DIRECTORATE, REGULATORY, CPSE, PSU, REFINERY, EPC
    org_type: Mapped[str] = mapped_column(String(24), index=True)
    parent_ministry: Mapped[str | None] = mapped_column(String(200), nullable=True)
    procurement_division: Mapped[str | None] = mapped_column(String(200), nullable=True)
    procurement_method_default: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # Committee policy for tenders above committee_threshold_value:
    # min members, must include finance member + user representative.
    committee_min_members: Mapped[int] = mapped_column(Integer, default=3)
    committee_requires_finance: Mapped[bool] = mapped_column(Boolean, default=True)
    committee_requires_user_rep: Mapped[bool] = mapped_column(Boolean, default=True)
    # Value-band authority chain. Each band:
    # {"min_value": 0, "max_value": 2500000, "review_role_title": "Tender Evaluation Officer",
    #  "approval_role_title": "Delegated Procurement Officer", "committee_required": false}
    authority_bands: Mapped[list | None] = mapped_column(JSON, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[object] = mapped_column(DateTime, default=utcnow)

    tenders: Mapped[list["Tender"]] = relationship(back_populates="procuring_organization")  # noqa: F821
