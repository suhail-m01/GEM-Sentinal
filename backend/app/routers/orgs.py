"""V2.1 organization endpoints.

- /auth/me            — current user, role, org memberships
- /organizations      — my organizations
- /organizations/{id}/members — list / add / revoke (owner-managed)
- /organizations/{id}/consents — consent records of the org's users

All isolation checks run here in the backend — the UI never decides.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from ..audit import append_audit
from ..core.db import get_db
from ..core.deps import get_current_user
from ..core.security import hash_password
from ..models import User, Organization, OrganizationMember, ConsentRecord
from ..policies.permissions import is_bidder_side

router = APIRouter(tags=["organizations"])

ORG_ADMIN_ROLES = ("BIDDER", "BID_PREPARER", "SIGNATORY")


def _membership(db: Session, user: User, org_id: str) -> OrganizationMember | None:
    return (
        db.query(OrganizationMember)
        .filter(OrganizationMember.organization_id == org_id,
                OrganizationMember.user_id == user.id,
                OrganizationMember.active.is_(True))
        .first()
    )


def _require_org_member(db: Session, user: User, org_id: str) -> OrganizationMember:
    m = _membership(db, user, org_id)
    if not m:
        raise HTTPException(status_code=404, detail="Organization not found")
    return m


def _require_org_owner(db: Session, user: User, org_id: str) -> OrganizationMember:
    m = _require_org_member(db, user, org_id)
    if m.org_role != "OWNER":
        raise HTTPException(status_code=403, detail="Only the organization owner can manage members")
    return m


def my_organizations(db: Session, user: User) -> list[Organization]:
    rows = (
        db.query(OrganizationMember)
        .filter(OrganizationMember.user_id == user.id, OrganizationMember.active.is_(True))
        .all()
    )
    return [m.organization for m in rows]


@router.get("/organizations")
def list_my_orgs(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    orgs = my_organizations(db, user)
    return {
        "organizations": [
            {"id": o.id, "legal_name": o.legal_name, "trade_name": o.trade_name,
             "constitution": o.constitution, "state": o.state, "district": o.district,
             "registered_address": o.registered_address, "active": o.active,
             "is_synthetic_demo": o.is_synthetic_demo}
            for o in orgs
        ]
    }


class AddMemberIn(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=255)
    password: str = Field(min_length=8)
    mobile: str | None = None
    org_role: str = Field(pattern="^(PREPARER|SIGNATORY)$")


@router.get("/organizations/{org_id}/members")
def list_members(org_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_org_member(db, user, org_id)
    rows = (
        db.query(OrganizationMember, User)
        .join(User, User.id == OrganizationMember.user_id)
        .filter(OrganizationMember.organization_id == org_id, OrganizationMember.active.is_(True))
        .all()
    )
    return {
        "members": [
            {"user_id": u.id, "full_name": u.full_name, "email": u.email,
             "org_role": m.org_role, "user_status": u.status, "joined_at": m.joined_at.isoformat()}
            for m, u in rows
        ]
    }


@router.post("/organizations/{org_id}/members", status_code=201)
def add_member(
    org_id: str,
    body: AddMemberIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Owner adds a preparer or signatory (creates the user account)."""
    _require_org_owner(db, user, org_id)
    existing = db.query(User).filter(User.email == body.email.lower()).first()
    if existing:
        raise HTTPException(status_code=409, detail="A user with this email already exists")
    new_user = User(
        email=body.email.lower(), mobile=body.mobile,
        password_hash=hash_password(body.password), full_name=body.full_name,
        role=body.org_role, status="ACTIVE", is_verified=True,
    )
    db.add(new_user)
    db.flush()
    db.add(OrganizationMember(organization_id=org_id, user_id=new_user.id,
                              org_role=body.org_role, invited_by=user.id))
    append_audit(db, "org_member_added", "Organization", org_id, actor_id=user.id,
                 actor_role=user.role, payload={"member_email": body.email, "org_role": body.org_role},
                 commit=False)
    db.commit()
    return {"user_id": new_user.id, "org_role": body.org_role, "status": "ACTIVE"}


@router.delete("/organizations/{org_id}/members/{member_user_id}", status_code=204)
def revoke_member(
    org_id: str, member_user_id: str,
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    _require_org_owner(db, user, org_id)
    m = (
        db.query(OrganizationMember)
        .filter(OrganizationMember.organization_id == org_id,
                OrganizationMember.user_id == member_user_id,
                OrganizationMember.active.is_(True))
        .first()
    )
    if not m:
        raise HTTPException(status_code=404, detail="Membership not found")
    if m.user_id == user.id:
        raise HTTPException(status_code=400, detail="Owner cannot revoke their own membership")
    owners = (
        db.query(OrganizationMember)
        .filter(OrganizationMember.organization_id == org_id,
                OrganizationMember.org_role == "OWNER",
                OrganizationMember.active.is_(True),
                OrganizationMember.user_id != m.id)
        .count()
    )
    if m.org_role == "OWNER" and owners == 0:
        raise HTTPException(status_code=400, detail="An organization must retain at least one active owner")
    m.active = False
    append_audit(db, "org_member_revoked", "Organization", org_id, actor_id=user.id,
                 actor_role=user.role, payload={"member_user_id": m.user_id, "org_role": m.org_role},
                 commit=False)
    db.commit()


@router.get("/organizations/{org_id}/consents")
def org_consents(org_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Consent records for all active users of this organization (owner view)."""
    _require_org_owner(db, user, org_id)
    member_rows = (
        db.query(OrganizationMember.user_id)
        .filter(OrganizationMember.organization_id == org_id, OrganizationMember.active.is_(True))
        .all()
    )
    user_ids = [r[0] for r in member_rows]
    rows = (
        db.query(ConsentRecord, User)
        .join(User, User.id == ConsentRecord.user_id)
        .filter(ConsentRecord.user_id.in_(user_ids))
        .order_by(ConsentRecord.granted_at.desc())
        .all()
    )
    return {
        "consents": [
            {"user_email": u.email, "user_name": u.full_name,
             "source_category": c.source_category, "purpose": c.purpose,
             "status": c.status, "granted_at": c.granted_at.isoformat() if c.granted_at else None,
             "revoked_at": c.revoked_at.isoformat() if c.revoked_at else None}
            for c, u in rows
        ]
    }
