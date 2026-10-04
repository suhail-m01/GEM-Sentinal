"""Override Accountability Engine (core product differentiator — not to be
simplified).

Trigger: L1/L2 attempts to approve/recommend qualification while the latest
report shows Ineligible / High Risk / Critical / Blacklisted / critical
mandatory mismatch.

Flow: blocking decision → override modal (evidence + findings displayed) →
reason category + meaningful justification + password re-authentication →
OverrideRecord + AuditLog + Admin notification → severity-based routing →
external Vigilance referral for serious/critical cases.

An override is NEVER treated as proof of bribery/corruption/coercion/misconduct.
It is recorded and, where required, referred for independent investigation.
"""
import hashlib
import json
import logging
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .audit import append_audit
from .core.config import settings
from .core.db import utcnow
from .core.security import create_token, decode_token, verify_password
from .models import (
    Application,
    AuditLog,
    OfficerDecision,
    OverrideRecord,
    VerificationReport,
    VigilanceReferral,
    User,
)
from .notifications import notify, notify_admins

logger = logging.getLogger("gem.override")

REASON_CATEGORIES = [
    "AI_DATA_OUTDATED",
    "PORTAL_DATA_MISMATCH_MANUALLY_VERIFIED",
    "DOCUMENT_VERIFIED_OFFLINE",
    "EXCEPTIONAL_APPROVAL_UNDER_TENDER_CLAUSE",
    "FALSE_POSITIVE",
    "EMERGENCY_PROCUREMENT_PROVISION",
    "OTHER",
]

REASON_LABELS = {
    "AI_DATA_OUTDATED": "AI data outdated",
    "PORTAL_DATA_MISMATCH_MANUALLY_VERIFIED": "Portal data mismatch — manually verified",
    "DOCUMENT_VERIFIED_OFFLINE": "Document verified offline",
    "EXCEPTIONAL_APPROVAL_UNDER_TENDER_CLAUSE": "Exceptional approval under tender clause",
    "FALSE_POSITIVE": "False positive",
    "EMERGENCY_PROCUREMENT_PROVISION": "Emergency procurement provision",
    "OTHER": "Other (detailed explanation required)",
}

OVERRIDE_TRIGGER_ACTIONS = ("RECOMMEND_QUALIFY", "APPROVE", "APPROVE_WITH_OBSERVATION")


class FraudBlockedError(HTTPException):
    def __init__(self):
        super().__init__(
            status_code=403,
            detail=(
                "Approval is blocked: this application has been flagged for suspected fraud. "
                "The case has been escalated to Admin and external Vigilance. No override is permitted."
            ),
        )


# ---------------- findings extraction ----------------

def _latest_report(db: Session, application_id: str) -> Optional[VerificationReport]:
    return (
        db.query(VerificationReport)
        .filter(VerificationReport.application_id == application_id)
        .order_by(VerificationReport.generated_at.desc())
        .first()
    )


