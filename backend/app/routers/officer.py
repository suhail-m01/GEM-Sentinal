from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..audit import append_audit
from ..core.config import settings
from ..core.db import get_db, utcnow
from ..core.deps import L1_BEHAVIOR_ROLES, L2_BEHAVIOR_ROLES, get_current_officer
from ..models import (
    Application,
    ApplicationDocument,
    ApplicationStatusHistory,
    Clarification,
    FinancialBid,
    Jurisdiction,
    OfficerAssignment,
    OverrideRecord,
    PROCUREMENT_MODE_LABELS,
    Tender,
    TenderAssignment,
    User,
    VerificationReport,
)
from ..notifications import notify
from ..override import (
    REASON_CATEGORIES,
    process_override,
)
from ..reports import report_to_html, report_to_pdf
from ..services.decisions import process_decision
from ..services.verification import run_verification
from ..scoring import risk_label

router = APIRouter(prefix="/officer", tags=["officer"])


def officer_application_query(db: Session, officer: User):
    """Return only applications this user is authorized to see.

    V2 authority is tender-scoped through active TenderAssignment rows. Admin
    and Auditor are global by design. Legacy L1/L2 accounts retain the old
    jurisdiction fallback only when they have no tender assignments.
    """
    if officer.role in ("ADMIN", "AUDITOR"):
        return db.query(Application)

    assigned_tender_ids = [
        row[0] for row in
        db.query(TenderAssignment.tender_id)
        .filter(TenderAssignment.user_id == officer.id, TenderAssignment.active.is_(True))
        .all()
    ]
    if assigned_tender_ids:
        return db.query(Application).filter(Application.tender_id.in_(assigned_tender_ids))

    if officer.role in ("L1_OFFICER", "L2_OFFICER"):
        codes = [
            j.code
            for j in (
                db.query(Jurisdiction)
                .join(OfficerAssignment, OfficerAssignment.jurisdiction_id == Jurisdiction.id)
                .filter(OfficerAssignment.user_id == officer.id)
                .all()
            )
        ]
        return (
            db.query(Application)
            .join(Tender, Tender.id == Application.tender_id)
            .filter(Tender.jurisdiction_code.in_(codes))
        )

    # Explicit V2 officer roles without a tender assignment see nothing.
    return db.query(Application).filter(Application.id == "__no_authorized_application__")


def ensure_bids_open(a: Application) -> None:
    """Bid-confidentiality gate: evaluation roles cannot open bid details or
    run verification before the bid deadline (bid opening)."""
    from ..core.bids import sealed_detail, tender_bids_open

    if not tender_bids_open(a.tender):
        raise HTTPException(status_code=403, detail=sealed_detail(a.tender))


def get_scoped_application(db: Session, officer: User, application_id: str) -> Application:
    a = db.get(Application, application_id)
    if not a:
        raise HTTPException(status_code=404, detail="Application not found")

    if officer.role not in ("ADMIN", "AUDITOR"):
        assigned = (
            db.query(TenderAssignment.id)
            .filter(TenderAssignment.tender_id == a.tender_id,
                    TenderAssignment.user_id == officer.id,
                    TenderAssignment.active.is_(True))
            .first()
        )
        if not assigned:
            # Migration-only fallback for genuinely legacy officer accounts.
            if officer.role not in ("L1_OFFICER", "L2_OFFICER"):
                raise HTTPException(status_code=403, detail="You are not assigned to this tender")
            codes = [
                j.code
                for j in (
                    db.query(Jurisdiction)
                    .join(OfficerAssignment, OfficerAssignment.jurisdiction_id == Jurisdiction.id)
                    .filter(OfficerAssignment.user_id == officer.id)
                    .all()
                )
            ]
            if a.tender.jurisdiction_code not in codes:
                raise HTTPException(status_code=403, detail="Application is outside your authorized jurisdiction")
    ensure_bids_open(a)
    return a


