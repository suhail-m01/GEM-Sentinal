"""V2.1 enhancement models (additive).

CollusionAnalysis: persisted runs of the tender-scoped collusion indicator
analysis. Findings are factual data points (shared attributes), never
allegations — consistent with the vigilance-referral doctrine.
"""
from sqlalchemy import JSON, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from ..core.db import Base, utcnow
from .user import new_id


class CollusionAnalysis(Base):
    __tablename__ = "collusion_analyses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tender_id: Mapped[str] = mapped_column(String(36), index=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_by_role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    indicator_level: Mapped[str] = mapped_column(String(16), default="NONE")  # NONE|LOW|MODERATE|HIGH
    bidders_analyzed: Mapped[int] = mapped_column(default=0)
    findings: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime, default=utcnow, index=True)


class VerificationCache(Base):
    """TTL-bounded cache of deterministic government-source verification results.

    Additive (BidVerify-AI's 3-tier cache, ported as in-process LRU → Postgres).
    Only deterministic source states are cached (VALID/EXPIRED/MISMATCH/
    NOT_FOUND/BLACKLIST); transient failures (UNAVAILABLE/ERROR) are NEVER
    cached, consistent with the UNABLE_TO_VERIFY doctrine.
    """
    __tablename__ = "verification_cache"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source: Mapped[str] = mapped_column(String(40), index=True)
    cache_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(16))
    data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    message: Mapped[str | None] = mapped_column(String(300), nullable=True)
    retrieved_at: Mapped[object] = mapped_column(DateTime)
    expires_at: Mapped[object] = mapped_column(DateTime, index=True)
    hits: Mapped[int] = mapped_column(default=0)
    last_hit_at: Mapped[object | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime, default=utcnow)
