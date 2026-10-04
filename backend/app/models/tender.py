from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, JSON, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..core.db import Base, utcnow
from .user import new_id

# Commercial procurement mechanisms (additive to procurement_method).
PROCUREMENT_MODES = ("STANDARD_E_BID", "E_BID_WITH_REVERSE_AUCTION", "FORWARD_E_AUCTION")
PROCUREMENT_MODE_LABELS = {
    "STANDARD_E_BID": "Standard e-Bid",
    "E_BID_WITH_REVERSE_AUCTION": "e-Bid + Reverse Auction",
    "FORWARD_E_AUCTION": "Electronic (Forward) Auction",
}


class Jurisdiction(Base):
    __tablename__ = "jurisdictions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    code: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    district: Mapped[str | None] = mapped_column(String(120), nullable=True)
    state: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    department: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    organization: Mapped[str | None] = mapped_column(String(150), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime, default=utcnow)


class OfficerAssignment(Base):
    __tablename__ = "officer_assignments"
    __table_args__ = (Index("ix_assignment_user_jur", "user_id", "jurisdiction_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    jurisdiction_id: Mapped[str] = mapped_column(ForeignKey("jurisdictions.id"), index=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[object] = mapped_column(DateTime, default=utcnow)

    jurisdiction: Mapped["Jurisdiction"] = relationship(lazy="joined")


class Tender(Base):
    __tablename__ = "tenders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tender_no: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(300), index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    department: Mapped[str] = mapped_column(String(150), index=True)
    organization: Mapped[str] = mapped_column(String(150), default="CPCL")
    category: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    product_service_category: Mapped[str | None] = mapped_column(String(150), nullable=True)
    district: Mapped[str] = mapped_column(String(120), index=True)
    state: Mapped[str] = mapped_column(String(120), index=True)
    jurisdiction_code: Mapped[str] = mapped_column(String(80), index=True)
    value_min: Mapped[float | None] = mapped_column(Numeric(16, 2), nullable=True)
    value_max: Mapped[float | None] = mapped_column(Numeric(16, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="INR")
    open_date: Mapped[object] = mapped_column(Date, nullable=True)
    close_date: Mapped[object] = mapped_column(Date, nullable=True, index=True)
    # Exact bid-submission deadline (naive UTC). When NULL, the effective
    # deadline defaults to close_date @ 18:00 IST (GeM standard cut-off).
    # Bid details stay sealed to officers until this moment (bid opening).
    bid_deadline: Mapped[object | None] = mapped_column(DateTime, nullable=True)
    # True once an admin force-opened evaluation for this tender (demo/ops
    # override). The scheduled auto-close job (spec §6) skips force-opened
    # tenders so the override does not hide them from the bidder portal.
    # Rows created by older builds hold NULL — treated as "never force-opened".
    force_opened: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=False)
    # Per-tender sealing override (demo district split). False = bids open
    # immediately, no waiting for the bid deadline (e.g. the Hubli
    # AI-verification demo tender). NULL (older builds) = sealed (spec default).
    sealing_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=True)
    # DRAFT, PUBLISHED, CLOSED
    status: Mapped[str] = mapped_column(String(16), default="DRAFT", index=True)
    # GEM_BID, CUSTOM_BID, TWO_PACKET_BID, OPEN_TENDER, LIMITED_TENDER,
    # DIRECT_PURCHASE, REVERSE_AUCTION, SINGLE_SOURCE
    procurement_method: Mapped[str] = mapped_column(String(24), default="GEM_BID", index=True)
    # Commercial mechanism (additive; independent of procurement_method):
    # STANDARD_E_BID | E_BID_WITH_REVERSE_AUCTION | FORWARD_E_AUCTION.
    # Rows from older builds hold NULL — treated as STANDARD_E_BID.
    procurement_mode: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True,
                                                         default="STANDARD_E_BID")
    # True = bidders must submit a financial quotation with the application
    # (FinancialBid). Seeded legacy tenders (t1–t9) keep False so their
    # existing flows remain unchanged; new tenders default True.
    financial_bid_required: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    # Publishing procuring entity (MoPNG, DGH, ONGC, IOC, CPCL, …)
    organization_id: Mapped[str | None] = mapped_column(ForeignKey("procuring_organizations.id"), nullable=True, index=True)
    msme_preference: Mapped[bool] = mapped_column(Boolean, default=False)

    # Tender-specific submission charges/security. These are deliberately
    # explicit per tender: CPPP/GeM procurement does not impose one universal
    # application fee across every tender. Amounts are stored in rupees.
    application_fee_required: Mapped[bool] = mapped_column(Boolean, default=True)
    application_fee_amount: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    application_fee_refundable: Mapped[bool] = mapped_column(Boolean, default=False)
    bid_security_required: Mapped[bool] = mapped_column(Boolean, default=False)
    bid_security_amount: Mapped[float | None] = mapped_column(Numeric(16, 2), nullable=True)
    bid_security_percent: Mapped[float | None] = mapped_column(Numeric(6, 3), nullable=True)
    bid_security_exemption_allowed: Mapped[bool] = mapped_column(Boolean, default=True)

    @property
    def effective_procurement_mode(self) -> str:
        """NULL-safe: rows from older builds (before this column existed)
        are STANDARD_E_BID."""
        return self.procurement_mode or "STANDARD_E_BID"

    @property
    def requires_financial_bid(self) -> bool:
        """NULL-safe: legacy rows (column added later) keep the old flow —
        a financial quotation is optional and the financial stage uses the
        officer-evaluated amount. Only explicit True requires a bidder quote."""
        return self.financial_bid_required is True
    make_in_india_required: Mapped[bool] = mapped_column(Boolean, default=False)
    startup_eligible: Mapped[bool] = mapped_column(Boolean, default=False)
    required_turnover: Mapped[int | None] = mapped_column(Integer, nullable=True)
    min_local_content: Mapped[int | None] = mapped_column(Integer, nullable=True)
    required_document_types: Mapped[list | None] = mapped_column(JSON, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    rules_version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[object] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    rules: Mapped[list["TenderRule"]] = relationship(back_populates="tender", cascade="all, delete-orphan")
    corrigenda: Mapped[list["TenderCorrigendum"]] = relationship(
        back_populates="tender", cascade="all, delete-orphan",
        order_by="TenderCorrigendum.seq_no",
    )
    procuring_organization: Mapped["ProcuringOrganization | None"] = relationship(  # noqa: F821
        back_populates="tenders", lazy="joined"
    )


class TenderRule(Base):
    __tablename__ = "tender_rules"
    __table_args__ = (Index("ix_rules_tender_key", "tender_id", "rule_key", unique=True),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tender_id: Mapped[str] = mapped_column(ForeignKey("tenders.id"), index=True)
    rule_key: Mapped[str] = mapped_column(String(64))
    label: Mapped[str] = mapped_column(String(300))
    # Compliance layer: STATUTORY (general statutory & GeM compliance) or
    # SECTOR_TECHNICAL (tender-specific petroleum technical & safety compliance)
    compliance_layer: Mapped[str] = mapped_column(String(20), default="STATUTORY", index=True)
    # Clause reference in the tender document, e.g. "Spec §4.2", "ATC-3"
    clause_ref: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # verification source: GST, UDYAM, PAN, EPFO, ESIC, MCA21, DIGILOCKER, STARTUP_INDIA, NSIC, BIS, BLACKLIST, DOCUMENT, PROFILE
    source: Mapped[str] = mapped_column(String(40), index=True)
    mandatory: Mapped[bool] = mapped_column(Boolean, default=True)
    # is_active, valid_unexpired, min_turnover, has_document, not_blacklisted, name_match,
    # filing_current, registration_required, has_authorization, min_local_content, startup_certified
    condition_type: Mapped[str] = mapped_column(String(40))
    condition_value: Mapped[str | None] = mapped_column(String(300), nullable=True)
    weight: Mapped[int] = mapped_column(Integer, default=10)
    # FAIL, MANUAL_REVIEW, REDUCE_SCORE
    failure_action: Mapped[str] = mapped_column(String(16), default="FAIL")
    allows_manual_review: Mapped[bool] = mapped_column(Boolean, default=False)
    hard_disqualification: Mapped[bool] = mapped_column(Boolean, default=False)
    # Evidence strength ladder (spec §4): S0..S4. A PASS on evidence weaker than
    # this downgrades to UNABLE_TO_VERIFY — never PASS. Format validity alone is S1.
    min_evidence_strength: Mapped[str] = mapped_column(String(2), default="S1")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[int] = mapped_column(Integer, default=1)

    tender: Mapped["Tender"] = relationship(back_populates="rules")


class TenderBookmark(Base):
    __tablename__ = "tender_bookmarks"
    __table_args__ = (Index("ix_bookmark_user_tender", "user_id", "tender_id", unique=True),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    tender_id: Mapped[str] = mapped_column(ForeignKey("tenders.id"), index=True)
    created_at: Mapped[object] = mapped_column(DateTime, default=utcnow)


class TenderCorrigendum(Base):
    """Corrigendum / amendment published after the original tender.

    A compliance result based on the original document can become INVALID
    after an amendment: publishing a corrigendum bumps the tender's
    rules_version and marks existing verification reports stale, so officers
    re-verify against the amended conditions.
    """

    __tablename__ = "tender_corrigenda"
    __table_args__ = (Index("ix_corr_tender_seq", "tender_id", "seq_no", unique=True),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tender_id: Mapped[str] = mapped_column(ForeignKey("tenders.id"), index=True)
    seq_no: Mapped[int] = mapped_column(Integer, default=1)
    title: Mapped[str] = mapped_column(String(300))
    published_at: Mapped[object] = mapped_column(DateTime, default=utcnow)
    summary: Mapped[str] = mapped_column(Text)
    # JSON: [{"clause": "Spec §4.2", "old": "...", "new": "...", "description": "..."}]
    changes: Mapped[list | None] = mapped_column(JSON, nullable=True)
    deadline_extended_to: Mapped[object] = mapped_column(Date, nullable=True)
    affects_rules: Mapped[bool] = mapped_column(Boolean, default=True)
    published_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime, default=utcnow)

    tender: Mapped["Tender"] = relationship(back_populates="corrigenda")