def _app_summary(a: Application) -> dict:
    from ..core.bids import effective_bid_deadline, tender_bids_open

    open_ = tender_bids_open(a.tender)
    dl = effective_bid_deadline(a.tender)
    if open_:
        return {
            "id": a.id,
            "application_no": a.application_no,
            "company": a.bidder_user.bidder_profile.company_name if a.bidder_user.bidder_profile else a.bidder_user.full_name,
            "tender_no": a.tender.tender_no,
            "tender_title": a.tender.title,
            "district": a.tender.district,
            "status": a.status,
            "risk_level": a.risk_level,
            "compliance_score": a.compliance_score,
            "eligibility_result": a.eligibility_result,
            "fraud_flagged": a.fraud_flagged,
            "report_stale": a.report_stale,
            "sla_due_at": a.sla_due_at.isoformat() if a.sla_due_at else None,
            "sla_breached": a.sla_breached,
            "submitted_at": a.submitted_at.isoformat() if a.submitted_at else None,
            "sealed": False,
            "bid_deadline_utc": dl.isoformat() if dl else None,
        }
    # Sealed: aggregates only — no bidder identity, score, or outcome.
    return {
        "id": a.id,
        "application_no": a.application_no,
        "company": None,
        "tender_no": a.tender.tender_no,
        "tender_title": a.tender.title,
        "district": a.tender.district,
        "status": "Sealed",
        "risk_level": None,
        "compliance_score": None,
        "eligibility_result": None,
        "fraud_flagged": None,
        "report_stale": None,
        "sla_due_at": None,
        "sla_breached": None,
        "submitted_at": None,
        "sealed": True,
        "bid_deadline_utc": dl.isoformat() if dl else None,
    }


def _authority_chain(t) -> list[dict] | None:
    """Resolve the delegated-authority chain for this tender's value under the
    procuring organization's configured bands. Role titles are org-specific
    (not a fixed L1/L2/L3 label), because designations and approval powers
    change with the organization and tender value."""
    org = t.procuring_organization
    if not org or not org.authority_bands:
        return None
    value = float(t.value_max) if t.value_max is not None else (float(t.value_min) if t.value_min is not None else 0.0)
    chain = []
    for b in sorted(org.authority_bands, key=lambda x: x.get("min_value") or 0):
        lo = float(b.get("min_value") or 0)
        hi = b.get("max_value")
        if value >= lo and (hi is None or value <= float(hi)):
            chain.append({
                "band": f"{lo:,.0f}–{hi:,.0f}" if hi else f"{lo:,.0f}+",
                "review_role_title": b.get("review_role_title"),
                "approval_role_title": b.get("approval_role_title"),
                "committee_required": bool(b.get("committee_required", False)),
            })
    return chain


@router.get("/dashboard")
def dashboard(officer: User = Depends(get_current_officer), db: Session = Depends(get_db)):
    q = officer_application_query(db, officer)
    if officer.role in L1_BEHAVIOR_ROLES:
        q = q.filter(Application.assigned_l1_id == officer.id)
    apps = q.all()
    now = utcnow()
    for a in apps:
        if a.sla_due_at and a.sla_due_at < now and a.status not in ("Approved", "Rejected", "Withdrawn", "Closed"):
            a.sla_breached = True
    blacklisted = [
        a for a in apps
        if (
            db.query(VerificationReport)
            .filter(VerificationReport.application_id == a.id, VerificationReport.risk_level == "CRITICAL")
            .first()
        )
    ]
    overrides = (
        db.query(OverrideRecord)
        .join(Application, Application.id == OverrideRecord.application_id)
        .filter(
            Application.assigned_l1_id == officer.id if officer.role in L1_BEHAVIOR_ROLES else True,
            OverrideRecord.created_at >= now - timedelta(days=30),
        )
        .all()
    ) if officer.role in L1_BEHAVIOR_ROLES else []
    return {
        "role": officer.role,
        "total_assigned": len(apps),
        "pending_reviews": sum(1 for a in apps if a.status == "Pending L1 Review"),
        "ai_verification_not_run": sum(1 for a in apps if a.status == "AI Verification Not Run"),
        "ai_in_progress": sum(1 for a in apps if a.status == "AI Verification In Progress"),
        "high_risk": sum(1 for a in apps if a.risk_level in ("HIGH", "CRITICAL")),
        "blacklisted_bidders": len(blacklisted),
        "clarification_requests": sum(1 for a in apps if a.status == "Clarification Requested"),
        "sla_breaches": sum(1 for a in apps if a.sla_breached),
        "awaiting_l2": sum(1 for a in apps if a.status in ("L1 Recommended", "Pending L2 Approval", "Admin Co-sign Required")),
        "recent_override_alerts": [
            {"override_id": o.id, "application_no": o.application.application_no, "severity": o.severity,
             "officer": db.get(User, o.officer_id).full_name if db.get(User, o.officer_id) else None,
             "at": o.created_at.isoformat()}
            for o in overrides[-5:]
        ],
        "applications": [_app_summary(a) for a in apps[-20:][::-1]],
    }


