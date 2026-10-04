import secrets
import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

from ..audit import append_audit
from ..core.config import settings
from ..core.db import get_db, utcnow
from ..core.deps import get_current_user
from ..core.security import (
    create_token,
    decode_token,
    hash_password,
    verify_password,
)
from ..ipcontext import resolve_client_ip
from ..models import User
from ..models.bidder import BidderProfile
from ..models.identity import LoginAttempt
from ..models.otp import TrustedDevice
from ..core.otp import (
    device_id_hash,
    issue_otp,
    mask_mobile,
    normalize_mobile,
    verify_otp as otp_verify,
)
from ..notifications import notify

router = APIRouter(prefix="/auth", tags=["auth"])
limiter = Limiter(key_func=get_remote_address)


def _device_hash(request: Request) -> str | None:
    """Client-generated device ID (localStorage UUID) sent as X-Device-Id.
    Stored only as a SHA-256 hash — the raw ID never touches the DB."""
    return device_id_hash(request.headers.get("x-device-id"))


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    full_name: str = Field(min_length=2, max_length=255)
    mobile: str = Field(min_length=10)  # required: real OTP is delivered here
    company_name: str | None = None
    pan: str | None = None
    gstin: str | None = None
    udyam_number: str | None = None
    business_type: str | None = None
    registered_address: str | None = None
    city: str | None = None
    district: str | None = None
    state: str | None = None
    authorized_rep_name: str | None = None
    authorized_rep_email: str | None = None
    authorized_rep_phone: str | None = None


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class RefreshIn(BaseModel):
    refresh_token: str


class VerifyOtpIn(BaseModel):
    email: EmailStr
    otp: str
    purpose: str = "register"  # register | login
    remember_device: bool = False  # trust this browser for TRUSTED_DEVICE_DAYS


class ResendOtpIn(BaseModel):
    email: EmailStr
    purpose: str = "register"


class GovOtpIn(BaseModel):
    gov_id: str = Field(min_length=3, max_length=64)


class GovVerifyIn(BaseModel):
    gov_id: str
    otp: str


def _find_gov_employee(db: Session, gov_id: str):
    from sqlalchemy import func

    from ..models import GovEmployee

    return (
        db.query(GovEmployee)
        .filter(func.lower(GovEmployee.gov_id) == (gov_id or "").strip().lower())
        .first()
    )


class ReauthIn(BaseModel):
    password: str


def _tokens_for(user: User, response: Response | None = None, rotate_session: bool = False) -> dict:
    # Session rotation: a new session id is minted on login and on each
    # refresh; audit events carry it via the token's sid claim.
    if rotate_session or not user.session_id:
        user.session_id = uuid.uuid4().hex
    tokens = {
        "access_token": create_token(user.id, user.role, "access", settings.ACCESS_TOKEN_MINUTES, session_id=user.session_id),
        "refresh_token": create_token(user.id, user.role, "refresh", settings.REFRESH_TOKEN_DAYS * 24 * 60, session_id=user.session_id),
        "token_type": "bearer",
    }
    if response is not None:
        # Server-side session cookie: works even for clients whose
        # Authorization header is stripped by a proxy (the Next.js middleware
        # restores the header from this cookie; the backend also reads it).
        response.set_cookie(
            "gs_access",
            tokens["access_token"],
            max_age=settings.ACCESS_TOKEN_MINUTES * 60,
            httponly=True,
            secure=(settings.APP_ENV or "").strip().lower() == "production",
            samesite="lax",
            path="/",
        )
    return tokens


def _user_out(user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "mobile": user.mobile,
        "mobile_masked": mask_mobile(user.mobile) if user.mobile else None,
        "role": user.role,
        "status": user.status,
        "is_verified": user.is_verified,
    }


