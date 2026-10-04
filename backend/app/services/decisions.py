"""Shared decision workflow for L1 and L2 with Override Accountability gate."""
from datetime import timedelta
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..audit import append_audit
from ..core.db import utcnow
from ..models import (
    Application,
    ApplicationStatusHistory,
    Clarification,
    OfficerDecision,
    User,
)
from ..notifications import notify, notify_admins
from ..override import (
    FraudBlockedError,
    extract_blocking_findings,
    create_override_challenge,
)
from .verification import mark_report_stale, run_verification

L1_ACTIONS = {
    "RECOMMEND_QUALIFY": "L1 Recommended",
    "RECOMMEND_REJECT": "Rejected",
    "REQUEST_CLARIFICATION": "Clarification Requested",
    "MANUAL_VERIFICATION_REQUIRED": "Pending L1 Review",
    "FORWARD_TO_L2": "Pending L2 Approval",
    "RAISE_ESCALATION": "Pending L2 Approval",
    "FLAG_SUSPECTED_FRAUD": None,
}


def set_status(db: Session, app: Application, new_status: str, actor: Optional[User], remarks: Optional[str] = None):
    if app.status != new_status:
        app.status = new_status
    db.add(
        ApplicationStatusHistory(
            application_id=app.id,
            status=new_status,
            actor_id=actor.id if actor else None,
            actor_role=actor.role if actor else None,
            remarks=remarks,
            created_at=utcnow(),
        )
    )


