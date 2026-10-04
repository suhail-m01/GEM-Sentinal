"""Auction endpoints (Features B/C).

Officer (mounted under /officer):
    POST   /auctions                          create (prerequisites enforced)
    GET    /auctions                          list (scoped)
    GET    /auctions/{id}                     detail + live snapshot
    POST   /auctions/{id}/schedule            set start/end (DRAFT→SCHEDULED)
    POST   /auctions/{id}/start               start (SCHEDULED→LIVE)
    POST   /auctions/{id}/pause               PAUSED (reason + reauth)
    POST   /auctions/{id}/resume               resume (reauth)
    POST   /auctions/{id}/close               close + ranking
    POST   /auctions/{id}/cancel              CANCELLED (reason + reauth)
    POST   /auctions/{id}/approve-result      CFA/ADMIN approval (reauth)
    GET    /auctions/{id}/live                officer monitor
    GET    /auctions/{id}/events              SSE stream (officer)
    GET    /auctions/{id}/report              auction summary PDF
    GET    /auctions/{id}/integrity           collusion risk-signal analysis

Bidder (mounted under /bidder):
    GET    /auctions                          My Auctions (upcoming/live/completed)
    GET    /auctions/{id}                     room (anonymous)
    GET    /auctions/{id}/live                poll fallback
    POST   /auctions/{id}/bids                place bid (rate-limited, idempotent)
    GET    /auctions/{id}/events              SSE stream (bidder)

Identity protection: bidder responses contain only anonymous identifiers.
Officer responses expose company names only for CFA/ADMIN (and to
financial-viewing roles once the auction is no longer live).
"""
import io
import json
import time
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..audit import append_audit
from ..core.config import settings
from ..core.db import SessionLocal, get_db, utcnow
from ..core.deps import get_current_bidder, get_current_pipeline_actor
from ..core.security import decode_token
from ..models import (
    Auction,
    AuctionBid,
    AuctionParticipant,
    AuctionResult,
    Application,
    AuditLog,
    Tender,
    TenderAssignment,
    User,
    PROCUREMENT_MODES,
)
from ..services import auction as auc
from ..services import financial as fin_svc
from ..override import verify_reauthentication
from .auth import limiter

router = APIRouter(prefix="/auctions", tags=["auctions"])
bidder_router = APIRouter(tags=["auctions-bidder"])

MANAGE_ROLES = ("PROCUREMENT_OFFICER", "CFA", "COMMITTEE_CHAIR")
APPROVE_ROLES = ("CFA",)


def _manage_authorized(db: Session, officer: User, tender: Tender) -> str:
    """(role, tender)-scoped management authority (pipeline doctrine)."""
    if officer.role in ("ADMIN", "AUDITOR"):
        return officer.role
    hat = (db.query(TenderAssignment)
           .filter(TenderAssignment.tender_id == tender.id,
                   TenderAssignment.user_id == officer.id,
                   TenderAssignment.workflow_role.in_(MANAGE_ROLES),
                   TenderAssignment.active.is_(True)).first())
    if hat is None:
        raise HTTPException(status_code=403, detail={
            "code": "AUCTION_NOT_AUTHORIZED",
            "message": "Your authority for this tender does not include auction management "
                       f"(requires {', '.join(MANAGE_ROLES)} or ADMIN)."})
    return hat.workflow_role


def _get_auction_scoped(db: Session, officer: User, auction_id: str) -> Auction:
    a = db.get(Auction, auction_id)
    if a is None:
        raise HTTPException(status_code=404, detail="Auction not found")
    _manage_authorized(db, officer, a.tender)
    return a


def _parse_dt(v: str | None) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(v).replace(tzinfo=None)
    except ValueError:
        raise HTTPException(status_code=400, detail="timestamps must be ISO-8601 (naive UTC)")