def extract_blocking_findings(db: Session, application: Application) -> Optional[dict]:
    """Returns the blocking finding if an override is required, else None."""
    report = _latest_report(db, application.id)
    if report is None:
        from .rules_engine import build_evidence_context, evaluate_tender

        evaluation = evaluate_tender(
            db, application.bidder_user, application.tender
        )
        risk = evaluation["risk_level"]
        eligibility = evaluation["eligibility_result"]
        blacklist = evaluation["blacklist_findings"]
        hard_fails = [
            {"label": r["label"], "message": r["message"], "evidence": r["evidence"]}
            for r in evaluation["rule_results"]
            if r["result"] == "FAIL" and (r["hard_disqualification"] or r["mandatory"])
        ]
        unables = [
            {"label": r["label"], "message": r["message"]}
            for r in evaluation["rule_results"]
            if r["result"] == "UNABLE_TO_VERIFY" and r["mandatory"]
        ]
        mismatches = evaluation["mismatches"]
        explanation = None
        model = "deterministic-live-evaluation"
        generated_at = utcnow().isoformat()
    else:
        risk = report.risk_level
        eligibility = report.eligibility_result
        blacklist = report.blacklist_findings or []
        hard_fails = [
            {"label": r["label"], "message": r["message"], "evidence": r.get("evidence")}
            for r in (report.rule_results or [])
            if r["result"] == "FAIL" and (r.get("hard_disqualification") or r.get("mandatory"))
        ]
        unables = [
            {"label": r["label"], "message": r["message"]}
            for r in (report.rule_results or [])
            if r["result"] == "UNABLE_TO_VERIFY" and r.get("mandatory")
        ]
        mismatches = report.mismatches or []
        explanation = report.ai_explanation
        model = report.model_identifier
        generated_at = report.generated_at.isoformat()

    blocking = (
        risk in ("HIGH", "CRITICAL")
        or eligibility in ("NOT_ELIGIBLE", "BLACKLISTED")
        or bool(blacklist)
        or bool(hard_fails)
    )
    if not blocking:
        return None

    finding = {
        "risk_level": risk,
        "eligibility_result": eligibility,
        "blacklisted": bool(blacklist),
        "blacklist_findings": blacklist,
        "critical_mandatory_mismatches": [],
        "failed_rules": hard_fails,
        "unverified_mandatory": unables,
        "mismatches": mismatches,
        "compliance_score": report.compliance_score if report else None,
        "ai_explanation": explanation,
        "model": model,
        "report_generated_at": generated_at,
        "report_id": report.id if report else None,
    }
    finding["critical_mandatory_mismatches"] = [
        m for m in mismatches
    ]
    return finding


def determine_severity(finding: dict) -> str:
    if finding["blacklisted"] or finding["risk_level"] == "CRITICAL":
        return "CRITICAL"
    if (
        finding["eligibility_result"] == "NOT_ELIGIBLE"
        or finding["risk_level"] == "HIGH"
        or finding["critical_mandatory_mismatches"]
    ):
        return "HIGH"
    if finding["risk_level"] == "MEDIUM":
        return "MEDIUM"
    return "MINOR"


# ---------------- challenge + verification ----------------