@router.get("/tenders")
def list_officer_tenders(officer: User = Depends(get_current_officer), db: Session = Depends(get_db)):
    """Tenders this officer has authority over (assignment-scoped).

    Used by the auction console to pick a tender for auction creation;
    exposes the procurement mode so the UI can offer the matching
    auction direction (REVERSE/FORWARD).
    """
    from ..core.bids import effective_bid_deadline, tender_bids_open

    if officer.role in ("ADMIN", "AUDITOR"):
        ts = db.query(Tender).order_by(Tender.tender_no.desc()).all()
    else:
        tender_ids = [ta.tender_id for ta in db.query(TenderAssignment)
                      .filter(TenderAssignment.user_id == officer.id,
                              TenderAssignment.active.is_(True))]
        ts = (db.query(Tender).filter(Tender.id.in_(tender_ids))
              .order_by(Tender.tender_no.desc()).all() if tender_ids else [])
    out = []
    for t in ts:
        dl = effective_bid_deadline(t)
        out.append({
            "id": t.id,
            "tender_no": t.tender_no,
            "title": t.title,
            "district": t.district,
            "status": t.status,
            "procurement_mode": t.effective_procurement_mode,
            "procurement_mode_label": PROCUREMENT_MODE_LABELS.get(t.effective_procurement_mode, t.effective_procurement_mode),
            "financial_bid_required": bool(t.requires_financial_bid),
            "bids_open": tender_bids_open(t),
            "effective_bid_deadline": dl.isoformat() if dl else None,
        })
    return {"tenders": out}


@router.get("/applications")
def list_applications(
    officer: User = Depends(get_current_officer),
    db: Session = Depends(get_db),
    status: str | None = None,
    risk: str | None = None,
    q: str | None = None,
):
    query = officer_application_query(db, officer)
    if officer.role in L1_BEHAVIOR_ROLES:
        query = query.filter(Application.assigned_l1_id == officer.id)
    if status and status != "Sealed":
        query = query.filter(Application.status == status)
    if risk:
        query = query.filter(Application.risk_level == risk)
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                Application.application_no.ilike(like),
                Application.tender.has(Tender.tender_no.ilike(like)),
                Application.tender.has(Tender.title.ilike(like)),
            )
        )
    apps = query.order_by(Application.created_at.desc()).all()
    summaries = [_app_summary(a) for a in apps]
    if status == "Sealed":
        summaries = [s for s in summaries if s.get("sealed")]
    return {"applications": summaries}