class AuctionCreateIn(BaseModel):
    tender_id: str
    auction_type: str = "REVERSE"
    title: str | None = None
    eligibility_rule: str = "ALL_TECHNICALLY_QUALIFIED"
    rule_params: dict | None = None
    min_increment: str  # decimal string
    opening_reference_price: str | None = None
    auto_extend_enabled: bool = True
    extension_trigger_seconds: int = Field(default=120, ge=1, le=3600)
    extension_seconds: int = Field(default=120, ge=5, le=3600)
    max_extensions: int = Field(default=3, ge=0, le=10)
    scheduled_start_at: str | None = None
    scheduled_end_at: str | None = None
    notes: str | None = None


class ScheduleIn(BaseModel):
    scheduled_start_at: str
    scheduled_end_at: str


class ReasonIn(BaseModel):
    reason: str = Field(min_length=10)
    reauth_token: str = Field(min_length=10)


class ReauthIn(BaseModel):
    reauth_token: str = Field(min_length=10)
    remarks: str | None = None


class ApproveIn(BaseModel):
    decision: str  # APPROVE | REJECT
    remarks: str = Field(min_length=10)
    reauth_token: str = Field(min_length=10)


class BidIn(BaseModel):
    amount: str  # decimal string, INR
    idempotency_key: str | None = None


# ---------------------------------------------------------------------------
# officer
# ---------------------------------------------------------------------------

@router.post("")
def create_auction(body: AuctionCreateIn, request: Request,
                   officer: User = Depends(get_current_pipeline_actor),
                   db: Session = Depends(get_db)):
    from ..routers.auth import limiter  # noqa: F401  (rate limiting applied via app.state)

    if body.auction_type not in ("REVERSE", "FORWARD"):
        raise HTTPException(status_code=400, detail="auction_type must be REVERSE or FORWARD")
    t = db.get(Tender, body.tender_id)
    if t is None:
        raise HTTPException(status_code=404, detail="Tender not found")
    _manage_authorized(db, officer, t)
    a = auc.create_auction(
        db, t, officer,
        auction_type=body.auction_type, title=body.title,
        eligibility_rule=body.eligibility_rule, rule_params=body.rule_params,
        min_increment=body.min_increment,
        opening_reference_price=body.opening_reference_price,
        auto_extend_enabled=body.auto_extend_enabled,
        extension_trigger_seconds=body.extension_trigger_seconds,
        extension_seconds=body.extension_seconds, max_extensions=body.max_extensions,
        scheduled_start_at=_parse_dt(body.scheduled_start_at),
        scheduled_end_at=_parse_dt(body.scheduled_end_at),
        notes=body.notes, request=request)
    db.commit()
    return {"auction_id": a.id, "auction_no": a.auction_no, "status": a.status}


@router.get("")
def list_auctions(officer: User = Depends(get_current_pipeline_actor),
                  db: Session = Depends(get_db)):
    if officer.role in ("ADMIN", "AUDITOR"):
        rows = db.query(Auction).order_by(Auction.created_at.desc()).all()
    else:
        tender_ids = [ta.tender_id for ta in
                      db.query(TenderAssignment).filter(TenderAssignment.user_id == officer.id,
                                                        TenderAssignment.active.is_(True))]
        if not tender_ids:
            return {"auctions": []}
        rows = (db.query(Auction).filter(Auction.tender_id.in_(tender_ids))
                .order_by(Auction.created_at.desc()).all())
    out = []
    for a in rows:
        eligible = (db.query(AuctionParticipant)
                    .filter(AuctionParticipant.auction_id == a.id,
                            AuctionParticipant.ra_eligible.is_(True)).count())
        out.append({
            "id": a.id, "auction_no": a.auction_no, "tender_no": a.tender.tender_no,
            "auction_type": a.auction_type, "title": a.title, "status": a.status,
            "scheduled_start_at": a.scheduled_start_at.isoformat() if a.scheduled_start_at else None,
            "effective_end_at": a.effective_end_at.isoformat() if a.effective_end_at else None,
            "eligible_participants": eligible,
        })
    return {"auctions": out}


