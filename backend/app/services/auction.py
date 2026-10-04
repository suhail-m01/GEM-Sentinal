"""Auction engine (Features B/C) — shared service for REVERSE and FORWARD.

Shared infrastructure: timing, authentication, audit, transactions,
real-time snapshots, participant anonymity, anti-sniping extensions,
idempotency, notifications, history, closure, reporting. The ONLY
direction-dependent logic is the price rule:

    REVERSE: new bid must be LOWER than the participant's last price AND at
             least min_increment below the current leading price.
    FORWARD: new bid must be HIGHER than the current leading price AND at
             least min_increment above it.

STRICT: no LLM anywhere in this path. All decisions are deterministic code
operating on server-authoritative time and the locked auction row.
"""
import hashlib
import json
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..audit import append_audit
from ..core.bids import tender_bids_open
from ..core.config import settings
from ..core.db import utcnow
from ..models import (
    Application,
    Auction,
    AuctionBid,
    AuctionParticipant,
    AuctionResult,
    Tender,
    User,
)
from ..notifications import notify
from ..services import auction_eligibility

TWO = Decimal("0.01")
GENESIS = "0" * 64


def _d(v) -> Decimal:
    try:
        d = v if isinstance(v, Decimal) else Decimal(str(v).strip())
    except (InvalidOperation, ValueError):
        raise HTTPException(status_code=422, detail={
            "code": "INVALID_AUCTION_BID", "message": "Bid amount must be a decimal number."})
    if not d.is_finite():
        raise HTTPException(status_code=422, detail={
            "code": "INVALID_AUCTION_BID", "message": "Bid amount must be a finite number."})
    return d.quantize(TWO, rounding=ROUND_HALF_UP)


def _audit(db, action, auction, actor, payload, request=None, commit=False):
    append_audit(db, action, "Auction", auction.id,
                 actor_id=getattr(actor, "id", actor),
                 actor_role=getattr(actor, "role", None),
                 tender_id=auction.tender_id,
                 payload={"auction_no": auction.auction_no, **(payload or {})},
                 request=request, commit=commit)


def _flag(auction_type: str) -> None:
    if auction_type == "REVERSE" and not settings.ENABLE_REVERSE_AUCTION:
        raise HTTPException(status_code=409, detail={
            "code": "FEATURE_DISABLED", "message": "Reverse auctions are disabled for this deployment."})
    if auction_type == "FORWARD" and not settings.ENABLE_FORWARD_AUCTION:
        raise HTTPException(status_code=409, detail={
            "code": "FEATURE_DISABLED", "message": "Forward auctions are disabled for this deployment."})


def _next_auction_no(db: Session, auction_type: str) -> str:
    prefix = "RA" if auction_type == "REVERSE" else "FA"
    year = utcnow().year
    last = (db.query(func.max(Auction.auction_no))
            .filter(Auction.auction_no.like(f"{prefix}-{year}-%")).scalar())
    n = (int(last.rsplit("-", 1)[-1]) + 1) if last else 1
    return f"{prefix}-{year}-{n:05d}"


# ---------------------------------------------------------------------------
# creation / scheduling / lifecycle
# ---------------------------------------------------------------------------

