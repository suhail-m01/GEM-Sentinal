"""Named permissions, role capability groups, and tender-scoped authority.

Design rules (spec §4, §12):
- Every permission check happens in backend services, never only in UI.
- Global roles express *capability class*; TenderAssignment expresses
  *where* a capability applies. A procurement decision requires the
  assigned procurement role on that tender — ADMIN alone can never act
  as decision-maker.
- Security-screen permissions (e.g. VIEW_FULL_IP) are explicit grants,
  re-authenticated at use time, and themselves audited.
"""
from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from ..models import User, TenderAssignment, UserPermission
from ..models.workflow import WORKFLOW_ROLES

# --- named permissions (security-aware screens) ---------------------------
VIEW_FULL_IP = "VIEW_FULL_IP"
AUDIT_EXPORT = "AUDIT_EXPORT"
DEMO_PROVISION = "DEMO_PROVISION"

# --- capability groups ------------------------------------------------------
BIDDER_SIDE_ROLES = ("BIDDER", "BID_PREPARER", "SIGNATORY")
PROCUREMENT_DECISION_ROLES = ("PROCUREMENT_OFFICER", "BID_OPENING_OFFICER")
EVALUATOR_ROLES = ("TECHNICAL_EVALUATOR", "FINANCE_EVALUATOR")
COMMITTEE_ROLES = ("COMMITTEE_CHAIR", "COMMITTEE_MEMBER")
AUTHORITY_ROLES = ("CFA",)
CONTRACT_ROLES = ("CONTRACT_MANAGER", "CONTRACT_OFFICER", "INSPECTION_OFFICER", "PAYMENT_OFFICER")
ADMIN_ROLES = ("ADMIN",)
AUDITOR_ROLES = ("AUDITOR",)
OFFICER_SIDE_ROLES = (
    *PROCUREMENT_DECISION_ROLES, *EVALUATOR_ROLES, *COMMITTEE_ROLES,
    *AUTHORITY_ROLES, *CONTRACT_ROLES, "INDENTOR", "ADMIN", "AUDITOR",
)

# Roles that may administer workflow templates / rule catalog (not decisions)
SYSTEM_ADMIN = "ADMIN"


def user_permissions(db: Session, user: User) -> set[str]:
    """Effective named permissions: explicit grants + role defaults."""
    perms = {p.permission for p in db.query(UserPermission).filter(UserPermission.user_id == user.id).all()}
    if user.role == SYSTEM_ADMIN:
        # Security administrator baseline (still re-authenticates per use).
        perms |= {VIEW_FULL_IP, AUDIT_EXPORT, DEMO_PROVISION}
    return perms


def has_permission(db: Session, user: User, permission: str) -> bool:
    return permission in user_permissions(db, user)


def _forbidden(detail: str = "You do not have permission to perform this action.") -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def require_permission(db: Session, user: User, permission: str) -> None:
    if not has_permission(db, user, permission):
        raise _forbidden(f"Requires permission: {permission}")


def is_bidder_side(user: User) -> bool:
    return user.role in BIDDER_SIDE_ROLES


def is_officer_side(user: User) -> bool:
    return user.role in OFFICER_SIDE_ROLES


def get_tender_assignments(db: Session, user: User, tender_id: str) -> list[str]:
    """Active workflow roles the user holds on a specific tender."""
    rows = (
        db.query(TenderAssignment)
        .filter(
            TenderAssignment.tender_id == tender_id,
            TenderAssignment.user_id == user.id,
            TenderAssignment.active.is_(True),
        )
        .all()
    )
    return [r.workflow_role for r in rows]


def require_tender_role(db: Session, user: User, tender_id: str, *roles: str) -> str:
    """Return the first matching active workflow role on this tender, else 403.

    ADMIN does NOT satisfy workflow roles (administrative power must not act
    as procurement decision-maker) — use require_admin for system operations.
    """
    held = get_tender_assignments(db, user, tender_id)
    for role in roles:
        if role in held:
            return role
    if user.role == "AUDITOR":
        # read-only oversight: allowed to view, never act (callers must not
        # grant mutations through this dependency alone)
        raise _forbidden("Read-only oversight role cannot perform this action.")
    raise _forbidden(f"Requires tender assignment: {'/'.join(roles)}")


def require_admin(user: User) -> None:
    if user.role != SYSTEM_ADMIN:
        raise _forbidden()


def require_auditor_or_admin(user: User) -> None:
    if user.role not in (*AUDITOR_ROLES, SYSTEM_ADMIN):
        raise _forbidden()
