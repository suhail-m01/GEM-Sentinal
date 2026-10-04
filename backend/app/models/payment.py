from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..core.db import Base, utcnow
from .user import new_id


class TenderPayment(Base):
    __tablename__ = "tender_payments"
    __table_args__ = (
        UniqueConstraint("razorpay_order_id", name="uq_tender_payment_order"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tender_id: Mapped[str] = mapped_column(ForeignKey("tenders.id"), index=True)
    bidder_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    payment_type: Mapped[str] = mapped_column(String(32), index=True)  # APPLICATION_FEE | EMD | APPLICATION_FEE_EMD
    amount_paise: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(8), default="INR")
    status: Mapped[str] = mapped_column(String(20), default="CREATED", index=True)
    razorpay_order_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    razorpay_payment_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    razorpay_signature: Mapped[str | None] = mapped_column(Text, nullable=True)
    receipt: Mapped[str | None] = mapped_column(String(80), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime, default=utcnow)
    verified_at: Mapped[object | None] = mapped_column(DateTime, nullable=True)