def _full_review(db: Session, a: Application, viewer_role: str | None = None) -> dict:
    from ..models import BidderProfile, Document, OfficerDecision, OverrideRecord as OR

    p = a.bidder_user.bidder_profile
    report = (
        db.query(VerificationReport)
        .filter(VerificationReport.application_id == a.id)
        .order_by(VerificationReport.generated_at.desc())
        .first()
    )
    from ..pipeline_docs import doc_scope_info, filter_docs_for_role, filter_rule_results, build_officer_summary

    _all_docs = db.query(Document).filter(Document.user_id == a.bidder_user_id).all()
    docs = filter_docs_for_role([
        {"id": d.id, "name": d.name, "doc_type": d.doc_type, "source": d.source} for d in _all_docs
    ], viewer_role)
    _visible_doc_ids = {d["id"] for d in docs}
    app_docs = db.query(ApplicationDocument).filter(ApplicationDocument.application_id == a.id).all()
    app_docs = [ad for ad in app_docs if ad.document_id in _visible_doc_ids] if viewer_role else app_docs
    clarifications = db.query(Clarification).filter(Clarification.application_id == a.id).order_by(Clarification.created_at.desc()).all()
    decisions = db.query(OfficerDecision).filter(OfficerDecision.application_id == a.id).order_by(OfficerDecision.created_at.asc()).all()
    overrides = db.query(OR).filter(OR.application_id == a.id).order_by(OR.created_at.asc()).all()
    # previous applications of same bidder on other tenders (where permitted: same organization)
    previous = (
        db.query(Application)
        .filter(
            Application.bidder_user_id == a.bidder_user_id,
            Application.id != a.id,
            Application.tender.has(Tender.organization == a.tender.organization),
            Application.status.in_(["Approved", "Rejected", "Withdrawn", "Closed"]),
        )
        .all()
    )
    from ..models import AuditLog

    audit_rows = (
        db.query(AuditLog).filter(AuditLog.entity_id == a.id)
        .order_by(AuditLog.created_at.desc()).limit(30).all()
    )
    from .. import pipeline as _pl

    return {
        "my_role": viewer_role,
        "pipeline": _pl.pipeline_state(db, a),
        "application": {
            **_app_summary(a),
            "assigned_l1": a.assigned_l1.full_name if a.assigned_l1 else None,
            "assigned_l2": a.assigned_l2.full_name if a.assigned_l2 else None,
            "routing_method": a.routing_method,
            "routing_fallback": a.routing_fallback,
            "declaration": a.declaration_text,
            "declaration_at": a.declaration_at.isoformat() if a.declaration_at else None,
            "snapshot_frozen_at": a.snapshot_frozen_at.isoformat() if a.snapshot_frozen_at else None,
        },
        "bidder": {
            "name": a.bidder_user.full_name,
            "email": a.bidder_user.email,
            "company": p.company_name if p else None,
            "trade_name": p.trade_name if p else None,
            "pan_masked": p.pan_masked if p else None,
            "gstin_masked": p.gstin_masked if p else None,
            "udyam_masked": p.udyam_masked if p else None,
            "business_type": p.business_type if p else None,
            "business_category": p.business_category if p else None,
            "registered_address": p.registered_address if p else None,
            "district": p.district if p else None,
            "state": p.state if p else None,
            "authorized_rep": p.authorized_rep_name if p else None,
            "annual_turnover": p.annual_turnover if p else None,
            "filing_status": p.filing_status if p else None,
            "local_content_percent": p.local_content_percent if p else None,
        },
        "tender": {
            "tender_no": a.tender.tender_no,
            "title": a.tender.title,
            "description": a.tender.description,
            "department": a.tender.department,
            "organization": a.tender.organization,
            "district": a.tender.district,
            "state": a.tender.state,
            "category": a.tender.category,
            "close_date": a.tender.close_date.isoformat(),
            "required_document_types": a.tender.required_document_types or [],
            "required_turnover": a.tender.required_turnover,
            "min_local_content": a.tender.min_local_content,
            "msme_preference": a.tender.msme_preference,
            "make_in_india_required": a.tender.make_in_india_required,
            "procurement_method": a.tender.procurement_method,
            "rules_version": a.tender.rules_version,
            "procuring_organization": (
                {"id": o.id, "name": o.name, "short_code": o.short_code, "org_type": o.org_type,
                 "parent_ministry": o.parent_ministry, "procurement_division": o.procurement_division,
                 "committee_min_members": o.committee_min_members,
                 "committee_requires_finance": o.committee_requires_finance,
                 "committee_requires_user_rep": o.committee_requires_user_rep}
                if (o := a.tender.procuring_organization) else None
            ),
            "authority_chain": _authority_chain(a.tender),
            "corrigenda": [
                {"seq_no": c.seq_no, "title": c.title, "summary": c.summary, "changes": c.changes or [],
                 "published_at": c.published_at.isoformat() if c.published_at else None,
                 "deadline_extended_to": c.deadline_extended_to.isoformat() if c.deadline_extended_to else None}
                for c in a.tender.corrigenda
            ],
            "rules": [
                {"rule_key": r.rule_key, "label": r.label, "source": r.source, "mandatory": r.mandatory,
                 "condition_type": r.condition_type, "condition_value": r.condition_value,
                 "weight": r.weight, "hard_disqualification": r.hard_disqualification,
                 "compliance_layer": r.compliance_layer, "clause_ref": r.clause_ref}
                for r in a.tender.rules
            ],
        },
        "documents": [
            {
                "id": d.id, "name": d.name, "doc_type": d.doc_type,
                "source": d.source, "source_classification": d.source_classification,
                "is_government_source": d.source_classification == "GOVERNMENT_SOURCE",
                "verification_status": d.verification_status,
                "issue_date": d.issue_date.isoformat() if d.issue_date else None,
                "expiry_date": d.expiry_date.isoformat() if d.expiry_date else None,
                "last_synced_at": d.last_synced_at.isoformat() if d.last_synced_at else None,
                "masked_identifier": d.masked_identifier,
                "document_hash": d.document_hash,
                "ocr_confidence": d.ocr_confidence,
                "authenticity_status": d.authenticity_status,
                "notes": d.notes,
                "in_application": any(ad.document_id == d.id for ad in app_docs),
            }
            for d in _all_docs
            if d.id in _visible_doc_ids
        ],
        "doc_scope": doc_scope_info([{"doc_type": d.doc_type} for d in _all_docs], viewer_role),
        "application_documents": [
            {"id": ad.id, "name": ad.name, "doc_type": ad.doc_type, "purpose": ad.purpose,
             "version": ad.version, "created_at": ad.created_at.isoformat()}
            for ad in app_docs
        ],
        "comparison_table": report.cross_verification if report else [],
        "verification_report": (
            {
                "id": report.id,
                "run_no": report.run_no,
                "generated_at": report.generated_at.isoformat(),
                "compliance_score": report.compliance_score,
                "risk_level": report.risk_level,
                "risk_label": risk_label(report.risk_level),
                "eligibility_result": report.eligibility_result,
                "rule_results": filter_rule_results(report.rule_results or [], viewer_role),
                "cross_verification": report.cross_verification,
                "mismatches": report.mismatches,
                "missing_documents": report.missing_documents,
                "expired_documents": report.expired_documents,
                "blacklist_findings": report.blacklist_findings,
                "low_confidence_fields": report.low_confidence_fields,
                "source_results": report.source_results,
                "authenticity": report.authenticity,
                "ai_explanation": report.ai_explanation,
                "officer_summary": build_officer_summary(report) if report else None,
                "ai_explanation_language": report.ai_explanation_language,
                "explanation_detail": report.explanation_detail,
                "model_identifier": report.model_identifier,
                "recommendation": report.recommendation,
                "coverage_pct": report.coverage_pct,
                "strength_distribution": report.strength_distribution,
                "determinism_hash": report.determinism_hash,
                "report_version": report.report_version,
                "created_from_report_id": report.created_from_report_id,
                "rules_version_at_run": report.rules_version_at_run,
                "tender_rules_version": a.tender.rules_version,
                "stale_due_to_corrigendum": (
                    report.rules_version_at_run is not None
                    and a.tender.rules_version > report.rules_version_at_run
                ),
                "latest_corrigendum": (
                    {"seq_no": a.tender.corrigenda[-1].seq_no,
                     "title": a.tender.corrigenda[-1].title,
                     "published_at": a.tender.corrigenda[-1].published_at.isoformat() if a.tender.corrigenda[-1].published_at else None}
                    if a.tender.corrigenda else None
                ),
                # Product boundary: verification & review recommendation only —
                # the tender award remains with the Tender Committee / Competent
                # Authority of the procuring organization.
                "system_boundary": (
                    "GeM-Sentinel verifies evidence and recommends a review status only. "
                    "It does not declare L1 or award the tender — the final procurement "
                    "decision rests with the procuring organization's Tender Committee and "
                    "Competent Financial Authority."
                ),
                "triggered_by": db.get(User, report.triggered_by).full_name if db.get(User, report.triggered_by) else None,
            }
            if report
            else None
        ),
        "last_verification": a.last_verified_at.isoformat() if a.last_verified_at else None,
        "clarifications": [
            {"id": c.id, "reason": c.reason, "documents_needed": c.documents_needed, "status": c.status,
             "deadline": c.deadline.isoformat() if c.deadline else None,
             "response_text": c.response_text, "created_at": c.created_at.isoformat()}
            for c in clarifications
        ],
        "decisions": [
            {"id": d.id, "level": d.level, "action": d.action, "remarks": d.remarks,
             "is_overridden": d.is_overridden,
             "officer": db.get(User, d.officer_id).full_name if db.get(User, d.officer_id) else None,
             "at": d.created_at.isoformat()}
            for d in decisions
        ],
        "overrides": [
            {"id": o.id, "severity": o.severity, "reason_category": o.reason_category,
             "justification": o.justification, "status": o.status,
             "admin_cosign_required": o.admin_cosign_required,
             "admin_cosigned_at": o.admin_cosigned_at.isoformat() if o.admin_cosigned_at else None,
             "vigilance_referral_id": o.vigilance_referral_id,
             "officer": db.get(User, o.officer_id).full_name if db.get(User, o.officer_id) else None,
             "at": o.created_at.isoformat()}
            for o in overrides
        ],
        "previous_applications": [
            {"application_no": x.application_no, "tender_no": x.tender.tender_no, "status": x.status,
             "compliance_score": x.compliance_score}
            for x in previous[:5]
        ],
        "timeline": [
            {"status": h.status, "remarks": h.remarks, "at": h.created_at.isoformat(), "actor_role": h.actor_role}
            for h in a.history
        ],
        "audit_trail": [
            {"id": x.id, "action": x.action, "actor_role": x.actor_role, "at": x.created_at.isoformat(),
             "record_hash": x.record_hash, "prev_hash": x.prev_hash}
            for x in audit_rows
        ],
        "report_stale": a.report_stale,
    }


