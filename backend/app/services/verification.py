"""AI verification pipeline (officer-triggered).

Pipeline (per specification):
1. Collect bidder profile  2. Collect documents  3. Extract document fields
4. Retrieve mock government source data  5. Normalize  6. Cross-compare
7. Execute deterministic tender rules  8. Compute compliance score
9. Determine risk  10. Generate AI explanation  11. Persist VerificationReport

AI explains only. The rule engine decides.
"""
import logging
from datetime import date

from sqlalchemy.orm import Session

from ..ai_engine import explain_verification, extract_document_fields
from ..audit import append_audit
from ..core.config import settings
from ..core.db import utcnow
from ..models import (
    Application,
    ApplicationDocument,
    Document,
    VerificationReport,
)
from ..notifications import notify, notify_admins
from ..rules_engine import build_evidence_context, evaluate_tender
from ..scoring import RISK_CRITICAL
from .authenticity import analyze_document
from .files import resolve_path

logger = logging.getLogger("gem.verification")


def _source_data_for_doc(ctx: dict, doc: Document) -> dict:
    """Issuing-source record used to corroborate a document's fields.

    A record still corroborates document fields when the registration is
    suspended/expired — the document can be genuine while the *status* is
    bad (the rule engine reports the status separately). Only truly absent
    or unfetchable records yield no corroboration.
    """
    mapping = {
        "GST_CERT": "GST",
        "UDYAM_CERT": "UDYAM",
        "PAN": "PAN",
        "MCA_DOC": "MCA21",
        "BIS_CERT": "BIS",
        "EPFO_DOC": "EPFO",
    }
    source = mapping.get(doc.doc_type)
    if source is None:
        return {}
    out = ctx["sources"].get(source, {})
    if out.get("status") in ("UNAVAILABLE", "ERROR", "NOT_FETCHED", "CONSENT_REQUIRED", "NOT_FOUND"):
        return {}
    return out.get("data", {}) or {}


