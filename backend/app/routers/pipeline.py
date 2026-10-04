"""Pipeline endpoints: the 11-stage procurement workflow.

    GET  /officer/pipeline/{app_id}                          — stage state
    POST /officer/pipeline/{app_id}/{stage}/action           — act on a stage
    GET  /officer/pipeline/{app_id}/certificate/{stage}      — stage certificate (PDF)
    GET  /bidder/applications/{app_id}/certificate/submission — bidder's submission cert
"""
import io
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..audit import append_audit, verify_chain
from ..core.bids import tender_bids_open
from ..core.db import get_db, utcnow
from ..core.deps import get_current_bidder, get_current_pipeline_actor
from ..models import Application, Document, User
from ..notifications import notify
from ..override import extract_blocking_findings, create_override_challenge
from .officer import get_scoped_application
from .. import pipeline as pl
from ..pipeline_docs import build_officer_summary, doc_scope_info

router = APIRouter(prefix="/pipeline", tags=["pipeline"])
bidder_router = APIRouter(tags=["pipeline-bidder"])


def _app_with_pipeline(db: Session, app: Application):
    pl.init_pipeline(db, app)
    db.flush()


class StageActionIn(BaseModel):
    action: str
    remarks: str = Field(min_length=1)
    # stage-specific optional fields
    finding: Optional[str] = None            # COMPLIANCE
    outcome: Optional[str] = None            # TECHNICAL / DELIVERY / FINANCIAL
    decision: Optional[str] = None           # APPROVAL
    amount: Optional[float] = None           # FINANCIAL
    tax_percent: Optional[float] = None
    contract_no: Optional[str] = None
    po_no: Optional[str] = None
    milestones: Optional[list[dict]] = None  # EXECUTION [{name, due, status}]
    batch_serial: Optional[str] = None       # DELIVERY
    invoice_no: Optional[str] = None         # PAYMENT
    payment_ref: Optional[str] = None
    payment_amount: Optional[float] = None


@router.get("/{application_id}")
def pipeline_status(application_id: str, officer: User = Depends(get_current_pipeline_actor),
                    db: Session = Depends(get_db)):
    a = get_scoped_application(db, officer, application_id)
    _app_with_pipeline(db, a)
    state = pl.pipeline_state(db, a)
    role = pl.effective_workflow_role(db, officer, a.tender, a)
    my_stage = next((s["key"] for s in state["stages"] if s["owner_role"] == role), None)
    # Two-envelope need-to-know: commercial figures (amount, L-rank) are
    # server-side suppressed for viewers without financial visibility.
    from ..services import financial as fin_svc
    if not fin_svc.financial_visible(db, a, role):
        for _s in state["stages"]:
            if _s["key"] == "FINANCIAL" and _s.get("payload"):
                _p = dict(_s["payload"])
                for _k in ("amount", "rank", "tax_percent", "evaluation_basis"):
                    _p.pop(_k, None)
                _s["payload"] = _p
                _s["status_label"] = "Financial Evaluation"
    from ..pipeline_docs import doc_scope_for_role
    docs = db.query(Document).filter(Document.user_id == a.bidder_user_id).all()
    scope = doc_scope_info(
        [{"doc_type": d.doc_type} for d in docs], role)
    cert_rows = [s for s in state["stages"] if s.get("certificate_no")]
    return {
        "application_no": a.application_no,
        "tender_no": a.tender.tender_no,
        "tender_title": a.tender.title,
        "status": a.status,
        "my_role": role,
        "my_stage": my_stage,
        "can_act_on_current": (
            state["current_stage"] is not None and
            pl.stage_owner(state["current_stage"]) in (role, "BIDDER") and
            (role == pl.stage_owner(state["current_stage"]) or officer.role == "ADMIN")
        ),
        "doc_scope": scope,
        "certificates": [
            {"stage": s["key"], "label": s["label"], "no": s["certificate_no"],
             "url": f"/officer/pipeline/{a.id}/certificate/{s['key']}"}
            for s in cert_rows
        ],
        **state,
    }


