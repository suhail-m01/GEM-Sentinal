"""End-to-end procurement pipeline (11 stages) — core engine.

Replaces the legacy two-step (L1/L2) approval model with the full
procurement process:

    Bid Submission -> Bid Closing -> Procurement Screening (AI compliance)
    -> Technical Evaluation -> Financial Evaluation -> Competent Authority
    Approval/Award -> Contract -> Execution -> Delivery & Inspection
    -> Payment & Settlement -> Audit & Closure

Each stage has ONE owning workflow role (per-tender assignment), a fixed
set of allowed outcomes, and generates a downloadable certificate of
completion. Only the owner role (or ADMIN) may act. Role-relevant document
visibility and the officer-readable summary live in ``pipeline_docs.py``.
"""
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import JSON, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .core.db import utcnow
from .models.workflow import ApplicationStage
from .models.user import new_id

# ---------------------------------------------------------------------------
# stage definitions
# ---------------------------------------------------------------------------
STAGES: list[tuple[str, str, str, str]] = [
    ("SUBMISSION", "Bid Submission", "BIDDER", "Bidder"),
    ("BID_CLOSURE", "Bid Closing & Opening", "BID_OPENING_OFFICER", "Bid Opening Officer"),
    ("COMPLIANCE", "Procurement Screening \u2014 AI Compliance Verification",
     "PROCUREMENT_OFFICER", "Bid Compliance Officer"),
    ("TECHNICAL", "Technical Evaluation", "TECHNICAL_EVALUATOR", "Technical Evaluation Officer"),
    ("FINANCIAL", "Financial Evaluation", "FINANCE_EVALUATOR", "Financial Evaluation Officer"),
    ("APPROVAL", "Competent Authority Approval & Award", "CFA", "Tender Approving Authority"),
    ("CONTRACT", "Contract Award & Management", "CONTRACT_MANAGER", "Contract Manager"),
    ("EXECUTION", "Contract Execution", "CONTRACT_OFFICER", "Contract Execution / Project Officer"),
    ("DELIVERY", "Delivery & Inspection", "INSPECTION_OFFICER", "Delivery & Inspection / Consignee Officer"),
    ("PAYMENT", "Payment & Settlement", "PAYMENT_OFFICER", "Payment & Settlement / Accounts Officer"),
    ("AUDIT", "Audit Review & Closure", "AUDITOR", "Audit / Compliance Officer"),
]
STAGE_KEYS = [s[0] for s in STAGES]
STAGE_BY_KEY = {s[0]: s for s in STAGES}
STAGE_INDEX = {k: i for i, k in enumerate(STAGE_KEYS)}

PENDING, COMPLETED, TERMINATED = "PENDING", "COMPLETED", "TERMINATED"

CERT_PREFIX = {
    "SUBMISSION": "SUBM", "BID_CLOSURE": "OPEN", "COMPLIANCE": "COMP",
    "TECHNICAL": "TECH", "FINANCIAL": "FINA", "APPROVAL": "APPR",
    "CONTRACT": "CONT", "EXECUTION": "EXEC", "DELIVERY": "DELI",
    "PAYMENT": "PAYM", "AUDIT": "AUDT",
}


