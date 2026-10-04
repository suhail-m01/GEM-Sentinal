"""RA eligibility engine (Feature B) — configurable policy, reasoned outcomes.

Policies (extensible without touching the auction engine):
    ALL_TECHNICALLY_QUALIFIED   — every technically qualified bidder
    PRICE_BASED_SHORTLIST       — top-N by initial price among qualified
    CONFIGURED_PERCENTAGE_RULE  — first ceil(N * pct%) qualified by price
    TENDER_SPECIFIC_RULE        — technical qualification only (default)

Every participant gets ra_eligible + qualification_rule + qualification_reason
+ calculated_at — never an unexplained boolean (§22).
"""
import math

from sqlalchemy.orm import Session

from .. import pipeline as pl
from ..core.db import utcnow
from ..models import Application, Auction, AuctionResult

QUALIFIED_FINDINGS = ("VERIFIED", "MANUAL_REVIEW_REQUIRED")


def _stage(db: Session, app: Application, key: str):
    rows = pl.stage_rows(db, app.id)
    return rows.get(key)


def _qualification_state(db: Session, app: Application) -> tuple[bool, str]:
    """Base gates shared by all policies. Returns (qualified, reason)."""
    if app.status == "Withdrawn":
        return False, "Bid withdrawn — excluded from auction qualification."
    if app.status in ("Rejected", "Closed"):
        return False, "Application rejected/closed — excluded from auction qualification."
    comp = _stage(db, app, "COMPLIANCE")
    if comp is None or comp.status != pl.COMPLETED:
        return False, "Compliance verification not completed."
    if (comp.payload or {}).get("finding") not in QUALIFIED_FINDINGS:
        return False, "Compliance verification failed."
    tech = _stage(db, app, "TECHNICAL")
    if tech is None or tech.status != pl.COMPLETED:
        return False, "Technical evaluation not completed."
    if (tech.payload or {}).get("outcome") != "TECHNICALLY_QUALIFIED":
        return False, "Technical evaluation failed."
    return True, "Technically qualified."


def _initial_price(db: Session, app: Application):
    """Initial qualifying financial bid amount (Decimal) or None."""
    from .financial import active_financial_bid

    bid = active_financial_bid(db, app.id)
    if bid is not None:
        return bid.total_quoted_amount
    if app.quoted_amount is not None:
        from decimal import Decimal
        return Decimal(str(app.quoted_amount)).quantize(Decimal("0.01"))
    return None


def compute_eligibility(db: Session, tender, rule: str, params: dict | None) -> list[dict]:
    """Evaluate every application on the tender under the given policy.

    Deterministic and re-runnable (the audit trail records when it ran).
    """
    params = params or {}
    apps = (db.query(Application)
            .filter(Application.tender_id == tender.id)
            .order_by(Application.application_no.asc()).all())

    qualified: list[Application] = []
    results: list[dict] = []
    for a in apps:
        ok, reason = _qualification_state(db, a)
        price = _initial_price(db, a) if ok else None
        if ok:
            qualified.append(a)
        # Base gate is the default outcome; shortlist policies refine below.
        results.append({"application": a, "eligible": ok, "reason": reason, "price": price})

    if rule in ("PRICE_BASED_SHORTLIST", "CONFIGURED_PERCENTAGE_RULE"):
        ranked = sorted([r for r in results if r["eligible"] and r["price"] is not None],
                        key=lambda r: r["price"])
        if rule == "PRICE_BASED_SHORTLIST":
            top = max(1, int(params.get("shortlist_top", 4)))
            for i, r in enumerate(ranked):
                r["eligible"] = i < top
                r["reason"] = (f"Eligible: technically qualified and within the configured "
                               f"top-{top} price shortlist.") if i < top else (
                               "Not eligible: outside the configured top "
                               f"{top} price shortlist.")
        else:
            pct = min(100, max(0, int(params.get("percentage", 100))))
            n = math.ceil(len(ranked) * pct / 100) if ranked else 0
            for i, r in enumerate(ranked):
                r["eligible"] = i < n
                r["reason"] = (f"Eligible: technically qualified and within the configured "
                               f"{pct}% participation rule.") if i < n else (
                               "Not eligible: outside the configured percentage participation rule.")

    for r in results:
        if not r["eligible"]:
            continue
        if r["price"] is None:
            r["eligible"] = False
            r["reason"] = "Not eligible: no sealed financial bid on record."
            continue
        if rule in ("PRICE_BASED_SHORTLIST", "CONFIGURED_PERCENTAGE_RULE"):
            continue  # reasoned above
        # default / ALL_TECHNICALLY_QUALIFIED / TENDER_SPECIFIC_RULE
        r["reason"] = ("Eligible: technically qualified and included under the configured "
                       "RA participation rule.")

    ts = utcnow()
    return [{
        "application_id": r["application"].id,
        "application_no": r["application"].application_no,
        "bidder_user_id": r["application"].bidder_user_id,
        "ra_eligible": bool(r["eligible"]),
        "qualification_rule": rule,
        "qualification_reason": r["reason"],
        "calculated_at": ts,
        "initial_price": r["price"],
    } for r in results]