@router.post("/{application_id}/{stage}/action")
def act_on_stage(application_id: str, stage: str, body: StageActionIn,
                 request: Request, officer: User = Depends(get_current_pipeline_actor),
                 db: Session = Depends(get_db)):
    if stage not in pl.STAGE_BY_KEY:
        raise HTTPException(status_code=404, detail=f"Unknown stage: {stage}")
    a = get_scoped_application(db, officer, application_id)
    _app_with_pipeline(db, a)
    role = pl.effective_workflow_role(db, officer, a.tender, a)
    if role != pl.stage_owner(stage) and officer.role != "ADMIN":
        raise HTTPException(
            status_code=403,
            detail=f"Stage '{stage}' is owned by the {pl.STAGE_BY_KEY[stage][3]} "
                   f"({pl.stage_owner(stage)}). Your authority for this tender: {role}.")

    # --- domain side-effects & payload assembly -----------------------------
    payload = {"action": body.action, "remarks": body.remarks}

    if stage == "BID_CLOSURE":
        if not tender_bids_open(a.tender):
            # Demo workflow intentionally supports an audited opening action so
            # the presentation can show sealed and opened districts side-by-side.
            # Production keeps the stricter deadline rule and requires the
            # dedicated Admin force-open path instead.
            from ..core.config import settings
            if not settings.DEMO_MODE:
                from ..core.bids import sealed_detail
                raise HTTPException(status_code=409, detail=sealed_detail(a.tender))
            a.tender.bid_deadline = utcnow()
            a.tender.force_opened = True
            append_audit(db, "tender_bids_force_opened", "Tender", a.tender.id,
                         actor_id=officer.id, actor_role=officer.role,
                         payload={"via": "pipeline BID_CLOSURE stage", "demo_mode": True})
    elif stage == "COMPLIANCE":
        from ..models import VerificationReport
        report = (db.query(VerificationReport)
                  .filter(VerificationReport.application_id == a.id)
                  .order_by(VerificationReport.generated_at.desc()).first())
        if report is None:
            raise HTTPException(status_code=409, detail=(
                "AI verification has not been run yet. Run 'Run AI Verification' on the review "
                "page first — the compliance stage records its outcome."))
        finding = body.finding or body.action
        if finding != body.action:
            raise HTTPException(status_code=400, detail="finding must match the selected compliance action")
        payload["finding"] = finding
        payload["score"] = report.compliance_score
        payload["risk"] = report.risk_level
        payload["report_id"] = report.id
        payload["report_run_no"] = report.run_no
    elif stage == "TECHNICAL":
        if body.outcome is not None and body.outcome != body.action:
            raise HTTPException(status_code=400, detail="outcome must match the selected technical action")
        payload["outcome"] = body.action
        # Technical qualification = authorized financial opening: the sealed
        # financial envelope becomes visible to financial-evaluation roles
        # (audited, one-directional). Rejected/disqualified apps never open.
        if body.action == "TECHNICALLY_QUALIFIED":
            from ..services import financial as fin_svc
            fin_svc.mark_financial_opened(db, a)
    elif stage == "FINANCIAL":
        if body.outcome is not None and body.outcome != body.action:
            raise HTTPException(status_code=400, detail="outcome must match the selected financial action")
        payload["outcome"] = body.action
        from ..services import financial as fin_svc
        if body.amount is not None:
            payload["amount"] = body.amount
        else:
            # Deterministic prefill: approved reverse-auction final price
            # where present, else the sealed financial bid's amount.
            price = fin_svc.final_price_for_ranking(db, a)
            if price is None:
                raise HTTPException(status_code=400, detail="evaluated amount is required")
            from ..models import Auction, AuctionResult
            ra = (db.query(AuctionResult, Auction.auction_type)
                  .join(Auction, Auction.id == AuctionResult.auction_id)
                  .filter(Auction.tender_id == a.tender_id, AuctionResult.status == "APPROVED")
                  .order_by(Auction.closed_at.desc().nullslast()).first())
            payload["amount"] = float(price)
            if ra is None:
                payload["evaluation_basis"] = "Sealed financial bid (quoted amount)"
            elif ra[1] == "FORWARD":
                payload["evaluation_basis"] = "Approved forward e-auction final price"
            else:
                payload["evaluation_basis"] = "Approved reverse auction final price"
        payload["tax_percent"] = body.tax_percent
    elif stage == "APPROVAL":
        if body.decision is not None and body.decision != body.action:
            raise HTTPException(status_code=400, detail="decision must match the selected approval action")
        payload["decision"] = body.action
        if body.action == "APPROVE" and not a.fraud_flagged:
            finding = extract_blocking_findings(db, a)
            from ..services.decisions import _has_accepted_override
            if finding is not None and not _has_accepted_override(db, a):
                challenge = create_override_challenge(a, officer, finding)
                raise HTTPException(status_code=409, detail=challenge)
    elif stage == "CONTRACT":
        payload["contract_no"] = body.contract_no
        payload["po_no"] = body.po_no
    elif stage == "EXECUTION":
        payload["milestones"] = body.milestones or []
    elif stage == "DELIVERY":
        if body.outcome is not None and body.outcome != body.action:
            raise HTTPException(status_code=400, detail="outcome must match the selected delivery action")
        payload["outcome"] = body.action
        payload["batch_serial"] = body.batch_serial
        row = (db.query(pl.ApplicationStage)
               .filter(pl.ApplicationStage.application_id == a.id,
                       pl.ApplicationStage.stage_key == "DELIVERY").first())
        if row and row.status == pl.COMPLETED and row.payload.get("outcome") == "RECTIFICATION_REQUIRED":
            pass  # re-inspection after rectification
        elif row and row.status == pl.COMPLETED:
            raise HTTPException(status_code=409, detail="Delivery stage already completed")
    elif stage == "PAYMENT":
        payload["invoice_no"] = body.invoice_no
        payload["payment_ref"] = body.payment_ref
        payload["payment_amount"] = body.payment_amount
    elif stage == "AUDIT":
        chain = verify_chain(db)
        payload["audit_chain_valid"] = bool(chain.get("valid"))
        payload["audit_records"] = chain.get("total_records")
        payload["chain_head_hash"] = (chain.get("last_hash") or "")[:16]

    # --- state transition ----------------------------------------------------
    row = pl.complete_stage(db, a, stage, officer, payload)

    # notify the next stage owner
    nxt = pl.next_owner(db, a, stage)
    if nxt and not pl.is_terminal_outcome(stage, payload):
        notify(db, nxt.id, "pipeline_stage_action_required",
               message=f"Application {a.application_no}: {pl.STAGE_BY_KEY[stage][0]} completed — "
                       f"action required at stage {pl.current_stage(db, a)}.",
               entity_type="Application", entity_id=a.id)
    notify(db, a.bidder_user_id, "pipeline_stage_completed",
           message=f"Application {a.application_no}: stage '{pl.STAGE_BY_KEY[stage][1]}' completed "
                   f"({pl.stage_status_label(stage, payload)}).",
           entity_type="Application", entity_id=a.id)
    db.commit()
    return {"stage": stage, "status": a.status,
            "certificate_no": row.certificate_no,
            "current_stage": pl.current_stage(db, a)}