def stage_status_label(stage_key: str, payload: Optional[dict]) -> str:
    p = payload or {}
    if stage_key == "SUBMISSION":
        return "Submitted"
    if stage_key == "BID_CLOSURE":
        return "Bids Opened \u2014 Evaluation In Progress"
    if stage_key == "COMPLIANCE":
        return {"VERIFIED": "Compliance Verified",
                "MANUAL_REVIEW_REQUIRED": "Compliance \u2014 Manual Review Required",
                "NON_COMPLIANT": "Non-Compliant \u2014 Rejected at Screening"}.get(p.get("finding"), "Compliance Review")
    if stage_key == "TECHNICAL":
        return {"TECHNICALLY_QUALIFIED": "Technically Qualified",
                "CLARIFICATION_REQUIRED": "Technical \u2014 Clarification Required",
                "TECHNICALLY_DISQUALIFIED": "Technically Disqualified"}.get(p.get("outcome"), "Technical Evaluation")
    if stage_key == "FINANCIAL":
        if p.get("outcome") == "FAIL":
            return "Financial Evaluation Failed"
        if p.get("tie"):
            return "Financially Qualified \u2014 JOINT LOWEST / TIE \u2014 tender-specific tie resolution required"
        rank = p.get("rank") or "—"
        return f"Financially Qualified — Rank L{rank}"
    if stage_key == "APPROVAL":
        return "Awarded \u2014 Letter of Award Issued" if p.get("decision") == "APPROVE" else "Not Awarded \u2014 Rejected"
    if stage_key == "CONTRACT":
        return "Contract Issued"
    if stage_key == "EXECUTION":
        return "In Execution"
    if stage_key == "DELIVERY":
        return {"ACCEPTED": "Delivery Accepted",
                "PARTIALLY_ACCEPTED": "Delivery Partially Accepted",
                "RECTIFICATION_REQUIRED": "Delivery \u2014 Rectification Required",
                "REJECTED": "Delivery Rejected"}.get(p.get("outcome"), "Delivery & Inspection")
    if stage_key == "PAYMENT":
        return "Payment Settled"
    if stage_key == "AUDIT":
        return "Closed \u2014 Audit Verified"
    return "In Progress"


def is_terminal_outcome(stage_key: str, payload: Optional[dict]) -> bool:
    p = payload or {}
    if stage_key == "COMPLIANCE":
        return p.get("finding") == "NON_COMPLIANT"
    if stage_key == "TECHNICAL":
        return p.get("outcome") == "TECHNICALLY_DISQUALIFIED"
    if stage_key == "FINANCIAL":
        return p.get("outcome") == "FAIL"
    if stage_key == "APPROVAL":
        return p.get("decision") == "REJECT"
    if stage_key == "DELIVERY":
        return p.get("outcome") == "REJECTED"
    return False


STAGE_ACTIONS: dict[str, list[str]] = {
    "BID_CLOSURE": ["OPEN_BIDS"],
    "COMPLIANCE": ["VERIFIED", "MANUAL_REVIEW_REQUIRED", "NON_COMPLIANT"],
    "TECHNICAL": ["TECHNICALLY_QUALIFIED", "CLARIFICATION_REQUIRED", "TECHNICALLY_DISQUALIFIED"],
    "FINANCIAL": ["PASS", "FAIL"],
    "APPROVAL": ["APPROVE", "REJECT"],
    "CONTRACT": ["ISSUE_CONTRACT"],
    "EXECUTION": ["RECORD_PROGRESS"],
    "DELIVERY": ["ACCEPTED", "PARTIALLY_ACCEPTED", "REJECTED", "RECTIFICATION_REQUIRED"],
    "PAYMENT": ["SETTLE_PAYMENT"],
    "AUDIT": ["CLOSE_AUDIT"],
}

# ---------------------------------------------------------------------------
# stage records
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# role -> stage access
# ---------------------------------------------------------------------------

def effective_workflow_role(db, user, tender, app=None) -> str:
    """The workflow role the officer wears for this tender (and stage).

    ADMIN / AUDITOR act globally. If ``app`` is given and the officer holds
    the role that owns its CURRENT stage on this tender, that hat is worn —
    officers move through the pipeline in different capacities. Otherwise
    the first active TenderAssignment for the tender wins; else the base role.
    """
    if user.role in ("ADMIN", "AUDITOR"):
        return user.role
    from .models.workflow import TenderAssignment
    rows = (db.query(TenderAssignment)
            .filter(TenderAssignment.tender_id == tender.id,
                    TenderAssignment.user_id == user.id,
                    TenderAssignment.active.is_(True)).all())
    if not rows:
        return user.role
    if app is not None:
        cs = current_stage(db, app)
        if cs:
            owner = STAGE_BY_KEY[cs][2]
            if owner in {r.workflow_role for r in rows}:
                return owner
    return rows[0].workflow_role


def stage_owner(stage_key: str) -> str:
    return STAGE_BY_KEY[stage_key][2]


# ---------------------------------------------------------------------------
# pipeline materialisation + state
# ---------------------------------------------------------------------------

