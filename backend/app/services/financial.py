"""Financial envelope service (Feature A) — validation, sealing, versioning.

Two-envelope doctrine:
  * technical/compliance envelope — documents, profile, declarations
  * financial envelope — this module (amounts, validity, commercial declaration)

Authorization rules treat them independently. The financial envelope is
sealed at submission (atomic with the application) and stays hidden from
evaluation roles until (a) bids are open AND (b) the application is
technically qualified AND (c) the viewer wears a financial-viewing role.
Suppression is server-side in the serializers — never CSS.

All money is Decimal. The canonical SHA-256 digest makes the submitted
quotation tamper-evident; modifications create a new version (previous rows
are kept, never overwritten or deleted).
"""
import hashlib
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import pipeline as pl
from ..audit import append_audit
from ..core.bids import effective_bid_deadline, tender_bids_open
from ..core.db import utcnow
from ..core.config import settings
from ..models import Application, ApplicationStage, FinancialBid, Tender
from ..models.auction import Auction, AuctionResult
from ..models.workflow import FINANCIAL_VIEW_ROLES
from ..models.financial import canonical_financial_bid

SUPPORTED_CURRENCIES = {"INR"}
TWO_PLACES = Decimal("0.01")


def _quant(v: Decimal) -> Decimal:
    return v.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def _d(v) -> Decimal:
    """Coerce API input to a finite 2-dp Decimal (rejects NaN/Inf/negative
    zero-width tricks at the trust boundary)."""
    if v is None:
        raise _invalid("amount missing")
    try:
        d = v if isinstance(v, Decimal) else Decimal(str(v).strip())
    except (InvalidOperation, ValueError):
        raise _invalid("amount must be a decimal number")
    if not d.is_finite():
        raise _invalid("amount must be a finite number")
    return _quant(d)


def _invalid(detail: str) -> HTTPException:
    return HTTPException(status_code=422, detail={
        "code": "NON_RESPONSIVE_FINANCIAL_BID",
        "message": f"Financial bid rejected: {detail}",
    })


def _require_enabled() -> None:
    if not settings.ENABLE_FINANCIAL_BIDDING:
        raise HTTPException(status_code=409, detail={
            "code": "FEATURE_DISABLED",
            "message": "Financial bidding is disabled for this deployment.",
        })


def validate_financial_quote(*, base, tax, other, currency: str,
                             price_valid_days: int) -> dict:
    """Deterministic quote validation. Returns quantized Decimals.

    Rules (§9): amounts finite; base > 0 (nil consideration is not a valid
    competitive quotation); tax/other >= 0; exact component equality;
    supported currency; sane validity window.
    """
    currency = (currency or "INR").upper()
    if currency not in SUPPORTED_CURRENCIES:
        raise _invalid(f"currency '{currency}' not supported (supported: {', '.join(sorted(SUPPORTED_CURRENCIES))})")
    base_d = _d(base)
    tax_d = _d(tax)
    other_d = _d(other)
    if base_d <= 0:
        raise _invalid("base quoted amount must be greater than zero")
    if tax_d < 0 or other_d < 0:
        raise _invalid("tax and other charges must not be negative")
    total = _quant(base_d + tax_d + other_d)
    if not isinstance(price_valid_days, int) or not (1 <= price_valid_days <= 365):
        raise _invalid("price validity must be between 1 and 365 days")
    return {"base": base_d, "tax": tax_d, "other": other_d,
            "total": total, "currency": currency, "price_valid_days": price_valid_days}


def tender_accepting_bids(tender: Tender, now=None) -> bool:
    """Server-authoritative: is this tender still accepting (re)submissions?"""
    from ..core.db import utcnow as _now
    dl = effective_bid_deadline(tender)
    if dl is None:
        return True  # no deadline defined → open (legacy tenders)
    return (now or _now()) < dl


def active_financial_bid(db: Session, application_id: str) -> FinancialBid | None:
    """The latest non-replaced, non-withdrawn version for an application."""
    return (db.query(FinancialBid)
            .filter(FinancialBid.application_id == application_id,
                    FinancialBid.status.in_(("SEALED", "OPENED")))
            .order_by(FinancialBid.version.desc()).first())