@router.post("/register")
@limiter.limit(settings.RATE_LIMIT_LOGIN)
def register(body: RegisterIn, request: Request, db: Session = Depends(get_db)):
    """Bidder self-registration. Officers must NOT self-activate: officer
    access requires Admin/department approval."""
    if body.email.lower() in ("admin@cpcl.gov.in",):
        pass  # reserved emails handled generically below
    existing = db.query(User).filter(User.email == body.email.lower()).first()
    if existing:
        raise HTTPException(status_code=409, detail="An account with this email already exists")
    if body.pan and len(body.pan) != 10:
        raise HTTPException(status_code=400, detail="PAN must be 10 characters")
    if body.gstin and len(body.gstin) != 15:
        raise HTTPException(status_code=400, detail="GSTIN must be 15 characters")
    mobile = normalize_mobile(body.mobile)  # raises 400 when invalid

    from ..core.security import encrypt_field, mask_gstin, mask_pan, mask_udyam

    from ..models import new_id as _new_id

    user = User(
        id=_new_id(),
        email=body.email.lower(),
        mobile=mobile,
        password_hash=hash_password(body.password),
        full_name=body.full_name,
        role="BIDDER",
        status="PENDING_OTP",
    )
    db.add(user)
    profile = None
    if body.company_name:
        profile = BidderProfile(
            user_id=user.id,
            company_name=body.company_name,
            pan_encrypted=encrypt_field(body.pan.upper()) if body.pan else None,
            gstin_encrypted=encrypt_field(body.gstin.upper()) if body.gstin else None,
            udyam_encrypted=encrypt_field(body.udyam_number.upper()) if body.udyam_number else None,
            pan_masked=mask_pan(body.pan) if body.pan else None,
            gstin_masked=mask_gstin(body.gstin) if body.gstin else None,
            udyam_masked=mask_udyam(body.udyam_number) if body.udyam_number else None,
            business_type=body.business_type,
            registered_address=body.registered_address,
            city=body.city,
            district=body.district,
            state=body.state,
            authorized_rep_name=body.authorized_rep_name,
            authorized_rep_email=body.authorized_rep_email,
            authorized_rep_phone=body.authorized_rep_phone,
        )
        db.add(profile)
    db.flush()
    append_audit(db, "user_registration", "User", user.id, actor_id=user.id, actor_role="BIDDER",
                 payload={"email": user.email, "mobile": mask_mobile(mobile)}, request=request)
    db.commit()
    # Real OTP → registered mobile (provider per SMS_PROVIDER config)
    otp_result = issue_otp(db, email=user.email, mobile=mobile, purpose="REGISTER",
                           source_ip=request.client.host if request.client else None,
                           device_hash=_device_hash(request))
    return {
        "user": _user_out(user),
        "message": "Account created. Verify with the OTP sent to your mobile to activate.",
        **otp_result,
    }


@router.post("/verify-otp")
@limiter.limit(settings.RATE_LIMIT_LOGIN)
def verify_otp(body: VerifyOtpIn, request: Request, response: Response, db: Session = Depends(get_db)):
    """Verify a real OTP. purpose=register activates the new account;
    purpose=login completes the second factor of sign-in."""
    user = db.query(User).filter(User.email == body.email.lower()).first()
    if not user:
        raise HTTPException(status_code=404, detail="Account not found")
    if body.purpose not in ("register", "login"):
        raise HTTPException(status_code=400, detail="Invalid OTP purpose")
    try:
        otp_verify(db, email=user.email, code=body.otp, purpose=body.purpose.upper(),
                   source_ip=request.client.host if request.client else None)
    except HTTPException:
        append_audit(db, "otp_failed", "User", user.id,
                     payload={"email": user.email, "purpose": body.purpose}, commit=False)
        db.commit()
        raise

    if body.purpose == "register":
        user.status = "ACTIVE"
        user.is_verified = True
        user.otp_token = None
        append_audit(db, "account_activated_otp", "User", user.id, actor_id=user.id,
                     actor_role="BIDDER", payload={"mobile": mask_mobile(user.mobile) if user.mobile else None},
                     commit=False)
        tokens = _tokens_for(user, response, rotate_session=True)
        db.commit()
        return {"message": "Account activated", "user": _user_out(user), **tokens}

    # purpose == "login": credentials already validated at /login
    user.failed_login_attempts = 0
    user.locked_until = None
    user.last_login_at = utcnow()
    if body.remember_device:
        _trust_device(db, user, request)
    append_audit(db, "login_2fa", "User", user.id, actor_id=user.id, actor_role=user.role,
                 payload={"email": user.email, "device_trusted": body.remember_device}, commit=False)
    tokens = _tokens_for(user, response, rotate_session=True)
    db.commit()
    return {"message": "Signed in", "user": _user_out(user), **tokens}


