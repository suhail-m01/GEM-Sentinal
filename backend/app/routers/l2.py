from datetime import timedelta

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..audit import append_audit
from ..core.db import get_db, utcnow
from ..core.deps import get_current_l2
from ..models import Application, OverrideRecord, User, VerificationReport
from ..override import process_override
from .officer import _app_summary, _full_review, get_scoped_application
from ..services.decisions import process_decision

router = APIRouter(prefix="/l2", tags=["l2"])


@router.get("/dashboard")
def dashboard(l2: User = Depends(get_current_l2), db: Session = Depends(get_db)):
    apps = (
        db.query(Application)
        .filter(Application.status.in_(["L1 Recommended", "Pending L2 Approval", "Admin Co-sign Required", "Clarification Requested"]))
        .order_by(Application.updated_at.desc())
        .all()
    )
    return {
        "role": l2.role,
        "queue": [
            {
                **_app_summary(a),
                "l1_recommendation": _latest_l1_recommendation(db, a),
                "override_history": [
                    {"id": o.id, "severity": o.severity, "reason_category": o.reason_category,
                     "status": o.status, "admin_cosign_required": o.admin_cosign_required,
                     "admin_cosigned_at": o.admin_cosigned_at.isoformat() if o.admin_cosigned_at else None,
                     "vigilance_referral_id": o.vigilance_referral_id}
                    for o in db.query(OverrideRecord).filter(OverrideRecord.application_id == a.id).all()
                ],
            }
            for a in apps
        ],
        "counts": {
            "l1_recommended": sum(1 for a in apps if a.status == "L1 Recommended"),
            "pending_approval": sum(1 for a in apps if a.status == "Pending L2 Approval"),
            "cosign_required": sum(1 for a in apps if a.status == "Admin Co-sign Required"),
        },
    }


def _latest_l1_recommendation(db: Session, a: Application) -> dict | None:
    from ..models import OfficerDecision

    rec = (
        db.query(OfficerDecision)
        .filter(OfficerDecision.application_id == a.id, OfficerDecision.level == "L1")
        .order_by(OfficerDecision.created_at.desc())
        .first()
    )
    if not rec:
        return None
    u = db.get(User, rec.officer_id)
    return {
        "action": rec.action,
        "remarks": rec.remarks,
        "is_overridden": rec.is_overridden,
        "officer": u.full_name if u else None,
        "at": rec.created_at.isoformat(),
    }


@router.get("/applications")
def list_applications(l2: User = Depends(get_current_l2), db: Session = Depends(get_db), status: str | None = None):
    q = db.query(Application)
    if status:
        q = q.filter(Application.status == status)
    apps = q.order_by(Application.updated_at.desc()).all()
    return {"applications": [_app_summary(a) for a in apps]}


@router.get("/applications/{application_id}")
def application_detail(application_id: str, l2: User = Depends(get_current_l2), db: Session = Depends(get_db)):
    a = get_scoped_application(db, l2, application_id)
    data = _full_review(db, a)
    data["l1_recommendation"] = _latest_l1_recommendation(db, a)
    pending_cosign = (
        db.query(OverrideRecord)
        .filter(
            OverrideRecord.application_id == a.id,
            OverrideRecord.admin_cosign_required.is_(True),
            OverrideRecord.admin_cosigned_at.is_(None),
        )
        .first()
    )
    data["admin_cosign_pending"] = bool(pending_cosign)
    data["admin_cosign_required"] = pending_cosign is not None
    return data


@router.get("/applications/{application_id}/report/download")
def download_report(application_id: str, l2: User = Depends(get_current_l2), db: Session = Depends(get_db)):
    """Download the verification report. PDF is the only downloadable format."""
    from ..reports import report_to_pdf

    a = get_scoped_application(db, l2, application_id)
    report = (
        db.query(VerificationReport)
        .filter(VerificationReport.application_id == a.id)
        .order_by(VerificationReport.generated_at.desc())
        .first()
    )
    if not report:
        raise HTTPException(status_code=404, detail="No verification report yet")
    bidder_name = (
        a.bidder_user.bidder_profile.company_name if a.bidder_user.bidder_profile else a.bidder_user.full_name
    )
    append_audit(
        db, "report_download", "VerificationReport", report.id,
        actor_id=l2.id, actor_role=l2.role, commit=False,
    )
    db.commit()
    pdf_bytes = report_to_pdf(report, a, a.tender, bidder_name)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename=report-{a.application_no}.pdf",
            "Cache-Control": "no-store",
        },
    )