def init_pipeline(db, app, submitted_at=None) -> None:
    """Materialise stage rows for an application; SUBMISSION completes at
    submission (idempotent)."""
    for key in STAGE_KEYS:
        row = (db.query(ApplicationStage)
               .filter(ApplicationStage.application_id == app.id,
                       ApplicationStage.stage_key == key).first())
        if row:
            continue
        row = ApplicationStage(application_id=app.id, stage_key=key)
        if key == "SUBMISSION":
            row.status = COMPLETED
            row.acted_by = app.bidder_user_id
            row.acted_at = submitted_at or app.submitted_at or utcnow()
        db.add(row)
    db.flush()


def stage_rows(db, app_id: str) -> dict[str, ApplicationStage]:
    return {r.stage_key: r for r in
            db.query(ApplicationStage).filter(ApplicationStage.application_id == app_id).all()}


def current_stage(db, app) -> Optional[str]:
    """The stage the pipeline is currently stopped at.

    Returns the first PENDING stage; a TERMINATED stage halts the pipeline
    (the pipeline stays pointed at the stage that stopped it, so later
    stages are unreachable and the UI can highlight the terminal outcome);
    a DELIVERY completed with RECTIFICATION_REQUIRED stays current so the
    officer can re-inspect after rectification.
    """
    rows = stage_rows(db, app.id)
    for key in STAGE_KEYS:
        r = rows.get(key)
        if r is None or r.status == PENDING:
            return key
        if r.status == TERMINATED:
            return key
        if key == "DELIVERY" and (r.payload or {}).get("outcome") == "RECTIFICATION_REQUIRED":
            return key
    return None  # fully completed


def pipeline_state(db, app) -> dict:
    rows = stage_rows(db, app.id)
    cur = current_stage(db, app)

    # Name the office: who is assigned to own each stage on this tender.
    from .models import User
    from .models.workflow import TenderAssignment
    assigned = {}
    for a in (db.query(TenderAssignment)
              .filter(TenderAssignment.tender_id == app.tender_id,
                      TenderAssignment.active.is_(True)).all()):
        assigned.setdefault(a.workflow_role, a)  # first active wins
    owner_people: dict[str, dict] = {}
    for role, ta in assigned.items():
        u = db.get(User, ta.user_id)
        if u:
            owner_people[role] = {"name": u.full_name, "email": u.email}
    # The Audit & Closure stage is owned by the (global) Auditor role, which is
    # not a per-tender assignment — fall back to the auditor account itself.
    if "AUDITOR" not in owner_people:
        aud = db.query(User).filter(User.role == "AUDITOR", User.status == "ACTIVE").first()
        if aud:
            owner_people["AUDITOR"] = {"name": aud.full_name, "email": aud.email}

    stages = []
    for key in STAGE_KEYS:
        _, label, owner_role, owner_title = STAGE_BY_KEY[key]
        r = rows.get(key)
        status = r.status if r else PENDING
        person = owner_people.get(owner_role, {})
        stages.append({
            "key": key,
            "label": label,
            "owner_role": owner_role,
            "owner_title": owner_title,
            "owner_name": person.get("name"),
            "owner_email": person.get("email"),
            "status": status,
            "current": (key == cur),
            "acted_by": r.acted_by if r else None,
            "acted_at": r.acted_at.isoformat() if (r and r.acted_at) else None,
            "payload": r.payload if (r and r.payload) else None,
            "status_label": stage_status_label(key, r.payload if r else None),
            "certificate_no": r.certificate_no if r else None,
            "actions": STAGE_ACTIONS.get(key, []),
        })
    return {"stages": stages, "current_stage": cur}


# ---------------------------------------------------------------------------
# stage completion
# ---------------------------------------------------------------------------