def _certificate_response(db: Session, a: Application, stage: str,
                          show_financial: bool = True) -> StreamingResponse:
    row = (db.query(pl.ApplicationStage)
           .filter(pl.ApplicationStage.application_id == a.id,
                   pl.ApplicationStage.stage_key == stage).first())
    if row is None or row.status not in (pl.COMPLETED, pl.TERMINATED):
        raise HTTPException(status_code=404, detail="Stage not completed — no certificate")
    actor = db.get(User, row.acted_by)
    p = row.payload or {}
    result_label = pl.stage_status_label(stage, p)
    if stage == "FINANCIAL" and not show_financial:
        result_label = "Financial Evaluation"
    extra_rows: list[tuple[str, str]] = []
    extra_lines: list[str] = []
    if stage == "COMPLIANCE":
        extra_rows = [("AI Compliance Score", f"{p.get('score')}/100"),
                      ("Risk Level", p.get("risk") or "—"),
                      ("Report Run No", p.get("report_run_no"))]
    if stage == "FINANCIAL" and show_financial:
        extra_rows = [("Evaluated Amount (INR)", f"{p.get('amount'):,.2f}" if p.get("amount") else "—"),
                      ("L-Rank", "TIE (joint lowest)" if p.get("tie") else f"L{p.get('rank', '—')}")]
    if stage == "APPROVAL" and p.get("decision") == "APPROVE":
        extra_lines.append("The Competent Authority hereby awards the tender to the above bidder, "
                           "subject to the terms and conditions of the tender document and "
                           "applicable procurement regulations.")
    if stage == "CONTRACT":
        extra_rows = [("Contract No", p.get("contract_no")), ("PO No", p.get("po_no"))]
    if stage == "DELIVERY":
        extra_rows = [("Outcome", p.get("outcome")), ("Batch / Serial", p.get("batch_serial") or "—")]
    if stage == "PAYMENT":
        extra_rows = [("Invoice No", p.get("invoice_no")), ("Payment Ref", p.get("payment_ref")),
                      ("Amount (INR)", f"{p.get('payment_amount'):,.2f}" if p.get("payment_amount") else "—")]
    if stage == "AUDIT":
        extra_rows = [("Audit Chain", "VALID — tamper-evident" if p.get("audit_chain_valid") else "INVALID"),
                      ("Audit Records", p.get("audit_records"))]
    extra_lines.append(f"Officer's remarks: {p.get('remarks') or '—'}")
    from ..certificates import render_certificate
    buf = io.BytesIO()
    render_certificate(
        buf, stage_key=stage, application_no=a.application_no,
        tender_no=a.tender.tender_no, tender_title=a.tender.title,
        bidder_company=a.bidder_user.bidder_profile.company_name
        if a.bidder_user.bidder_profile else a.bidder_user.full_name,
        result_label=result_label,
        acted_by=f"{actor.full_name} ({actor.role})" if actor else "System",
        acted_at=row.acted_at, certificate_no=row.certificate_no or "—",
        extra_rows=extra_rows, extra_lines=extra_lines,
        org_name=(a.tender.procuring_organization.name if a.tender.procuring_organization
                  else a.tender.organization),
    )
    buf.seek(0)
    fname = f"{row.certificate_no or 'certificate'}_{stage.lower()}.pdf"
    return StreamingResponse(buf, media_type="application/pdf",
                             headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@router.get("/{application_id}/certificate/{stage}")
def stage_certificate(application_id: str, stage: str,
                      officer: User = Depends(get_current_pipeline_actor),
                      db: Session = Depends(get_db)):
    a = get_scoped_application(db, officer, application_id)
    if stage.upper() not in pl.STAGE_BY_KEY:
        raise HTTPException(status_code=404, detail="Unknown stage")
    role = pl.effective_workflow_role(db, officer, a.tender, a)
    from ..services import financial as fin_svc
    show_financial = fin_svc.financial_visible(db, a, role)
    return _certificate_response(db, a, stage.upper(), show_financial=show_financial)


@bidder_router.get("/applications/{application_id}/certificate/submission")
def submission_certificate(application_id: str, user: User = Depends(get_current_bidder),
                           db: Session = Depends(get_db)):
    a = (db.query(Application).filter(Application.id == application_id,
                                      Application.bidder_user_id == user.id).first())
    if not a:
        raise HTTPException(status_code=404, detail="Application not found")
    pl.init_pipeline(db, a)
    row = (db.query(pl.ApplicationStage)
           .filter(pl.ApplicationStage.application_id == a.id,
                   pl.ApplicationStage.stage_key == "SUBMISSION").first())
    if row is None or row.status != pl.COMPLETED:
        raise HTTPException(status_code=404, detail="Submission certificate not available")
    # Financial envelope acknowledgement: sealed status + hash + version.
    # The quoted amount is deliberately NOT printed (confidential).
    fin_rows: list[tuple[str, str]] = []
    from ..services import financial as fin_svc
    _fb = fin_svc.active_financial_bid(db, a.id)
    if _fb is not None:
        fin_rows = [("Financial Envelope", f"SEALED (v{_fb.version})" if _fb.status in ("SEALED", "OPENED") else _fb.status),
                    ("Financial Bid Hash", _fb.financial_bid_hash)]
    from ..certificates import render_certificate
    buf = io.BytesIO()
    render_certificate(
        buf, stage_key="SUBMISSION", application_no=a.application_no,
        tender_no=a.tender.tender_no, tender_title=a.tender.title,
        bidder_company=a.bidder_user.bidder_profile.company_name
        if a.bidder_user.bidder_profile else a.bidder_user.full_name,
        result_label="Bid submitted successfully",
        acted_by=a.bidder_user.full_name, acted_at=a.submitted_at,
        certificate_no=row.certificate_no or f"CERT-SUB-{a.application_no.replace('-', '')}",
        extra_rows=[("Submitted At", a.submitted_at),
                    ("Routed To", a.assigned_l1.full_name if a.assigned_l1 else "—"),
                    ("Routing Method", a.routing_method or "—"),
                    *fin_rows],
        extra_lines=["This confirms the bid application and its supporting declaration were "
                     "successfully submitted and recorded in the procurement system."],
        org_name=(a.tender.procuring_organization.name if a.tender.procuring_organization
                  else a.tender.organization),
    )
    buf.seek(0)
    fname = f"Submission_Certificate_{a.application_no}.pdf"
    return StreamingResponse(buf, media_type="application/pdf",
                             headers={"Content-Disposition": f'attachment; filename="{fname}"'})