def process_decision(
    db: Session,
    app: Application,
    officer: User,
    level: str,
    action: str,
    remarks: Optional[str] = None,
    clarification_reason: Optional[str] = None,
    clarification_docs: Optional[list] = None,
    clarification_deadline_days: int = 3,
    request=None,
) -> dict:
    if action in ("RECOMMEND_QUALIFY", "APPROVE", "APPROVE_WITH_OBSERVATION"):
        if app.fraud_flagged:
            raise FraudBlockedError()
        finding = extract_blocking_findings(db, app)
        if finding is not None and not _has_accepted_override(db, app):
            challenge = create_override_challenge(app, officer, finding)
            raise HTTPException(status_code=409, detail=challenge)

    decision = OfficerDecision(
        application_id=app.id,
        officer_id=officer.id,
        level=level,
        action=action,
        remarks=remarks,
    )

    # Cross-cutting integrity actions — usable by ANY process officer
    # (clarification request / fraud flag). These are not part of the legacy
    # L1/L2 approval criteria, so they work regardless of `level`.
    if action == "REQUEST_CLARIFICATION":
        if not (clarification_reason or "").strip():
            raise HTTPException(status_code=400, detail="Clarification reason is required")
        cl = Clarification(
            application_id=app.id,
            requested_by=officer.id,
            reason=clarification_reason.strip(),
            documents_needed=clarification_docs or [],
            deadline=utcnow() + timedelta(days=clarification_deadline_days),
        )
        db.add(cl)
        set_status(db, app, "Clarification Requested", officer, remarks or clarification_reason)
        notify(
            db, app.bidder_user_id, "clarification_requested",
            message=f"Clarification requested for {app.application_no}: {clarification_reason}",
            entity_type="Clarification", entity_id=cl.id,
        )
    elif action == "FLAG_SUSPECTED_FRAUD":
        app.fraud_flagged = True
        set_status(db, app, "Pending L1 Review", officer, remarks or "Flagged for suspected fraud")
        # escalation + external vigilance notice (investigation referral, not accusation)
        from ..override import generate_vigilance_referral

        finding = extract_blocking_findings(db, app) or {
            "risk_level": app.risk_level, "eligibility_result": app.eligibility_result,
            "blacklisted": False, "blacklist_findings": [], "failed_rules": [],
            "unverified_mandatory": [], "mismatches": [], "compliance_score": app.compliance_score,
            "ai_explanation": None, "report_generated_at": None,
        }
        finding["fraud_flag_reason"] = remarks
        finding["fraud_flag_by"] = officer.full_name
        referral = generate_vigilance_referral(db, app, officer, finding, record=None, status="DRAFT")
        db.flush()
        notify_admins(
            db, "override_alert",
            message=f"Suspected fraud flagged on {app.application_no}. External Vigilance referral {referral.referral_no} prepared. Approval blocked.",
            entity_type="VigilanceReferral", entity_id=referral.id,
        )
    elif level == "L1":
        if action not in L1_ACTIONS:
            raise HTTPException(status_code=400, detail=f"Invalid L1 action: {action}")
        if action == "FORWARD_TO_L2" or action == "RAISE_ESCALATION":
            set_status(db, app, "Pending L2 Approval", officer, remarks)
            if app.assigned_l2_id:
                notify(db, app.assigned_l2_id, "l2_action_required",
                       message=f"Application {app.application_no} requires L2 action.",
                       entity_type="Application", entity_id=app.id)
            if action == "RAISE_ESCALATION":
                notify_admins(db, "override_alert",
                              message=f"Escalation raised on {app.application_no} by {officer.full_name}: {remarks or 'n/a'}",
                              entity_type="Application", entity_id=app.id)
        elif action == "RECOMMEND_QUALIFY":
            set_status(db, app, "L1 Recommended", officer, remarks)
            if app.assigned_l2_id:
                notify(db, app.assigned_l2_id, "l1_recommendation_submitted",
                       message=f"{officer.full_name} recommended qualification for {app.application_no}.",
                       entity_type="Application", entity_id=app.id)
        elif action == "RECOMMEND_REJECT":
            set_status(db, app, "Rejected", officer, remarks)
            notify(db, app.bidder_user_id, "application_rejected",
                   message=f"Application {app.application_no} was rejected. {remarks or ''}".strip(),
                   entity_type="Application", entity_id=app.id)
        else:  # MANUAL_VERIFICATION_REQUIRED
            set_status(db, app, "Pending L1 Review", officer, remarks)
    else:  # L2
        if action not in ("APPROVE", "REJECT", "SEND_BACK_TO_L1", "ADDITIONAL_CLARIFICATION", "REQUIRE_FRESH_VERIFICATION", "ESCALATE_TO_ADMIN", "APPROVE_WITH_OBSERVATION"):
            raise HTTPException(status_code=400, detail=f"Invalid L2 action: {action}")
        if action == "APPROVE":
            # critical overrides still require Admin co-sign before final approval
            _ensure_cosign_if_required(db, app)
            set_status(db, app, "Approved", officer, remarks)
            notify(db, app.bidder_user_id, "application_approved",
                   message=f"Application {app.application_no} approved. {remarks or ''}".strip(),
                   entity_type="Application", entity_id=app.id)
        elif action == "APPROVE_WITH_OBSERVATION":
            _ensure_cosign_if_required(db, app)
            obs = remarks or "(observation not recorded)"
            set_status(db, app, "Approved", officer, f"Approved with observation: {obs}")
            notify_admins(db, "l1_recommendation_submitted",
                          message=f"{app.application_no} approved WITH OBSERVATION by {officer.full_name}: {obs}",
                          entity_type="Application", entity_id=app.id)
        elif action == "REJECT":
            set_status(db, app, "Rejected", officer, remarks)
            notify(db, app.bidder_user_id, "application_rejected",
                   message=f"Application {app.application_no} rejected by L2. {remarks or ''}".strip(),
                   entity_type="Application", entity_id=app.id)
        elif action == "SEND_BACK_TO_L1":
            set_status(db, app, "Pending L1 Review", officer, remarks or "Sent back to L1")
            if app.assigned_l1_id:
                notify(db, app.assigned_l1_id, "l2_action_required",
                       message=f"{app.application_no} sent back to L1. {remarks or ''}",
                       entity_type="Application", entity_id=app.id)
        elif action == "ADDITIONAL_CLARIFICATION":
            if not (clarification_reason or "").strip():
                raise HTTPException(status_code=400, detail="Clarification reason is required")
            cl = Clarification(
                application_id=app.id, requested_by=officer.id, reason=clarification_reason.strip(),
                documents_needed=clarification_docs or [],
                deadline=utcnow() + timedelta(days=clarification_deadline_days),
            )
            db.add(cl)
            set_status(db, app, "Clarification Requested", officer, remarks or clarification_reason)
            notify(db, app.bidder_user_id, "clarification_requested",
                   message=f"Additional clarification requested for {app.application_no}: {clarification_reason}",
                   entity_type="Clarification", entity_id=cl.id)
        elif action == "REQUIRE_FRESH_VERIFICATION":
            report = run_verification(db, app, officer)
            set_status(db, app, "Pending L1 Review", officer, remarks or "Fresh verification completed")
            if app.assigned_l1_id:
                notify(db, app.assigned_l1_id, "ai_report_ready",
                       message=f"Fresh verification completed for {app.application_no} (score {report.compliance_score}).",
                       entity_type="Application", entity_id=app.id)
        elif action == "ESCALATE_TO_ADMIN":
            set_status(db, app, "Pending L2 Approval", officer, remarks)
            notify_admins(db, "override_alert",
                          message=f"L2 escalated {app.application_no}: {remarks or 'n/a'}",
                          entity_type="Application", entity_id=app.id)

    db.add(decision)
    append_audit(
        db,
        f"officer_decision_{action.lower()}",
        "Application",
        app.id,
        actor_id=officer.id,
        actor_role=officer.role,
        payload={"level": level, "action": action, "remarks": remarks},
        application_id=app.id,
        tender_id=app.tender_id,
        request=request,
        commit=False,
    )
    db.commit()
    return {"application_id": app.id, "status": app.status, "decision_id": decision.id}


def _has_accepted_override(db: Session, app: Application) -> bool:
    """True when a prior override on this application has completed its
    accountability chain (co-signed where required, not rejected) — the
    decision may then proceed through normal L1/L2 flow."""
    from ..models import OverrideRecord

    o = (
        db.query(OverrideRecord)
        .filter(OverrideRecord.application_id == app.id, OverrideRecord.status != "REJECTED")
        .order_by(OverrideRecord.created_at.desc())
        .first()
    )
    if not o:
        return False
    if o.admin_cosign_required and not o.admin_cosigned_at:
        return False
    return True


def _ensure_cosign_if_required(db: Session, app: Application) -> None:
    from ..models import OverrideRecord

    pending = (
        db.query(OverrideRecord)
        .filter(
            OverrideRecord.application_id == app.id,
            OverrideRecord.admin_cosign_required.is_(True),
            OverrideRecord.admin_cosigned_at.is_(None),
        )
        .first()
    )
    if pending:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "ADMIN_COSIGN_REQUIRED",
                "message": "This application carries a CRITICAL override requiring System Admin co-sign. Final approval is blocked until Admin co-sign is complete.",
                "override_id": pending.id,
            },
        )