def _finding_hash(finding: dict) -> str:
    return hashlib.sha256(json.dumps(finding, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()


def create_override_challenge(application: Application, officer: User, finding: dict) -> dict:
    token = create_token(
        subject=officer.id,
        role=officer.role,
        purpose="override_challenge",
        minutes=15,
    )
    token = f"{token}.{_finding_hash(finding)}"  # bound to exact finding
    return {
        "override_required": True,
        "challenge": token,
        "severity": determine_severity(finding),
        "finding": {
            "risk_level": finding["risk_level"],
            "eligibility_result": finding["eligibility_result"],
            "blacklisted": finding["blacklisted"],
            "blacklist_findings": finding["blacklist_findings"],
            "failed_rules": finding["failed_rules"],
            "unverified_mandatory": finding["unverified_mandatory"],
            "mismatches": finding["mismatches"],
            "compliance_score": finding["compliance_score"],
            "ai_explanation": finding["ai_explanation"],
            "report_generated_at": finding["report_generated_at"],
        },
        "reason_categories": [{"id": c, "label": REASON_LABELS[c]} for c in REASON_CATEGORIES],
        "min_justification_length": settings.MIN_JUSTIFICATION_LENGTH,
        "min_other_justification_length": settings.MIN_OTHER_JUSTIFICATION_LENGTH,
        "requires_password_reauthentication": True,
        "policy": "Proceeding with qualification despite these findings will create an accountable override record, notify System Admin, and may be referred to the external Vigilance authority for independent investigation.",
    }


def verify_challenge(challenge: str, application: Application, officer: User, finding: dict) -> None:
    try:
        token, bound_hash = challenge.rsplit(".", 1)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid override challenge token")
    payload = decode_token(token, purpose="override_challenge")
    if not payload:
        raise HTTPException(status_code=400, detail="Override challenge expired or invalid — re-trigger the decision")
    if payload.get("sub") != officer.id:
        raise HTTPException(status_code=403, detail="Override challenge was issued to a different officer")
    if bound_hash != _finding_hash(finding):
        raise HTTPException(status_code=400, detail="Override challenge does not match current findings — rerun verification and retry")


def verify_reauthentication(reauth_token: str, officer: User) -> None:
    payload = decode_token(reauth_token, purpose="reauth")
    if not payload or payload.get("sub") != officer.id:
        raise HTTPException(status_code=401, detail="Re-authentication failed: invalid or expired re-authentication token")


# ---------------- vigilance referral ----------------

def build_vigilance_report(
    db: Session, application: Application, officer: User, finding: dict, record: Optional[OverrideRecord]
) -> dict:
    user = application.bidder_user
    profile = user.bidder_profile
    tender = application.tender
    decisions = (
        db.query(OfficerDecision)
        .filter(OfficerDecision.application_id == application.id)
        .order_by(OfficerDecision.created_at.asc())
        .all()
    )
    audit_refs = (
        db.query(AuditLog)
        .filter(AuditLog.entity_id == application.id)
        .order_by(AuditLog.created_at.asc())
        .all()[-10:]
    )
    return {
        "application_id": application.id,
        "application_no": application.application_no,
        "bidder_details": {
            "user_id": user.id,
            "name": user.full_name,
            "email": user.email,
            "company": profile.company_name if profile else None,
            "district": profile.district if profile else None,
            "state": profile.state if profile else None,
            "gstin_masked": profile.gstin_masked if profile else None,
            "pan_masked": profile.pan_masked if profile else None,
        },
        "tender_details": {
            "tender_no": tender.tender_no,
            "title": tender.title,
            "department": tender.department,
            "district": tender.district,
            "state": tender.state,
        },
        "officer_details": {
            "officer_id": officer.id,
            "name": officer.full_name,
            "email": officer.email,
            "level": record.level if record else officer.role,
        },
        "deterministic_findings": {
            "risk_level": finding["risk_level"],
            "eligibility_result": finding["eligibility_result"],
            "compliance_score": finding["compliance_score"],
            "failed_rules": finding["failed_rules"],
            "unverified_mandatory": finding["unverified_mandatory"],
            "mismatches": finding["mismatches"],
            "blacklist_findings": finding["blacklist_findings"],
        },
        "ai_explanation": finding.get("ai_explanation"),
        "original_finding_generated_at": finding.get("report_generated_at"),
        "override": (
            {
                "reason_category": record.reason_category,
                "reason_label": REASON_LABELS.get(record.reason_category, record.reason_category),
                "justification": record.justification,
                "supporting_evidence": record.supporting_evidence,
                "reauth_at": record.reauth_at.isoformat() if record.reauth_at else None,
                "ip_address": record.ip_address,
            }
            if record
            else None
        ),
        "decision_history": [
            {
                "level": d.level,
                "action": d.action,
                "remarks": d.remarks,
                "is_overridden": d.is_overridden,
                "at": d.created_at.isoformat(),
            }
            for d in decisions
        ],
        "audit_hash_chain_reference": [
            {"record_id": a.id, "record_hash": a.record_hash, "action": a.action, "at": a.created_at.isoformat()}
            for a in audit_refs
        ],
        "generated_at": utcnow().isoformat(),
    }


def build_vigilance_notice(referral_no: str, report: dict) -> str:
    b = report["bidder_details"]
    t = report["tender_details"]
    o = report["officer_details"]
    f = report["deterministic_findings"]
    ov = report.get("override") or {}
    lines = [
        "VIGILANCE REFERRAL NOTICE — GeM-Sentinel",
        "=" * 60,
        f"Referral No: {referral_no}",
        f"Date: {report['generated_at']}",
        "",
        "This notice is generated by the GeM-Sentinel Override Accountability",
        "Engine to inform the external Vigilance authority that a serious",
        "system finding was overridden by an authorized officer and that",
        "independent investigation is recommended.",
        "",
        f"1. Application: {report['application_no']} (ID {report['application_id']})",
        f"2. Bidder: {b.get('company')} / {b.get('name')} ({b.get('email')}, {b.get('district')}, {b.get('state')})",
        f"   GSTIN(masked): {b.get('gstin_masked')}, PAN(masked): {b.get('pan_masked')}",
        f"3. Tender: {t['tender_no']} — {t['title']} ({t['department']}, {t['district']})",
        f"4. Officer: {o['name']} <{o['email']}> (Level: {o['level']})",
        f"5. Original deterministic system finding (generated {report.get('original_finding_generated_at')}):",
        f"   Risk level: {f['risk_level']} | Eligibility: {f['eligibility_result']} | Score: {f['compliance_score']}",
        f"6. Rule failures:",
    ]
    for r in f.get("failed_rules", [])[:10]:
        lines.append(f"   - {r['label']}: {r['message']}")
    if f.get("blacklist_findings"):
        bl = f["blacklist_findings"][0]
        lines.append(
            f"7. Blacklist/debarment finding: matched on {bl.get('matched_on')}; "
            f"authority: {bl.get('source_authority')}; order ref: {bl.get('official_order_reference')}"
        )
    if f.get("mismatches"):
        lines.append(f"8. Cross-verification mismatches: {len(f['mismatches'])} field(s) — see attached report.")
    lines += [
        f"9. Override reason category: {ov.get('reason_label')}",
        f"10. Officer justification: {ov.get('justification')}",
        f"11. Supporting evidence: {len(ov.get('supporting_evidence') or [])} file(s) attached to OverrideRecord",
        f"12. Decision history: {len(report.get('decision_history', []))} entries (attached)",
        f"13. Audit trail references (SHA-256 hash chain): {len(report.get('audit_hash_chain_reference', []))} records (attached)",
        f"14. Re-authentication timestamp: {ov.get('reauth_at')} | Source IP: {ov.get('ip_address')}",
        "",
        "IMPORTANT: This referral states that a serious system finding was",
        "overridden and that the matter requires independent investigation.",
        "It does NOT state — and must not be read as stating — that bribery,",
        "corruption, coercion, undue pressure, collusion, or any other",
        "misconduct has occurred or been proven. Any such determination rests",
        "solely with the competent investigating authority.",
    ]
    return "\n".join(lines)


def generate_vigilance_referral(
    db: Session,
    application: Application,
    officer: User,
    finding: dict,
    record: Optional[OverrideRecord] = None,
    status: str = "DRAFT",
) -> VigilanceReferral:
    count = db.query(VigilanceReferral).count() + 1
    referral_no = f"VIG-2026-{count:04d}"
    report = build_vigilance_report(db, application, officer, finding, record)
    referral = VigilanceReferral(
        referral_no=referral_no,
        override_record_id=record.id if record else None,
        application_id=application.id,
        status=status,
        report=report,
        notice_text=build_vigilance_notice(referral_no, report),
    )
    db.add(referral)
    return referral


# ---------------- main entry point ----------------

def process_override(
    db: Session,
    application: Application,
    officer: User,
    level: str,
    challenge: str,
    reason_category: str,
    justification: str,
    reauth_token: str,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    evidence_files: Optional[list[dict]] = None,
    request=None,
) -> OverrideRecord:
    # Secure IP context: prefer the request (trusted-proxy-aware derivation)
    if request is not None:
        from .ipcontext import resolve_client_ip
        peer = request.client.host if getattr(request, "client", None) else None
        ip_address, _ = resolve_client_ip(
            peer,
            x_forwarded_for=request.headers.get("x-forwarded-for"),
            x_real_ip=request.headers.get("x-real-ip"),
        )
        user_agent = (request.headers.get("user-agent") or "")[:300] or None
    if reason_category not in REASON_CATEGORIES:
        raise HTTPException(status_code=400, detail="Invalid reason category")
    justification = (justification or "").strip()
    min_len = (
        settings.MIN_OTHER_JUSTIFICATION_LENGTH
        if reason_category == "OTHER"
        else settings.MIN_JUSTIFICATION_LENGTH
    )
    if len(justification) < min_len:
        raise HTTPException(
            status_code=400,
            detail=f"Justification must be meaningful: minimum {min_len} characters"
            + (" (reason category 'Other' requires a detailed explanation)" if reason_category == "OTHER" else ""),
        )

    # re-authenticate officer credentials; invalid → override rejected
    verify_reauthentication(reauth_token, officer)

    finding = extract_blocking_findings(db, application)
    if finding is None:
        raise HTTPException(
            status_code=409,
            detail="No blocking finding on the latest report — override not required. Proceed with the normal decision.",
        )
    verify_challenge(challenge, application, officer, finding)

    severity = determine_severity(finding)

    record = OverrideRecord(
        application_id=application.id,
        officer_id=officer.id,
        level=level,
        severity=severity,
        overridden_finding=finding,
        reason_category=reason_category,
        justification=justification,
        supporting_evidence=evidence_files or [],
        reauth_at=utcnow(),
        ip_address=ip_address,
        user_agent=user_agent,
        admin_notified=severity in ("MEDIUM", "HIGH", "CRITICAL"),
        admin_cosign_required=severity == "CRITICAL",
        l2_review_required=True,
        status="PENDING_COSIGN" if severity == "CRITICAL" else "PENDING_REVIEW",
    )
    db.add(record)
    db.flush()

    # mark the triggering decision as overridden
    latest_dec = (
        db.query(OfficerDecision)
        .filter(OfficerDecision.application_id == application.id, OfficerDecision.officer_id == officer.id)
        .order_by(OfficerDecision.created_at.desc())
        .first()
    )
    if latest_dec and not latest_dec.is_overridden:
        latest_dec.is_overridden = True
        latest_dec.override_record_id = record.id

    # route application by severity
    if severity == "CRITICAL":
        application.status = "Admin Co-sign Required"
        referral = generate_vigilance_referral(db, application, officer, finding, record, status="DRAFT")
        db.flush()
        record.vigilance_referral_id = referral.id
        notify_admins(
            db,
            "admin_cosign_required",
            message=(
                f"CRITICAL override on {application.application_no} by {officer.full_name} "
                f"({REASON_LABELS[reason_category]}). Admin co-sign required. "
                f"Vigilance Referral {referral.referral_no} generated."
            ),
            entity_type="OverrideRecord",
            entity_id=record.id,
        )
        notify_admins(
            db,
            "vigilance_referral_generated",
            message=f"External Vigilance Referral {referral.referral_no} prepared for {application.application_no}.",
            entity_type="VigilanceReferral",
            entity_id=referral.id,
        )
    else:
        application.status = "Pending L2 Approval"
        if severity in ("MEDIUM", "HIGH"):
            notify_admins(
                db,
                "override_alert",
                message=(
                    f"{severity} override on {application.application_no} by {officer.full_name} "
                    f"({REASON_LABELS[reason_category]}). Routed for L2 review."
                ),
                entity_type="OverrideRecord",
                entity_id=record.id,
            )

    append_audit(
        db,
        "override_created",
        "OverrideRecord",
        record.id,
        actor_id=officer.id,
        actor_role=officer.role,
        payload={
            "application_id": application.id,
            "severity": severity,
            "reason_category": reason_category,
            "cosign_required": record.admin_cosign_required,
            "vigilance_referral": record.vigilance_referral_id,
        },
        application_id=application.id,
        ip_address=ip_address,
        user_agent=user_agent,
        request=request,
        commit=False,
    )
    db.commit()
    db.refresh(record)
    return record
