from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from ..core import security
from ..core.db import get_db
from ..models.user import User

bearer_scheme = HTTPBearer(auto_error=False)

# --- V2.1 role model -------------------------------------------------------
# L1/L2 are FINANCIAL RANKINGS (lowest / second-lowest acceptable evaluated
# bids), never officer role names. Officer roles are explicit titles.
ROLE_BIDDER = "BIDDER"
ROLE_BID_PREPARER = "BID_PREPARER"
ROLE_SIGNATORY = "SIGNATORY"
ROLE_INDENTOR = "INDENTOR"
ROLE_PROC_OFFICER = "PROCUREMENT_OFFICER"
ROLE_BID_OPENING = "BID_OPENING_OFFICER"
ROLE_TECH_EVALUATOR = "TECHNICAL_EVALUATOR"
ROLE_FIN_EVALUATOR = "FINANCE_EVALUATOR"
ROLE_COMMITTEE_CHAIR = "COMMITTEE_CHAIR"
ROLE_COMMITTEE_MEMBER = "COMMITTEE_MEMBER"
ROLE_CFA = "CFA"
ROLE_CONTRACT_MANAGER = "CONTRACT_MANAGER"
ROLE_CONTRACT_OFFICER = "CONTRACT_OFFICER"
ROLE_INSPECTION = "INSPECTION_OFFICER"
ROLE_PAYMENT = "PAYMENT_OFFICER"
ROLE_ADMIN = "ADMIN"
ROLE_AUDITOR = "AUDITOR"

# Legacy V1 roles (mapping kept for migration only; not a workflow hierarchy)
ROLE_L1 = "L1_OFFICER"
ROLE_L2 = "L2_OFFICER"

LEGACY_OFFICER_ROLES = (ROLE_L1, ROLE_L2)

PROCUREMENT_OFFICER_ROLES = (ROLE_PROC_OFFICER, ROLE_BID_OPENING)
EVALUATOR_ROLES = (ROLE_TECH_EVALUATOR, ROLE_FIN_EVALUATOR)
BUYER_SIDE_ROLES = (
    ROLE_INDENTOR, *PROCUREMENT_OFFICER_ROLES, *EVALUATOR_ROLES,
    ROLE_COMMITTEE_CHAIR, ROLE_COMMITTEE_MEMBER, ROLE_CFA,
    ROLE_CONTRACT_MANAGER, ROLE_CONTRACT_OFFICER, ROLE_INSPECTION, ROLE_PAYMENT,
)
# Migration bridge: legacy officers count as buyer-side during the rollout
# (they carry no tender assignments yet, so require_tender_role still blocks
# them from acting until explicitly assigned).
OFFICER_ROLES = (*BUYER_SIDE_ROLES, *LEGACY_OFFICER_ROLES, ROLE_ADMIN)

# Behavioral role groups used by the legacy endpoints during migration.
# "L1 behavior" = first-line review/verification (Procurement / Technical).
# "L2 behavior" = second-line financial review (Finance Evaluator / CFA).
L1_BEHAVIOR_ROLES = (
    ROLE_L1, ROLE_PROC_OFFICER, ROLE_BID_OPENING, ROLE_TECH_EVALUATOR,
)
L2_BEHAVIOR_ROLES = (ROLE_L2, ROLE_FIN_EVALUATOR, ROLE_CFA)


def _unauthorized(detail: str = "Not authenticated. Please sign in.") -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


def _forbidden(detail: str = "You do not have permission to perform this action.") -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    token = None
    if credentials is not None:
        token = getattr(credentials, "credentials", None) or getattr(credentials, "token", None)
    if not token:
        # Safe fallbacks for proxies that strip Authorization. Tokens are never
        # accepted from query parameters because URLs leak into browser history,
        # access logs, referrers and monitoring systems.
        token = (
            request.cookies.get("gs_access")
            or request.headers.get("x-gs-token")
        )
    if not token:
        raise _unauthorized()
    payload = security.decode_token(token, purpose="access")
    if not payload:
        raise _unauthorized("Invalid or expired token.")
    user = db.get(User, payload["sub"])
    if user is None or user.status != "ACTIVE":
        raise _unauthorized("Account is not active.")
    # Correlate subsequent audit events with this session (sid, then jti).
    request.state.session_id = payload.get("sid") or payload.get("jti") or user.id
    return user


def require_roles(*roles: str):
    def dependency(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise _forbidden()
        return user

    return dependency


def get_current_bidder(user: User = Depends(get_current_user)) -> User:
    if user.role not in ("BIDDER", "BID_PREPARER", "SIGNATORY"):
        raise _forbidden()
    return user


def get_current_officer(user: User = Depends(get_current_user)) -> User:
    """Buyer-side access (migration bridge). Fine-grained authority on a
    specific tender additionally requires a TenderAssignment (Slice 4+)."""
    if user.role not in OFFICER_ROLES:
        raise _forbidden()
    return user


def get_current_pipeline_actor(user: User = Depends(get_current_user)) -> User:
    """Pipeline access: buyer-side officers, plus the AUDITOR (read-only
    visibility plus the AUDIT stage). The per-stage owner gate confines the
    auditor to the audit/closure stage."""
    if user.role not in (*OFFICER_ROLES, ROLE_AUDITOR):
        raise _forbidden()
    return user


def get_current_l2(user: User = Depends(get_current_user)) -> User:
    if user.role not in L2_BEHAVIOR_ROLES:
        raise _forbidden()
    return user


def get_current_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != ROLE_ADMIN:
        raise _forbidden()
    return user