def _validate_payload(stage_key: str, payload: dict) -> None:
    action = payload.get("action")
    if stage_key in STAGE_ACTIONS and action not in STAGE_ACTIONS[stage_key]:
        raise HTTPException(status_code=400, detail=f"Invalid action '{action}' for stage {stage_key}")
    if stage_key == "COMPLIANCE":
        if payload.get("finding") not in STAGE_ACTIONS[stage_key]:
            raise HTTPException(status_code=400, detail="Invalid compliance finding")
    if stage_key == "TECHNICAL":
        if payload.get("outcome") not in STAGE_ACTIONS[stage_key]:
            raise HTTPException(status_code=400, detail="Invalid technical outcome")
    if stage_key == "FINANCIAL":
        if payload.get("outcome") not in STAGE_ACTIONS[stage_key]:
            raise HTTPException(status_code=400, detail="Invalid financial outcome")
        if payload.get("amount") in (None, "", 0):
            raise HTTPException(status_code=400, detail="evaluated amount is required")
    if stage_key == "APPROVAL" and payload.get("decision") not in STAGE_ACTIONS[stage_key]:
        raise HTTPException(status_code=400, detail="Invalid approval decision")
    if stage_key == "DELIVERY" and payload.get("outcome") not in STAGE_ACTIONS[stage_key]:
        raise HTTPException(status_code=400, detail="Invalid delivery outcome")
    if stage_key == "CONTRACT" and not (payload.get("contract_no") or "").strip():
        raise HTTPException(status_code=400, detail="contract_no is required")
    if stage_key == "PAYMENT":
        if not (payload.get("payment_ref") or "").strip():
            raise HTTPException(status_code=400, detail="payment_ref is required")
    if not (payload.get("remarks") or "").strip():
        raise HTTPException(status_code=400, detail="remarks are required (this becomes part of the audited record)")


def _commercial_price(db, app) -> "Decimal | None":
    """Authoritative commercial price for an application (see
    services.financial.final_price_for_ranking): approved RA final price →
    evaluated/quoted financial bid → legacy stage amount → legacy quote.
    """
    from .services.financial import final_price_for_ranking
    return final_price_for_ranking(db, app)


def _financially_eligible(db, app) -> bool:
    """May this application take part in commercial ranking?

    Mandatory rule: DISQUALIFIED bidders are never ranked. Eligible =
    not withdrawn/rejected, and (compliance passed or manual-review-cleared
    AND) technically qualified. Apps whose pipeline never passed technical
    (e.g. still at screening) are not ranked yet.
    """
    if app.status in ("Withdrawn", "Rejected", "Closed"):
        return False
    rows = stage_rows(db, app.id)
    comp = rows.get("COMPLIANCE")
    tech = rows.get("TECHNICAL")
    if tech is None or tech.status != COMPLETED:
        return False
    if (tech.payload or {}).get("outcome") != "TECHNICALLY_QUALIFIED":
        return False
    if comp is not None and comp.status == TERMINATED:
        return False  # terminated at compliance (NON_COMPLIANT)
    return True


def _auction_forward(db, tender_id: str) -> bool:
    """True when the tender's approved auction is a FORWARD e-auction —
    the price direction the L-ranking must follow (higher valid bid = L1)."""
    from .models import Auction, AuctionResult
    a = (db.query(Auction)
         .join(AuctionResult, AuctionResult.auction_id == Auction.id)
         .filter(Auction.tender_id == tender_id, AuctionResult.status == "APPROVED")
         .order_by(Auction.closed_at.desc().nullslast()).first())
    return a is not None and a.auction_type == "FORWARD"


def _rank_financial(db, app, amount: float) -> int:
    """L-rank among ELIGIBLE bids on the same tender —
    price source: approved auction final price where present, else the
    evaluated financial bid / legacy amount. Direction follows the approved
    auction type: REVERSE/legacy = lower price L1, FORWARD = higher price L1.
    Ties are reported, not broken (see `financial_tie(db, app)`)."""
    from .models import Application
    others = (db.query(Application)
              .filter(Application.tender_id == app.tender_id,
                      Application.id != app.id).all())
    eligible = [o for o in others if _financially_eligible(db, o)]
    priced = [(o, _commercial_price(db, o)) for o in eligible]
    priced = [(o, p) for o, p in priced if p is not None]
    forward = _auction_forward(db, app.tender_id)
    if forward:
        rank = 1 + sum(1 for o, p in priced if p > amount)
    else:
        rank = 1 + sum(1 for o, p in priced if p < amount)
    return rank


def financial_tie(db, app, amount) -> bool:
    """True when another eligible bidder holds the same commercial price."""
    from .models import Application
    others = (db.query(Application)
              .filter(Application.tender_id == app.tender_id,
                      Application.id != app.id).all())
    for o in others:
        if not _financially_eligible(db, o):
            continue
        p = _commercial_price(db, o)
        if p is not None and p == amount:
            return True
    return False