def _auction_detail(db: Session, a: Auction, officer: User, role: str) -> dict:
    snap = auc.live_snapshot(db, a, viewer=officer, viewer_is_officer=True, officer_role=role)
    snap["eligible_rule"] = a.eligibility_rule
    snap["rule_params"] = a.rule_params
    snap["max_extensions"] = a.max_extensions
    snap["extension_trigger_seconds"] = a.extension_trigger_seconds
    snap["extension_seconds"] = a.extension_seconds
    snap["cancel_reason"] = a.cancel_reason
    snap["paused_reason"] = a.paused_reason
    snap["created_by"] = a.created_by
    return snap


@router.get("/{auction_id}")
def auction_detail(auction_id: str, officer: User = Depends(get_current_pipeline_actor),
                   db: Session = Depends(get_db)):
    a = _get_auction_scoped(db, officer, auction_id)
    role = "ADMIN" if officer.role in ("ADMIN", "AUDITOR") else _manage_authorized(db, officer, a.tender)
    return _auction_detail(db, a, officer, role)


@router.post("/{auction_id}/schedule")
def schedule(auction_id: str, body: ScheduleIn, request: Request,
             officer: User = Depends(get_current_pipeline_actor),
             db: Session = Depends(get_db)):
    a = _get_auction_scoped(db, officer, auction_id)
    auc.schedule_auction(db, a, officer,
                         scheduled_start_at=_parse_dt(body.scheduled_start_at),
                         scheduled_end_at=_parse_dt(body.scheduled_end_at),
                         request=request)
    db.commit()
    return {"auction_id": a.id, "status": a.status}


@router.post("/{auction_id}/start")
def start(auction_id: str, request: Request,
          officer: User = Depends(get_current_pipeline_actor),
          db: Session = Depends(get_db)):
    a = _get_auction_scoped(db, officer, auction_id)
    auc.start_auction(db, a, officer, request=request)
    db.commit()
    return {"auction_id": a.id, "status": a.status,
            "effective_end_at": a.effective_end_at.isoformat() if a.effective_end_at else None}


@router.post("/{auction_id}/pause")
def pause(auction_id: str, body: ReasonIn, request: Request,
          officer: User = Depends(get_current_pipeline_actor),
          db: Session = Depends(get_db)):
    a = _get_auction_scoped(db, officer, auction_id)
    verify_reauthentication(body.reauth_token, officer)
    auc.pause_auction(db, a, officer, reason=body.reason, request=request)
    db.commit()
    return {"auction_id": a.id, "status": a.status}


@router.post("/{auction_id}/resume")
def resume(auction_id: str, body: ReauthIn, request: Request,
           officer: User = Depends(get_current_pipeline_actor),
           db: Session = Depends(get_db)):
    a = _get_auction_scoped(db, officer, auction_id)
    verify_reauthentication(body.reauth_token, officer)
    auc.resume_auction(db, a, officer, request=request)
    db.commit()
    return {"auction_id": a.id, "status": a.status}


@router.post("/{auction_id}/close")
def close(auction_id: str, request: Request,
          officer: User = Depends(get_current_pipeline_actor),
          db: Session = Depends(get_db)):
    a = _get_auction_scoped(db, officer, auction_id)
    auc.close_auction(db, a, officer, reason="authorized_close", request=request)
    db.commit()
    return {"auction_id": a.id, "status": a.status}


@router.post("/{auction_id}/cancel")
def cancel(auction_id: str, body: ReasonIn, request: Request,
           officer: User = Depends(get_current_pipeline_actor),
           db: Session = Depends(get_db)):
    a = _get_auction_scoped(db, officer, auction_id)
    verify_reauthentication(body.reauth_token, officer)
    auc.cancel_auction(db, a, officer, reason=body.reason, request=request)
    db.commit()
    return {"auction_id": a.id, "status": a.status}