def create_financial_bid(db: Session, *, app: Application, tender: Tender,
                         user, quote: dict, commercial_declaration: str,
                         version: int = 1, prev: FinancialBid | None = None,
                         submitted_at=None, ip_address=None, user_agent=None,
                         request_id=None, idempotency_key=None,
                         commit: bool = False) -> FinancialBid:
    """Atomically record a (re)sealed financial quotation for an application."""
    ts = submitted_at or utcnow()
    bid = FinancialBid(
        application_id=app.id, tender_id=tender.id, bidder_user_id=user.id,
        version=version,
        currency=quote["currency"],
        base_amount=quote["base"], tax_amount=quote["tax"],
        other_charges=quote["other"], total_quoted_amount=quote["total"],
        price_valid_days=quote["price_valid_days"],
        price_valid_until=ts,  # refined by caller via days; set below
        commercial_declaration=commercial_declaration,
        status="SEALED", submitted_at=ts, sealed_at=ts,
        idempotency_key=idempotency_key,
        ip_address=ip_address, user_agent=user_agent, request_id=request_id,
    )
    from datetime import timedelta
    bid.price_valid_until = ts + timedelta(days=quote["price_valid_days"])
    bid.prev_version_hash = prev.financial_bid_hash if prev else None
    bid.financial_bid_hash = canonical_financial_bid(
        tender_id=tender.id, bidder_id=user.id, version=version,
        base_amount=quote["base"], tax_amount=quote["tax"],
        other_charges=quote["other"], total_quoted_amount=quote["total"],
        currency=quote["currency"], price_valid_days=quote["price_valid_days"],
        submitted_at=ts,
    )
    db.add(bid)
    if prev is not None:
        prev.status = "REPLACED"
        prev.replaced_at = ts
    # Legacy display field stays in sync (authoritative value is the
    # FinancialBid Decimal; this Float column only feeds old read paths).
    app.quoted_amount = float(quote["total"])
    # One guard in the shared function: the idempotency key is UNIQUE, so a
    # replayed request (double-click, client retry after a timeout) collides
    # here on flush -- before any caller's commit-time handler can see it.
    # Translate it into the contract 409 so a retry can never seal a second
    # envelope or surface a 500. Covers both submit and re-quote callers.
    from fastapi import HTTPException
    from sqlalchemy.exc import IntegrityError
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail={
            "code": "DUPLICATE_BID_REQUEST",
            "message": "Duplicate bid request - this idempotency key was already used.",
        })
    return bid


def verify_financial_bid_hash(b: FinancialBid) -> bool:
    """Re-compute the canonical digest (hash-integrity check)."""
    expected = canonical_financial_bid(
        tender_id=b.tender_id, bidder_id=b.bidder_user_id, version=b.version,
        base_amount=b.base_amount, tax_amount=b.tax_amount,
        other_charges=b.other_charges, total_quoted_amount=b.total_quoted_amount,
        currency=b.currency, price_valid_days=b.price_valid_days,
        submitted_at=b.submitted_at,
    )
    return b.financial_bid_hash == expected


def financial_envelope_opened(db: Session, app: Application) -> bool:
    """True once the authorized financial opening has occurred:
    bids are open AND the application passed technical evaluation
    (TECHNICALLY_QUALIFIED) — or the pipeline has moved past the technical
    stage (e.g. admin-driven flows). Disqualified/withdrawn apps never open."""
    if not tender_bids_open(app.tender):
        return False
    if app.status in ("Withdrawn", "Rejected", "Closed"):
        return False
    rows = pl.stage_rows(db, app.id)
    tech = rows.get("TECHNICAL")
    if tech is not None and tech.status == pl.COMPLETED:
        return (tech.payload or {}).get("outcome") == "TECHNICALLY_QUALIFIED"
    # Pipeline advanced beyond TECHNICAL without an explicit row (legacy data)
    cur = pl.current_stage(db, app)
    if cur is None:  # fully completed
        return True
    return pl.STAGE_INDEX.get("TECHNICAL", 4) < pl.STAGE_INDEX.get(cur, 99)


def financial_visible(db: Session, app: Application, viewer_role: str) -> bool:
    """Server-side gate: may this viewer see financial amounts for this app?"""
    if viewer_role in ("ADMIN", "AUDITOR"):
        return financial_envelope_opened(db, app)
    if viewer_role not in FINANCIAL_VIEW_ROLES:
        return False
    return financial_envelope_opened(db, app)


def assert_financial_visible(db: Session, app: Application, viewer_role: str) -> None:
    if not financial_visible(db, app, viewer_role):
        raise HTTPException(status_code=403, detail={
            "code": "FINANCIAL_ENVELOPE_SEALED",
            "message": ("The financial envelope is sealed for this viewer. It opens after "
                        "technical qualification and is visible only to financial-evaluation "
                        "roles."),
        })