@router.get("/applications/{application_id}/replay")
def replay_report(application_id: str, l2: User = Depends(get_current_l2), db: Session = Depends(get_db)):
    """Reproducibility replay (spec §7-stage-10 / §12.3): re-run the
    deterministic portion from the current (immutable) inputs and assert
    bit-identical rule results and score. This is the evidence for the
    'removes inconsistency and bias' claim — proven, not asserted."""
    a = get_scoped_application(db, l2, application_id)
    report = (
        db.query(VerificationReport)
        .filter(VerificationReport.application_id == application_id)
        .order_by(VerificationReport.run_no.desc())
        .first()
    )
    if report is None:
        raise HTTPException(status_code=404, detail="No verification report to replay")
    from ..rules_engine import build_evidence_context, evaluate_tender
    from ..scoring import determinism_hash

    ctx = build_evidence_context(db, a.bidder_user, a.tender, fetch_sources=True)
    evaluation = evaluate_tender(db, a.bidder_user, a.tender, ctx=ctx)

    stored_hash = report.determinism_hash
    recomputed_hash = evaluation["determinism_hash"]
    identical = (
        stored_hash is not None
        and stored_hash == recomputed_hash
        and report.compliance_score == evaluation["compliance_score"]
        and report.risk_level == evaluation["risk_level"]
        and report.eligibility_result == evaluation["eligibility_result"]
    )
    append_audit(
        db, "report_replayed", "VerificationReport", report.id,
        actor_id=l2.id, actor_role=l2.role,
        payload={"identical": identical, "stored_hash": stored_hash,
                 "recomputed_hash": recomputed_hash,
                 "score": evaluation["compliance_score"]},
    )
    db.commit()
    return {
        "report_id": report.id,
        "run_no": report.run_no,
        "identical": identical,
        "stored_hash": stored_hash,
        "recomputed_hash": recomputed_hash,
        "score_stored": report.compliance_score,
        "score_recomputed": evaluation["compliance_score"],
        "risk_stored": report.risk_level,
        "risk_recomputed": evaluation["risk_level"],
        "eligibility_stored": report.eligibility_result,
        "eligibility_recomputed": evaluation["eligibility_result"],
        "note": None if identical else "Deterministic inputs or rules changed since the stored run — hashes differ (see rule set version).",
    }


class DecisionIn(BaseModel):
    action: str
    remarks: str | None = None
    clarification_reason: str | None = None
    clarification_documents: list[str] = Field(default_factory=list)
    clarification_deadline_days: int = 3


@router.post("/applications/{application_id}/decision")
def make_decision(application_id: str, body: DecisionIn, request: Request, l2: User = Depends(get_current_l2), db: Session = Depends(get_db)):
    a = get_scoped_application(db, l2, application_id)
    # SENTINEL WATCH mandatory gate check (spec §8.2): fresh blocking
    # re-verification before commitment actions. An unacknowledged CRITICAL
    # drift blocks the decision until reviewed — "fresh check at the moment
    # of commitment: the compliance state at award time is proven, not assumed."
    if body.action in ("RECOMMEND_QUALIFY", "APPROVE", "APPROVE_WITH_OBSERVATION"):
        from ..services import watch as watch_svc

        gate = watch_svc.run_gate_check(db, a, l2)
        if gate["blocked"]:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "WATCH_GATE_BLOCKED",
                    "message": (
                        "Commitment action blocked by Sentinel Watch: unacknowledged "
                        "CRITICAL compliance drift exists for this bid. Review and "
                        "acknowledge the drift alert(s), then re-run the gate check."
                    ),
                    "blocking_alerts": gate["blocking_alerts"],
                    "score_now": gate["score_now"],
                    "checked_at": gate["checked_at"],
                },
            )
    try:
        return process_decision(
            db, a, l2, "L2", body.action, remarks=body.remarks,
            clarification_reason=body.clarification_reason,
            clarification_docs=body.clarification_documents,
            clarification_deadline_days=body.clarification_deadline_days,
            request=request,
        )
    except HTTPException as exc:
        raise  # override challenge (409) or co-sign block propagates


class OverrideIn(BaseModel):
    challenge: str
    reason_category: str
    justification: str
    reauth_token: str
    evidence: list[dict] = Field(default_factory=list)


@router.post("/applications/{application_id}/override-evidence-upload")
def override_evidence_upload(
    application_id: str,
    request: Request,
    l2: User = Depends(get_current_l2),
    db: Session = Depends(get_db),
    file: UploadFile = File(...),
):
    from ..services.files import save_upload

    a = get_scoped_application(db, l2, application_id)
    content = file.file.read()
    try:
        safe_name, digest = save_upload(content, file.filename or "evidence.bin", kind="evidence")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"name": file.filename, "file_path": safe_name, "sha256": digest, "application_id": a.id}


@router.post("/applications/{application_id}/override")
def override_endpoint(
    application_id: str,
    body: OverrideIn,
    request: Request,
    l2: User = Depends(get_current_l2),
    db: Session = Depends(get_db),
):
    a = get_scoped_application(db, l2, application_id)
    record = process_override(
        db, a, l2, "L2",
        challenge=body.challenge,
        reason_category=body.reason_category,
        justification=body.justification,
        reauth_token=body.reauth_token,
        request=request,
        evidence_files=body.evidence or None,
    )
    return {
        "override_id": record.id,
        "severity": record.severity,
        "status": record.status,
        "admin_cosign_required": record.admin_cosign_required,
        "vigilance_referral_id": record.vigilance_referral_id,
        "application_status": a.status,
    }