@router.post("/{auction_id}/approve-result")
def approve_result(auction_id: str, body: ApproveIn, request: Request,
                   officer: User = Depends(get_current_pipeline_actor),
                   db: Session = Depends(get_db)):
    a = _get_auction_scoped(db, officer, auction_id)
    if officer.role not in ("ADMIN", *APPROVE_ROLES):
        raise HTTPException(status_code=403, detail={
            "code": "RESULT_APPROVAL_NOT_AUTHORIZED",
            "message": "Only the Competent Authority (CFA) or ADMIN may approve or reject an auction result."})
    verify_reauthentication(body.reauth_token, officer)
    auc.approve_result(db, a, officer, decision=body.decision, remarks=body.remarks, request=request)
    db.commit()
    return {"auction_id": a.id, "status": a.status}


@router.get("/{auction_id}/live")
def auction_live(auction_id: str, officer: User = Depends(get_current_pipeline_actor),
                 db: Session = Depends(get_db)):
    a = _get_auction_scoped(db, officer, auction_id)
    role = "ADMIN" if officer.role in ("ADMIN", "AUDITOR") else _manage_authorized(db, officer, a.tender)
    return auc.live_snapshot(db, a, viewer=officer, viewer_is_officer=True, officer_role=role)


# ---------------------------------------------------------------------------
# report (PDF) + integrity (deterministic signals)
# ---------------------------------------------------------------------------

def _identities_disclosable(role: str, a: Auction) -> bool:
    """Identity-protection doctrine: company names are exposed to CFA/ADMIN
    always, and to other managing roles once the auction is no longer live."""
    if role in ("CFA", "ADMIN"):
        return True
    return a.status not in ("LIVE", "EXTENDED", "PAUSED")


def _auction_report_data(db: Session, a: Auction, show_names: bool) -> dict:
    from ..models import AuctionResult, BidderProfile

    parts = (db.query(AuctionParticipant)
             .filter(AuctionParticipant.auction_id == a.id).all())
    result = (db.query(AuctionResult).filter(AuctionResult.auction_id == a.id).first())
    rank_by_anon: dict[str, str] = {}
    source_by_anon: dict[str, str] = {}
    if result and result.ranking:
        for e in result.ranking:
            rank_by_anon[e["anonymous_id"]] = str(e["rank"])
            source_by_anon[e["anonymous_id"]] = e.get("price_source", "")

    names: dict[str, str | None] = {}
    if show_names:
        for u in {p.bidder_user_id for p in parts}:
            prof = (db.query(BidderProfile).filter(BidderProfile.user_id == u).first())
            names[u] = prof.company_name if prof and prof.company_name else None
    else:
        names = {u: None for u in {p.bidder_user_id for p in parts}}

    participants = []
    for p in sorted(parts, key=lambda x: (not x.ra_eligible, x.anonymous_id)):
        participants.append({
            "rank": rank_by_anon.get(p.anonymous_id) if result else None,
            "anonymous_id": p.anonymous_id,
            "company": names.get(p.bidder_user_id),
            "ra_eligible": p.ra_eligible,
            "qualification_reason": None if p.ra_eligible else p.qualification_reason,
            "initial_price": p.initial_price,
            "final_price": p.final_ra_price,
            "price_source": source_by_anon.get(p.anonymous_id) or (
                "INITIAL_QUALIFYING_BID" if p.final_ra_price == p.initial_price else ""),
        })

    bids = []
    if show_names or a.status in ("RESULT_PENDING", "CLOSED", "COMPLETED", "CANCELLED"):
        anon_by_part = {p.id: p.anonymous_id for p in parts}
        rows = (db.query(AuctionBid)
                .filter(AuctionBid.auction_id == a.id, AuctionBid.status == "ACCEPTED")
                .order_by(AuctionBid.sequence_number.asc()).all())
        for b in rows:
            bids.append({"seq": b.sequence_number,
                         "anonymous_id": anon_by_part.get(b.participant_id, "—"),
                         "amount": b.amount,
                         "ts": b.server_received_at.strftime("%Y-%m-%d %H:%M:%S UTC")
                if b.server_received_at else "—",
                         "hash": b.bid_hash})

    from ..audit import verify_chain
    audit_events = (db.query(AuditLog).filter(AuditLog.entity_id == a.id).count())
    data = {
        "participants": participants,
        "bids": bids,
        "result": None,
        "integrity": {
            "bid_chain": auc.verify_bid_chain(db, a),
            "audit_events": audit_events,
            "audit_chain_valid": bool(verify_chain(db).get("valid")),
        },
        "created_by": "—",
    }
    if result:
        data["result"] = {
            "initial_lowest": result.initial_lowest,
            "final_lowest": result.final_lowest,
            "savings_amount": result.savings_amount,
            "savings_pct": result.savings_pct,
            "accepted_bid_count": result.accepted_bid_count,
            "tie": result.tie,
            "result_hash": result.result_hash,
            "status": result.status,
            "ranking": result.ranking,
        }
    creator = db.get(User, a.created_by)
    data["created_by"] = (f"{creator.full_name} ({creator.role})") if creator else "—"
    return data