def complete_stage(db, app, stage_key: str, actor, payload: dict,
                   audit_extra: Optional[dict] = None) -> ApplicationStage:
    """Validate, transition, audit, notify and certificate-number a stage.

    Callers handle the domain side-effects (bid opening, override gate,
    notifications) via the stage router; this is the state machine core.
    """
    from .audit import append_audit
    from .models import ApplicationStatusHistory

    if stage_key not in STAGE_BY_KEY:
        raise HTTPException(status_code=404, detail=f"Unknown stage: {stage_key}")
    init_pipeline(db, app)
    rows = stage_rows(db, app.id)
    row = rows.get(stage_key)
    cur = current_stage(db, app)
    if cur != stage_key:
        raise HTTPException(status_code=409, detail=(
            f"Stage {stage_key} is not the current stage "
            f"(current: {cur or 'pipeline complete/terminated'})"))
    if row.status == TERMINATED:
        raise HTTPException(status_code=409, detail=(
            "The pipeline is terminated at this stage — no further action is possible"))
    if row.status == COMPLETED and stage_key != "DELIVERY":
        raise HTTPException(status_code=409, detail="Stage already completed")

    _validate_payload(stage_key, payload)
    if stage_key == "FINANCIAL":
        # Deterministic Decimal math for the commercial evaluation (no float
        # comparison anywhere in the ranking path).
        from decimal import Decimal, InvalidOperation
        try:
            amount_d = Decimal(str(payload["amount"])).quantize(Decimal("0.01"))
        except InvalidOperation:
            raise HTTPException(status_code=400, detail="evaluated amount must be a valid number")
        payload["rank"] = _rank_financial(db, app, float(amount_d))
        if financial_tie(db, app, amount_d):
            payload["tie"] = True
        # Persist the evaluated amount into the financial envelope
        # (evaluated == quoted unless the officer applied an adjustment).
        from .services.financial import active_financial_bid
        fb = active_financial_bid(db, app.id)
        if fb is not None:
            fb.evaluated_amount = amount_d
            fb.evaluation_adjustment = amount_d - fb.total_quoted_amount
            fb.evaluation_basis = payload.get("evaluation_basis") or "Officer financial evaluation"
            if fb.status == "SEALED":
                fb.status = "OPENED"
                fb.opened_at = utcnow()
        app.quoted_amount = float(amount_d)

    terminal = is_terminal_outcome(stage_key, payload)
    row.status = TERMINATED if terminal else COMPLETED
    row.acted_by = actor.id
    row.acted_at = utcnow()
    merged = dict(row.payload or {})
    merged.update(payload)
    row.payload = merged
    if not row.certificate_no:
        row.certificate_no = f"CERT-{CERT_PREFIX.get(stage_key, stage_key[:4].upper())}-{app.application_no.replace('-', '')}"

    new_status = stage_status_label(stage_key, merged)
    if app.status != new_status:
        app.status = new_status
    db.add(ApplicationStatusHistory(
        application_id=app.id, status=new_status,
        actor_id=actor.id, actor_role=actor.role, remarks=payload.get("remarks")))
    audit_payload = {"stage": stage_key, "outcome": payload.get("action") or payload.get("finding")
                     or payload.get("outcome") or payload.get("decision"),
                     "status_label": new_status, "certificate_no": row.certificate_no}
    if audit_extra:
        audit_payload.update(audit_extra)
    append_audit(db, "pipeline_stage_completed", "Application", app.id,
                 actor_id=actor.id, actor_role=actor.role, payload=audit_payload)
    db.flush()
    return row


def next_owner(db, app, stage_key: str):
    """User assigned to the NEXT stage's owning role on this tender."""
    idx = STAGE_INDEX.get(stage_key)
    if idx is None or idx + 1 >= len(STAGE_KEYS):
        return None
    nxt = STAGES[idx + 1]
    if nxt[2] in ("BIDDER",):
        return None
    from .models import User
    from .models.workflow import TenderAssignment
    a = (db.query(TenderAssignment)
          .filter(TenderAssignment.tender_id == app.tender_id,
                  TenderAssignment.workflow_role == nxt[2],
                  TenderAssignment.active.is_(True))
          .first())
    return db.get(User, a.user_id) if a else None
