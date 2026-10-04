"""SENTINEL WATCH router — Watch Tower API (officer + admin).

Reads: any procurement-side role (officers, admin, read-only auditor).
Actions (ack/snooze/escalate/run-now/gate-check): Procurement Officer + Admin.
"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..audit import append_audit
from ..core.config import settings
from ..core.db import get_db, utcnow
from ..core.deps import require_roles
from ..models import Application, ComplianceWatch, DriftAlert, DriftEvent, SourceHealth, SourcePollLog, User
from ..services import watch as watch_svc
from .officer import get_scoped_application

router = APIRouter(prefix="/watch", tags=["watch"])

READ_ROLES = ("PROCUREMENT_OFFICER", "TECHNICAL_EVALUATOR", "FINANCE_EVALUATOR",
              "BID_OPENING_OFFICER", "COMMITTEE_CHAIR", "COMMITTEE_MEMBER",
              "CFA", "CONTRACT_MANAGER", "ADMIN", "AUDITOR")
ACTION_ROLES = ("PROCUREMENT_OFFICER", "ADMIN")

_any_reader = require_roles(*READ_ROLES)
_any_actor = require_roles(*ACTION_ROLES)


def _watch_out(w: ComplianceWatch) -> dict:
    a = w.application
    latest_score = None
    baseline = w.baseline or {}
    return {
        "id": w.id,
        "application_id": a.id,
        "application_no": a.application_no,
        "bidder_name": a.bidder_user.full_name if a.bidder_user else None,
        "tender_no": a.tender.tender_no if a.tender else None,
        "stage": w.stage,
        "active": w.active,
        "watched_attributes": w.watched_attributes,
        "baseline": baseline,
        "created_from_report_id": w.created_from_report_id,
        "last_polled_at": w.last_polled_at.isoformat() if w.last_polled_at else None,
        "created_at": w.created_at.isoformat() if w.created_at else None,
        "cadence_policy": w.cadence_policy,
    }


def _drift_out(d: DriftEvent) -> dict:
    return {
        "id": d.id,
        "watch_id": d.watch_id,
        "application_id": d.application_id,
        "attribute": d.attribute,
        "old_value": d.old_value,
        "new_value": d.new_value,
        "direction": d.direction,
        "severity": d.severity,
        "detected_at": d.detected_at.isoformat() if d.detected_at else None,
        "source": d.source,
        "evidence_strength": d.evidence_strength,
        "score_before": d.score_before,
        "score_after": d.score_after,
        "cause_rule_id": d.cause_rule_id,
        "cause_summary": d.cause_summary,
        "report_revision_id": d.report_revision_id,
        "original_report_id": d.original_report_id,
    }


@router.get("/watches")
def list_watches(db: Session = Depends(get_db), user: User = Depends(_any_reader)):
    watches = db.query(ComplianceWatch).order_by(ComplianceWatch.created_at.desc()).all()
    out = [_watch_out(w) for w in watches]
    # attach latest score for convenience
    from ..models import VerificationReport

    for w in out:
        latest = (
            db.query(VerificationReport)
            .filter(VerificationReport.application_id == w["application_id"])
            .order_by(VerificationReport.run_no.desc())
            .first()
        )
        w["score_now"] = latest.compliance_score if latest else None
        w["score_at_verification"] = None
    return {"watches": out}


@router.get("/watches/{watch_id}")
def watch_detail(watch_id: str, db: Session = Depends(get_db), user: User = Depends(_any_reader)):
    w = db.get(ComplianceWatch, watch_id)
    if not w:
        raise HTTPException(status_code=404, detail="Watch not found")
    out = _watch_out(w)
    drifts = (
        db.query(DriftEvent)
        .filter(DriftEvent.watch_id == watch_id)
        .order_by(DriftEvent.detected_at.desc())
        .all()
    )
    out["drift_events"] = [_drift_out(d) for d in drifts]
    return out


@router.get("/drift")
def drift_feed(db: Session = Depends(get_db), user: User = Depends(_any_reader),
               severity: str | None = None, application_id: str | None = None):
    q = db.query(DriftEvent).order_by(DriftEvent.detected_at.desc())
    if severity:
        q = q.filter(DriftEvent.severity == severity.upper())
    if application_id:
        q = q.filter(DriftEvent.application_id == application_id)
    events = q.limit(200).all()
    rows = []
    for d in events:
        row = _drift_out(d)
        app = db.get(Application, d.application_id)
        row["application_no"] = app.application_no if app else None
        row["bidder_name"] = app.bidder_user.full_name if (app and app.bidder_user) else None
        alert = (
            db.query(DriftAlert)
            .filter(DriftAlert.drift_event_id == d.id)
            .first()
        )
        row["alert_status"] = alert.status if alert else None
        rows.append(row)
    return {"drifts": rows}


@router.get("/alerts")
def alerts(db: Session = Depends(get_db), user: User = Depends(_any_reader),
           status: str | None = None):
    q = db.query(DriftAlert).order_by(DriftAlert.created_at.desc())
    if status:
        q = q.filter(DriftAlert.status == status.upper())
    rows = []
    for a in q.limit(200).all():
        app = db.get(Application, a.application_id)
        rows.append({
            "id": a.id,
            "drift_event_id": a.drift_event_id,
            "application_id": a.application_id,
            "application_no": app.application_no if app else None,
            "attribute": a.drift_event.attribute,
            "severity": a.severity,
            "title": a.title,
            "message": a.message,
            "status": a.status,
            "intended_recipient_id": a.intended_recipient_id,
            "resolved_recipient_id": a.resolved_recipient_id,
            "resolution_reason": a.resolution_reason,
            "delivery_status": a.delivery_status,
            "snooze_until": a.snooze_until.isoformat() if a.snooze_until else None,
            "acknowledged_by": a.acknowledged_by,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        })
    return {"alerts": rows}


class SimulateIn(BaseModel):
    application_id: str
    to_status: str = Field(default="CANCELLED")


@router.post("/demo/simulate-drift")
def simulate_drift(body: SimulateIn, db: Session = Depends(get_db), user: User = Depends(_any_actor)):
    """Demo beat (§8.8): flip a watched source VALUE in the mock portal."""
    if not settings.DEMO_MODE:
        raise HTTPException(status_code=404, detail="Demo endpoint is disabled")
    import httpx

    a = db.get(Application, body.application_id)
    if not a:
        raise HTTPException(status_code=404, detail="Application not found")
    profile = a.bidder_user.bidder_profile
    gstin = getattr(profile, "gstin_plain", None)
    if not gstin:
        raise HTTPException(status_code=400, detail="Bidder has no GSTIN on file")
    try:
        resp = httpx.post(
            f"{settings.MOCK_PORTAL_URL.rstrip('/')}/api/admin/state",
            params={"source": "gst"},
            json={"identifier": gstin, "status": "VALID", "inner_status": body.to_status,
                  "message": f"Demo drift applied to {a.application_no} (demo drift beat)"},
            timeout=10,
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=502,
                                detail=f"Mock portal state flip failed: HTTP {resp.status_code} {resp.text[:120]}")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Mock portal state flip failed: {type(exc).__name__}")
    append_audit(db, "watch_demo_drift_simulated", "Application", a.id,
                 actor_id=user.id, actor_role=user.role,
                 payload={"gstin": gstin, "to_status": body.to_status, "simulated": True})
    res = watch_svc.run_tick(db)
    append_audit(db, "watch_run_now", "SentinelWatch", "tick",
                 actor_id=user.id, actor_role=user.role, payload=res)
    db.commit()
    return {"ok": True, "simulated": True, "tick": res,
            "note": f"Mock GST for {a.application_no} set to {body.to_status}; one watch pass ran."}


@router.post("/demo/restore-drift")
def restore_drift(body: SimulateIn, db: Session = Depends(get_db), user: User = Depends(_any_actor)):
    """Restore the mock GST value to its seeded state (simulated)."""
    if not settings.DEMO_MODE:
        raise HTTPException(status_code=404, detail="Demo endpoint is disabled")
    import httpx

    a = db.get(Application, body.application_id)
    if not a:
        raise HTTPException(status_code=404, detail="Application not found")
    gstin = getattr(a.bidder_user.bidder_profile, "gstin_plain", None)
    try:
        httpx.delete(f"{settings.MOCK_PORTAL_URL.rstrip('/')}/api/admin/state/gst/{gstin}", timeout=10)
    except Exception:
        pass  # nothing to restore
    res = watch_svc.run_tick(db)
    append_audit(db, "watch_demo_drift_restored", "Application", a.id,
                 actor_id=user.id, actor_role=user.role, payload={"gstin": gstin, "tick": res})
    db.commit()
    return {"ok": True, "simulated": True, "tick": res, "note": "Mock GST restored to seeded value."}


class SnoozeIn(BaseModel):
    reason: str = Field(min_length=5, max_length=300)
    hours: int = Field(default=12, ge=1, le=168)


class EscalateIn(BaseModel):
    note: str | None = Field(default=None, max_length=300)


@router.post("/alerts/{alert_id}/acknowledge")
def ack_alert(alert_id: str, db: Session = Depends(get_db), user: User = Depends(_any_actor)):
    a = db.get(DriftAlert, alert_id)
    if not a:
        raise HTTPException(status_code=404, detail="Alert not found")
    watch_svc.acknowledge_alert(db, a, user)
    db.commit()
    return {"ok": True, "status": a.status}


@router.post("/alerts/{alert_id}/snooze")
def snooze_alert(alert_id: str, body: SnoozeIn, db: Session = Depends(get_db), user: User = Depends(_any_actor)):
    a = db.get(DriftAlert, alert_id)
    if not a:
        raise HTTPException(status_code=404, detail="Alert not found")
    try:
        watch_svc.snooze_alert(db, a, user, body.reason, body.hours)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    db.commit()
    return {"ok": True, "status": a.status, "snooze_until": a.snooze_until.isoformat()}


@router.post("/alerts/{alert_id}/escalate")
def escalate_alert(alert_id: str, body: EscalateIn, db: Session = Depends(get_db), user: User = Depends(_any_actor)):
    a = db.get(DriftAlert, alert_id)
    if not a:
        raise HTTPException(status_code=404, detail="Alert not found")
    watch_svc.escalate_alert(db, a, user, body.note)
    db.commit()
    return {"ok": True, "status": a.status}


@router.get("/source-health")
def source_health(db: Session = Depends(get_db), user: User = Depends(_any_reader)):
    polls = (
        db.query(SourcePollLog)
        .order_by(SourcePollLog.polled_at.desc())
        .limit(200)
        .all()
    )
    # latest poll per source
    latest: dict[str, SourcePollLog] = {}
    for p in polls:
        latest.setdefault(p.source, p)
    health = db.query(SourceHealth).order_by(SourceHealth.checked_at.desc()).all()
    by_source = {}
    for h in health:
        by_source.setdefault(h.source, h)
    return {
        "sources": {
            s: {
                "last_poll": (p.status if p else None),
                "last_poll_at": (p.polled_at.isoformat() if p and p.polled_at else None),
                "source_status": (by_source[s].status if s in by_source else None),
            }
            for s, p in latest.items()
        },
        "poll_log": [
            {"source": p.source, "status": p.status, "polled_at": p.polled_at.isoformat() if p.polled_at else None}
            for p in polls[:50]
        ],
    }


@router.post("/run-now")
def run_now(db: Session = Depends(get_db), user: User = Depends(_any_actor)):
    """Operator/demo trigger for one scheduler pass (event-driven cadence)."""
    res = watch_svc.run_tick(db)
    append_audit(db, "watch_run_now", "SentinelWatch", "tick",
                 actor_id=user.id, actor_role=user.role, payload=res)
    db.commit()
    return res


@router.post("/applications/{application_id}/gate-check")
def gate_check(application_id: str, db: Session = Depends(get_db), user: User = Depends(_any_actor)):
    a = get_scoped_application(db, user, application_id)
    return watch_svc.run_gate_check(db, a, user)


@router.get("/applications/{application_id}/comparison")
def then_vs_now(application_id: str, db: Session = Depends(get_db), user: User = Depends(_any_reader)):
    """'Compliance Then vs Now' — baseline score vs current, changed attributes."""
    a = get_scoped_application(db, user, application_id)
    watch = (
        db.query(ComplianceWatch)
        .filter(ComplianceWatch.application_id == application_id, ComplianceWatch.active.is_(True))
        .first()
    )
    from ..models import VerificationReport

    original = None
    if watch and watch.created_from_report_id:
        original = db.get(VerificationReport, watch.created_from_report_id)
    latest = (
        db.query(VerificationReport)
        .filter(VerificationReport.application_id == application_id)
        .order_by(VerificationReport.run_no.desc())
        .first()
    )
    drifts = (
        db.query(DriftEvent)
        .filter(DriftEvent.application_id == application_id)
        .order_by(DriftEvent.detected_at.desc())
        .all()
    )
    return {
        "application_id": a.id,
        "application_no": a.application_no,
        "bidder_name": a.bidder_user.full_name if a.bidder_user else None,
        "score_then": original.compliance_score if original else None,
        "score_now": latest.compliance_score if latest else None,
        "risk_now": latest.risk_level if latest else None,
        "active_watch": watch is not None,
        "changed_attributes": [
            {"attribute": d.attribute, "from": (d.old_value or {}).get("inner_status") or (d.old_value or {}).get("source_status"),
             "to": (d.new_value or {}).get("inner_status") or (d.new_value or {}).get("source_status"),
             "severity": d.severity, "detected_at": d.detected_at.isoformat() if d.detected_at else None}
            for d in drifts
        ],
    }


@router.get("/drift/{drift_id}/report.pdf")
def drift_report_pdf(drift_id: str, db: Session = Depends(get_db), user: User = Depends(_any_reader)):
    """One-click COMPLIANCE DRIFT REPORT (spec §15)."""
    d = db.get(DriftEvent, drift_id)
    if not d:
        raise HTTPException(status_code=404, detail="Drift event not found")
    app = db.get(Application, d.application_id)
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")
    pdf_bytes = drift_report_to_pdf(d, app)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="compliance-drift-{d.id[:8]}.pdf"'},
    )


def drift_report_to_pdf(d: DriftEvent, app: Application) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        BaseDocTemplate, Frame, HRFlowable, PageTemplate, Paragraph, Spacer, Table, TableStyle,
    )
    from io import BytesIO

    from ..reports import _register_fonts, _fonts

    _register_fonts()
    fnt, fnt_b = _fonts("en")
    NAVY = colors.HexColor("#0b2545")
    SLATE = colors.HexColor("#475569")
    RED = colors.HexColor("#b91c1c")
    LINE = colors.HexColor("#cbd5e1")

    S = {
        "title": ParagraphStyle("t", fontName=fnt_b, fontSize=16, leading=20, textColor=NAVY),
        "meta": ParagraphStyle("m", fontName=fnt, fontSize=8, leading=11, textColor=SLATE),
        "h2": ParagraphStyle("h2", fontName=fnt_b, fontSize=11, leading=14, textColor=NAVY, spaceBefore=10, spaceAfter=4),
        "body": ParagraphStyle("b", fontName=fnt, fontSize=9, leading=13),
        "sev": ParagraphStyle("sev", fontName=fnt_b, fontSize=12, leading=15, textColor=RED),
        "small": ParagraphStyle("s", fontName=fnt, fontSize=8, leading=11, textColor=SLATE),
    }
    TD = ParagraphStyle("td", fontName=fnt, fontSize=8.5, leading=12)
    T = ParagraphStyle("th", fontName=fnt_b, fontSize=8.5, leading=10, textColor=colors.white)

    def esc(x):
        return str(x if x is not None else "—").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    buf = BytesIO()
    doc = BaseDocTemplate(buf, pagesize=A4,
                          leftMargin=18 * mm, rightMargin=18 * mm, topMargin=18 * mm, bottomMargin=20 * mm,
                          title="Compliance Drift Report — GeM-Sentinel",
                          author="GeM-Sentinel")

    def footer(canvas, _doc):
        canvas.saveState()
        canvas.setFont(fnt, 7.5)
        canvas.setFillColor(SLATE)
        canvas.drawString(18 * mm, 10 * mm,
                          "GeM-Sentinel · Compliance Drift Report · Decision-support")
        canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {_doc.page} · generated {utcnow().isoformat()}")
        canvas.restoreState()

    doc.addPageTemplates([PageTemplate(id="p", frames=[Frame(doc.leftMargin, doc.bottomMargin,
                                                             doc.width, doc.height, id="f")], onPage=footer)])

    old = d.old_value or {}
    new = d.new_value or {}
    flow = [
        Paragraph("SENTINEL WATCH — COMPLIANCE DRIFT REPORT", S["title"]),
        Spacer(1, 2 * mm),
        Paragraph(f"Application {esc(app.application_no)} · {esc(app.bidder_user.full_name if app.bidder_user else '—')} · "
                  f"Tender {esc(app.tender.tender_no if app.tender else '—')}", S["meta"]),
        Paragraph(f"Report ref: {d.id} · detected {d.detected_at.isoformat() if d.detected_at else '—'} · evidence strength {d.evidence_strength} (authoritative source)", S["meta"]),
        Spacer(1, 3 * mm),
        Paragraph(f"{d.severity} — {d.direction} drift on {esc(d.attribute)}", S["sev"]),
        Spacer(1, 2 * mm),
        Table([
            [Paragraph("Attribute", T), Paragraph("Then (baseline)", T), Paragraph("Now", T)],
            [Paragraph(esc(d.attribute), TD),
             Paragraph(esc(old.get("inner_status") or old.get("source_status")), TD),
             Paragraph(esc(new.get("inner_status") or new.get("source_status")), TD)],
            [Paragraph("Compliance score", T), Paragraph(str(d.score_before), TD), Paragraph(str(d.score_after), TD)],
        ], colWidths=[45 * mm, 65 * mm, 65 * mm], style=TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("GRID", (0, 0), (-1, -1), 0.5, LINE),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ])),
        Spacer(1, 3 * mm),
        Paragraph("Cause", S["h2"]),
        Paragraph(esc(d.cause_summary or "—"), S["body"]),
        Spacer(1, 2 * mm),
        Paragraph("Source responses (stored verbatim)", S["h2"]),
        Paragraph(f"OLD: {esc(str(d.old_response)[:600])}", S["small"]),
        Paragraph(f"NEW: {esc(str(d.new_response)[:600])}", S["small"]),
        Spacer(1, 2 * mm),
        Paragraph("Revision linkage", S["h2"]),
        Paragraph(f"This drift did NOT modify the historical report. New deterministic revision: {esc(d.report_revision_id or '—')} "
                  f"(original: {esc(d.original_report_id or '—')}). Full history is preserved in the hash-chained Sentinel Ledger.", S["body"]),
        Spacer(1, 3 * mm),
        HRFlowable(width="100%", thickness=0.5, color=LINE),
        Paragraph("Decision-support report prepared by GeM-Sentinel. Final authority remains with the "
                  "authorized procurement officials.", S["small"]),
    ]
    doc.build(flow)
    return buf.getvalue()