@router.get("/{auction_id}/report")
def auction_report(auction_id: str, officer: User = Depends(get_current_pipeline_actor),
                   db: Session = Depends(get_db)):
    a = _get_auction_scoped(db, officer, auction_id)
    role = "ADMIN" if officer.role in ("ADMIN", "AUDITOR") else _manage_authorized(db, officer, a.tender)
    show_names = _identities_disclosable(role, a)
    data = _auction_report_data(db, a, show_names)
    from .. import reports
    from ..core.db import utcnow
    data["generated_at"] = utcnow()
    data["generated_by"] = f"{officer.full_name} ({officer.role})"
    pdf = reports.auction_report_to_pdf(a, data)
    return StreamingResponse(io.BytesIO(pdf), media_type="application/pdf",
                             headers={"Content-Disposition": f'attachment; filename="{a.auction_no}-report.pdf"'})


@router.get("/{auction_id}/integrity")
def auction_integrity(auction_id: str, officer: User = Depends(get_current_pipeline_actor),
                      db: Session = Depends(get_db)):
    """Deterministic integrity + collusion risk-signal analysis for the auction.

    Read-only and advisory: bid-hash chain recomputation, audit-chain status,
    bid-time source-IP overlap, and the tender-scoped shared-attribute
    indicators. The rule engine decides — these signals never trigger
    automatic action.
    """
    a = _get_auction_scoped(db, officer, auction_id)
    from ..audit import verify_chain
    from ..services.collusion import _mask_ip, analyze_tender_collusion

    chain = verify_chain(db)
    audit_events = (db.query(AuditLog).filter(AuditLog.entity_id == a.id).count())

    parts = (db.query(AuctionParticipant)
             .filter(AuctionParticipant.auction_id == a.id).all())
    bids = (db.query(AuctionBid)
            .filter(AuctionBid.auction_id == a.id, AuctionBid.status == "ACCEPTED")
            .order_by(AuctionBid.sequence_number.asc()).all())
    anon_by_part = {p.id: p.anonymous_id for p in parts}
    ips_by_anon: dict[str, set] = {}
    for b in bids:
        if b.ip_address:
            masked = _mask_ip(b.ip_address)
            ips_by_anon.setdefault(anon_by_part.get(b.participant_id, "—"), set()).add(masked)
    ip_by_anon = {k: sorted(v) for k, v in ips_by_anon.items() if v}
    shared_bid_ips: list[dict] = []
    for ip, anons in _group_by_value(ip_by_anon).items():
        if len(anons) >= 2:
            shared_bid_ips.append({"masked_ip": ip, "participants": sorted(anons)})

    result = (db.query(AuctionResult).filter(AuctionResult.auction_id == a.id).first())
    coll = analyze_tender_collusion(db, a.tender_id)
    return {
        "auction_id": a.id,
        "auction_no": a.auction_no,
        "auction_type": a.auction_type,
        "status": a.status,
        "bid_chain": auc.verify_bid_chain(db, a),
        "audit": {"auction_events": audit_events, "audit_chain_valid": bool(chain.get("valid"))},
        "result_hash": result.result_hash if result else None,
        "bid_source_ips": [
            {"anonymous_id": k, "distinct_ips": len(v), "ips": v}
            for k, v in sorted(ip_by_anon.items())
        ],
        "shared_bid_ips": shared_bid_ips,
        "collusion_signals": {
            "bidders_analyzed": coll["bidders_analyzed"],
            "risk_level": coll["risk_level"],
            "cartel_detected": coll["cartel_detected"],
            "indicators": coll["indicators"],
            "note": ("Advisory indicators for human review. The rule engine decides; "
                     "no automatic disqualification or award action is taken from these signals."),
        },
    }