@router.get("/applications/{application_id}")
def application_review(application_id: str, officer: User = Depends(get_current_officer), db: Session = Depends(get_db)):
    a = get_scoped_application(db, officer, application_id)
    from .. import pipeline as pl

    pl.init_pipeline(db, a)
    db.flush()
    return _full_review(db, a, viewer_role=pl.effective_workflow_role(db, officer, a.tender, a))


@router.get("/applications/{application_id}/financial-bid")
def application_financial_bid(application_id: str,
                              officer: User = Depends(get_current_officer),
                              db: Session = Depends(get_db)):
    """Financial envelope content — two-envelope gate.

    Returns the sealed quotation only to financial-viewing roles AFTER the
    application is technically qualified and bids are open; otherwise 403
    (server-side, never hidden values).
    """
    a = get_scoped_application(db, officer, application_id)
    from .. import pipeline as pl
    from ..services import financial as fin_svc

    pl.init_pipeline(db, a)
    db.flush()
    role = pl.effective_workflow_role(db, officer, a.tender, a)
    fin_svc.assert_financial_visible(db, a, role)
    bid = fin_svc.active_financial_bid(db, a.id)
    out = fin_svc.financial_envelope_status(db, a, viewer_role=role)
    if bid is None:
        out["versions"] = []
        return out
    versions = (db.query(FinancialBid)
                .filter(FinancialBid.application_id == a.id)
                .order_by(FinancialBid.version.desc()).all())
    out["versions"] = [
        {"version": v.version, "status": v.status,
         "sealed_at": v.sealed_at.isoformat() if v.sealed_at else None,
         "financial_bid_hash": v.financial_bid_hash}
        for v in versions
    ]
    return out


