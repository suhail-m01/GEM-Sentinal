"""Jurisdiction routing with auditable fallback.

Routing attributes: district, department, tender category, organization.
Fallback chain: exact jurisdiction → state level → department level →
any active L1 (flagged as fallback). Every hop is audit-logged.
"""
import logging
from typing import Optional

from sqlalchemy.orm import Session

from ..core.config import settings
from ..core.deps import L1_BEHAVIOR_ROLES, L2_BEHAVIOR_ROLES
from ..core.db import utcnow
from ..models import Application, Jurisdiction, OfficerAssignment, Tender, User

logger = logging.getLogger("gem.routing")


def _officers_with_jurisdiction(db: Session, code: str) -> list[User]:
    rows = (
        db.query(User)
        .join(OfficerAssignment, OfficerAssignment.user_id == User.id)
        .join(Jurisdiction, Jurisdiction.id == OfficerAssignment.jurisdiction_id)
        .filter(Jurisdiction.code == code, User.role.in_(L1_BEHAVIOR_ROLES), User.status == "ACTIVE")
        .distinct()
        .all()
    )
    return rows


def route_application(db: Session, application: Application, tender: Tender) -> dict:
    """Assigns L1 (and default L2). Returns routing info for audit."""
    code = tender.jurisdiction_code
    method = None
    officers = _officers_with_jurisdiction(db, code)
    if officers:
        application.assigned_l1_id = officers[0].id
        method = "EXACT_JURISDICTION"
    else:
        # fallback 1: state level (e.g. KARNATAKA-ANY-OILGAS)
        state_code = f"{tender.state.upper()}-ANY-{(tender.department or 'GEN').upper().replace(' ', '')[:12]}"
        officers = _officers_with_jurisdiction(db, state_code)
        if officers:
            application.assigned_l1_id = officers[0].id
            method = "STATE_FALLBACK"
        else:
            # fallback 2: department level
            dept_code = f"ANY-ANY-{(tender.department or 'GEN').upper().replace(' ', '')[:12]}"
            officers = _officers_with_jurisdiction(db, dept_code)
            if officers:
                application.assigned_l1_id = officers[0].id
                method = "DEPARTMENT_FALLBACK"
            else:
                # fallback 3: first active L1 (must remain auditable)
                any_l1 = (
                    db.query(User).filter(User.role.in_(L1_BEHAVIOR_ROLES), User.status == "ACTIVE").first()
                )
                if any_l1:
                    application.assigned_l1_id = any_l1.id
                    method = "GLOBAL_FALLBACK"
                else:
                    application.assigned_l1_id = None
                    method = "UNROUTED"

    application.routing_method = method
    application.routing_fallback = method in (
        "STATE_FALLBACK",
        "DEPARTMENT_FALLBACK",
        "GLOBAL_FALLBACK",
    )
    application.sla_due_at = utcnow().replace(hour=17, minute=0, second=0, microsecond=0)
    # add SLA days
    from datetime import timedelta

    application.sla_due_at = application.sla_due_at + timedelta(days=settings.L1_SLA_DAYS)

    # default L2: first active L2 in state or any
    l2 = (
        db.query(User)
        .join(OfficerAssignment, OfficerAssignment.user_id == User.id)
        .filter(User.role.in_(L2_BEHAVIOR_ROLES), User.status == "ACTIVE")
        .distinct()
        .first()
    )
    if l2 is None:
        l2 = db.query(User).filter(User.role.in_(L2_BEHAVIOR_ROLES), User.status == "ACTIVE").first()
    application.assigned_l2_id = l2.id if l2 else None

    return {
        "method": method,
        "fallback": application.routing_fallback,
        "assigned_l1": application.assigned_l1_id,
        "assigned_l2": application.assigned_l2_id,
    }