def _trust_device(db: Session, user: User, request: Request) -> None:
    dh = _device_hash(request)
    if not dh:
        return
    existing = db.query(TrustedDevice).filter(
        TrustedDevice.user_id == user.id, TrustedDevice.device_hash == dh
    ).first()
    now = utcnow()
    if existing:
        existing.expires_at = now + timedelta(days=settings.TRUSTED_DEVICE_DAYS)
        existing.revoked_at = None
        existing.last_used_at = now
        return
    db.add(TrustedDevice(
        user_id=user.id, device_hash=dh,
        label=(request.headers.get("user-agent") or "")[:200] or None,
        source_ip=request.client.host if request.client else None,
        last_used_at=now,
        expires_at=now + timedelta(days=settings.TRUSTED_DEVICE_DAYS),
    ))


@router.post("/resend-otp")
@limiter.limit("5/minute")
def resend_otp(body: ResendOtpIn, request: Request, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == body.email.lower()).first()
    if not user:
        raise HTTPException(status_code=404, detail="Account not found")
    if body.purpose not in ("register", "login"):
        raise HTTPException(status_code=400, detail="Invalid OTP purpose")
    if not user.mobile:
        raise HTTPException(status_code=400, detail="No registered mobile number on this account")
    result = issue_otp(db, email=user.email, mobile=user.mobile, purpose=body.purpose.upper(),
                       source_ip=request.client.host if request.client else None,
                       device_hash=_device_hash(request))
    return result


# ------------------------- Government portal (Govt ID + OTP) -----------------

@router.post("/gov-login")
@limiter.limit("10/minute")
def gov_login(body: GovOtpIn, request: Request, db: Session = Depends(get_db)):
    """Step 1 of government sign-in: Government Unique ID → OTP to the phone
    registered against it. No email, no password on the gov portal."""
    emp = _find_gov_employee(db, body.gov_id)
    if emp is None:
        raise HTTPException(status_code=400, detail="Government ID not recognized. Contact your department admin.")
    if emp.status == "SUSPENDED":
        raise HTTPException(status_code=403, detail="This account is suspended — contact the System Admin.")
    result = issue_otp(db, email=emp.gov_id.lower(), mobile=emp.phone, purpose="GOV_LOGIN",
                       source_ip=request.client.host if request.client else None,
                       device_hash=_device_hash(request))
    return {
        "pending_approval": emp.status == "PENDING_APPROVAL",
        "role": emp.role,
        "name": emp.full_name,
        **result,
    }


@router.post("/gov-verify")
@limiter.limit(settings.RATE_LIMIT_LOGIN)
def gov_verify(body: GovVerifyIn, request: Request, response: Response, db: Session = Depends(get_db)):
    """Step 2 of government sign-in: verify the OTP, then check approval."""
    from ..core.security import create_token  # noqa: F401  (tokens via _tokens_for)
    from ..models import GovEmployee
    from ..models.user import new_id

    emp = _find_gov_employee(db, body.gov_id)
    if emp is None:
        raise HTTPException(status_code=400, detail="Government ID not recognized.")
    if emp.status == "SUSPENDED":
        raise HTTPException(status_code=403, detail="This account is suspended — contact the System Admin.")
    try:
        otp_verify(db, email=emp.gov_id.lower(), code=body.otp, purpose="GOV_LOGIN",
                   source_ip=request.client.host if request.client else None)
    except HTTPException:
        append_audit(db, "gov_otp_failed", "GovEmployee", emp.id,
                     payload={"gov_id": emp.gov_id}, request=request, commit=False)
        db.commit()
        raise
    if emp.status == "PENDING_APPROVAL":
        append_audit(db, "gov_login_pending_approval", "GovEmployee", emp.id,
                     payload={"gov_id": emp.gov_id}, request=request, commit=False)
        db.commit()
        return {"pending_approval": True,
                "message": "Phone verified. Your account is awaiting System Admin approval — please try again once approved."}
    # ensure the linked user account exists (auto-provisioned on first login)
    user = db.get(User, emp.user_id) if emp.user_id else None
    if user is None:
        user = User(
            id=new_id(),
            email=f"{emp.gov_id.lower()}@gov.in",
            mobile=emp.phone,
            password_hash=secrets.token_urlsafe(32),  # gov sign-in is OTP-only
            full_name=emp.full_name,
            role=emp.role,
            status="ACTIVE",
            is_verified=True,
        )
        db.add(user)
        db.flush()
        emp.user_id = user.id
    user.last_login_at = utcnow()
    emp.last_login_at = utcnow()
    append_audit(db, "gov_login", "GovEmployee", emp.id, actor_id=user.id, actor_role=emp.role,
                 payload={"gov_id": emp.gov_id}, request=request, commit=False)
    tokens = _tokens_for(user, response, rotate_session=True)
    db.commit()
    return {"user": _user_out(user), "otp_required": False, **tokens}