def create_auction(db: Session, tender: Tender, officer: User, *, auction_type: str,
                   title: str, eligibility_rule: str = "ALL_TECHNICALLY_QUALIFIED",
                   rule_params: dict | None = None, min_increment, opening_reference_price=None,
                   auto_extend_enabled: bool = True, extension_trigger_seconds: int = 120,
                   extension_seconds: int = 120, max_extensions: int = 3,
                   scheduled_start_at: datetime | None = None,
                   scheduled_end_at: datetime | None = None, notes: str | None = None,
                   request=None) -> Auction:
    """Create an auction in DRAFT with participants + reasoned eligibility.

    Prerequisites (§20) are validated — if any fails the auction is not
    created (controlled 409, never partial state).
    """
    _flag(auction_type)
    if auction_type == "REVERSE" and tender.effective_procurement_mode != "E_BID_WITH_REVERSE_AUCTION":
        raise HTTPException(status_code=409, detail={
            "code": "MODE_MISMATCH",
            "message": "Reverse auctions are available only for tenders configured as e-Bid + Reverse Auction."})
    if auction_type == "FORWARD" and tender.effective_procurement_mode != "FORWARD_E_AUCTION":
        raise HTTPException(status_code=409, detail={
            "code": "MODE_MISMATCH",
            "message": "Forward auctions are available only for tenders configured as Electronic (Forward) Auction."})
    if not tender_bids_open(tender):
        raise HTTPException(status_code=409, detail={
            "code": "BIDS_NOT_OPEN", "message": "Bid opening has not occurred — the auction cannot be created yet."})
    existing = (db.query(Auction)
                .filter(Auction.tender_id == tender.id,
                        Auction.status.in_(("DRAFT", "SCHEDULED", "READY", "LIVE", "EXTENDED",
                                            "PAUSED", "CLOSED", "RESULT_PENDING"))).first())
    if existing is not None:
        raise HTTPException(status_code=409, detail={
            "code": "AUCTION_ALREADY_EXISTS",
            "message": f"An active auction ({existing.auction_no}) already exists for this tender."})
    inc = _d(min_increment)
    if inc <= 0:
        raise HTTPException(status_code=409, detail={
            "code": "INVALID_CONFIG", "message": "Minimum increment must be greater than zero."})
    if scheduled_start_at and scheduled_end_at and scheduled_end_at <= scheduled_start_at:
        raise HTTPException(status_code=409, detail={
            "code": "INVALID_CONFIG", "message": "Auction end must be after its start."})
    if not (1 <= extension_trigger_seconds <= 3600) or not (5 <= extension_seconds <= 3600) or max_extensions < 0:
        raise HTTPException(status_code=409, detail={
            "code": "INVALID_CONFIG", "message": "Extension window/duration must be sane (seconds)."})

    eligible_rows = auction_eligibility.compute_eligibility(db, tender, eligibility_rule, rule_params)
    qualified = [r for r in eligible_rows if r["ra_eligible"]]
    if not qualified:
        raise HTTPException(status_code=409, detail={
            "code": "NO_QUALIFIED_BIDDERS",
            "message": "No technically qualified bidders on record — complete compliance and technical evaluation first."})
    if len(qualified) < 2:
        raise HTTPException(status_code=409, detail={
            "code": "INSUFFICIENT_PARTICIPANTS",
            "message": "At least two qualified bidders are required to run a competitive auction."})

    auction = Auction(
        auction_no=_next_auction_no(db, auction_type),
        tender_id=tender.id, auction_type=auction_type, title=title or f"{tender.tender_no} auction",
        status="DRAFT", eligibility_rule=eligibility_rule, rule_params=rule_params,
        scheduled_start_at=scheduled_start_at, scheduled_end_at=scheduled_end_at,
        min_increment=inc,
        opening_reference_price=_d(opening_reference_price) if opening_reference_price is not None else None,
        auto_extend_enabled=auto_extend_enabled,
        extension_trigger_seconds=extension_trigger_seconds,
        extension_seconds=extension_seconds, max_extensions=max_extensions,
        created_by=officer.id, notes=notes,
    )
    db.add(auction)
    db.flush()

    used_anon = set()
    for r in eligible_rows:
        auction_prefix = "RA" if auction_type == "REVERSE" else "FA"
        anon_hash = hashlib.sha256(
            (auction.auction_no + r["application_id"]).encode()
        ).hexdigest()[:4].upper()
        anon = f"{auction_prefix}-BIDDER-{anon_hash}"
        while anon in used_anon:  # collision guard (4 hex chars)
            anon_hash = hashlib.sha256(
                (auction.auction_no + r["application_id"] + str(len(used_anon))).encode()
            ).hexdigest()[:4].upper()
            anon = f"{auction_prefix}-BIDDER-{anon_hash}"
        used_anon.add(anon)
        db.add(AuctionParticipant(
            auction_id=auction.id, application_id=r["application_id"],
            bidder_user_id=r["bidder_user_id"], anonymous_id=anon,
            ra_eligible=r["ra_eligible"], qualification_rule=r["qualification_rule"],
            qualification_reason=r["qualification_reason"], calculated_at=r["calculated_at"],
            initial_price=r["initial_price"],
        ))
    db.flush()
    _audit(db, "AUCTION_CREATED", auction, officer,
           {"type": auction_type, "rule": eligibility_rule, "eligible": len(qualified)}, request)
    _audit(db, "RA_ELIGIBILITY_CALCULATED", auction, officer,
           {"rule": eligibility_rule, "evaluated": len(eligible_rows), "eligible": len(qualified)}, request)
    return auction


def _get_auction(db: Session, auction_id: str, for_update: bool = False) -> Auction:
    q = db.query(Auction).filter(Auction.id == auction_id)
    if for_update:
        q = q.with_for_update()
    a = q.first()
    if a is None:
        raise HTTPException(status_code=404, detail="Auction not found")
    return a


