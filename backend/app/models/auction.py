"""Auction models (additive — Features B/C).

Shared engine for two directions:

    REVERSE  — buyer procures; eligible suppliers compete DOWNWARD
    FORWARD  — seller disposes; participants compete UPWARD

All money is Decimal (Numeric(16,2)); all timing is server-authoritative
(naive UTC, same convention as the rest of the project). Authoritative bid
order is the monotonic per-auction ``sequence_number`` assigned under the
auction row lock — never a client timestamp.

Participant identities are protected: bidders see only ``anonymous_id``.
``bidder_user_id``/``application_id`` on participants and bids are officer-
only fields and are never serialized on bidder-facing endpoints.
"""
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..core.db import Base, utcnow
from .user import new_id

AUCTION_TYPES = ("REVERSE", "FORWARD")

# Controlled state machine — transitions enforced in services/auction.py
AUCTION_STATUSES = (
    "DRAFT", "SCHEDULED", "READY", "LIVE", "EXTENDED",
    "PAUSED", "CLOSED", "RESULT_PENDING", "COMPLETED", "CANCELLED",
)

ELIGIBILITY_RULES = (
    "ALL_TECHNICALLY_QUALIFIED",
    "PRICE_BASED_SHORTLIST",
    "CONFIGURED_PERCENTAGE_RULE",
    "TENDER_SPECIFIC_RULE",
)


class Auction(Base):
    __tablename__ = "auctions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    auction_no: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    tender_id: Mapped[str] = mapped_column(ForeignKey("tenders.id"), index=True)
    # ponytail: select-lazy (not joined) — joined eager loading would outer-join
    # tenders into the auction row query, which breaks FOR UPDATE locking
    # in place_bid. Upgrade path: with_for_update(of=Auction) if join caching matters.
    tender: Mapped["Tender"] = relationship()  # noqa: F821
    # REVERSE | FORWARD
    auction_type: Mapped[str] = mapped_column(String(8), default="REVERSE", index=True)
    title: Mapped[str] = mapped_column(String(300))

    status: Mapped[str] = mapped_column(String(20), default="DRAFT", index=True)
    # qualification policy (see services/auction_eligibility.py)
    eligibility_rule: Mapped[str] = mapped_column(String(40), default="ALL_TECHNICALLY_QUALIFIED")
    # rule parameters, e.g. {"shortlist_top": 4} or {"percentage": 100}
    rule_params: Mapped[dict | None] = mapped_column("rule_params_json", JSON, nullable=True)

    scheduled_start_at: Mapped[object | None] = mapped_column(DateTime, nullable=True)
    scheduled_end_at: Mapped[object | None] = mapped_column(DateTime, nullable=True)
    # server-authoritative close time (extended by anti-sniping)
    effective_end_at: Mapped[object | None] = mapped_column(DateTime, nullable=True, index=True)
    started_at: Mapped[object | None] = mapped_column(DateTime, nullable=True)
    closed_at: Mapped[object | None] = mapped_column(DateTime, nullable=True)

    min_increment: Mapped[object] = mapped_column(Numeric(16, 2))
    opening_reference_price: Mapped[object | None] = mapped_column(Numeric(16, 2), nullable=True)

    auto_extend_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    extension_trigger_seconds: Mapped[int] = mapped_column(Integer, default=120)
    extension_seconds: Mapped[int] = mapped_column(Integer, default=120)
    max_extensions: Mapped[int] = mapped_column(Integer, default=3)
    extension_count: Mapped[int] = mapped_column(Integer, default=0)

    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    paused_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[object] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class AuctionParticipant(Base):
    __tablename__ = "auction_participants"
    __table_args__ = (
        UniqueConstraint("auction_id", "application_id", name="uq_apppart_auction_app"),
        Index("ix_apppart_auction", "auction_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    auction_id: Mapped[str] = mapped_column(ForeignKey("auctions.id"), index=True)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    bidder_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    # Anonymous public identity (RA-BIDDER-XXXX). Never derived from PII.
    anonymous_id: Mapped[str] = mapped_column(String(24), index=True)

    ra_eligible: Mapped[bool] = mapped_column(Boolean, default=False)
    qualification_rule: Mapped[str | None] = mapped_column(String(40), nullable=True)
    qualification_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    calculated_at: Mapped[object | None] = mapped_column(DateTime, nullable=True)

    initial_price: Mapped[object | None] = mapped_column(Numeric(16, 2), nullable=True)
    final_ra_price: Mapped[object | None] = mapped_column(Numeric(16, 2), nullable=True)

    # Sentinel Watch integration: critical compliance drift suspends a
    # participant pending officer review — never auto-disqualification.
    suspended: Mapped[bool] = mapped_column(Boolean, default=False)
    suspended_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[object | None] = mapped_column(DateTime, default=utcnow)


class AuctionBid(Base):
    __tablename__ = "auction_bids"
    __table_args__ = (
        UniqueConstraint("auction_id", "idempotency_key", name="uq_auctionbid_idem"),
        UniqueConstraint("auction_id", "sequence_number", name="uq_auctionbid_seq"),
        Index("ix_auctionbid_auction", "auction_id"),
        Index("ix_auctionbid_participant", "participant_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    auction_id: Mapped[str] = mapped_column(ForeignKey("auctions.id"), index=True)
    participant_id: Mapped[str] = mapped_column(ForeignKey("auction_participants.id"), index=True)
    # Officer-only: never serialized for bidders.
    bidder_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))

    amount: Mapped[object] = mapped_column(Numeric(16, 2))
    currency: Mapped[str] = mapped_column(String(8), default="INR")
    sequence_number: Mapped[int] = mapped_column(Integer)
    previous_bid_amount: Mapped[object | None] = mapped_column(Numeric(16, 2), nullable=True)
    decrement_amount: Mapped[object | None] = mapped_column(Numeric(16, 2), nullable=True)
    server_received_at: Mapped[object] = mapped_column(DateTime, default=utcnow)

    idempotency_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(300), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(40), nullable=True)

    # ACCEPTED | REJECTED (rejected bids are recorded, never silently dropped)
    status: Mapped[str] = mapped_column(String(16), default="ACCEPTED", index=True)
    reject_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    bid_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)