def _group_by_value(ip_by_anon: dict) -> dict:
    out: dict[str, set] = {}
    for anon, ips in ip_by_anon.items():
        for ip in ips:
            out.setdefault(ip, set()).add(anon)
    return out


# ---------------------------------------------------------------------------
# SSE (officer + bidder) — short-poll DB inside the stream; clients also have
# a plain polling endpoint as fallback.
# ---------------------------------------------------------------------------

def _sse(db_factory, snapshot_fn, max_seconds: int = 600):
    last = None
    start = time.time()
    yield f"event: hello\ndata: {json.dumps({'ok': True})}\n\n"
    while time.time() - start < max_seconds:
        db = db_factory()
        try:
            snap = snapshot_fn(db)
        except Exception:  # noqa: BLE001 — keep the stream alive, client polls
            db.close()
            time.sleep(3)
            continue
        finally:
            db.close()
        payload = json.dumps(snap, default=str)
        if payload != last:
            yield f"event: update\ndata: {payload}\n\n"
            last = payload
        time.sleep(2)
    yield "event: end\ndata: {}\n\n"


@router.get("/{auction_id}/events")
def auction_events_officer(auction_id: str, officer: User = Depends(get_current_pipeline_actor),
                           db: Session = Depends(get_db)):
    a = _get_auction_scoped(db, officer, auction_id)
    role = "ADMIN" if officer.role in ("ADMIN", "AUDITOR") else _manage_authorized(db, officer, a.tender)

    def snap(d: Session):
        return auc.live_snapshot(d, a, viewer=officer, viewer_is_officer=True, officer_role=role)

    return StreamingResponse(_sse(SessionLocal, snap), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------------------
# bidder
# ---------------------------------------------------------------------------

@bidder_router.get("/auctions")
def my_auctions(user: User = Depends(get_current_bidder), db: Session = Depends(get_db)):
    apps = (db.query(Application).filter(Application.bidder_user_id == user.id).all())
    tender_ids = {a.tender_id for a in apps}
    auctions = (db.query(Auction).filter(Auction.tender_id.in_(tender_ids))
                .order_by(Auction.created_at.desc()).all() if tender_ids else [])
    groups = {"upcoming": [], "live": [], "completed": []}
    for a in auctions:
        p = (db.query(AuctionParticipant)
             .filter(AuctionParticipant.auction_id == a.id,
                     AuctionParticipant.bidder_user_id == user.id).first())
        if p is None:
            continue
        entry = {
            "auction_id": a.id, "auction_no": a.auction_no,
            "tender_no": a.tender.tender_no, "tender_title": a.tender.title,
            "auction_type": a.auction_type, "status": a.status,
            "your_status": "Qualified" if p.ra_eligible else "Not qualified",
            "qualification_reason": p.qualification_reason,
            "initial_price": str(p.initial_price) if p.initial_price is not None else None,
            "current_price": None,
            "effective_end_at": a.effective_end_at.isoformat() if a.effective_end_at else None,
        }
        if p.ra_eligible and a.status in ("LIVE", "EXTENDED", "PAUSED", "CLOSED", "RESULT_PENDING", "COMPLETED"):
            from ..models import AuctionBid as _AB
            my_bid = (db.query(_AB)
                      .filter(_AB.participant_id == p.id, _AB.status == "ACCEPTED")
                      .order_by(_AB.sequence_number.desc()).first())
            entry["current_price"] = str(my_bid.amount) if my_bid else (
                str(p.initial_price) if p.initial_price is not None else None)
        if a.status in ("DRAFT", "SCHEDULED", "READY"):
            groups["upcoming"].append(entry)
        elif a.status in ("LIVE", "EXTENDED", "PAUSED"):
            groups["live"].append(entry)
        else:
            groups["completed"].append(entry)
    return groups


@bidder_router.get("/auctions/{auction_id}")
def my_auction_room(auction_id: str, user: User = Depends(get_current_bidder),
                    db: Session = Depends(get_db)):
    from ..models import AuctionBid  # noqa: F401

    a = db.get(Auction, auction_id)
    if a is None:
        raise HTTPException(status_code=404, detail="Auction not found")
    p = (db.query(AuctionParticipant)
         .filter(AuctionParticipant.auction_id == a.id,
                 AuctionParticipant.bidder_user_id == user.id).first())
    if p is None:
        raise HTTPException(status_code=403, detail={
            "code": "NOT_A_PARTICIPANT", "message": "You are not a participant in this auction."})
    return auc.live_snapshot(db, a, viewer=user, viewer_is_officer=False)


@bidder_router.get("/auctions/{auction_id}/live")
def my_auction_live(auction_id: str, user: User = Depends(get_current_bidder),
                    db: Session = Depends(get_db)):
    return my_auction_room(auction_id, user, db)


@bidder_router.post("/auctions/{auction_id}/bids")
@limiter.limit(settings.RATE_LIMIT_AUCTION_BID)
def place_auction_bid(auction_id: str, body: BidIn, request: Request,
                      user: User = Depends(get_current_bidder),
                      db: Session = Depends(get_db)):
    a = db.get(Auction, auction_id)
    if a is None:
        raise HTTPException(status_code=404, detail="Auction not found")
    p = (db.query(AuctionParticipant)
         .filter(AuctionParticipant.auction_id == a.id,
                 AuctionParticipant.bidder_user_id == user.id).first())
    if p is None:
        raise HTTPException(status_code=403, detail={
            "code": "NOT_A_PARTICIPANT", "message": "You are not a participant in this auction."})
    from ..ipcontext import resolve_client_ip
    ip, _src = resolve_client_ip(request.client.host if request.client else None,
                                 request.headers.get("x-forwarded-for"),
                                 request.headers.get("x-real-ip"))
    try:
        out = auc.place_bid(
            db, a, p, user, amount=body.amount, idempotency_key=body.idempotency_key,
            ip_address=ip, user_agent=(request.headers.get("user-agent") or "")[:290] or None,
            request_id=getattr(request.state, "request_id", None), request=request)
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise HTTPException(status_code=409, detail={
            "code": "BID_REJECTED", "message": "Bid could not be processed — please retry."})
    return out



@bidder_router.get("/auctions/{auction_id}/events")
def my_auction_events(auction_id: str, user: User = Depends(get_current_bidder),
                      db: Session = Depends(get_db)):
    a = db.get(Auction, auction_id)
    if a is None:
        raise HTTPException(status_code=404, detail="Auction not found")
    p = (db.query(AuctionParticipant)
         .filter(AuctionParticipant.auction_id == a.id,
                 AuctionParticipant.bidder_user_id == user.id).first())
    if p is None:
        raise HTTPException(status_code=403, detail={
            "code": "NOT_A_PARTICIPANT", "message": "You are not a participant in this auction."})

    def snap(d: Session):
        return auc.live_snapshot(d, a, viewer=user, viewer_is_officer=False)

    return StreamingResponse(_sse(SessionLocal, snap), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
