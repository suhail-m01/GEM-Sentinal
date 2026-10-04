from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..audit import append_audit, verify_chain
from ..core.bids import effective_bid_deadline, tender_bids_open
from ..core.config import settings
from ..core.db import get_db, utcnow
from ..core.deps import get_current_admin, get_current_user
from ..core.security import hash_password, mask_gstin, mask_pan, mask_udyam
from ..models import (
    Application,
    ApplicationStatusHistory,
    AuditLog,
    BidderProfile,
    BlacklistEntry,
    Clarification,
    Grievance,
    Jurisdiction,
    Notification,
    OfficerAssignment,
    OverrideRecord,
    ProcuringOrganization,
    SourceHealth,
    Tender,
    TenderCorrigendum,
    TenderRule,
    User,
    VigilanceReferral,
    VerificationReport,
)
from ..notifications import notify, notify_admins
from ..services.verification import mark_report_stale

router = APIRouter(prefix="/admin", tags=["admin"])


# ---------------- dashboard ----------------

@router.get("/dashboard")
def dashboard(admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    apps = db.query(Application).all()
    overrides = db.query(OverrideRecord).all()
    pending_cosign = [o for o in overrides if o.admin_cosign_required and not o.admin_cosigned_at]
    pending_review = [o for o in overrides if o.status == "PENDING_REVIEW"]
    referrals = db.query(VigilanceReferral).all()
    return {
        "applications_total": len(apps),
        "by_status": {s: sum(1 for a in apps if a.status == s) for s in {a.status for a in apps}},
        "users_total": db.query(User).count(),
        "officers_pending_approval": db.query(User).filter(User.status == "PENDING_APPROVAL").count(),
        "tenders_published": db.query(Tender).filter(Tender.status == "PUBLISHED").count(),
        "overrides_total": len(overrides),
        "high_risk_overrides": sum(1 for o in overrides if o.severity in ("HIGH", "CRITICAL")),
        "blacklist_override_queue": sum(1 for o in overrides if o.overridden_finding and (o.overridden_finding or {}).get("blacklisted")),
        "pending_admin_cosigns": len(pending_cosign),
        "pending_override_reviews": len(pending_review),
        "vigilance_referrals_total": len(referrals),
        "vigilance_by_status": {s: sum(1 for r in referrals if r.status == s) for s in {r.status for r in referrals}},
        "sla_breaches": sum(1 for a in apps if a.sla_breached),
        "critical_watchlist": [
            {
                "override_id": o.id, "application_no": o.application.application_no,
                "severity": o.severity, "officer": db.get(User, o.officer_id).full_name if db.get(User, o.officer_id) else None,
                "status": o.status, "cosign_required": o.admin_cosign_required,
                "cosigned": bool(o.admin_cosigned_at),
                "vigilance_referral_id": o.vigilance_referral_id,
                "at": o.created_at.isoformat(),
            }
            for o in sorted(overrides, key=lambda o: o.created_at, reverse=True)
            if o.severity in ("HIGH", "CRITICAL") or (o.overridden_finding or {}).get("blacklisted")
        ][:15],
    }


# ---------------- users ----------------

@router.get("/users")
def list_users(admin: User = Depends(get_current_admin), db: Session = Depends(get_db), role: str | None = None, status: str | None = None, q: str | None = None):
    query = db.query(User)
    if role:
        query = query.filter(User.role == role)
    if status:
        query = query.filter(User.status == status)
    if q:
        query = query.filter(User.full_name.ilike(f"%{q}%") | User.email.ilike(f"%{q}%"))
    users = query.order_by(User.created_at.desc()).all()
    out = []
    for u in users:
        p = u.bidder_profile
        out.append({
            "id": u.id, "email": u.email, "full_name": u.full_name, "mobile": u.mobile,
            "role": u.role, "status": u.status, "is_verified": u.is_verified,
            "company": p.company_name if p else None,
            "district": p.district if p else None,
            "jurisdictions": [
                {"id": j.id, "code": j.code, "district": j.district, "state": j.state, "department": j.department}
                for j in (
                    db.query(Jurisdiction)
                    .join(OfficerAssignment, OfficerAssignment.jurisdiction_id == Jurisdiction.id)
                    .filter(OfficerAssignment.user_id == u.id).all()
                )
            ],
            "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
            "created_at": u.created_at.isoformat(),
        })
    return {"users": out}


class OfficerApproveIn(BaseModel):
    role: str = Field(pattern=r"^(L1_OFFICER|L2_OFFICER)$")
    jurisdiction_ids: list[str] = Field(min_length=1)


@router.post("/users/{user_id}/approve")
def approve_officer(user_id: str, body: OfficerApproveIn, request: Request, admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    if u.status not in ("PENDING_APPROVAL", "PENDING_OTP", "ACTIVE"):
        raise HTTPException(status_code=400, detail=f"Cannot approve user in status {u.status}")
    u.role = body.role
    u.status = "ACTIVE"
    u.is_verified = True
    # replace assignments
    db.query(OfficerAssignment).filter(OfficerAssignment.user_id == u.id).delete()
    for i, jid in enumerate(body.jurisdiction_ids):
        if not db.get(Jurisdiction, jid):
            raise HTTPException(status_code=400, detail=f"Unknown jurisdiction: {jid}")
        db.add(OfficerAssignment(user_id=u.id, jurisdiction_id=jid, is_primary=(i == 0)))
    append_audit(db, "officer_account_approved", "User", u.id, actor_id=admin.id, actor_role="ADMIN",
                 payload={"role": body.role, "jurisdictions": body.jurisdiction_ids}, request=request, commit=False)
    db.commit()
    notify(db, u.id, "account_approved", message=f"Your officer account is active with role {body.role}.")
    return {"message": "Officer approved", "user": {"id": u.id, "role": u.role, "status": u.status}}


class RoleIn(BaseModel):
    role: str = Field(pattern=r"^(L1_OFFICER|L2_OFFICER|ADMIN|BIDDER)$")


@router.put("/users/{user_id}/role")
def set_role(user_id: str, body: RoleIn, request: Request, admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    old = u.role
    u.role = body.role
    append_audit(db, "user_role_changed", "User", u.id, actor_id=admin.id, actor_role="ADMIN",
                 payload={"from": old, "to": body.role}, request=request, commit=False)
    db.commit()
    return {"message": "Role updated", "role": u.role}


class StatusIn(BaseModel):
    status: str = Field(pattern=r"^(SUSPENDED|ACTIVE|DISABLED)$")


@router.post("/users/{user_id}/suspend")
def suspend_user(user_id: str, request: Request, admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    if u.id == admin.id:
        raise HTTPException(status_code=400, detail="You cannot suspend your own account")
    old = u.status
    u.status = "SUSPENDED"
    append_audit(db, "user_suspended", "User", u.id, actor_id=admin.id, actor_role="ADMIN",
                 payload={"from": old}, request=request, commit=False)
    db.commit()
    return {"message": "User suspended"}


@router.post("/users/{user_id}/activate")
def activate_user(user_id: str, request: Request, admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    u.status = "ACTIVE"
    append_audit(db, "user_activated", "User", u.id, actor_id=admin.id, actor_role="ADMIN",
                 payload={"to": "ACTIVE"}, request=request, commit=False)
    db.commit()
    return {"message": "User activated"}


class JurisdictionAssignIn(BaseModel):
    jurisdiction_ids: list[str]


@router.put("/users/{user_id}/jurisdiction")
def set_jurisdiction(user_id: str, body: JurisdictionAssignIn, request: Request, admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    from ..core.deps import BUYER_SIDE_ROLES, LEGACY_OFFICER_ROLES
    if u.role not in (*BUYER_SIDE_ROLES, *LEGACY_OFFICER_ROLES):
        raise HTTPException(status_code=400, detail="Jurisdiction applies to officer roles only")
    for jid in body.jurisdiction_ids:
        if not db.get(Jurisdiction, jid):
            raise HTTPException(status_code=400, detail=f"Unknown jurisdiction: {jid}")
    db.query(OfficerAssignment).filter(OfficerAssignment.user_id == u.id).delete()
    for i, jid in enumerate(body.jurisdiction_ids):
        db.add(OfficerAssignment(user_id=u.id, jurisdiction_id=jid, is_primary=(i == 0)))
    append_audit(db, "officer_jurisdiction_changed", "User", u.id, actor_id=admin.id, actor_role="ADMIN",
                 payload={"jurisdictions": body.jurisdiction_ids}, request=request, commit=False)
    db.commit()
    return {"message": "Jurisdictions updated"}


# ---------------- government employee master ----------------
# Government-portal sign-in is Govt Unique ID + OTP to the registered phone.
# The System Admin maintains this master; an officer can log in only when
# their record exists and is ACTIVE (first-time logins require approval).

class GovEmployeeIn(BaseModel):
    gov_id: str = Field(min_length=3, max_length=64)
    full_name: str = Field(min_length=2, max_length=255)
    phone: str = Field(min_length=10)
    role: str
    department: str | None = None


class GovEmployeeUpdateIn(BaseModel):
    full_name: str | None = None
    phone: str | None = None
    role: str | None = None
    department: str | None = None


@router.get("/gov-employees")
def list_gov_employees(admin: User = Depends(get_current_admin), db: Session = Depends(get_db), q: str | None = None, role: str | None = None):
    from ..core.otp import mask_mobile
    from ..models import GovEmployee

    query = db.query(GovEmployee)
    if role:
        query = query.filter(GovEmployee.role == role.upper())
    if q:
        like = f"%{q.strip().lower()}%"
        query = query.filter(
            func.lower(GovEmployee.gov_id).like(like)
            | func.lower(GovEmployee.full_name).like(like)
            | func.lower(GovEmployee.phone).like(like)
        )
    rows = query.order_by(GovEmployee.gov_id).all()
    out = []
    for e in rows:
        d = {
            "id": e.id, "gov_id": e.gov_id, "full_name": e.full_name,
            "phone_masked": mask_mobile(e.phone),
            "role": e.role, "department": e.department, "status": e.status,
            "user_id": e.user_id,
            "approved_at": e.approved_at.isoformat() if e.approved_at else None,
            "last_login_at": e.last_login_at.isoformat() if e.last_login_at else None,
        }
        if e.user_id:
            u = db.get(User, e.user_id)
            d["user_email"] = u.email if u else None
        out.append(d)
    return {"employees": out, "count": len(out)}


@router.post("/gov-employees", status_code=201)
def create_gov_employee(body: GovEmployeeIn, request: Request, admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    import secrets as _secrets

    from ..core.deps import OFFICER_ROLES
    from ..core.otp import normalize_mobile
    from ..models import GovEmployee
    from ..models.user import new_id

    GOV_ROLES = (*OFFICER_ROLES, "AUDITOR")
    if body.role.upper() not in GOV_ROLES:
        raise HTTPException(status_code=400, detail=f"Role must be a government role (one of {', '.join(GOV_ROLES)})")
    gov_id = body.gov_id.strip().upper()
    if db.query(GovEmployee).filter(GovEmployee.gov_id == gov_id).first():
        raise HTTPException(status_code=409, detail=f"Government ID {gov_id} already exists")
    phone = normalize_mobile(body.phone)
    e = GovEmployee(
        gov_id=gov_id, full_name=body.full_name.strip(), phone=phone,
        role=body.role.upper(), department=body.department,
        status="PENDING_APPROVAL", created_by=admin.id,
    )
    db.add(e)
    db.flush()
    # auto-provision the linked user (inactive until admin approval)
    user = User(
        id=new_id(),
        email=f"{gov_id.lower()}@gov.in",
        mobile=phone,
        password_hash=_secrets.token_urlsafe(32),  # gov sign-in is OTP-only
        full_name=body.full_name.strip(),
        role=e.role,
        status="PENDING_APPROVAL",
        is_verified=False,
    )
    db.add(user)
    db.flush()
    e.user_id = user.id
    append_audit(db, "gov_employee_created", "GovEmployee", e.id, actor_id=admin.id, actor_role="ADMIN",
                 payload={"gov_id": gov_id, "role": e.role, "status": e.status}, request=request, commit=False)
    db.commit()
    return {"id": e.id, "gov_id": e.gov_id, "status": e.status,
            "message": f"Employee added — pending your approval before first login"}


def _get_gov_employee_or_404(db: Session, emp_id: str):
    from ..models import GovEmployee

    e = db.get(GovEmployee, emp_id)
    if not e:
        raise HTTPException(status_code=404, detail="Employee not found")
    return e


@router.post("/gov-employees/{emp_id}/approve")
def approve_gov_employee(emp_id: str, request: Request, admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    e = _get_gov_employee_or_404(db, emp_id)
    if e.status == "ACTIVE":
        return {"message": "Already approved"}
    e.status = "ACTIVE"
    e.approved_by = admin.id
    e.approved_at = utcnow()
    if e.user_id:
        u = db.get(User, e.user_id)
        if u:
            u.status = "ACTIVE"
            u.is_verified = True
    append_audit(db, "gov_employee_approved", "GovEmployee", e.id, actor_id=admin.id, actor_role="ADMIN",
                 payload={"gov_id": e.gov_id}, request=request, commit=False)
    db.commit()
    return {"message": f"{e.gov_id} approved — can now sign in with Govt ID + OTP"}


@router.post("/gov-employees/{emp_id}/suspend")
def suspend_gov_employee(emp_id: str, request: Request, admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    e = _get_gov_employee_or_404(db, emp_id)
    e.status = "SUSPENDED"
    if e.user_id:
        u = db.get(User, e.user_id)
        if u and u.status == "ACTIVE":
            u.status = "SUSPENDED"
    append_audit(db, "gov_employee_suspended", "GovEmployee", e.id, actor_id=admin.id, actor_role="ADMIN",
                 payload={"gov_id": e.gov_id}, request=request, commit=False)
    db.commit()
    return {"message": f"{e.gov_id} suspended — sign-in blocked"}


@router.put("/gov-employees/{emp_id}")
def update_gov_employee(emp_id: str, body: GovEmployeeUpdateIn, request: Request, admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    from ..core.deps import OFFICER_ROLES
    from ..core.otp import normalize_mobile

    e = _get_gov_employee_or_404(db, emp_id)
    data = body.model_dump(exclude_none=True)
    changed = []
    if "full_name" in data:
        e.full_name = data["full_name"].strip()
        changed.append("full_name")
    if "phone" in data:
        e.phone = normalize_mobile(data["phone"])
        changed.append("phone")
    if "role" in data:
        GOV_ROLES = (*OFFICER_ROLES, "AUDITOR")
        if data["role"].upper() not in GOV_ROLES:
            raise HTTPException(status_code=400, detail="Role must be a government role")
        e.role = data["role"].upper()
        changed.append("role")
    if "department" in data:
        e.department = data["department"]
        changed.append("department")
    if e.user_id:
        u = db.get(User, e.user_id)
        if u:
            if "full_name" in data:
                u.full_name = e.full_name
            if "phone" in data:
                u.mobile = e.phone
            if "role" in data:
                u.role = e.role
    append_audit(db, "gov_employee_updated", "GovEmployee", e.id, actor_id=admin.id, actor_role="ADMIN",
                 payload={"gov_id": e.gov_id, "fields": changed}, request=request, commit=False)
    db.commit()
    return {"message": "Employee updated"}


# ---------------- jurisdictions ----------------

class JurisdictionIn(BaseModel):
    code: str
    district: str | None = None
    state: str | None = None
    department: str | None = None
    organization: str | None = None
    description: str | None = None


@router.get("/jurisdictions")
def list_jurisdictions(admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    rows = db.query(Jurisdiction).all()
    return {"jurisdictions": [
        {"id": j.id, "code": j.code, "district": j.district, "state": j.state,
         "department": j.department, "organization": j.organization, "description": j.description}
        for j in rows
    ]}


@router.post("/jurisdictions")
def create_jurisdiction(body: JurisdictionIn, request: Request, admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    if db.query(Jurisdiction).filter(Jurisdiction.code == body.code).first():
        raise HTTPException(status_code=409, detail="Jurisdiction code already exists")
    j = Jurisdiction(**body.model_dump())
    db.add(j)
    append_audit(db, "jurisdiction_created", "Jurisdiction", j.id, actor_id=admin.id, actor_role="ADMIN",
                 payload={"code": body.code}, request=request, commit=False)
    db.commit()
    return {"jurisdiction": {"id": j.id, "code": j.code}}


# ---------------- tenders ----------------

class TenderIn(BaseModel):
    title: str
    description: str | None = None
    department: str
    organization: str = "CPCL"
    category: str | None = None
    product_service_category: str | None = None
    district: str
    state: str
    jurisdiction_code: str
    value_min: float | None = None
    value_max: float | None = None
    close_date: str
    open_date: str | None = None
    bid_deadline: str | None = None  # ISO datetime (naive UTC); default close_date @ 18:00 IST
    msme_preference: bool = False
    make_in_india_required: bool = False
    startup_eligible: bool = False
    required_turnover: int | None = None
    min_local_content: int | None = None
    required_document_types: list[str] = Field(default_factory=list)
    notes: str | None = None
    organization_id: str | None = None
    procurement_method: str = "GEM_BID"
    application_fee_required: bool = True
    application_fee_amount: float | None = None
    application_fee_refundable: bool = False
    bid_security_required: bool = False
    bid_security_amount: float | None = None
    bid_security_percent: float | None = None
    bid_security_exemption_allowed: bool = True


def _org_out(o: ProcuringOrganization) -> dict:
    return {
        "id": o.id, "name": o.name, "short_code": o.short_code, "org_type": o.org_type,
        "parent_ministry": o.parent_ministry, "procurement_division": o.procurement_division,
        "procurement_method_default": o.procurement_method_default,
        "committee_min_members": o.committee_min_members,
        "committee_requires_finance": o.committee_requires_finance,
        "committee_requires_user_rep": o.committee_requires_user_rep,
        "authority_bands": o.authority_bands or [],
        "active": o.active,
    }


def _authority_chain_for(org: ProcuringOrganization | None, value: float | None) -> list[dict] | None:
    """Resolve the delegated-authority chain for a tender value under an
    organization's configured bands (titles are org-specific, not fixed
    L1/L2/L3 labels)."""
    if not org or not org.authority_bands:
        return None
    bands = sorted(org.authority_bands, key=lambda b: b.get("min_value") or 0)
    v = float(value or 0)
    chain = []
    for b in bands:
        lo = float(b.get("min_value") or 0)
        hi = b.get("max_value")
        if v >= lo and (hi is None or v <= float(hi)):
            chain.append({
                "band": f"{lo:,.0f}–{hi:,.0f}" if hi else f"{lo:,.0f}+",
                "review_role_title": b.get("review_role_title"),
                "approval_role_title": b.get("approval_role_title"),
                "committee_required": bool(b.get("committee_required", False)),
            })
    return chain


def _tender_out(t: Tender, db: Session, with_rules: bool = False) -> dict:
    out = {
        "id": t.id, "tender_no": t.tender_no, "title": t.title, "description": t.description,
        "department": t.department, "organization": t.organization, "category": t.category,
        "product_service_category": t.product_service_category,
        "district": t.district, "state": t.state, "jurisdiction_code": t.jurisdiction_code,
        "procurement_method": t.procurement_method,
        "application_fee_required": True,
        "application_fee_amount": float(settings.APPLICATION_FEE_AMOUNT),
        "application_fee_refundable": False,
        "bid_security_required": bool(t.bid_security_required),
        "bid_security_amount": float(t.bid_security_amount) if t.bid_security_amount is not None else None,
        "bid_security_percent": float(t.bid_security_percent) if t.bid_security_percent is not None else None,
        "bid_security_exemption_allowed": bool(t.bid_security_exemption_allowed),
        "procuring_organization": _org_out(t.procuring_organization) if t.procuring_organization else None,
        "value_min": float(t.value_min) if t.value_min is not None else None,
        "value_max": float(t.value_max) if t.value_max is not None else None,
        "close_date": t.close_date.isoformat() if t.close_date else None,
        "open_date": t.open_date.isoformat() if t.open_date else None,
        "bid_deadline": t.bid_deadline.isoformat() if t.bid_deadline else None,
        "effective_bid_deadline": (
            effective_bid_deadline(t).isoformat() if effective_bid_deadline(t) else None
        ),
        "bids_open": tender_bids_open(t),
        "status": t.status, "msme_preference": t.msme_preference,
        "make_in_india_required": t.make_in_india_required, "startup_eligible": t.startup_eligible,
        "required_turnover": t.required_turnover, "min_local_content": t.min_local_content,
        "required_document_types": t.required_document_types or [], "notes": t.notes,
        "rules_version": t.rules_version,
        "corrigendum_count": len(t.corrigenda),
        "corrigenda": [_corr_out(c) for c in t.corrigenda],
        "application_count": db.query(Application).filter(Application.tender_id == t.id).count(),
    }
    if with_rules:
        out["rules"] = [
            {"id": r.id, "rule_key": r.rule_key, "label": r.label, "source": r.source,
             "mandatory": r.mandatory, "condition_type": r.condition_type,
             "condition_value": r.condition_value, "weight": r.weight,
             "failure_action": r.failure_action, "allows_manual_review": r.allows_manual_review,
             "hard_disqualification": r.hard_disqualification, "enabled": r.enabled,
             "sort_order": r.sort_order, "compliance_layer": r.compliance_layer,
             "clause_ref": r.clause_ref}
            for r in t.rules
        ]
    return out



@router.get("/tenders")
def list_tenders(admin: User = Depends(get_current_admin), db: Session = Depends(get_db), status: str | None = None):
    query = db.query(Tender)
    if status:
        query = query.filter(Tender.status == status)
    return {"tenders": [_tender_out(t, db, with_rules=False) for t in query.order_by(Tender.created_at.desc()).all()]}


@router.post("/tenders")
def create_tender(body: TenderIn, request: Request, admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    close = date.fromisoformat(body.close_date)
    if close < date.today():
        raise HTTPException(status_code=400, detail="close_date must be in the future")
    count = db.query(Tender).count() + 1
    data = body.model_dump()
    data["application_fee_required"] = True
    data["application_fee_amount"] = settings.APPLICATION_FEE_AMOUNT
    data["application_fee_refundable"] = False
    data.pop("open_date", None)
    data.pop("close_date", None)
    data["bid_deadline"] = datetime.fromisoformat(body.bid_deadline) if body.bid_deadline else None
    t = Tender(
        tender_no=f"TDR-2026-{count:03d}",
        **data,
        open_date=date.fromisoformat(body.open_date) if body.open_date else date.today(),
        close_date=close,
        created_by=admin.id,
    )
    db.add(t)
    db.flush()
    append_audit(db, "tender_created", "Tender", t.id, actor_id=admin.id, actor_role="ADMIN",
                 payload={"tender_no": t.tender_no}, request=request, commit=False)
    db.commit()
    return {"tender": _tender_out(t, db, with_rules=True)}


@router.get("/tenders/{tender_id}")
def get_tender(tender_id: str, admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    t = db.get(Tender, tender_id)
    if not t:
        raise HTTPException(status_code=404, detail="Tender not found")
    # NOTE: returns the bare tender object (frontend admin detail page expects this shape)
    return _tender_out(t, db, with_rules=True)


@router.put("/tenders/{tender_id}")
def update_tender(tender_id: str, body: TenderIn, request: Request, admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    t = db.get(Tender, tender_id)
    if not t:
        raise HTTPException(status_code=404, detail="Tender not found")
    data = body.model_dump()
    data["application_fee_required"] = True
    data["application_fee_amount"] = settings.APPLICATION_FEE_AMOUNT
    data["application_fee_refundable"] = False
    data["close_date"] = date.fromisoformat(data["close_date"])
    data["open_date"] = date.fromisoformat(data["open_date"]) if data.get("open_date") else t.open_date
    data["bid_deadline"] = datetime.fromisoformat(data["bid_deadline"]) if data.get("bid_deadline") else t.bid_deadline
    for k, v in data.items():
        setattr(t, k, v)
    t.rules_version += 1
    # version rules: bump
    for r in t.rules:
        r.version += 1
    append_audit(db, "tender_updated", "Tender", t.id, actor_id=admin.id, actor_role="ADMIN",
                 payload={"rules_version": t.rules_version}, request=request, commit=False)
    db.commit()
    return {"tender": _tender_out(t, db, with_rules=True)}


@router.post("/tenders/{tender_id}/open-bids")
def open_bids(tender_id: str, request: Request, admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    """Force-open bid evaluation (sets the bid deadline to now).

    Used for demos and operational recovery; audited. In normal operation
    bids open automatically at the deadline.
    """
    t = db.get(Tender, tender_id)
    if not t:
        raise HTTPException(status_code=404, detail="Tender not found")
    if tender_bids_open(t):
        return {"message": "Bids already open", "tender_no": t.tender_no}
    prev = effective_bid_deadline(t)
    t.bid_deadline = utcnow()
    t.force_opened = True
    # NOTE: status is left as-is — the bidder portal lists PUBLISHED tenders;
    # the scheduled deadline job (spec §6) sets CLOSED for real deadline
    # transitions but skips force_opened tenders. Force-open is a demo/ops
    # override and must not hide the tender from bidders.
    append_audit(db, "tender_bids_force_opened", "Tender", t.id, actor_id=admin.id, actor_role="ADMIN",
                 payload={"previous_deadline": prev.isoformat() if prev else None},
                 request=request, commit=False)
    db.commit()
    return {"message": "Bids opened — evaluation may proceed", "tender_no": t.tender_no}


class RuleIn(BaseModel):
    rule_key: str
    label: str
    source: str
    mandatory: bool = True
    condition_type: str
    condition_value: str | None = None
    weight: int = Field(default=10, ge=1, le=100)
    failure_action: str = "FAIL"
    allows_manual_review: bool = False
    hard_disqualification: bool = False
    enabled: bool = True
    sort_order: int = 0
    compliance_layer: str = "STATUTORY"  # STATUTORY | SECTOR_TECHNICAL
    clause_ref: str | None = None


# ---------------- procuring organizations ----------------

class OrgIn(BaseModel):
    name: str
    short_code: str
    org_type: str = "CPSE"
    parent_ministry: str | None = None
    procurement_division: str | None = None
    procurement_method_default: str | None = "GEM_BID"
    committee_min_members: int = Field(default=3, ge=1, le=9)
    committee_requires_finance: bool = True
    committee_requires_user_rep: bool = True
    authority_bands: list[dict] = Field(default_factory=list)
    notes: str | None = None
    active: bool = True


def _tender_value(t: Tender) -> float | None:
    if t.value_max is not None:
        return float(t.value_max)
    if t.value_min is not None:
        return float(t.value_min)
    return None


@router.get("/organizations")
def list_organizations(admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    out = []
    for o in db.query(ProcuringOrganization).order_by(ProcuringOrganization.name).all():
        d = _org_out(o)
        d["tender_count"] = db.query(Tender).filter(Tender.organization_id == o.id).count()
        out.append(d)
    return {"organizations": out}


@router.post("/organizations")
def upsert_organization(body: OrgIn, request: Request, admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    o = db.query(ProcuringOrganization).filter(
        (ProcuringOrganization.short_code == body.short_code.upper())
        | (ProcuringOrganization.name == body.name)
    ).first()
    is_new = o is None
    if is_new:
        o = ProcuringOrganization(short_code=body.short_code.upper())
        db.add(o)
    for k, v in body.model_dump().items():
        if k == "short_code":
            v = str(v).upper()
        setattr(o, k, v)
    db.flush()
    append_audit(db, "organization_created" if is_new else "organization_updated",
                 "ProcuringOrganization", o.id, actor_id=admin.id, actor_role="ADMIN",
                 payload={"name": o.name, "short_code": o.short_code},
                 request=request, commit=False)
    db.commit()
    return {"organization": _org_out(o)}


@router.put("/tenders/{tender_id}/rules")
def save_rules(tender_id: str, body: list[RuleIn], request: Request, admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    """Admin Rule Builder bulk save (form-driven configuration, also supports JSON)."""
    t = db.get(Tender, tender_id)
    if not t:
        raise HTTPException(status_code=404, detail="Tender not found")
    if t.status == "CLOSED":
        raise HTTPException(status_code=400, detail="Cannot edit rules of a closed tender")
    # blacklist rule is a hard compliance rule: ensure it exists and stays hard
    keys = {r.rule_key for r in body}
    db.query(TenderRule).filter(TenderRule.tender_id == t.id).delete()
    for i, r in enumerate(body):
        db.add(TenderRule(
            tender_id=t.id,
            rule_key=r.rule_key,
            label=r.label,
            source=r.source.upper(),
            mandatory=r.mandatory,
            condition_type=r.condition_type,
            condition_value=r.condition_value,
            weight=r.weight,
            failure_action=r.failure_action,
            allows_manual_review=r.allows_manual_review,
            hard_disqualification=(r.hard_disqualification or r.rule_key == "not_blacklisted"),
            enabled=r.enabled,
            sort_order=r.sort_order or i,
            version=t.rules_version,
            compliance_layer=r.compliance_layer.upper() if r.compliance_layer in ("STATUTORY", "SECTOR_TECHNICAL") else "STATUTORY",
            clause_ref=r.clause_ref,
        ))
    if "not_blacklisted" not in keys:
        db.add(TenderRule(
            tender_id=t.id, rule_key="not_blacklisted", label="Not blacklisted/debarred",
            source="BLACKLIST", mandatory=True, condition_type="not_blacklisted",
            condition_value=None, weight=100, failure_action="FAIL",
            allows_manual_review=False, hard_disqualification=True, enabled=True, sort_order=999,
            version=t.rules_version, compliance_layer="STATUTORY",
        ))
    append_audit(db, "tender_rules_saved", "Tender", t.id, actor_id=admin.id, actor_role="ADMIN",
                 payload={"rule_count": len(body) + (0 if "not_blacklisted" in keys else 1)},
                 request=request, commit=False)
    db.commit()
    return {"message": "Rules saved", "tender": _tender_out(t, db, with_rules=True)}


@router.post("/tenders/{tender_id}/publish")
def publish_tender(tender_id: str, request: Request, admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    t = db.get(Tender, tender_id)
    if not t:
        raise HTTPException(status_code=404, detail="Tender not found")
    if not t.rules:
        raise HTTPException(status_code=400, detail="Configure at least one rule before publishing")
    t.status = "PUBLISHED"
    append_audit(db, "tender_published", "Tender", t.id, actor_id=admin.id, actor_role="ADMIN", request=request, commit=False)
    db.commit()
    users = db.query(User).filter(User.role == "BIDDER", User.status == "ACTIVE").all()
    for u in users:
        notify(db, u.id, "tender_published", message=f"New tender {t.tender_no}: {t.title}", entity_type="Tender", entity_id=t.id, commit=False)
    db.commit()
    return {"message": "Tender published", "status": t.status}


@router.post("/tenders/{tender_id}/close")
def close_tender(tender_id: str, request: Request, admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    t = db.get(Tender, tender_id)
    if not t:
        raise HTTPException(status_code=404, detail="Tender not found")
    t.status = "CLOSED"
    append_audit(db, "tender_closed", "Tender", t.id, actor_id=admin.id, actor_role="ADMIN", request=request, commit=False)
    db.commit()
    return {"message": "Tender closed"}


# ---------------- corrigenda (corrigendum-aware verification) ----------------

class CorrigendumIn(BaseModel):
    title: str
    summary: str
    changes: list[dict] = Field(default_factory=list)  # [{clause, old, new, description}]
    deadline_extended_to: str | None = None
    affects_rules: bool = True


def _corr_out(c: TenderCorrigendum) -> dict:
    return {
        "id": c.id, "seq_no": c.seq_no, "title": c.title, "summary": c.summary,
        "changes": c.changes or [], "deadline_extended_to": c.deadline_extended_to.isoformat() if c.deadline_extended_to else None,
        "affects_rules": c.affects_rules,
        "published_at": c.published_at.isoformat() if c.published_at else None,
    }


@router.get("/tenders/{tender_id}/corrigenda")
def list_corrigenda(tender_id: str, admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    t = db.get(Tender, tender_id)
    if not t:
        raise HTTPException(status_code=404, detail="Tender not found")
    return {"corrigenda": [_corr_out(c) for c in t.corrigenda]}


@router.post("/tenders/{tender_id}/corrigenda")
def publish_corrigendum(tender_id: str, body: CorrigendumIn, request: Request,
                        admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    """Publish a corrigendum. Per the specification, a compliance result based
    on the original document can become INVALID after an amendment: this bumps
    the tender's rules_version, marks every active application's report stale,
    and notifies assigned officers and applicants to re-verify."""
    t = db.get(Tender, tender_id)
    if not t:
        raise HTTPException(status_code=404, detail="Tender not found")
    if t.status == "CLOSED":
        raise HTTPException(status_code=400, detail="Cannot publish corrigenda for a closed tender")
    seq = (db.query(func.max(TenderCorrigendum.seq_no))
           .filter(TenderCorrigendum.tender_id == t.id).scalar() or 0) + 1
    c = TenderCorrigendum(
        tender_id=t.id, seq_no=seq, title=body.title, summary=body.summary,
        changes=body.changes or [], affects_rules=body.affects_rules, published_by=admin.id,
    )
    extended = None
    if body.deadline_extended_to:
        extended = date.fromisoformat(body.deadline_extended_to)
        if extended <= t.close_date:
            raise HTTPException(status_code=400, detail="deadline_extended_to must be after the current close date")
        c.deadline_extended_to = extended
        t.close_date = extended
    if body.affects_rules:
        t.rules_version += 1
        for r in t.rules:
            r.version = t.rules_version
    db.add(c)
    db.flush()

    # stale-mark + notify every active application on this tender
    active = db.query(Application).filter(
        Application.tender_id == t.id,
        Application.status.notin_(["Approved", "Rejected", "Withdrawn", "Closed"]),
    ).all()
    for a in active:
        mark_report_stale(db, a, f"Corrigendum {seq} published: {body.title}")
        if a.assigned_l1_id:
            notify(db, a.assigned_l1_id, "tender_corrigendum",
                   message=f"Corrigendum {seq} on {t.tender_no} — re-verify {a.application_no}",
                   entity_type="Application", entity_id=a.id, commit=False)
        notify(db, a.bidder_user_id, "tender_corrigendum",
               message=f"Corrigendum {seq} published for {t.tender_no} ({body.title}). "
                       f"Your application {a.application_no} must be re-verified.",
               entity_type="Tender", entity_id=t.id, commit=False)

    append_audit(db, "corrigendum_published", "Tender", t.id, actor_id=admin.id, actor_role="ADMIN",
                 payload={"seq_no": seq, "title": body.title, "rules_version": t.rules_version,
                          "deadline_extended_to": extended.isoformat() if extended else None,
                          "applications_marked_stale": len(active)},
                 request=request, commit=False)
    db.commit()
    return {"corrigendum": _corr_out(c), "applications_marked_stale": len(active)}


# ---------------- blacklist reference database ----------------

class BlacklistIn(BaseModel):
    entity_name: str
    pan: str | None = None
    gstin: str | None = None
    reference_id: str | None = None
    reason: str
    source_authority: str
    official_order_reference: str | None = None
    valid_from: str | None = None
    valid_until: str | None = None
    review_status: str | None = None


@router.get("/blacklist")
def list_blacklist(admin: User = Depends(get_current_admin), db: Session = Depends(get_db), status: str | None = None):
    query = db.query(BlacklistEntry)
    if status:
        query = query.filter(BlacklistEntry.status == status)
    rows = query.order_by(BlacklistEntry.created_at.desc()).all()
    return {"label": "Reference Blacklist Database — Source and Last Verification Date Recorded",
            "entries": [
                {"id": e.id, "entity_name": e.entity_name, "pan_masked": mask_pan(e.pan) if e.pan else None,
                 "gstin_masked": mask_gstin(e.gstin) if e.gstin else None, "reference_id": e.reference_id,
                 "reason": e.reason, "source_authority": e.source_authority,
                 "official_order_reference": e.official_order_reference,
                 "valid_from": e.valid_from.isoformat() if e.valid_from else None,
                 "valid_until": e.valid_until.isoformat() if e.valid_until else None,
                 "status": e.status, "review_status": e.review_status, "version": e.version,
                 "created_at": e.created_at.isoformat()}
                for e in rows
            ]}


@router.post("/blacklist")
def add_blacklist(body: BlacklistIn, request: Request, admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    e = BlacklistEntry(
        entity_name=body.entity_name, pan=body.pan, gstin=body.gstin, reference_id=body.reference_id,
        reason=body.reason, source_authority=body.source_authority,
        official_order_reference=body.official_order_reference,
        valid_from=date.fromisoformat(body.valid_from) if body.valid_from else date.today(),
        valid_until=date.fromisoformat(body.valid_until) if body.valid_until else None,
        review_status=body.review_status, created_by=admin.id,
    )
    db.add(e)
    append_audit(db, "blacklist_entry_added", "BlacklistEntry", e.id, actor_id=admin.id, actor_role="ADMIN",
                 payload={"entity": body.entity_name}, request=request, commit=False)
    db.commit()
    return {"message": "Blacklist reference entry added", "id": e.id}


class BlacklistStatusIn(BaseModel):
    status: str = Field(pattern=r"^(ACTIVE|EXPIRED|APPEAL_PENDING|REMOVED)$")
    review_status: str | None = None


@router.post("/blacklist/{entry_id}/status")
def blacklist_status(entry_id: str, body: BlacklistStatusIn, request: Request, admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    e = db.get(BlacklistEntry, entry_id)
    if not e:
        raise HTTPException(status_code=404, detail="Entry not found")
    old = e.status
    e.status = body.status
    e.review_status = body.review_status or e.review_status
    e.version += 1
    # expiry scheduling: auto-transition handled on read; record explicit action here
    append_audit(db, "blacklist_status_changed", "BlacklistEntry", e.id, actor_id=admin.id, actor_role="ADMIN",
                 payload={"from": old, "to": body.status}, request=request, commit=False)
    db.commit()
    return {"message": "Status updated (no hard delete is permitted)", "status": e.status, "version": e.version}


# ---------------- override monitoring & escalation ----------------

@router.get("/overrides")
def list_overrides(
    admin: User = Depends(get_current_admin), db: Session = Depends(get_db),
    district: str | None = None, officer_id: str | None = None,
    tender_id: str | None = None, date_from: str | None = None, date_to: str | None = None,
    severity: str | None = None,
):
    query = db.query(OverrideRecord).join(Application, Application.id == OverrideRecord.application_id)
    if district:
        query = query.join(Tender, Tender.id == Application.tender_id).filter(Tender.district == district)
    if officer_id:
        query = query.filter(OverrideRecord.officer_id == officer_id)
    if tender_id:
        query = query.filter(Application.tender_id == tender_id)
    if severity:
        query = query.filter(OverrideRecord.severity == severity)
    if date_from:
        query = query.filter(OverrideRecord.created_at >= datetime.fromisoformat(date_from))
    if date_to:
        query = query.filter(OverrideRecord.created_at <= datetime.fromisoformat(date_to + "T23:59:59"))
    rows = query.order_by(OverrideRecord.created_at.desc()).all()
    out = []
    for o in rows:
        off = db.get(User, o.officer_id)
        out.append({
            "id": o.id, "application_no": o.application.application_no,
            "tender_no": o.application.tender.tender_no,
            "district": o.application.tender.district,
            "officer": off.full_name if off else None,
            "officer_email": off.email if off else None,
            "level": o.level, "severity": o.severity, "status": o.status,
            "reason_category": o.reason_category,
            "finding": o.overridden_finding,
            "justification": o.justification,
            "admin_cosign_required": o.admin_cosign_required,
            "admin_cosigned_by": (db.get(User, o.admin_cosigned_by).full_name if o.admin_cosigned_by and db.get(User, o.admin_cosigned_by) else None),
            "admin_cosigned_at": o.admin_cosigned_at.isoformat() if o.admin_cosigned_at else None,
            "vigilance_referral_id": o.vigilance_referral_id,
            "review_note": o.review_note,
            "created_at": o.created_at.isoformat(),
        })
    # officer override frequency
    freq: dict[str, int] = {}
    for o in rows:
        off = db.get(User, o.officer_id)
        key = off.email if off else o.officer_id
        freq[key] = freq.get(key, 0) + 1
    return {"overrides": out, "officer_frequency": freq}


class ReviewIn(BaseModel):
    review_note: str | None = None
    action: str = "mark_reviewed"  # mark_reviewed | reject | escalate


@router.post("/overrides/{override_id}/review")
def review_override(override_id: str, body: ReviewIn, request: Request, admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    o = db.get(OverrideRecord, override_id)
    if not o:
        raise HTTPException(status_code=404, detail="Override not found")
    if body.action == "reject":
        o.status = "REJECTED"
        o.review_note = body.review_note or "Override rejected by Admin"
        o.reviewed_by = admin.id
        o.reviewed_at = utcnow()
        o.application.status = "Pending L1 Review"
        db.add(ApplicationStatusHistory(application_id=o.application.id, status="Pending L1 Review",
                                        actor_id=admin.id, actor_role="ADMIN", remarks="Override rejected — returned to L1"))
        notify(db, o.officer_id, "l2_action_required",
               message=f"Your override on {o.application.application_no} was REJECTED by Admin. Re-decision required.",
               entity_type="Application", entity_id=o.application.id)
        append_audit(db, "override_rejected", "OverrideRecord", o.id, actor_id=admin.id, actor_role="ADMIN",
                     payload={"note": body.review_note}, request=request, commit=False)
        db.commit()
        return {"message": "Override rejected; application returned to L1", "status": o.status}
    if body.action == "escalate":
        o.status = "ESCALATED"
        o.review_note = body.review_note or "Escalated by Admin"
        o.reviewed_by = admin.id
        o.reviewed_at = utcnow()
        append_audit(db, "override_escalated", "OverrideRecord", o.id, actor_id=admin.id, actor_role="ADMIN",
                     payload={"note": body.review_note}, request=request, commit=False)
        db.commit()
        return {"message": "Override escalated", "status": o.status}
    o.status = "REVIEWED"
    o.review_note = body.review_note or "Reviewed"
    o.reviewed_by = admin.id
    o.reviewed_at = utcnow()
    append_audit(db, "override_reviewed", "OverrideRecord", o.id, actor_id=admin.id, actor_role="ADMIN",
                 payload={"note": body.review_note}, request=request, commit=False)
    db.commit()
    return {"message": "Override marked as reviewed", "status": o.status}


@router.post("/overrides/{override_id}/cosign")
def cosign_override(override_id: str, request: Request, admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    o = db.get(OverrideRecord, override_id)
    if not o:
        raise HTTPException(status_code=404, detail="Override not found")
    if not o.admin_cosign_required:
        raise HTTPException(status_code=400, detail="Co-sign not required for this override")
    if o.admin_cosigned_at:
        raise HTTPException(status_code=400, detail="Already co-signed")
    o.admin_cosigned_by = admin.id
    o.admin_cosigned_at = utcnow()
    if o.status == "PENDING_COSIGN":
        o.status = "REVIEWED"
    o.application.status = "Pending L2 Approval"
    db.add(ApplicationStatusHistory(application_id=o.application.id, status="Pending L2 Approval",
                                    actor_id=admin.id, actor_role="ADMIN", remarks="Admin co-sign completed"))
    if o.application.assigned_l2_id:
        notify(db, o.application.assigned_l2_id, "l2_action_required",
               message=f"Admin co-sign complete for {o.application.application_no}. L2 decision now possible.",
               entity_type="Application", entity_id=o.application.id)
    append_audit(db, "admin_cosign_completed", "OverrideRecord", o.id, actor_id=admin.id, actor_role="ADMIN",
                 request=request, commit=False)
    db.commit()
    return {"message": "Admin co-sign recorded", "application_status": o.application.status}


# ---------------- audit viewer ----------------

def _audit_row(r: AuditLog, full_ip: bool = False) -> dict:
    from ..ipcontext import mask_ip
    ip = r.source_ip
    return {
        "id": r.id, "actor_id": r.actor_id, "actor_role": r.actor_role, "action": r.action,
        "entity_type": r.entity_type, "entity_id": r.entity_id,
        "tender_id": r.tender_id, "application_id": r.application_id,
        "source_ip": ip if full_ip else mask_ip(ip),
        "ip_source": r.ip_source, "request_id": r.request_id, "session_id": r.session_id,
        "user_agent": r.user_agent,
        "payload": r.payload,
        "prev_hash": r.prev_hash, "record_hash": r.record_hash,
        "created_at": r.created_at.isoformat(),
    }


@router.get("/audit-logs")
def audit_logs(
    viewer: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    action: str | None = None,
    actor_id: str | None = None,
    tender_id: str | None = None,
    limit: int = 200,
):
    """IP-aware audit explorer. IPs are masked by default; full disclosure
    requires the VIEW_FULL_IP permission (per-event, re-authenticated).

    Access: ADMIN or read-only AUDITOR (oversight role)."""
    from ..policies.permissions import AUDITOR_ROLES
    if viewer.role not in ("ADMIN", *AUDITOR_ROLES):
        raise HTTPException(status_code=403, detail="Insufficient permission")
    full_ip = viewer.role == "ADMIN" and _can_see_full_ips(db, viewer)
    query = db.query(AuditLog)
    if action:
        query = query.filter(AuditLog.action == action)
    if actor_id:
        query = query.filter(AuditLog.actor_id == actor_id)
    if tender_id:
        query = query.filter(AuditLog.tender_id == tender_id)
    rows = query.order_by(AuditLog.created_at.desc()).limit(min(limit, 1000)).all()
    return {"logs": [_audit_row(r, full_ip=full_ip) for r in rows]}


def _can_see_full_ips(db: Session, user: User) -> bool:
    from ..policies.permissions import VIEW_FULL_IP, has_permission
    return has_permission(db, user, VIEW_FULL_IP)


class RevealIpIn(BaseModel):
    event_id: str
    password: str


@router.post("/audit-logs/reveal-ip")
def reveal_audit_ip(
    body: RevealIpIn,
    request: Request,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Reveal the full source IP for one audit event.

    Requires the VIEW_FULL_IP permission, password re-authentication, and
    the reveal action itself is recorded as a high-severity audit event.
    """
    from ..core.security import verify_password
    from ..policies.permissions import VIEW_FULL_IP, has_permission, require_permission
    if not has_permission(db, admin, VIEW_FULL_IP):
        raise HTTPException(status_code=403, detail="Requires VIEW_FULL_IP permission")
    if not verify_password(body.password, admin.password_hash):
        append_audit(db, "audit_ip_reveal_rejected", "AuditLog", body.event_id,
                     actor_id=admin.id, actor_role=admin.role,
                     payload={"reason": "REAUTH_FAILED"}, request=request, commit=False)
        db.commit()
        raise HTTPException(status_code=401, detail="Re-authentication failed")
    log = db.get(AuditLog, body.event_id)
    if not log:
        raise HTTPException(status_code=404, detail="Audit event not found")
    append_audit(db, "audit_ip_revealed", "AuditLog", log.id,
                 actor_id=admin.id, actor_role=admin.role,
                 payload={"target_event": log.id, "target_action": log.action, "severity": "HIGH"},
                 request=request, commit=False)
    db.commit()
    return {"event_id": log.id, "source_ip": log.source_ip, "ip_source": log.ip_source}


@router.get("/audit-logs/verify")
def verify_audit_chain(admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    return verify_chain(db)


# ---------------- vigilance referral management ----------------

@router.get("/vigilance-referrals")
def list_referrals(admin: User = Depends(get_current_admin), db: Session = Depends(get_db), status: str | None = None):
    query = db.query(VigilanceReferral)
    if status:
        query = query.filter(VigilanceReferral.status == status)
    rows = query.order_by(VigilanceReferral.created_at.desc()).all()
    return {"referrals": [
        {"id": r.id, "referral_no": r.referral_no, "application_no": r.application.application_no,
         "application_id": r.application_id, "status": r.status,
         "severity": (r.report or {}).get("deterministic_findings", {}).get("risk_level") if r.report else None,
         "created_at": r.created_at.isoformat(),
         "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None,
         "filed_at": r.filed_at.isoformat() if r.filed_at else None}
        for r in rows
    ]}


@router.get("/vigilance-referrals/{referral_id}")
def referral_detail(referral_id: str, admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    r = db.get(VigilanceReferral, referral_id)
    if not r:
        raise HTTPException(status_code=404, detail="Referral not found")
    return {"referral": {"id": r.id, "referral_no": r.referral_no, "status": r.status,
                         "report": r.report, "notice_text": r.notice_text,
                         "created_at": r.created_at.isoformat()}}


class ReferralStatusIn(BaseModel):
    note: str | None = None


@router.post("/vigilance-referrals/{referral_id}/review")
def review_referral(referral_id: str, body: ReferralStatusIn, request: Request, admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    r = db.get(VigilanceReferral, referral_id)
    if not r:
        raise HTTPException(status_code=404, detail="Referral not found")
    if r.status != "DRAFT":
        raise HTTPException(status_code=400, detail=f"Cannot review referral in status {r.status}")
    r.status = "REVIEWED"
    r.reviewed_by = admin.id
    r.reviewed_at = utcnow()
    append_audit(db, "vigilance_referral_reviewed", "VigilanceReferral", r.id, actor_id=admin.id, actor_role="ADMIN",
                 request=request, commit=False)
    db.commit()
    return {"message": "Referral reviewed", "status": r.status}


@router.post("/vigilance-referrals/{referral_id}/mark-ready")
def mark_ready(referral_id: str, body: ReferralStatusIn, request: Request, admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    r = db.get(VigilanceReferral, referral_id)
    if not r:
        raise HTTPException(status_code=404, detail="Referral not found")
    if r.status not in ("DRAFT", "REVIEWED"):
        raise HTTPException(status_code=400, detail=f"Cannot mark ready from status {r.status}")
    r.status = "READY_FOR_CVC_FILING"
    append_audit(db, "vigilance_referral_ready", "VigilanceReferral", r.id, actor_id=admin.id, actor_role="ADMIN",
                 payload={"note": body.note}, request=request, commit=False)
    db.commit()
    return {"message": "Referral ready for CVC filing (manual submission only — no automatic CVC submission)", "status": r.status}


@router.post("/vigilance-referrals/{referral_id}/filed")
def mark_filed(referral_id: str, body: ReferralStatusIn, request: Request, admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    r = db.get(VigilanceReferral, referral_id)
    if not r:
        raise HTTPException(status_code=404, detail="Referral not found")
    if r.status != "READY_FOR_CVC_FILING":
        raise HTTPException(status_code=400, detail="Only READY_FOR_CVC_FILING referrals can be marked filed")
    r.status = "FILED_MANUALLY"
    r.filed_at = utcnow()
    r.filed_note = body.note
    append_audit(db, "vigilance_referral_filed_manually", "VigilanceReferral", r.id, actor_id=admin.id, actor_role="ADMIN",
                 payload={"note": body.note}, request=request, commit=False)
    db.commit()
    return {"message": "Referral marked as manually filed with external authority", "status": r.status}


from fastapi.responses import PlainTextResponse


@router.get("/vigilance-referrals/{referral_id}/export")
def export_referral(referral_id: str, admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    r = db.get(VigilanceReferral, referral_id)
    if not r:
        raise HTTPException(status_code=404, detail="Referral not found")
    return PlainTextResponse(
        r.notice_text or "",
        media_type="text/plain",
        headers={"Content-Disposition": f"attachment; filename={r.referral_no}.txt"},
    )


# ---------------- grievances ----------------

@router.get("/grievances")
def list_grievances(admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    rows = db.query(Grievance).order_by(Grievance.created_at.desc()).all()
    return {"grievances": [
        {"id": g.id, "application_no": g.application.application_no,
         "bidder": g.application.bidder_user.full_name,
         "reason": g.reason, "details": g.details, "status": g.status,
         "admin_response": g.admin_response, "created_at": g.created_at.isoformat()}
        for g in rows
    ]}


class GrievanceRespondIn(BaseModel):
    response: str = Field(min_length=10)
    outcome: str = Field(pattern=r"^(RESPONDED|ALLOWED|DISMISSED)$")


@router.post("/grievances/{grievance_id}/respond")
def respond_grievance(grievance_id: str, body: GrievanceRespondIn, request: Request, admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    g = db.get(Grievance, grievance_id)
    if not g:
        raise HTTPException(status_code=404, detail="Grievance not found")
    g.status = body.outcome
    g.admin_response = body.response
    g.responded_by = admin.id
    g.responded_at = utcnow()
    notify(db, g.bidder_user_id, "application_rejected" if body.outcome == "DISMISSED" else "application_approved",
           title="Grievance response",
           message=f"Your grievance on {g.application.application_no} was {body.outcome.lower()}: {body.response[:200]}",
           entity_type="Grievance", entity_id=g.id)
    append_audit(db, "grievance_responded", "Grievance", g.id, actor_id=admin.id, actor_role="ADMIN",
                 payload={"outcome": body.outcome}, request=request, commit=False)
    db.commit()
    return {"message": "Grievance responded", "status": g.status}


# ---------------- analytics ----------------

@router.get("/analytics")
def analytics(admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    apps = db.query(Application).all()
    reports = db.query(VerificationReport).all()
    clarifications = db.query(Clarification).all()
    overrides = db.query(OverrideRecord).all()
    health = db.query(SourceHealth).all()

    def days_between(a, b):
        return (b - a).total_seconds() / 86400

    verif_times = []
    for r in reports:
        a = r.application
        if a.submitted_at:
            verif_times.append(max(0.0, days_between(a.submitted_at, r.generated_at)))
    by_status: dict[str, int] = {}
    for a in apps:
        by_status[a.status] = by_status.get(a.status, 0) + 1
    rejection_reasons: dict[str, int] = {}
    from ..models import OfficerDecision

    for d in db.query(OfficerDecision).filter(OfficerDecision.action.in_(["RECOMMEND_REJECT", "REJECT"])).all():
        reason = (d.remarks or "unspecified")[:60]
        rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1
    tender_wise: dict[str, list[float]] = {}
    for r in reports:
        tender_wise.setdefault(r.application.tender.tender_no, []).append(r.compliance_score)
    district_wise: dict[str, int] = {}
    for a in apps:
        district_wise[a.tender.district] = district_wise.get(a.tender.district, 0) + 1
    total_checks = 0
    failed_checks = 0
    for h in health:
        pass
    src_counts: dict[str, dict] = {}
    for h in health:
        src_counts.setdefault(h.source, {"up": 0, "down": 0})
        src_counts[h.source]["up" if h.status == "UP" else "down"] += 1
    confs = [d for r in reports for d in (r.low_confidence_fields or [])]
    override_trends: dict[str, int] = {}
    for o in overrides:
        k = o.created_at.strftime("%Y-%m")
        override_trends[k] = override_trends.get(k, 0) + 1
    officer_freq: dict[str, int] = {}
    for o in overrides:
        u = db.get(User, o.officer_id)
        officer_freq[u.full_name if u else o.officer_id] = officer_freq.get(u.full_name if u else o.officer_id, 0) + 1
    bidders = db.query(User).filter(User.role == "BIDDER").count()
    bidders_with_apps = len({a.bidder_user_id for a in apps})
    return {
        "average_verification_days": round(sum(verif_times) / len(verif_times), 3) if verif_times else None,
        "applications_by_status": by_status,
        "rejection_reasons": rejection_reasons,
        "clarification_frequency": len(clarifications),
        "tender_wise_compliance": {k: round(sum(v) / len(v), 1) for k, v in tender_wise.items()},
        "district_wise_workload": district_wise,
        "portal_failure_rate": {
            s: round(d["down"] / max(1, d["up"] + d["down"]), 3) for s, d in src_counts.items()
        },
        "low_confidence_ocr_documents": len(confs),
        "override_trends": override_trends,
        "officer_override_frequency": officer_freq,
        "sla_performance": {
            "breaches": sum(1 for a in apps if a.sla_breached),
            "total": len(apps),
            "breach_rate": round(sum(1 for a in apps if a.sla_breached) / max(1, len(apps)), 3),
        },
        "bidder_participation_rate": round(bidders_with_apps / max(1, bidders), 3),
    }


# ---------------- system configuration & health ----------------

@router.get("/source-health")
def source_health(admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    latest: dict[str, dict] = {}
    for h in db.query(SourceHealth).order_by(SourceHealth.checked_at.asc()).all():
        latest[h.source] = {"source": h.source, "status": h.status, "message": h.message,
                            "checked_at": h.checked_at.isoformat()}
    return {"sources": list(latest.values())}


@router.get("/source-resiliency")
def source_resiliency(admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    """Circuit-breaker + stale-while-revalidate status per government source."""
    from ..services.portal import source_resiliency_status
    return {"sources": source_resiliency_status()}


class VerifyCacheClearIn(BaseModel):
    source: str | None = None  # None = clear all


@router.get("/verify-cache")
def verify_cache_stats(admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    """Verification-result cache status (entries, hits, per source)."""
    from ..services import verify_cache
    return verify_cache.stats(db)


@router.post("/verify-cache/clear")
def verify_cache_clear(body: VerifyCacheClearIn, request: Request,
                       admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    """Clear the verification cache (all, or one source). Audited."""
    from ..services import verify_cache
    n = verify_cache.invalidate(db, source=body.source)
    append_audit(db, "verify_cache_cleared", "VerificationCache", None,
                 actor_id=admin.id, actor_role="ADMIN",
                 payload={"source": body.source or "*", "rows_cleared": n},
                 request=request, commit=False)
    db.commit()
    return {"message": "Verification cache cleared", "rows_cleared": n}