class AuctionResult(Base):
    """Immutable post-close commercial outcome (one per auction).

    Raw bid history is never overwritten; this is a computed summary whose
    ``result_hash`` covers the final ranking for tamper-evidence.
    """
    __tablename__ = "auction_results"
    __table_args__ = (UniqueConstraint("auction_id", name="uq_auctionresult_auction"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    auction_id: Mapped[str] = mapped_column(ForeignKey("auctions.id"), unique=True, index=True)
    tender_id: Mapped[str] = mapped_column(ForeignKey("tenders.id"), index=True)
    closed_at: Mapped[object | None] = mapped_column(DateTime, nullable=True)
    participant_count: Mapped[int] = mapped_column(Integer, default=0)
    accepted_bid_count: Mapped[int] = mapped_column(Integer, default=0)
    initial_lowest: Mapped[object | None] = mapped_column(Numeric(16, 2), nullable=True)
    final_lowest: Mapped[object | None] = mapped_column(Numeric(16, 2), nullable=True)
    savings_amount: Mapped[object | None] = mapped_column(Numeric(16, 2), nullable=True)
    savings_pct: Mapped[object | None] = mapped_column(Numeric(6, 3), nullable=True)
    # [{rank, anonymous_id, bidder_user_id, amount, price_source}] — rank "T"
    # marks a joint-lowest tie (tender-specific resolution required, never random)
    ranking: Mapped[list | None] = mapped_column("ranking_json", JSON, nullable=True)
    tie: Mapped[bool] = mapped_column(Boolean, default=False)
    result_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # PENDING_APPROVAL | APPROVED | REJECTED
    status: Mapped[str] = mapped_column(String(20), default="PENDING_APPROVAL")
    approved_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    approved_at: Mapped[object | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime, default=utcnow)