@router.post("/login")
@limiter.limit(settings.RATE_LIMIT_LOGIN)
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == body.email.lower()).first()
    peer = request.client.host if request.client else None
    ip, ip_src = resolve_client_ip(
        peer,
        x_forwarded_for=request.headers.get("x-forwarded-for"),
        x_real_ip=request.headers.get("x-real-ip"),
    )

    def _attempt(success: bool, reason: str | None) -> None:
        db.add(LoginAttempt(
            email=body.email.lower(), success=success, reason=reason,
            ip_address=ip, ip_source=ip_src,
            user_agent=(request.headers.get("user-agent") or "")[:290] or None,
        ))
    if user and user.locked_until and user.locked_until > utcnow():
        raise HTTPException(status_code=423, detail="Account temporarily locked due to repeated failed logins")
    if not user or not verify_password(body.password, user.password_hash):
        if user:
            user.failed_login_attempts += 1
            if user.failed_login_attempts >= 5:
                user.locked_until = utcnow() + timedelta(minutes=15)
            append_audit(db, "login_failed", "User", user.id, actor_id=user.id, actor_role=user.role,
                         payload={"email": user.email, "attempts": user.failed_login_attempts},
                         request=request, commit=False)
            _attempt(False, "BAD_CREDENTIALS")
            db.commit()
        else:
            append_audit(db, "login_failed", "User", None, payload={"email": body.email}, request=request, commit=False)
            _attempt(False, "BAD_CREDENTIALS")
            db.commit()
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if user.status not in ("ACTIVE",):
        if user.status == "PENDING_OTP":
            raise HTTPException(status_code=403, detail="Account pending OTP verification")
        if user.status == "PENDING_APPROVAL":
            raise HTTPException(status_code=403, detail="Officer account awaiting Admin approval")
        raise HTTPException(status_code=403, detail=f"Account status: {user.status}")
    # --- Trusted-device fast path (2FA skip for bound browsers) --------------
    now = utcnow()
    dh = _device_hash(request)
    if dh:
        td = (
            db.query(TrustedDevice)
            .filter(TrustedDevice.user_id == user.id, TrustedDevice.device_hash == dh,
                    TrustedDevice.revoked_at.is_(None), TrustedDevice.expires_at > now)
            .first()
        )
        if td:
            td.last_used_at = now
            user.failed_login_attempts = 0
            user.locked_until = None
            user.last_login_at = now
            append_audit(db, "login_trusted_device", "User", user.id, actor_id=user.id, actor_role=user.role,
                         payload={"email": user.email}, request=request, commit=False)
            _attempt(True, None)
            tokens = _tokens_for(user, response, rotate_session=True)
            db.commit()
            return {"user": _user_out(user), "otp_required": False, **tokens}
    # --- Two-factor: real OTP to the account's own registered mobile ---------
    # Scope: bidder-portal roles (bidders + org preparers/signatories).
    # Government/officer accounts are gated by Admin approval (phase 1 scope:
    # bidder portal 2FA).
    BIDDER_SIDE_ROLES = {"BIDDER", "BID_PREPARER", "SIGNATORY"}
    if user.role in BIDDER_SIDE_ROLES:
        if not user.mobile:
            raise HTTPException(status_code=400, detail="No registered mobile number on this account — contact support to add one")
        user.failed_login_attempts = 0
        user.locked_until = None
        append_audit(db, "login_2fa_issued", "User", user.id, actor_id=user.id, actor_role=user.role,
                     payload={"email": user.email, "mobile": mask_mobile(user.mobile)}, request=request, commit=False)
        _attempt(True, "OTP_ISSUED")
        db.commit()
        result = issue_otp(db, email=user.email, mobile=user.mobile, purpose="LOGIN",
                           source_ip=ip, device_hash=dh)
        return {"user": _user_out(user), **result}
    # Government side: direct sign-in (trusted-device fast path already handled above)
    user.failed_login_attempts = 0
    user.locked_until = None
    user.last_login_at = utcnow()
    append_audit(db, "login", "User", user.id, actor_id=user.id, actor_role=user.role,
                 payload={"email": user.email}, request=request, commit=False)
    _attempt(True, None)
    tokens = _tokens_for(user, response, rotate_session=True)
    db.commit()
    return {"user": _user_out(user), "otp_required": False, **tokens}