def mark_financial_opened(db: Session, app: Application) -> None:
    """Flip the active financial bid SEALED → OPENED (idempotent) and record
    the authorized opening in the audit chain."""
    bid = active_financial_bid(db, app.id)
    if bid is not None and bid.status == "SEALED":
        bid.status = "OPENED"
        bid.opened_at = utcnow()
    append_audit(db, "financial_envelope_opened", "Application", app.id,
                 actor_id="system", actor_role="SYSTEM",
                 tender_id=app.tender_id,
                 payload={"application_no": app.application_no,
                          "tender": app.tender.tender_no,
                          "bid_version": bid.version if bid else None},
                 commit=False)


def financial_bid_out(b: FinancialBid) -> dict:
    """Serializer for viewers who passed the gate (or the owning bidder)."""
    return {
        "id": b.id,
        "application_id": b.application_id,
        "version": b.version,
        "currency": b.currency,
        "base_amount": str(b.base_amount),
        "tax_amount": str(b.tax_amount),
        "other_charges": str(b.other_charges),
        "total_quoted_amount": str(b.total_quoted_amount),
        "evaluated_amount": str(b.evaluated_amount) if b.evaluated_amount is not None else None,
        "evaluation_basis": b.evaluation_basis,
        "price_valid_days": b.price_valid_days,
        "price_valid_until": b.price_valid_until.isoformat() if b.price_valid_until else None,
        "commercial_declaration": b.commercial_declaration,
        "status": b.status,
        "submitted_at": b.submitted_at.isoformat() if b.submitted_at else None,
        "sealed_at": b.sealed_at.isoformat() if b.sealed_at else None,
        "opened_at": b.opened_at.isoformat() if b.opened_at else None,
        "financial_bid_hash": b.financial_bid_hash,
        "hash_valid": verify_financial_bid_hash(b),
    }


def financial_envelope_status(db: Session, app: Application, *, viewer_role: str | None = None,
                              owner: bool = False) -> dict:
    """Bidder-facing (or gated officer-facing) envelope status.

    The owning bidder may always see their own quote (``owner=True``);
    officers get amounts only after passing the financial-visibility gate.
    """
    bid = active_financial_bid(db, app.id)
    opened = financial_envelope_opened(db, app)
    out = {
        "required": app.tender.requires_financial_bid,
        "status": bid.status if bid else ("NOT_SUBMITTED" if app.tender.requires_financial_bid else "NOT_REQUIRED"),
        "version": bid.version if bid else None,
        "sealed_at": bid.sealed_at.isoformat() if bid and bid.sealed_at else None,
        "opened": opened,
        "financial_bid_hash": bid.financial_bid_hash if bid else None,
    }
    if bid is not None and (owner or financial_visible(db, app, viewer_role or "")):
        out.update(financial_bid_out(bid))
    return out


def final_price_for_ranking(db: Session, app: Application) -> Decimal | None:
    """Authoritative commercial price for L-ranking (eligible bidders only).

    Source switch (§63): if an approved/completed auction exists for the
    tender and this app participated, the final RA price wins; otherwise the
    evaluated financial bid amount (== quoted unless adjusted); otherwise the
    legacy officer-evaluated stage amount; otherwise the legacy quoted amount.
    """
    result = (db.query(AuctionResult)
              .join(Auction, Auction.id == AuctionResult.auction_id)
              .filter(Auction.tender_id == app.tender_id,
                      AuctionResult.status == "APPROVED")
              .order_by(Auction.closed_at.desc().nullslast())
              .first())
    if result is not None and result.ranking:
        for entry in result.ranking:
            if entry.get("bidder_user_id") == app.bidder_user_id:
                try:
                    return _quant(Decimal(str(entry["amount"])))
                except (InvalidOperation, KeyError, ValueError):
                    break
    bid = active_financial_bid(db, app.id)
    if bid is not None:
        amt = bid.evaluated_amount if bid.evaluated_amount is not None else bid.total_quoted_amount
        return _quant(Decimal(str(amt)))
    rows = pl.stage_rows(db, app.id)
    fin = rows.get("FINANCIAL")
    if fin is not None and fin.status == pl.COMPLETED and (fin.payload or {}).get("amount") is not None:
        try:
            return _quant(Decimal(str(fin.payload["amount"])))
        except (InvalidOperation, ValueError):
            return None
    if app.quoted_amount is not None:
        return _quant(Decimal(str(app.quoted_amount)))
    return None
