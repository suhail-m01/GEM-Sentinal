"""Financial envelope models (additive — Feature A).

Two-envelope doctrine: the technical/compliance envelope (documents, profile,
declarations) and the financial envelope (this module) are stored separately
and authorized independently. Financial amounts are Decimal (Numeric(16,2)) —
never float — and the canonical SHA-256 digest of the submitted quotation is
stored so post-hoc tampering is detectable.

Versioning: each pre-deadline modification creates a NEW row with
``version += 1``; the previous row is marked REPLACED and kept (audit trail).
Only the latest SEALED/OPENED version is active at deadline.
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..core.db import Base, utcnow
from .user import new_id


def canonical_financial_bid(*, tender_id: str, bidder_id: str, version: int,
                            base_amount, tax_amount, other_charges,
                            total_quoted_amount, currency: str,
                            price_valid_days: int, submitted_at: datetime) -> str:
    """Deterministic SHA-256 over the canonical commercial fields.

    Amounts are rendered as fixed 2-decimal strings so the digest is stable
    across Decimal formatting (no float representation anywhere in the path).
    """
    import hashlib
    import json

    def _d(v) -> str:
        return f"{v:.2f}"

    body = json.dumps({
        "tender_id": tender_id,
        "bidder_id": bidder_id,
        "version": version,
        "base": _d(base_amount),
        "tax": _d(tax_amount),
        "other": _d(other_charges),
        "total": _d(total_quoted_amount),
        "currency": currency,
        "price_valid_days": price_valid_days,
        "submitted_at": submitted_at.isoformat(),
    }, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


class FinancialBid(Base):
    __tablename__ = "financial_bids"
    __table_args__ = (
        UniqueConstraint("application_id", "version", name="uq_finbid_app_version"),
        Index("ix_finbid_app", "application_id"),
        Index("ix_finbid_tender", "tender_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    tender_id: Mapped[str] = mapped_column(ForeignKey("tenders.id"), index=True)
    bidder_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)

    currency: Mapped[str] = mapped_column(String(8), default="INR")
    base_amount: Mapped[object] = mapped_column(Numeric(16, 2))
    tax_amount: Mapped[object] = mapped_column(Numeric(16, 2), default=0)
    other_charges: Mapped[object] = mapped_column(Numeric(16, 2), default=0)
    total_quoted_amount: Mapped[object] = mapped_column(Numeric(16, 2))

    # Financial-evaluation outputs (set when the financial envelope is opened
    # and evaluated; evaluated == quoted unless an adjustment applies).
    evaluated_amount: Mapped[object | None] = mapped_column(Numeric(16, 2), nullable=True)
    evaluation_adjustment: Mapped[object | None] = mapped_column(Numeric(16, 2), nullable=True)
    evaluation_basis: Mapped[str | None] = mapped_column(String(300), nullable=True)

    price_valid_days: Mapped[int] = mapped_column(Integer, default=90)
    price_valid_until: Mapped[object | None] = mapped_column(DateTime, nullable=True)
    commercial_declaration: Mapped[str | None] = mapped_column(Text, nullable=True)

    # SEALED (submitted, protected) | OPENED (authorized financial opening done)
    # | REPLACED (superseded by a newer pre-deadline version) | WITHDRAWN
    status: Mapped[str] = mapped_column(String(16), default="SEALED", index=True)
    submitted_at: Mapped[object] = mapped_column(DateTime, default=utcnow)
    sealed_at: Mapped[object | None] = mapped_column(DateTime, nullable=True)
    opened_at: Mapped[object | None] = mapped_column(DateTime, nullable=True)
    replaced_at: Mapped[object | None] = mapped_column(DateTime, nullable=True)
    withdrawn_at: Mapped[object | None] = mapped_column(DateTime, nullable=True)

    financial_bid_hash: Mapped[str] = mapped_column(String(64))
    prev_version_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    idempotency_key: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(300), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(40), nullable=True)

    line_items: Mapped[list["FinancialBidLineItem"]] = relationship(
        back_populates="financial_bid", cascade="all, delete-orphan")


class FinancialBidLineItem(Base):
    """Optional line-item pricing (schema-ready; UI uses total-price bidding)."""
    __tablename__ = "financial_bid_line_items"
    __table_args__ = (Index("ix_finbidli_bid", "financial_bid_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    financial_bid_id: Mapped[str] = mapped_column(ForeignKey("financial_bids.id"), index=True)
    tender_item_ref: Mapped[str | None] = mapped_column(String(40), nullable=True)
    description: Mapped[str] = mapped_column(String(300))
    quantity: Mapped[object] = mapped_column(Numeric(16, 3), default=1)
    unit: Mapped[str | None] = mapped_column(String(24), nullable=True)
    unit_price: Mapped[object] = mapped_column(Numeric(16, 2))
    tax_rate: Mapped[object] = mapped_column(Numeric(5, 2), default=0)
    tax_amount: Mapped[object] = mapped_column(Numeric(16, 2), default=0)
    line_total: Mapped[object] = mapped_column(Numeric(16, 2))

    financial_bid: Mapped["FinancialBid"] = relationship(back_populates="line_items")