def schedule_auction(db: Session, auction: Auction, officer: User, *,
                     scheduled_start_at: datetime | None = None,
                     scheduled_end_at: datetime | None = None, request=None) -> Auction:
    if auction.status != "DRAFT":
        raise HTTPException(status_code=409, detail={
            "code": "INVALID_STATE", "message": f"Auction is {auction.status} — only DRAFT auctions can be scheduled."})
    if scheduled_start_at:
        auction.scheduled_start_at = scheduled_start_at
    if scheduled_end_at:
        auction.scheduled_end_at = scheduled_end_at
    if not auction.scheduled_start_at or not auction.scheduled_end_at or auction.scheduled_end_at <= auction.scheduled_start_at:
        raise HTTPException(status_code=409, detail={
            "code": "INVALID_CONFIG", "message": "A valid start and end (end after start) are required to schedule."})
    auction.status = "SCHEDULED"
    _audit(db, "AUCTION_SCHEDULED", auction, officer,
           {"start": auction.scheduled_start_at.isoformat(), "end": auction.scheduled_end_at.isoformat()}, request)
    for p in db.query(AuctionParticipant).filter(
            AuctionParticipant.auction_id == auction.id, AuctionParticipant.ra_eligible.is_(True)).all():
        notify(db, p.bidder_user_id, "ra_scheduled",
               message=f"Auction {auction.auction_no} scheduled "
                       f"{auction.scheduled_start_at.isoformat()}–{auction.scheduled_end_at.isoformat()} UTC.",
               entity_type="Auction", entity_id=auction.id)
    return auction


def start_auction(db: Session, auction: Auction, officer: User, request=None) -> Auction:
    if auction.status not in ("SCHEDULED", "READY"):
        raise HTTPException(status_code=409, detail={
            "code": "INVALID_STATE", "message": f"Auction is {auction.status} — only scheduled auctions can start."})
    now = utcnow()
    if auction.scheduled_start_at and now < auction.scheduled_start_at:
        raise HTTPException(status_code=409, detail={
            "code": "AUCTION_NOT_STARTED",
            "message": "Auction cannot start before its scheduled start time (server time)."})
    auction.status = "LIVE"
    auction.started_at = now
    auction.effective_end_at = auction.scheduled_end_at
    _audit(db, "AUCTION_STARTED", auction, officer, {"started_at": now.isoformat()}, request)
    for p in db.query(AuctionParticipant).filter(
            AuctionParticipant.auction_id == auction.id, AuctionParticipant.ra_eligible.is_(True)).all():
        notify(db, p.bidder_user_id, "ra_live",
               message=f"Auction {auction.auction_no} is now LIVE. You may place revised bids.",
               entity_type="Auction", entity_id=auction.id)
    return auction


def close_auction(db: Session, auction: Auction, actor, *, reason: str = "time_expired",
                  request=None) -> Auction:
    """LIVE/EXTENDED/PAUSED → CLOSED → RESULT_PENDING with computed ranking.

    Idempotent: an already-closed auction returns as-is. Freezes final
    prices; raw bid history is never modified.
    """
    if auction.status in ("CLOSED", "RESULT_PENDING", "COMPLETED", "CANCELLED"):
        return auction
    if auction.status not in ("LIVE", "EXTENDED", "PAUSED"):
        raise HTTPException(status_code=409, detail={
            "code": "INVALID_STATE", "message": f"Auction is {auction.status} — nothing to close."})
    now = utcnow()
    auction.status = "CLOSED"
    auction.closed_at = now

    reverse = auction.auction_type == "REVERSE"
    parts = (db.query(AuctionParticipant)
             .filter(AuctionParticipant.auction_id == auction.id,
                     AuctionParticipant.ra_eligible.is_(True)).all())
    revised: set[str] = set()
    for p in parts:
        bids = (db.query(AuctionBid)
                .filter(AuctionBid.auction_id == auction.id,
                        AuctionBid.participant_id == p.id,
                        AuctionBid.status == "ACCEPTED")
                .order_by(AuctionBid.sequence_number.asc()).all())
        if bids:
            revised.add(p.id)
            p.final_ra_price = min(b.amount for b in bids) if reverse else max(b.amount for b in bids)
        elif p.initial_price is not None:
            # Prototype default treatment (§38): a participant who never
            # revised keeps the initial qualifying financial bid.
            p.final_ra_price = p.initial_price
        else:
            p.final_ra_price = None

    priced = [p for p in parts if p.final_ra_price is not None]
    priced.sort(key=lambda p: p.final_ra_price, reverse=not reverse)
    # Rank by price level: a strictly better price gets the next number; an
    # identical eligible price is flagged "T" (joint — tender-specific
    # resolution required, never random).
    ranking: list[dict] = []
    rank_no = 0
    tie = False
    prev_price = None
    for p in priced:
        if prev_price is None or p.final_ra_price != prev_price:
            rank_no += 1
            label: int | str = rank_no
        else:
            tie = True
            label = "T"
        ranking.append({
            "rank": label,
            "anonymous_id": p.anonymous_id,
            "bidder_user_id": p.bidder_user_id,
            "amount": str(p.final_ra_price),
            "price_source": ("REVERSE_AUCTION_FINAL" if reverse else "FORWARD_AUCTION_FINAL")
            if p.id in revised else "INITIAL_QUALIFYING_BID",
        })
        prev_price = p.final_ra_price

    initials = [p.initial_price for p in parts if p.initial_price is not None]
    finals = [p.final_ra_price for p in priced]
    initial_lowest = (min(initials) if reverse else max(initials)) if initials else None
    final_lowest = (min(finals) if reverse else max(finals)) if finals else None
    savings = None
    savings_pct = None
    if initial_lowest is not None and final_lowest is not None:
        savings = initial_lowest - final_lowest if reverse else final_lowest - initial_lowest
        if initial_lowest:
            savings_pct = (savings / initial_lowest * 100).quantize(Decimal("0.001"))
    accepted_count = (db.query(func.count(AuctionBid.id))
                      .filter(AuctionBid.auction_id == auction.id, AuctionBid.status == "ACCEPTED").scalar())

    canonical = json.dumps(
        {"auction": auction.auction_no, "type": auction.auction_type, "closed_at": now.isoformat(),
         "ranking": [{k: str(v) for k, v in e.items()} for e in ranking], "tie": tie},
        sort_keys=True, separators=(",", ":"))
    result = AuctionResult(
        auction_id=auction.id, tender_id=auction.tender_id, closed_at=now,
        participant_count=len(parts), accepted_bid_count=int(accepted_count or 0),
        initial_lowest=initial_lowest, final_lowest=final_lowest,
        savings_amount=savings, savings_pct=savings_pct, ranking=ranking, tie=tie,
        result_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        status="PENDING_APPROVAL",
    )
    db.add(result)
    auction.status = "RESULT_PENDING"
    _audit(db, "AUCTION_CLOSED", auction, actor, {"reason": reason, "closed_at": now.isoformat()}, request)
    _audit(db, "RANKING_CALCULATED", auction, actor,
           {"final_lowest": str(final_lowest) if final_lowest else None,
            "savings": str(savings) if savings is not None else None, "tie": tie}, request)
    for p in parts:
        notify(db, p.bidder_user_id, "ra_result_ready",
               message=f"Auction {auction.auction_no} closed — the commercial result is being reviewed by the authority.",
               entity_type="Auction", entity_id=auction.id)
    if tie:
        from ..notifications import notify_admins
        notify_admins(db, "auction_tie_detected",
                      message=f"Auction {auction.auction_no}: JOINT LOWEST price — tender-specific tie resolution required.")
    return auction