def run_verification(
    db: Session, application: Application, officer, language: str = "en"
) -> VerificationReport:
    application.status = "AI Verification In Progress"
    db.commit()

    user = application.bidder_user
    tender = application.tender

    # 1-2. collect profile + documents
    ctx = build_evidence_context(db, user, tender)
    profile = ctx["profile"]
    vault_docs = ctx["documents"]
    app_docs: list[Document] = []
    for ad in (
        db.query(ApplicationDocument)
        .filter(ApplicationDocument.application_id == application.id)
        .all()
    ):
        if ad.document_id:
            d = db.get(Document, ad.document_id)
            if d:
                app_docs.append(d)

    all_docs = list({d.id: d for d in vault_docs + app_docs}.values())

    # 3. document field extraction (AI/deterministic)
    for doc in all_docs:
        if doc.ocr_data is None and doc.file_path:
            try:
                path = resolve_path("docs", doc.file_path)
            except FileNotFoundError:
                path = None
            fields, conf, engine = extract_document_fields(doc, path)
            if fields:
                doc.ocr_data = fields
                doc.ocr_confidence = conf

    # 6-7. deterministic evaluation (sources fetched inside build_evidence_context)
    evaluation = evaluate_tender(db, user, tender, ctx=ctx)

    # document authenticity assessment
    authenticity_results = []
    for doc in all_docs:
        if not doc.file_path:
            continue
        try:
            findings = analyze_document(db, doc, source_data=_source_data_for_doc(ctx, doc))
        except Exception as exc:
            logger.exception("authenticity failed for %s", doc.id)
            findings = {
                "document_id": doc.id,
                "document_name": doc.name,
                "doc_type": doc.doc_type,
                "status": "UNABLE_TO_VERIFY",
                "reasons": [f"Authenticity analysis error: {type(exc).__name__}"],
            }
        doc.authenticity_status = findings["status"]
        doc.authenticity_findings = findings
        authenticity_results.append(findings)

    # 10. AI explanation (advisory only) — the XAI engine needs the full rule
    # results (clause refs, weights, evidence) to ground its explanations.
    payload_for_ai = {
        "compliance_score": evaluation["compliance_score"],
        "risk_level": evaluation["risk_level"],
        "eligibility_result": evaluation["eligibility_result"],
        "rule_results": evaluation["rule_results"],
        "mismatches": evaluation["mismatches"],
        "missing_documents": evaluation["missing_documents"],
        "expired_documents": evaluation["expired_documents"],
        "blacklist_findings": evaluation["blacklist_findings"],
        "low_confidence_fields": evaluation["low_confidence_fields"],
        "source_results": evaluation["source_results"],
        "authenticity_summary": [
            {"name": a.get("document_name"), "status": a.get("status")} for a in authenticity_results
        ],
        "documents_summary": (
            f"{sum(1 for a in authenticity_results if a.get('status') == 'VERIFIED')} verified against "
            f"issuing sources · {sum(1 for a in authenticity_results if a.get('status') not in (None, 'VERIFIED'))} "
            f"flagged for review · {len(evaluation['missing_documents'])} missing"
        ),
    }
    explanation, model_id, explanation_detail = explain_verification(payload_for_ai, language)

    run_no = (
        db.query(VerificationReport)
        .filter(VerificationReport.application_id == application.id)
        .count()
        + 1
    )
    report = VerificationReport(
        application_id=application.id,
        run_no=run_no,
        triggered_by=officer.id,
        status="COMPLETED",
        compliance_score=evaluation["compliance_score"],
        risk_level=evaluation["risk_level"],
        eligibility_result=evaluation["eligibility_result"],
        rule_results=evaluation["rule_results"],
        cross_verification=evaluation["cross_verification"],
        mismatches=evaluation["mismatches"],
        missing_documents=evaluation["missing_documents"],
        expired_documents=evaluation["expired_documents"],
        blacklist_findings=evaluation["blacklist_findings"],
        low_confidence_fields=evaluation["low_confidence_fields"],
        source_results=evaluation["source_results"],
        authenticity=authenticity_results,
        ai_explanation=explanation,
        ai_explanation_language=language,
        explanation_detail=explanation_detail,
        model_identifier=model_id,
        rules_version_at_run=tender.rules_version,
        report_version="1.0",
        recommendation=_recommendation(evaluation, authenticity_results),
        evidence_refs=[
            {"kind": "source", "source": s, "status": v.get("status"), "retrieved_at": v.get("retrieved_at")}
            for s, v in evaluation["source_results"].items()
        ],
        strength_distribution=evaluation["strength_distribution"],
        coverage_pct=evaluation["coverage_pct"],
        determinism_hash=evaluation["determinism_hash"],
        generated_at=utcnow(),
    )
    db.add(report)

    application.compliance_score = evaluation["compliance_score"]
    application.risk_level = evaluation["risk_level"]
    application.eligibility_result = evaluation["eligibility_result"]
    application.last_verified_at = utcnow()
    application.report_stale = False
    application.status = "Pending L1 Review"
    db.flush()

    # notifications + audit
    if application.assigned_l1_id:
        notify(
            db,
            application.assigned_l1_id,
            "ai_report_ready",
            message=f"AI verification complete for {application.application_no}: "
            f"score {report.compliance_score}, risk {report.risk_level}.",
            entity_type="Application",
            entity_id=application.id,
        )
    notify(
        db,
        user.id,
        "eligibility_updated",
        message=f"Verification completed for your application {application.application_no}.",
        entity_type="Application",
        entity_id=application.id,
    )
    if evaluation["risk_level"] == RISK_CRITICAL:
        notify_admins(
            db,
            "override_alert",
            message=f"CRITICAL risk for {application.application_no} ({tender.tender_no}). "
            f"Blacklist/mandatory hard failure detected.",
            entity_type="Application",
            entity_id=application.id,
        )
    append_audit(
        db,
        "ai_verification_completed",
        "Application",
        application.id,
        actor_id=officer.id,
        actor_role=officer.role,
        payload={
            "report_id": report.id,
            "score": report.compliance_score,
            "risk": report.risk_level,
            "eligibility": report.eligibility_result,
            "model": model_id,
        },
    )
    db.commit()
    db.refresh(report)

    # SENTINEL WATCH: on completion of any verification, register (or refresh)
    # a ComplianceWatch so the bid is continuously monitored, not just
    # point-in-time checked (spec §8.1).
    try:
        from .watch import register_watch

        register_watch(db, application, report)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("watch registration failed for %s", application.id)

    return report


def _recommendation(evaluation: dict, authenticity: list) -> str:
    risk = evaluation["risk_level"]
    if risk == RISK_CRITICAL:
        return "REJECT — hard-disqualification condition confirmed. Proceeding requires the Override Accountability Engine with full accountability chain."
    fails = [r for r in evaluation["rule_results"] if r["result"] == "FAIL"]
    unables = [r for r in evaluation["rule_results"] if r["result"] == "UNABLE_TO_VERIFY"]
    suspicious = [a for a in authenticity if a.get("status") == "SUSPICIOUS"]
    if fails:
        detail = "; ".join(r["label"] for r in fails[:4])
        if unables:
            return f"REQUEST_CLARIFICATION — confirmed failures ({detail}) plus unverifiable checks; obtain corrected evidence, then rerun verification."
        return f"REQUEST_CLARIFICATION — confirmed failures: {detail}."
    if unables:
        detail = "; ".join(r["label"] for r in unables[:4])
        return f"MANUAL_REVIEW_REQUIRED — sources unavailable/unverifiable ({detail}). Never treat as PASS; verify offline and document."
    if suspicious:
        return "MANUAL_REVIEW_REQUIRED — document authenticity findings require officer review."
    return "QUALIFY — all deterministic checks passed; recommend qualification."


def mark_report_stale(db: Session, application: Application, reason: str) -> None:
    application.report_stale = True
    append_audit(
        db,
        "verification_report_stale",
        "Application",
        application.id,
        payload={"reason": reason},
    )
    db.commit()