@router.post("/refresh")
def refresh(body: RefreshIn, response: Response, db: Session = Depends(get_db)):
    payload = decode_token(body.refresh_token, "refresh")
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")
    user = db.get(User, payload["sub"])
    if not user or user.status != "ACTIVE":
        raise HTTPException(status_code=401, detail="Account not active")
    # Refresh-token rotation is server-enforced. A refresh token from an older
    # session id cannot be replayed after a successful refresh/login.
    token_sid = payload.get("sid")
    if not user.session_id or not token_sid or token_sid != user.session_id:
        raise HTTPException(status_code=401, detail="Refresh token has been rotated or revoked")
    tokens = _tokens_for(user, response, rotate_session=True)
    db.commit()  # persist the newly-rotated session id
    return tokens


@router.get("/me")
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    out = _user_out(user)
    if user.role == "BIDDER":
        p = user.bidder_profile
        if p:
            out["profile"] = {
                "company_name": p.company_name,
                "trade_name": p.trade_name,
                "pan_masked": p.pan_masked,
                "gstin_masked": p.gstin_masked,
                "udyam_masked": p.udyam_masked,
                "business_type": p.business_type,
                "business_category": p.business_category,
                "registered_address": p.registered_address,
                "city": p.city,
                "district": p.district,
                "state": p.state,
                "pincode": p.pincode,
                "authorized_rep_name": p.authorized_rep_name,
                "authorized_rep_email": p.authorized_rep_email,
                "authorized_rep_phone": p.authorized_rep_phone,
                "annual_turnover": p.annual_turnover,
                "established_year": p.established_year,
                "registration_date": p.registration_date.isoformat() if p.registration_date else None,
                "filing_status": p.filing_status,
                "local_content_percent": p.local_content_percent,
                "profile_completion": p.profile_completion,
            }
    # V2.1 — organization memberships (bidder-side org isolation)
    from ..models import OrganizationMember
    memberships = (
        db.query(OrganizationMember)
        .filter(OrganizationMember.user_id == user.id, OrganizationMember.active.is_(True))
        .all()
    )
    out["org_memberships"] = [
        {"organization_id": m.organization_id, "org_role": m.org_role,
         "organization": m.organization.legal_name if m.organization else None,
         "trade_name": m.organization.trade_name if m.organization else None}
        for m in memberships
    ]
    out["organizations"] = [
        {"id": m.organization.id, "legal_name": m.organization.legal_name,
         "trade_name": m.organization.trade_name,
         "district": m.organization.district, "state": m.organization.state}
        for m in memberships if m.organization
    ]
    return out


@router.post("/reauth")
def reauth(body: ReauthIn, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Officer password re-authentication required before overrides are accepted."""
    if not verify_password(body.password, user.password_hash):
        append_audit(db, "reauth_failed", "User", user.id,
                     actor_id=user.id, actor_role=user.role,
                     request=request, commit=False)
        db.commit()
        raise HTTPException(status_code=401, detail="Re-authentication failed: incorrect password")
    token = create_token(user.id, user.role, "reauth", settings.REAUTH_TOKEN_MINUTES)
    return {"reauth_token": token, "expires_in_minutes": settings.REAUTH_TOKEN_MINUTES}


@router.post("/logout")
def logout(response: Response, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Clear the browser cookie and revoke the current refresh-token session."""
    user.session_id = None
    db.commit()
    response.delete_cookie(
        "gs_access", path="/",
        secure=(settings.APP_ENV or "").strip().lower() == "production",
        httponly=True, samesite="lax",
    )
    return {"message": "Logged out"}