def pause_auction(db: Session, auction: Auction, officer: User, *, reason: str, request=None) -> Auction:
    if auction.status not in ("LIVE", "EXTENDED"):
        raise HTTPException(status_code=409, detail={
            "code": "INVALID_STATE", "message": f"Auction is {auction.status} — only live auctions can be paused."})
    if not (reason or "").strip():
        raise HTTPException(status_code=400, detail="A reason is required to pause an auction.")
    auction.status = "PAUSED"
    auction.paused_reason = reason[:300]
    _audit(db, "AUCTION_PAUSED", auction, officer, {"reason": reason}, request)
    for p in db.query(AuctionParticipant).filter(
            AuctionParticipant.auction_id == auction.id, AuctionParticipant.ra_eligible.is_(True)).all():
        notify(db, p.bidder_user_id, "ra_paused",
               message=f"Auction {auction.auction_no} paused by the procurement authority (administrative).",
               entity_type="Auction", entity_id=auction.id)
    return auction


def resume_auction(db: Session, auction: Auction, officer: User, request=None) -> Auction:
    if auction.status != "PAUSED":
        raise HTTPException(status_code=409, detail={
            "code": "INVALID_STATE", "message": f"Auction is {auction.status} — only paused auctions can resume."})
    # Resume into LIVE if time remains, else EXTENDED (the remaining window
    # is whatever the effective end still allows — extensions already counted).
    auction.status = "LIVE" if (auction.effective_end_at and utcnow() < auction.effective_end_at) else "EXTENDED"
    _audit(db, "AUCTION_RESUMED", auction, officer, {"status": auction.status}, request)
    for p in db.query(AuctionParticipant).filter(
            AuctionParticipant.auction_id == auction.id, AuctionParticipant.ra_eligible.is_(True)).all():
        notify(db, p.bidder_user_id, "ra_resumed",
               message=f"Auction {auction.auction_no} resumed — bidding continues.",
               entity_type="Auction", entity_id=auction.id)
    return auction


