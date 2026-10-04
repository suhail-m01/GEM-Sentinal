"""V2.1 identity & organization models.

- Organization: bidder-side company entity (buyer-side entities remain
  ProcuringOrganization).
- OrganizationMember: user ↔ organization with an org-level role
  (OWNER / PREPARER / SIGNATORY). Submission rights derive from org role,
  not from global user role.
- UserPermission: named permission grants (e.g. VIEW_FULL_IP) for
  security-aware controls that require explicit, audited re-authentication.
- LoginAttempt: authentication monitoring (rate limiting, lockout, audit).
"""
from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..core.db import Base, utcnow
from .user import new_id


ORG_ROLES = ("OWNER", "PREPARER", "SIGNATORY")


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    legal_name: Mapped[str] = mapped_column(String(255), index=True)
    trade_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    constitution: Mapped[str | None] = mapped_column(String(80), nullable=True)  # Pvt Ltd, LLP, Proprietorship ...
    state: Mapped[str | None] = mapped_column(String(80), nullable=True)
    district: Mapped[str | None] = mapped_column(String(80), nullable=True)
    registered_address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    product_service_categories: Mapped[str | None] = mapped_column(String(500), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_synthetic_demo: Mapped[bool] = mapped_column(Boolean, default=False)  # demo-provisioned data flag
    created_at: Mapped[object] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[object] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    members: Mapped[list["OrganizationMember"]] = relationship(back_populates="organization")


class OrganizationMember(Base):
    __tablename__ = "organization_members"
    __table_args__ = (
        UniqueConstraint("organization_id", "user_id", name="uq_orgmember_org_user"),
        Index("ix_orgmember_user", "user_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    org_role: Mapped[str] = mapped_column(String(24), default="OWNER")  # OWNER | PREPARER | SIGNATORY
    invited_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    joined_at: Mapped[object] = mapped_column(DateTime, default=utcnow)

    organization: Mapped["Organization"] = relationship(back_populates="members", lazy="joined")


class UserPermission(Base):
    """Explicit named-permission grant (security-aware, auditable)."""

    __tablename__ = "user_permissions"
    __table_args__ = (UniqueConstraint("user_id", "permission", name="uq_userperm_user_perm"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    permission: Mapped[str] = mapped_column(String(48), index=True)  # e.g. VIEW_FULL_IP
    granted_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    granted_at: Mapped[object] = mapped_column(DateTime, default=utcnow)


class LoginAttempt(Base):
    __tablename__ = "login_attempts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(255), index=True)
    success: Mapped[bool] = mapped_column(Boolean, default=False)
    reason: Mapped[str | None] = mapped_column(String(80), nullable=True)  # BAD_CREDENTIALS, LOCKED, OTP_INVALID ...
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)  # normalized
    ip_source: Mapped[str | None] = mapped_column(String(16), nullable=True)  # DIRECT | TRUSTED_PROXY
    user_agent: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime, default=utcnow, index=True)