@router.post("/applications/{application_id}/run-verification")
def run_verification_endpoint(application_id: str, request: Request, officer: User = Depends(get_current_officer), db: Session = Depends(get_db)):
    a = get_scoped_application(db, officer, application_id)
    if a.status in ("Approved", "Rejected", "Withdrawn", "Closed"):
        raise HTTPException(status_code=400, detail=f"Cannot run verification in status: {a.status}")
    language = request.headers.get("x-preferred-language", "en")
    report = run_verification(db, a, officer, language=language)
    return {
        "report_id": report.id,
        "run_no": report.run_no,
        "compliance_score": report.compliance_score,
        "risk_level": report.risk_level,
        "eligibility_result": report.eligibility_result,
        "application_status": a.status,
    }


class DecisionIn(BaseModel):
    action: str
    remarks: str | None = None
    clarification_reason: str | None = None
    clarification_documents: list[str] = Field(default_factory=list)
    clarification_deadline_days: int = 3


@router.post("/applications/{application_id}/decision")
def make_decision(application_id: str, body: DecisionIn, request: Request, officer: User = Depends(get_current_officer), db: Session = Depends(get_db)):
    a = get_scoped_application(db, officer, application_id)
    level = "L1" if officer.role in L1_BEHAVIOR_ROLES else "L2"
    try:
        return process_decision(
            db, a, officer, level, body.action, remarks=body.remarks,
            clarification_reason=body.clarification_reason,
            clarification_docs=body.clarification_documents,
            clarification_deadline_days=body.clarification_deadline_days,
            request=request,
        )
    except HTTPException as exc:
        # 409 override challenge propagates to the client (override modal)
        raise