def cancel_auction(db: Session, auction: Auction, officer: User, *, reason: str, request=None) -> Auction:
    if auction.status in ("COMPLETED", "CANCELLED"):
        raise HTTPException(status_code=409, detail={
            "code": "INVALID_STATE", "message": f"Auction is {auction.status} — it cannot be cancelled."})
    if not (reason or "").strip():
        raise HTTPException(status_code=400, detail="A reason is required to cancel an auction.")
    if auction.status in ("LIVE", "EXTENDED", "PAUSED"):
        auction.closed_at = utcnow()
    auction.status = "CANCELLED"
    auction.cancel_reason = reason[:300]
    _audit(db, "AUCTION_CANCELLED", auction, officer, {"reason": reason}, request)
    for p in db.query(AuctionParticipant).filter(
            AuctionParticipant.auction_id == auction.id, AuctionParticipant.ra_eligible.is_(True)).all():
        notify(db, p.bidder_user_id, "ra_cancelled",
               message=f"Auction {auction.auction_no} cancelled by the procurement authority.",
               entity_type="Auction", entity_id=auction.id)
    return auction


def approve_result(db: Session, auction: Auction, officer: User, *, decision: str,
                   remarks: str, request=None) -> Auction:
    if auction.status != "RESULT_PENDING":
        raise HTTPException(status_code=409, detail={
            "code": "INVALID_STATE", "message": f"Auction is {auction.status} — the result is not awaiting approval."})
    result = (db.query(AuctionResult).filter(AuctionResult.auction_id == auction.id).first())
    if result is None:
        raise HTTPException(status_code=409, detail="No computed result found for this auction.")
    if decision not in ("APPROVE", "REJECT"):
        raise HTTPException(status_code=400, detail="decision must be APPROVE or REJECT")
    if not (remarks or "").strip():
        raise HTTPException(status_code=400, detail="remarks are required (this becomes part of the audited record)")
    if decision == "APPROVE":
        result.status = "APPROVED"
        auction.status = "COMPLETED"
        action = "RESULT_APPROVED"
    else:
        result.status = "REJECTED"
        auction.status = "CANCELLED"
        auction.cancel_reason = f"Result rejected by {officer.role}: {remarks[:200]}"
        action = "RESULT_REVIEWED"
    result.approved_by = officer.id
    result.approved_at = utcnow()
    _audit(db, action, auction, officer, {"decision": decision, "remarks": remarks[:300]}, request)
    return auction


# ---------------------------------------------------------------------------
# live state + bidding
# ---------------------------------------------------------------------------

def current_leading(db: Session, auction: Auction) -> dict | None:
    """Current leading price (anonymous) — min for REVERSE, max for FORWARD.
    Ties broken by earlier sequence (authoritative server order)."""
    bid = (db.query(AuctionBid)
           .filter(AuctionBid.auction_id == auction.id, AuctionBid.status == "ACCEPTED")
           .order_by(AuctionBid.amount.asc(), AuctionBid.sequence_number.asc())
           .first() if auction.auction_type == "REVERSE" else
           db.query(AuctionBid)
           .filter(AuctionBid.auction_id == auction.id, AuctionBid.status == "ACCEPTED")
           .order_by(AuctionBid.amount.desc(), AuctionBid.sequence_number.asc())
           .first())
    if bid is None:
        return None
    p = db.get(AuctionParticipant, bid.participant_id)
    return {"amount": bid.amount, "sequence_number": bid.sequence_number,
            "anonymous_id": p.anonymous_id if p else None,
            "server_received_at": bid.server_received_at.isoformat() if bid.server_received_at else None}


def _participant_price_floor(db: Session, auction: Auction, p: AuctionParticipant) -> Decimal:
    """The price the participant must beat (their last accepted bid or initial)."""
    last = (db.query(AuctionBid)
            .filter(AuctionBid.auction_id == auction.id, AuctionBid.participant_id == p.id,
                    AuctionBid.status == "ACCEPTED")
            .order_by(AuctionBid.sequence_number.desc()).first())
    return Decimal(str(last.amount)) if last else Decimal(str(p.initial_price))


