"""SENTINEL WATCH — continuous compliance monitoring service.

Design (spec §8):
- A ComplianceWatch is registered on completion of any verification
  (baseline snapshot = attribute -> value + hash + source + observed_at).
- A scheduler tick re-polls the watched sources, normalizes, and diffs
  against the baseline. Drift produces a DriftEvent (old + new source
  responses stored verbatim) and a DriftAlert routed to the CURRENT owner
  (resolved at notification time, with fallback + recorded reason).
- Historical VerificationReports are NEVER mutated — drift creates a NEW
  report revision linked to the original.
- CRITICAL drift is flap-suppressed (N consistent observations / immediate
  confirmation re-poll) and deduplicated; acknowledgement and snoozes are
  audited and snoozes expire.
- Mandatory gate checks (run_gate_check) re-verify as a blocking
  prerequisite before commitment actions (award/decision).
"""
import hashlib
import json
import logging
from datetime import timedelta

from sqlalchemy.orm import Session

from ..audit import append_audit
from ..core.config import settings
from ..core.db import utcnow
from ..models import (
    Application,
    ComplianceWatch,
    DriftAlert,
    DriftEvent,
    SourcePollLog,
    User,
    VerificationReport,
)
from ..notifications import notify, notify_admins
from ..rules_engine import IDENTIFIER_FOR_SOURCE, build_evidence_context, evaluate_tender
from .portal import fetch_source

logger = logging.getLogger("gem.watch")

# Attribute classes with cadence (spec §8.2). The demo tick is short; in
# production cadence is per-attribute-class with jitter and backoff.
# (Sources are limited to those the mock portal actually serves.)
WATCHED_ATTRIBUTES = [
    # (attribute, source, cadence class)
    ("BLACKLIST", "BLACKLIST", "HIGH"),
    ("GST", "GST", "HIGH"),
    ("UDYAM", "UDYAM", "MEDIUM"),
    ("PAN", "PAN", "MEDIUM"),
    ("EPFO", "EPFO", "MEDIUM"),
    ("MCA21", "MCA21", "LOW"),
]

CADENCE_POLICY = {
    "HIGH": "daily (immediate in demo tick)",
    "MEDIUM": "weekly (immediate in demo tick)",
    "LOW": "monthly (immediate in demo tick)",
}

DEGRADED_STATUSES = {"CANCELLED", "SUSPENDED", "REVOKED", "INACTIVE", "STRIKE_OFF", "STRUCK_OFF", "DEFAULTED", "EXPIRED", "NOT_FOUND"}
IMPROVED_STATUSES = {"ACTIVE", "VALID", "OK", "CURRENT"}