class OverrideIn(BaseModel):
    challenge: str
    reason_category: str
    justification: str
    reauth_token: str
    evidence: list[dict] = Field(default_factory=list)  # [{name, file_path, sha256}] from evidence upload


@router.post("/applications/{application_id}/override-evidence-upload")
def override_evidence_upload(
    application_id: str,
    request: Request,
    officer: User = Depends(get_current_officer),
    db: Session = Depends(get_db),
    file: UploadFile = File(...),
):
    """Pre-upload optional supporting evidence for an override (multipart)."""
    a = get_scoped_application(db, officer, application_id)
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
    officer: User = Depends(get_current_officer),
    db: Session = Depends(get_db),
):
    a = get_scoped_application(db, officer, application_id)
    level = "L1" if officer.role in L1_BEHAVIOR_ROLES else "L2"
    record = process_override(
        db, a, officer, level,
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
        "message": "Override recorded with full accountability chain. "
        + ("Admin co-sign is required before final approval." if record.admin_cosign_required else "Routed for L2 review."),
    }


@router.get("/documents/{doc_id}/view")
def view_document_officer(
    doc_id: str,
    application_id: str,
    officer: User = Depends(get_current_officer),
    db: Session = Depends(get_db),
):
    """Officer manual-review view of a bidder document (synthetic replica).
    Gated on the bid deadline for the linked application (confidentiality)."""
    if not settings.DEMO_MODE:
        raise HTTPException(status_code=404, detail="Synthetic Document Studio is disabled")
    from ..models import Document as DocModel
    from ..services.doc_studio import get_replica

    a = get_scoped_application(db, officer, application_id)
    d = db.get(DocModel, doc_id)
    if not d or d.user_id != a.bidder_user_id:
        raise HTTPException(status_code=404, detail="Document not found")
    profile = a.bidder_user.bidder_profile if a.bidder_user.bidder_profile else None
    png, digest = get_replica(d, profile, a.bidder_user, officer_view=True)
    append_audit(db, "document_replica_viewed", "Document", d.id,
                 actor_id=officer.id, actor_role=officer.role, commit=False)
    db.commit()
    return Response(
        content=png,
        media_type="image/png",
        headers={
            "Content-Disposition": f'inline; filename="replica-{d.doc_type}-review.png"',
            "X-Document-Hash": digest,
            "X-Synthetic": "true",
            "Cache-Control": "private, max-age=300",
        },
    )


@router.get("/applications/{application_id}/report/download")
def download_report(application_id: str, officer: User = Depends(get_current_officer), db: Session = Depends(get_db)):
    """Download the verification report. PDF is the only downloadable format."""
    a = get_scoped_application(db, officer, application_id)
    report = (
        db.query(VerificationReport).filter(VerificationReport.application_id == a.id)
        .order_by(VerificationReport.generated_at.desc()).first()
    )
    if not report:
        raise HTTPException(status_code=404, detail="No verification report yet")
    bidder_name = a.bidder_user.bidder_profile.company_name if a.bidder_user.bidder_profile else a.bidder_user.full_name
    append_audit(db, "report_download", "VerificationReport", report.id, actor_id=officer.id, actor_role=officer.role, commit=False)
    db.commit()
    pdf_bytes = report_to_pdf(report, a, a.tender, bidder_name)
    from fastapi.responses import Response

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename=report-{a.application_no}.pdf",
            "Cache-Control": "no-store",
        },
    )


@router.get("/applications/{application_id}/report")
def get_report_json(application_id: str, officer: User = Depends(get_current_officer), db: Session = Depends(get_db)):
    a = get_scoped_application(db, officer, application_id)
    report = (
        db.query(VerificationReport).filter(VerificationReport.application_id == a.id)
        .order_by(VerificationReport.generated_at.desc()).first()
    )
    if not report:
        raise HTTPException(status_code=404, detail="No verification report yet")
    data = _full_review(db, a)
    return data["verification_report"]