def place_bid(db: Session, auction: Auction, participant: AuctionParticipant, user: User,
              *, amount, idempotency_key: str | None = None,
              ip_address: str | None = None, user_agent: str | None = None,
              request_id: str | None = None, request=None) -> dict:
    """Atomic bid placement (REVERSE/FORWARD). Server-authoritative.

    Locks the auction row, validates every rule deterministically, assigns
    the monotonic sequence number, applies anti-sniping extension, and
    records audit + notifications. Idempotent on (auction, idempotency_key).
    """
    a = _get_auction(db, auction.id, for_update=True)
    if a.status == "LIVE" and a.effective_end_at and utcnow() >= a.effective_end_at:
        close_auction(db, a, "system", reason="time_expired")
        a = _get_auction(db, a.id, for_update=True)

    if a.status not in ("LIVE", "EXTENDED"):
        code = {"DRAFT": "AUCTION_NOT_STARTED", "SCHEDULED": "AUCTION_NOT_STARTED",
                "READY": "AUCTION_NOT_STARTED", "PAUSED": "AUCTION_PAUSED"}.get(a.status, "AUCTION_CLOSED")
        msg = {"PAUSED": "Auction is paused by the procurement authority.",
               "COMPLETED": "Auction is closed."}.get(a.status,
               "Auction has not started." if a.status in ("DRAFT", "SCHEDULED", "READY") else "Auction is closed.")
        raise HTTPException(status_code=409, detail={"code": code, "message": msg})

    if participant.bidder_user_id != user.id:
        raise HTTPException(status_code=403, detail={
            "code": "NOT_YOUR_AUCTION", "message": "You are not a participant in this auction."})
    if not participant.ra_eligible:
        raise HTTPException(status_code=403, detail={
            "code": "NOT_ELIGIBLE", "message": "You are not eligible for this auction."})
    if participant.suspended:
        raise HTTPException(status_code=403, detail={
            "code": "PARTICIPATION_SUSPENDED",
            "message": "Your participation is suspended pending compliance review."})

    # Idempotency FIRST (before price math): a double-click / retry of the
    # identical request must return the original outcome, not re-validate
    # against the price the first bid just set.
    if idempotency_key:
        existing = (db.query(AuctionBid)
                    .filter(AuctionBid.auction_id == a.id,
                            AuctionBid.idempotency_key == idempotency_key).first())
        if existing is not None:
            return {"bid": _bid_out(existing, participant), "duplicate": True}

    amt = _d(amount)
    if amt <= 0:
        raise HTTPException(status_code=422, detail={
            "code": "INVALID_REVERSE_AUCTION_BID", "message": "Bid amount must be greater than zero."})

    reverse = a.auction_type == "REVERSE"
    last_own = _participant_price_floor(db, a, participant)
    leading = current_leading(db, a)
    if leading is None and (reverse or a.opening_reference_price is None):
        # No revised bids yet — the market leader is the best initial
        # qualifying price (min for reverse, max for forward), so the
        # minimum-increment rule applies from the very first bid.
        # (Forward auctions with a reference price open against it instead.)
        _parts = (db.query(AuctionParticipant)
                  .filter(AuctionParticipant.auction_id == a.id,
                          AuctionParticipant.ra_eligible.is_(True),
                          AuctionParticipant.initial_price.isnot(None)).all())
        if _parts:
            _best = (min(_parts, key=lambda x: x.initial_price) if reverse
                     else max(_parts, key=lambda x: x.initial_price))
            leading = {"amount": _best.initial_price, "sequence_number": None,
                       "anonymous_id": _best.anonymous_id, "server_received_at": None}
    if reverse:
        if amt >= last_own:
            raise HTTPException(status_code=422, detail={
                "code": "INVALID_REVERSE_AUCTION_BID",
                "message": "A reverse-auction bid must be lower than your current price."})
        if leading is not None and amt > leading["amount"] - a.min_increment:
            raise HTTPException(status_code=422, detail={
                "code": "INVALID_REVERSE_AUCTION_BID",
                "message": "Bid does not satisfy the minimum decrement below the leading price."})
    else:
        if leading is None:
            # Forward auction opens against the reference price.
            base = a.opening_reference_price or amt
            if amt < base + a.min_increment:
                raise HTTPException(status_code=422, detail={
                    "code": "INVALID_FORWARD_AUCTION_BID",
                    "message": "Bid must exceed the opening reference price by at least the minimum increment."})
        else:
            if amt <= leading["amount"]:
                raise HTTPException(status_code=422, detail={
                    "code": "INVALID_FORWARD_AUCTION_BID",
                    "message": "A forward-auction bid must be higher than the current leading price."})
            if amt < leading["amount"] + a.min_increment:
                raise HTTPException(status_code=422, detail={
                    "code": "INVALID_FORWARD_AUCTION_BID",
                    "message": "Bid does not satisfy the minimum increment above the leading price."})

    seq = (db.query(func.max(AuctionBid.sequence_number))
           .filter(AuctionBid.auction_id == a.id).scalar() or 0) + 1
    prev = (db.query(AuctionBid)
            .filter(AuctionBid.auction_id == a.id, AuctionBid.status == "ACCEPTED")
            .order_by(AuctionBid.sequence_number.desc()).first())
    now = utcnow()
    bid_hash = hashlib.sha256(json.dumps(
        {"prev_hash": prev.bid_hash if prev else GENESIS,
         "auction_id": a.id, "participant_id": participant.id,
         "amount": f"{amt:.2f}", "seq": seq, "ts": now.isoformat()},
        sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

    bid = AuctionBid(
        auction_id=a.id, participant_id=participant.id, bidder_user_id=user.id,
        amount=amt, currency="INR", sequence_number=seq,
        previous_bid_amount=last_own,
        decrement_amount=(last_own - amt) if reverse else (amt - last_own),
        idempotency_key=idempotency_key, ip_address=ip_address, user_agent=user_agent,
        request_id=request_id, status="ACCEPTED", bid_hash=bid_hash,
        # Store the exact timestamp the hash committed to, so the bid-hash
        # chain is verifiable from the persisted row alone (read-only).
        server_received_at=now,
    )
    db.add(bid)
    db.flush()

    # anti-sniping: a bid inside the trigger window extends the auction
    extended = False
    if (a.auto_extend_enabled and a.effective_end_at and a.extension_count < a.max_extensions
            and (a.effective_end_at - now).total_seconds() <= a.extension_trigger_seconds):
        a.effective_end_at = a.effective_end_at + timedelta(seconds=a.extension_seconds)
        a.extension_count += 1
        if a.status == "LIVE":
            a.status = "EXTENDED"
        extended = True
        _audit(db, "AUCTION_AUTO_EXTENDED", a, "system",
               {"extension_count": a.extension_count,
                "new_effective_end": a.effective_end_at.isoformat()}, request)
        for p in db.query(AuctionParticipant).filter(
                AuctionParticipant.auction_id == a.id, AuctionParticipant.ra_eligible.is_(True)).all():
            notify(db, p.bidder_user_id, "ra_extended",
                   message=f"Auction {a.auction_no} extended — a late bid triggered the anti-sniping rule. "
                           f"New close: {a.effective_end_at.isoformat()} UTC.",
                   entity_type="Auction", entity_id=a.id)

    _audit(db, "BID_PLACED", a, user,
           {"participant": participant.anonymous_id, "seq": seq, "amount": f"{amt:.2f}",
            "bid_hash": bid_hash, "extended": extended}, request)
    notify(db, user.id, "ra_bid_accepted",
           message=f"Your revised bid #{seq} was accepted.", entity_type="Auction", entity_id=a.id)
    return {"bid": _bid_out(bid, participant), "duplicate": False, "extended": extended}


def verify_bid_chain(db: Session, auction: Auction) -> dict:
    """Read-only recomputation of the auction's SHA-256 bid hash chain.

    Each accepted bid commits to (prev_hash, participant, amount, sequence,
    server timestamp); any change to any persisted field breaks the
    recomputation (tamper-evident, no writes).
    """
    bids = (db.query(AuctionBid)
            .filter(AuctionBid.auction_id == auction.id,
                    AuctionBid.status == "ACCEPTED")
            .order_by(AuctionBid.sequence_number.asc()).all())
    prev_hash = GENESIS
    for b in bids:
        expect = hashlib.sha256(json.dumps(
            {"prev_hash": prev_hash, "auction_id": auction.id,
             "participant_id": b.participant_id, "amount": f"{b.amount:.2f}",
             "seq": b.sequence_number,
             "ts": b.server_received_at.isoformat() if b.server_received_at else None},
            sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        if b.bid_hash != expect:
            return {"valid": False, "bids_checked": len(bids),
                    "broken_at_sequence": b.sequence_number}
        prev_hash = b.bid_hash
    return {"valid": True, "bids_checked": len(bids)}


def _bid_out(b: AuctionBid, participant: AuctionParticipant | None,
             include_identity: bool = False) -> dict:
    out = {
        "auction_bid_id": b.id,
        "sequence_number": b.sequence_number,
        "amount": str(b.amount),
        "currency": b.currency,
        "server_received_at": b.server_received_at.isoformat() if b.server_received_at else None,
        "status": b.status,
        "anonymous_id": participant.anonymous_id if participant else None,
    }
    if include_identity:
        out["bidder_user_id"] = b.bidder_user_id
    return out


def live_snapshot(db: Session, auction: Auction, *, viewer: User | None = None,
                  viewer_is_officer: bool = False, officer_role: str | None = None) -> dict:
    """Real-time auction state. Bidder view is fully anonymized; officer
    view maps identities only for CFA/ADMIN/financial-viewing roles."""
    a = _get_auction(db, auction.id)
    leading = current_leading(db, a)
    now = utcnow()
    parts = (db.query(AuctionParticipant)
             .filter(AuctionParticipant.auction_id == a.id).all())
    eligible = [p for p in parts if p.ra_eligible]

    show_identity = viewer_is_officer and (
        officer_role in ("ADMIN", "CFA") or (officer_role in (
            "FINANCE_EVALUATOR", "COMMITTEE_CHAIR", "COMMITTEE_MEMBER")
        and a.status not in ("LIVE", "EXTENDED")))

    participants = []
    for p in sorted(parts, key=lambda x: (not x.ra_eligible, x.anonymous_id)):
        entry = {
            "anonymous_id": p.anonymous_id,
            "ra_eligible": p.ra_eligible,
            "qualification_reason": p.qualification_reason if (p.ra_eligible or viewer_is_officer) else None,
            "suspended": p.suspended,
            "initial_price": str(p.initial_price) if (p.initial_price is not None and (p.ra_eligible or viewer_is_officer)) else None,
        }
        if show_identity:
            u = db.get(User, p.bidder_user_id)
            prof = u.bidder_profile if u else None
            entry["company"] = prof.company_name if prof else (u.full_name if u else None)
        participants.append(entry)

    recent = (db.query(AuctionBid)
              .filter(AuctionBid.auction_id == a.id, AuctionBid.status == "ACCEPTED")
              .order_by(AuctionBid.sequence_number.desc()).limit(20).all())
    recent_out = []
    for b in reversed(recent):
        p = db.get(AuctionParticipant, b.participant_id)
        recent_out.append({
            "sequence_number": b.sequence_number,
            "anonymous_id": p.anonymous_id if p else None,
            "amount": str(b.amount),
            "server_received_at": b.server_received_at.isoformat() if b.server_received_at else None,
        })

    result = None
    if a.status in ("RESULT_PENDING", "COMPLETED", "CANCELLED"):
        r = db.query(AuctionResult).filter(AuctionResult.auction_id == a.id).first()
        if r is not None:
            ranking = r.ranking or []
            if not show_identity:
                ranking = [{k: v for k, v in e.items() if k != "bidder_user_id"} for e in ranking]
            result = {
                "status": r.status, "tie": r.tie,
                "initial_lowest": str(r.initial_lowest) if r.initial_lowest is not None else None,
                "final_lowest": str(r.final_lowest) if r.final_lowest is not None else None,
                "savings_amount": str(r.savings_amount) if r.savings_amount is not None else None,
                "savings_pct": str(r.savings_pct) if r.savings_pct is not None else None,
                "accepted_bid_count": r.accepted_bid_count,
                "ranking": ranking,
            }

    my_participant = None
    if viewer is not None:
        mp = next((p for p in parts if p.bidder_user_id == viewer.id), None)
        if mp is not None:
            my_bids = (db.query(AuctionBid)
                       .filter(AuctionBid.auction_id == a.id, AuctionBid.participant_id == mp.id,
                               AuctionBid.status == "ACCEPTED")
                       .order_by(AuctionBid.sequence_number.desc()).limit(10).all())
            my_participant = {
                "anonymous_id": mp.anonymous_id,
                "ra_eligible": mp.ra_eligible,
                "suspended": mp.suspended,
                "qualification_reason": mp.qualification_reason,
                "initial_price": str(mp.initial_price) if mp.initial_price is not None else None,
                "current_price": str(my_bids[0].amount) if my_bids else
                                 (str(mp.initial_price) if mp.initial_price is not None else None),
                "bids": [{"sequence_number": b.sequence_number, "amount": str(b.amount),
                          "server_received_at": b.server_received_at.isoformat()} for b in my_bids],
            }

    return {
        "auction_id": a.id, "auction_no": a.auction_no,
        "tender_id": a.tender_id,
        "auction_type": a.auction_type, "title": a.title, "status": a.status,
        "server_time": now.isoformat(),
        "scheduled_start_at": a.scheduled_start_at.isoformat() if a.scheduled_start_at else None,
        "scheduled_end_at": a.scheduled_end_at.isoformat() if a.scheduled_end_at else None,
        "effective_end_at": a.effective_end_at.isoformat() if a.effective_end_at else None,
        "started_at": a.started_at.isoformat() if a.started_at else None,
        "closed_at": a.closed_at.isoformat() if a.closed_at else None,
        "seconds_remaining": max(0, int((a.effective_end_at - now).total_seconds()))
        if a.effective_end_at and a.status in ("LIVE", "EXTENDED") else None,
        "min_increment": str(a.min_increment),
        "opening_reference_price": str(a.opening_reference_price) if a.opening_reference_price is not None else None,
        "extension_count": a.extension_count,
        "auto_extend_enabled": a.auto_extend_enabled,
        "leading": ({**leading, "amount": str(leading["amount"])} if leading else None),
        "participants": participants,
        "participant_count": len(eligible),
        "recent_bids": recent_out,
        "result": result,
        "your_participation": my_participant,
    }


def auto_close_due(db: Session) -> int:
    """Scheduler hook: close any live auction whose effective time expired.
    Returns the number closed. Runs in its own session from the scheduler."""
    due = (db.query(Auction)
           .filter(Auction.status.in_(("LIVE", "EXTENDED")),
                   Auction.effective_end_at.isnot(None),
                   Auction.effective_end_at <= utcnow()).all())
    for a in due:
        try:
            close_auction(db, a, "system", reason="time_expired")
            db.commit()
        except Exception:  # noqa: BLE001 — one failed close must not stop the sweep
            db.rollback()
    return len(due)