def _canon(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _h(value) -> str:
    return hashlib.sha256(_canon(value).encode()).hexdigest()[:16]


def _identifier_for(profile, source: str):
    if profile is None:
        return None
    key = IDENTIFIER_FOR_SOURCE.get(source, "pan")
    return getattr(profile, {"pan": "pan_plain", "gstin": "gstin_plain", "udyam": "udyam_plain"}.get(key, "pan_plain"), None)


def attribute_value(source_outcome: dict) -> dict:
    """Normalize a source outcome to a comparable value vector."""
    status = str(source_outcome.get("status") or "").upper()
    data = source_outcome.get("data") or {}
    return {
        "source_status": status,
        "inner_status": str(data.get("status") or "").upper() or None,
        "filing_status": str(data.get("filing_status") or "").upper() or None,
    }


def _direction(attr: str, old: dict, new: dict) -> str:
    old_key = (old.get("inner_status") or old.get("source_status") or "").upper()
    new_key = (new.get("inner_status") or new.get("source_status") or "").upper()
    if attr == "BLACKLIST":
        if new_key == "BLACKLIST" and old_key != "BLACKLIST":
            return "DEGRADED"
        if old_key == "BLACKLIST" and new_key != "BLACKLIST":
            return "IMPROVED"
        return "NEUTRAL"
    if new_key in DEGRADED_STATUSES and old_key not in DEGRADED_STATUSES:
        return "DEGRADED"
    if new_key in IMPROVED_STATUSES and old_key in DEGRADED_STATUSES:
        return "IMPROVED"
    if new.get("filing_status") == "DEFAULTED" and old.get("filing_status") != "DEFAULTED":
        return "DEGRADED"
    return "NEUTRAL"


def _severity(attr: str, direction: str, new: dict) -> str:
    if direction == "NEUTRAL":
        return "LOW"
    if direction == "IMPROVED":
        return "LOW"
    new_key = (new.get("inner_status") or new.get("source_status") or "").upper()
    if attr == "BLACKLIST" and new_key == "BLACKLIST":
        return "CRITICAL"
    if new_key in ("UNAVAILABLE", "NOT_FETCHED"):
        return "MEDIUM"  # staleness is itself an alert (S3 -> S0)
    if attr == "GST" and new_key in ("CANCELLED", "SUSPENDED", "REVOKED", "EXPIRED", "NOT_FOUND"):
        return "CRITICAL"
    if new_key in DEGRADED_STATUSES:
        return "HIGH"
    if new.get("filing_status") == "DEFAULTED":
        return "HIGH"
    return "MEDIUM"


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_watch(db: Session, application: Application, report: VerificationReport) -> ComplianceWatch:
    """Register (or refresh) the ComplianceWatch for a verified application."""
    watch = (
        db.query(ComplianceWatch)
        .filter(ComplianceWatch.application_id == application.id, ComplianceWatch.active.is_(True))
        .first()
    )
    baseline: dict = {}
    for attr, source, cadence in WATCHED_ATTRIBUTES:
        outcome = (report.source_results or {}).get(source, {})
        val = attribute_value(outcome)
        baseline[attr] = {
            "value": val,
            "hash": _h(val),
            "source": source,
            "observed_at": utcnow().isoformat(),
        }
    if watch is None:
        watch = ComplianceWatch(
            application_id=application.id,
            subject_user_id=application.bidder_user_id,
            tender_id=application.tender_id,
            watched_attributes=[a for a, _, _ in WATCHED_ATTRIBUTES],
            baseline=baseline,
            stage="EVALUATION",
            cadence_policy={c: CADENCE_POLICY[c] for c in ("HIGH", "MEDIUM", "LOW")},
            active=True,
            created_from_report_id=report.id,
        )
        db.add(watch)
    else:
        watch.baseline = baseline
        watch.created_from_report_id = report.id
        watch.active = True
    db.flush()
    append_audit(
        db, "watch_registered", "ComplianceWatch", watch.id,
        actor_id="system", actor_role="SYSTEM",
        payload={"application_id": application.id, "report_id": report.id,
                 "attributes": list(baseline.keys())},
    )
    return watch


def deactivate_watch(db: Session, watch: ComplianceWatch, reason: str, actor_id: str | None = None) -> None:
    """Deactivation is audited; watches are never silently dropped."""
    watch.active = False
    watch.deactivated_at = utcnow()
    watch.deactivation_reason = reason
    db.flush()
    append_audit(
        db, "watch_deactivated", "ComplianceWatch", watch.id,
        actor_id=actor_id or "system", actor_role="SYSTEM",
        payload={"application_id": watch.application_id, "reason": reason},
    )


# ---------------------------------------------------------------------------
# Ownership resolution (spec §8.5) — at notification time, not creation time
# ---------------------------------------------------------------------------

def resolve_owner(db: Session, application: Application) -> tuple[User, str, User | None]:
    """Return (resolved_owner, resolution_reason, intended_owner)."""
    intended = application.assigned_l1
    if intended is not None and intended.status == "ACTIVE":
        return intended, "assigned_l1", intended
    # Fallback: System Administrator
    admin = (
        db.query(User).filter(User.role == "ADMIN", User.status == "ACTIVE").first()
    )
    if admin is not None:
        reason = "l1_unassigned_or_inactive -> admin fallback"
        if intended is not None:
            reason = f"l1_inactive({intended.status}) -> admin fallback"
        return admin, reason, intended
    raise RuntimeError("No active owner or fallback admin found for drift routing")


# ---------------------------------------------------------------------------
# Polling + drift detection
# ---------------------------------------------------------------------------

def _poll_attribute(db: Session, application: Application, source: str) -> dict:
    profile = application.bidder_user.bidder_profile
    identifier = _identifier_for(profile, source)
    extra = {}
    if source == "BLACKLIST":
        extra = {"gstin": getattr(profile, "gstin_plain", None),
                 "company_name": getattr(profile, "company_name", None)}
    # use_cache=False: a watch must see live source state — serving a cached
    # snapshot would let real drift hide behind the verification cache TTL.
    res = fetch_source(db, source, identifier=identifier, extra=extra or None, use_cache=False)
    outcome = {
        "status": res.status,
        "message": res.message,
        "data": res.data,
        "retrieved_at": res.retrieved_at.isoformat() if res.retrieved_at else None,
    }
    return outcome


def _recompute_evaluation(db: Session, application: Application) -> dict:
    """Fresh deterministic re-verification with live sources (no AI)."""
    user = application.bidder_user
    tender = application.tender
    ctx = build_evidence_context(db, user, tender, fetch_sources=True)
    return evaluate_tender(db, user, tender, ctx=ctx)


def _find_cause(rule_results_now: list[dict], rule_results_before: list[dict]) -> tuple[str | None, str]:
    """First rule that failed now but passed before — the drift's cause."""
    before = {r["rule_key"]: r["result"] for r in (rule_results_before or [])}
    for r in rule_results_now:
        if r["result"] == "FAIL" and before.get(r["rule_key"]) in (None, "PASS"):
            return r["rule_id"], f"rule {r['rule_key']} ({r['label']}): {r['message']}"
    return None, "multiple rule outcomes changed"


def _create_report_revision(
    db: Session, application: Application, evaluation: dict,
    owner: User, original_report: VerificationReport | None, cause: str,
) -> VerificationReport:
    """Drift NEVER mutates history — it creates a new report revision
    linked to the original (spec §8.3)."""
    run_no = (
        db.query(VerificationReport)
        .filter(VerificationReport.application_id == application.id)
        .count()
        + 1
    )
    rev = VerificationReport(
        application_id=application.id,
        run_no=run_no,
        triggered_by=owner.id,
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
        strength_distribution=evaluation["strength_distribution"],
        coverage_pct=evaluation["coverage_pct"],
        determinism_hash=evaluation["determinism_hash"],
        ai_explanation=(
            "Sentinel Watch deterministic re-verification "
            f"(triggered by compliance drift: {cause}). AI explanation not run."
        ),
        ai_explanation_language="en",
        rules_version_at_run=application.tender.rules_version,
        report_version=f"watch-revision-{run_no}",
        recommendation=f"Auto re-verification after drift: {cause}",
        generated_at=utcnow(),
    )
    rev.created_from_report_id = original_report.id if original_report else None
    db.add(rev)
    db.flush()  # populate rev.id so the DriftEvent can reference it
    return rev


def poll_watch(db: Session, watch: ComplianceWatch) -> list[DriftEvent]:
    """Re-poll all watched attributes; detect + classify drift."""
    application = watch.application
    drifts: list[DriftEvent] = []
    watch.last_polled_at = utcnow()
    baseline = watch.baseline or {}

    for attr, source, cadence in WATCHED_ATTRIBUTES:
        if attr not in (watch.watched_attributes or []):
            continue
        base = baseline.get(attr)
        if base is None:
            continue
        try:
            outcome = _poll_attribute(db, application, source)
        except Exception as exc:  # source error itself is a signal
            outcome = {"status": "UNAVAILABLE", "message": f"poll error: {type(exc).__name__}", "data": {}}
        new_val = attribute_value(outcome)
        poll_ok = str(outcome.get("status")).upper() in ("VALID", "ACTIVE", "NOT_FOUND", "BLACKLIST", "OK", "CURRENT")
        db.add(SourcePollLog(
            watch_id=watch.id, source=source,
            status="UP" if poll_ok else "DOWN",
            message=str(outcome.get("message") or "")[:290],
        ))

        if _h(new_val) == base.get("hash"):
            continue  # no drift

        # First-ever observation (source had no data at registration):
        # establish the baseline silently instead of firing a false drift.
        base_val = base.get("value") or {}
        if not base_val.get("source_status") and not base_val.get("inner_status"):
            baseline[attr] = {"value": new_val, "hash": _h(new_val),
                              "source": source, "observed_at": utcnow().isoformat()}
            continue

        direction = _direction(attr, base.get("value") or {}, new_val)
        severity = _severity(attr, direction, new_val)

        # Dedup: an unacknowledged alert for the same attribute+new value
        # already exists -> suppress (alert hygiene, spec §8.6).
        dup = (
            db.query(DriftEvent)
            .filter(DriftEvent.watch_id == watch.id, DriftEvent.attribute == attr)
            .order_by(DriftEvent.detected_at.desc())
            .first()
        )
        if dup is not None and _h(new_val) == _h(dup.new_value or {}):
            open_alert = (
                db.query(DriftAlert)
                .filter(DriftAlert.drift_event_id == dup.id,
                        DriftAlert.status.in_(["OPEN", "SNOOZED"]))
                .first()
            )
            if open_alert is not None:
                logger.info("watch dedup: %s %s suppressed (unacknowledged)", application.application_no, attr)
                continue

        # Flap suppression: CRITICAL requires a confirmation re-poll.
        if severity == "CRITICAL":
            conf = _poll_attribute(db, application, source)
            conf_val = attribute_value(conf)
            if _h(conf_val) != _h(new_val):
                logger.info("watch flap: %s %s not confirmed on re-poll", application.application_no, attr)
                continue
            outcome, new_val = conf, conf_val

        # Score delta: fresh deterministic re-verification (no AI).
        evaluation = _recompute_evaluation(db, application)
        original_report = (
            db.query(VerificationReport)
            .filter(VerificationReport.id == watch.created_from_report_id)
            .first()
        ) or (
            db.query(VerificationReport)
            .filter(VerificationReport.application_id == application.id)
            .order_by(VerificationReport.run_no.desc())
            .first()
        )
        score_before = original_report.compliance_score if original_report else None
        cause_rule, cause_summary = _find_cause(
            evaluation["rule_results"], original_report.rule_results if original_report else None
        )

        owner, reason, intended = resolve_owner(db, application)
        revision = _create_report_revision(db, application, evaluation, owner, original_report, cause_summary)

        drift = DriftEvent(
            watch_id=watch.id,
            application_id=application.id,
            attribute=attr,
            old_value=base.get("value"),
            new_value=new_val,
            direction=direction,
            severity=severity,
            detected_at=utcnow(),
            source=source,
            evidence_strength="S3",
            score_before=score_before,
            score_after=evaluation["compliance_score"],
            cause_rule_id=cause_rule,
            cause_summary=cause_summary[:400],
            old_response=base,  # full baseline entry (value + hash + observed_at)
            new_response=outcome,
            report_revision_id=revision.id,
            original_report_id=original_report.id if original_report else None,
        )
        db.add(drift)
        db.flush()

        _fire_alert(db, watch, drift, owner, reason, intended)
        drifts.append(drift)

    # refresh baseline for drifted attributes (new current state)
    for d in drifts:
        baseline[d.attribute] = {
            "value": d.new_value, "hash": _h(d.new_value),
            "source": d.source, "observed_at": d.detected_at.isoformat(),
        }
    watch.baseline = baseline
    db.flush()
    return drifts


def _fire_alert(
    db: Session, watch: ComplianceWatch, drift: DriftEvent,
    owner: User, reason: str, intended: User | None,
) -> DriftAlert:
    application = watch.application
    delta = ""
    if drift.score_before is not None and drift.score_after is not None:
        delta = (
            f" Compliance at verification: {drift.score_before}. "
            f"Compliance today: {drift.score_after}. Cause: {drift.cause_summary}."
        )
    title = (
        f"[{drift.severity}] Compliance drift: {drift.attribute} "
        f"{'DEGRADED' if drift.direction == 'DEGRADED' else drift.direction} "
        f"for {application.application_no}"
    )
    message = (
        f"{application.bidder_user.full_name} ({application.application_no}): "
        f"watched attribute {drift.attribute} changed from "
        f"{(drift.old_value or {}).get('inner_status') or (drift.old_value or {}).get('source_status')} to "
        f"{(drift.new_value or {}).get('inner_status') or (drift.new_value or {}).get('source_status')}. "
        f"{delta}"
    )
    alert = DriftAlert(
        drift_event_id=drift.id,
        watch_id=watch.id,
        application_id=application.id,
        severity=drift.severity,
        title=title[:300],
        message=message,
        intended_recipient_id=intended.id if intended else owner.id,
        resolved_recipient_id=owner.id,
        resolution_reason=reason,
        delivery_status="DELIVERED",
        status="OPEN",
    )
    db.add(alert)
    db.flush()
    notify(
        db, owner.id, "compliance_drift",
        message=message, entity_type="DriftAlert", entity_id=alert.id,
    )
    if drift.severity == "CRITICAL":
        notify_admins(
            db, "compliance_drift_critical",
            message=f"CRITICAL compliance drift on {application.application_no}: "
                    f"{drift.attribute}. Award/commitment actions are blocked pending review.",
            entity_type="DriftAlert", entity_id=alert.id,
        )
    append_audit(
        db, "drift_detected", "DriftEvent", drift.id,
        actor_id="system", actor_role="SYSTEM",
        payload={
            "application_id": application.id, "attribute": drift.attribute,
            "severity": drift.severity, "direction": drift.direction,
            "score_before": drift.score_before, "score_after": drift.score_after,
            "alert_id": alert.id, "resolved_recipient": owner.id,
            "resolution_reason": reason,
        },
    )
    return alert


# ---------------------------------------------------------------------------
# Alert hygiene: acknowledge / snooze / escalate (all audited)
# ---------------------------------------------------------------------------

def acknowledge_alert(db: Session, alert: DriftAlert, actor: User) -> DriftAlert:
    if alert.severity == "CRITICAL":
        # CRITICAL cannot be silently dismissed — acknowledgement must be
        # explicit (this is it) and is always audited.
        pass
    alert.status = "ACKNOWLEDGED"
    alert.acknowledged_by = actor.id
    alert.acknowledged_at = utcnow()
    db.flush()
    append_audit(
        db, "drift_alert_acknowledged", "DriftAlert", alert.id,
        actor_id=actor.id, actor_role=actor.role,
        payload={"application_id": alert.application_id, "severity": alert.severity},
    )
    return alert


def snooze_alert(db: Session, alert: DriftAlert, actor: User, reason: str, hours: int = 12) -> DriftAlert:
    if alert.severity == "CRITICAL":
        raise ValueError("A CRITICAL drift can never be snoozed — acknowledge or escalate it.")
    alert.status = "SNOOZED"
    alert.snooze_until = utcnow() + timedelta(hours=hours)
    alert.snooze_reason = reason[:300]
    db.flush()
    append_audit(
        db, "drift_alert_snoozed", "DriftAlert", alert.id,
        actor_id=actor.id, actor_role=actor.role,
        payload={"application_id": alert.application_id, "hours": hours, "reason": reason[:300]},
    )
    return alert


def escalate_alert(db: Session, alert: DriftAlert, actor: User, note: str | None = None) -> DriftAlert:
    admin = db.query(User).filter(User.role == "ADMIN", User.status == "ACTIVE").first()
    if admin is not None and admin.id != alert.resolved_recipient_id:
        alert.status = "ESCALATED"
        alert.escalation_note = (note or "")[:300] or f"escalated by {actor.full_name}"
        notify(db, admin.id, "compliance_drift_escalated",
               message=f"{alert.title} — escalated by {actor.full_name}.",
               entity_type="DriftAlert", entity_id=alert.id)
    else:
        alert.status = "ESCALATED"
        alert.escalation_note = (note or "")[:300] or "no further escalation target"
    db.flush()
    append_audit(
        db, "drift_alert_escalated", "DriftAlert", alert.id,
        actor_id=actor.id, actor_role=actor.role,
        payload={"application_id": alert.application_id, "note": (note or "")[:300]},
    )
    return alert


def expire_snoozes(db: Session) -> int:
    """Snoozes are audited and expire (spec §8.6)."""
    now = utcnow()
    stale = (
        db.query(DriftAlert)
        .filter(DriftAlert.status == "SNOOZED", DriftAlert.snooze_until != None, DriftAlert.snooze_until <= now)
        .all()
    )
    for a in stale:
        a.status = "OPEN"
        a.snooze_reason = (a.snooze_reason or "") + " [snooze expired]"
        append_audit(db, "drift_alert_snooze_expired", "DriftAlert", a.id,
                     actor_id="system", actor_role="SYSTEM",
                     payload={"application_id": a.application_id})
    if stale:
        db.flush()
    return len(stale)


# ---------------------------------------------------------------------------
# Scheduler tick
# ---------------------------------------------------------------------------

def run_tick(db: Session) -> dict:
    """One scheduler pass over all active watches."""
    expired = expire_snoozes(db)
    watches = (
        db.query(ComplianceWatch).filter(ComplianceWatch.active.is_(True)).all()
    )
    total_drifts = 0
    for w in watches:
        try:
            drifts = poll_watch(db, w)
            total_drifts += len(drifts)
        except Exception:
            db.rollback()
            logger.exception("watch poll failed for %s", w.id)
    db.commit()
    return {"watches_polled": len(watches), "drifts_detected": total_drifts, "snoozes_expired": expired}


# ---------------------------------------------------------------------------
# Mandatory gate check — blocking prerequisite before commitment (spec §8.2)
# ---------------------------------------------------------------------------

def run_gate_check(db: Session, application: Application, actor: User) -> dict:
    """Fresh blocking re-verify before award/decision. Returns a block
    decision with the exact unacknowledged CRITICAL drifts (if any)."""
    watch = (
        db.query(ComplianceWatch)
        .filter(ComplianceWatch.application_id == application.id, ComplianceWatch.active.is_(True))
        .first()
    )
    blocked_alerts = []
    if watch is not None:
        try:
            poll_watch(db, watch)
        except Exception:
            db.rollback()
            logger.exception("gate-check poll failed for %s", application.id)
        blocked_alerts = (
            db.query(DriftAlert)
            .filter(DriftAlert.application_id == application.id,
                    DriftAlert.severity == "CRITICAL",
                    DriftAlert.status.in_(["OPEN", "SNOOZED"]))
            .all()
        )
    latest = (
        db.query(VerificationReport)
        .filter(VerificationReport.application_id == application.id)
        .order_by(VerificationReport.run_no.desc())
        .first()
    )
    result = {
        "application_id": application.id,
        "blocked": bool(blocked_alerts),
        "blocking_alerts": [
            {"alert_id": a.id, "attribute": a.drift_event.attribute, "message": a.message,
             "status": a.status, "detected_at": a.drift_event.detected_at.isoformat()}
            for a in blocked_alerts
        ],
        "score_now": latest.compliance_score if latest else None,
        "checked_at": utcnow().isoformat(),
    }
    append_audit(
        db, "watch_gate_check", "Application", application.id,
        actor_id=actor.id, actor_role=actor.role,
        payload={"blocked": result["blocked"], "score_now": result["score_now"],
                 "blocking": [a.id for a in blocked_alerts]},
    )
    db.commit()
    return result
