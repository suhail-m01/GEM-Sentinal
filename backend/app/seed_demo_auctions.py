"""Demo auction seeder - puts 3 auctions LIVE for the demonstration.

Additive and idempotent: run it as often as you like.

    .venv/bin/python -m app.seed_demo_auctions          # build + go live
    .venv/bin/python -m app.seed_demo_auctions status   # report only

What it builds
    TDR-2026-010  REVERSE auction  (Belagavi)  - price walks DOWN
    TDR-2026-011  FORWARD auction  (Belagavi)  - price walks UP
    TDR-2026-018  REVERSE auction  (Hubli)     - created here if absent

Everything goes through the real service paths, never raw SQL:
qualification uses ``pipeline.complete_stage`` (so the stage certificates and
the audit chain are genuine), auctions use ``services.auction.create_auction``
/ ``start_auction``, and the opening bids use ``services.auction.place_bid``
(so the min-increment rule, sequence numbers and anti-sniping all really ran).

Bid opening: an auction cannot exist while bids are sealed. These three
tenders are past their submission phase, so their bid deadline is moved into
the past - bid opening has genuinely occurred. ``align_demo_state`` skips
them for that reason; every other Belagavi tender stays sealed.
"""
from datetime import timedelta
from decimal import Decimal

from .core.db import SessionLocal, utcnow
from .models import (Application, Auction, AuctionBid, AuctionParticipant,
                     Tender, User)
from . import pipeline as pl
from .services import auction as auc

# Tenders that host a live auction. align_demo_state must not re-seal these.
AUCTION_TENDERS = ("TDR-2026-010", "TDR-2026-011", "TDR-2026-018")

# The bidder signed in during the demonstration must be able to bid live.
DEMO_BIDDER = "ramesh@bidder.in"

# Auction runtime: long enough that the demo never runs out of clock.
RUN_HOURS = 8


def _officer(db) -> User:
    """Belagavi procurement officer drives the auctions (tender-assigned)."""
    return db.query(User).filter(User.email == "procurement.belagavi@cpcl.gov.in").first()


def _ensure_third_tender(db, now):
    """TDR-2026-018: a second reverse auction, in Hubli."""
    t = db.query(Tender).filter(Tender.tender_no == "TDR-2026-018").first()
    if t is not None:
        return t, False
    src = db.query(Tender).filter(Tender.tender_no == "TDR-2026-015").first()
    t = Tender(
        tender_no="TDR-2026-018",
        title="Supply and Commissioning of Fire Safety Equipment - Hubli Depot",
        description=("Supply, installation and commissioning of fire detection and "
                     "suppression equipment at the Hubli storage depot, including "
                     "one year of preventive maintenance."),
        department=src.department, organization=src.organization,
        organization_id=src.organization_id,
        jurisdiction_code=src.jurisdiction_code,
        district="Hubli", state="Karnataka",
        category=src.category, product_service_category=src.product_service_category,
        procurement_method=src.procurement_method,
        procurement_mode="E_BID_WITH_REVERSE_AUCTION",
        financial_bid_required=True,
        value_min=Decimal("18000000.00"), value_max=Decimal("24000000.00"),
        open_date=(now - timedelta(days=21)).date(),
        close_date=(now - timedelta(days=2)).date(),
        bid_deadline=now - timedelta(days=2),
        status="CLOSED",
        sealing_enabled=False, force_opened=False,
        required_turnover=src.required_turnover,
        min_local_content=src.min_local_content,
        required_document_types=src.required_document_types,
        msme_preference=True, make_in_india_required=True, startup_eligible=False,
    )
    db.add(t)
    db.flush()
    return t, True


def _clone_applications(db, tender, now):
    """Give a new auction tender a field of bidders (reuses real bidder users)."""
    from .services import financial as fin_svc

    if db.query(Application).filter(Application.tender_id == tender.id).count():
        return 0
    donors = (db.query(Application)
              .join(Tender, Tender.id == Application.tender_id)
              .filter(Tender.tender_no == "TDR-2026-015")
              .order_by(Application.application_no.asc()).all())
    seq = db.query(Application).count()
    made = 0
    for d in donors[:4]:
        seq += 1
        app = Application(
            tender_id=tender.id, bidder_user_id=d.bidder_user_id,
            application_no=f"APP-2026-{seq:06d}", status="Submitted",
            eligibility_result=d.eligibility_result,
            compliance_score=d.compliance_score, risk_level=d.risk_level,
            declaration_text=d.declaration_text, declaration_at=now,
            submitted_at=now, snapshot_frozen_at=now,
            assigned_l1_id=d.assigned_l1_id,
        )
        db.add(app)
        db.flush()
        pl.init_pipeline(db, app)
        pl.stage_rows(db, app.id)["SUBMISSION"].certificate_no = (
            f"CERT-SUB-{app.application_no.replace('-', '')}")
        anchor = tender.value_max or tender.value_min
        spread = (int(app.bidder_user_id.replace("-", "")[:8], 16) % 15) - 8
        base = (Decimal(str(anchor)) * (Decimal(100 + spread) / Decimal(100))).quantize(Decimal("0.01"))
        quote = fin_svc.validate_financial_quote(
            base=base, tax=(base * Decimal("0.18")).quantize(Decimal("0.01")),
            other=Decimal("0.00"), currency="INR", price_valid_days=90)
        fin_svc.create_financial_bid(
            db, app=app, tender=tender, user=app.bidder_user, quote=quote,
            commercial_declaration=("Price is firm, inclusive of the quoted taxes, "
                                    "and held valid for 90 days from submission."),
            submitted_at=now, commit=False)
        made += 1
    db.flush()
    return made


def _ensure_assignments(db, tender, donor_tender_no="TDR-2026-015"):
    """Mirror a sibling tender's officer roster onto a newly created tender.

    Without assignments the tender (and its auction) is invisible in every
    officer console, because the auction list is scoped by TenderAssignment.
    """
    from .models import TenderAssignment

    if db.query(TenderAssignment).filter(TenderAssignment.tender_id == tender.id).count():
        return 0
    donor = db.query(Tender).filter(Tender.tender_no == donor_tender_no).first()
    if donor is None:
        return 0
    made = 0
    for ta in db.query(TenderAssignment).filter(TenderAssignment.tender_id == donor.id).all():
        db.add(TenderAssignment(tender_id=tender.id, user_id=ta.user_id,
                                workflow_role=ta.workflow_role, active=True))
        made += 1
    # The Belagavi officer runs the auction demonstration end to end.
    bel = _officer(db)
    if bel is not None and not any(
            ta.user_id == bel.id
            for ta in db.query(TenderAssignment).filter(TenderAssignment.tender_id == tender.id)):
        db.add(TenderAssignment(tender_id=tender.id, user_id=bel.id,
                                workflow_role="PROCUREMENT_OFFICER", active=True))
        made += 1
    db.flush()
    return made


def _ensure_bidder_application(db, tender, email, now):
    """Make sure the demo bidder actually competes in this auction.

    The participant list is fixed when the auction is created, so the bidder
    who will be logged in during the demonstration must hold an application
    on the tender before that point.
    """
    from .services import financial as fin_svc

    user = db.query(User).filter(User.email == email).first()
    if user is None:
        return False
    existing = (db.query(Application)
                .filter(Application.tender_id == tender.id,
                        Application.bidder_user_id == user.id).first())
    if existing is not None:
        return False
    seq = db.query(Application).count() + 1
    donor = (db.query(Application)
             .filter(Application.tender_id == tender.id).first())
    app = Application(
        tender_id=tender.id, bidder_user_id=user.id,
        application_no=f"APP-2026-{seq:06d}", status="Submitted",
        eligibility_result="ELIGIBLE", compliance_score=100, risk_level="LOW",
        declaration_text=("I confirm the submitted information and documents are "
                          "true and complete in all respects."),
        declaration_at=now, submitted_at=now, snapshot_frozen_at=now,
        assigned_l1_id=donor.assigned_l1_id if donor is not None else None,
    )
    db.add(app)
    db.flush()
    pl.init_pipeline(db, app)
    pl.stage_rows(db, app.id)["SUBMISSION"].certificate_no = (
        f"CERT-SUB-{app.application_no.replace('-', '')}")
    anchor = tender.value_max or tender.value_min
    # Mid-field opening price: leaves room to bid in either direction on camera.
    base = (Decimal(str(anchor)) * Decimal("0.99")).quantize(Decimal("0.01"))
    quote = fin_svc.validate_financial_quote(
        base=base, tax=(base * Decimal("0.18")).quantize(Decimal("0.01")),
        other=Decimal("0.00"), currency="INR", price_valid_days=90)
    fin_svc.create_financial_bid(
        db, app=app, tender=tender, user=user, quote=quote,
        commercial_declaration=("Price is firm, inclusive of the quoted taxes, "
                                "and held valid for 90 days from submission."),
        submitted_at=now, commit=False)
    db.flush()
    return True


def _reset(db):
    """Drop the demo auctions so they can be rebuilt with a fresh field."""
    from .models import AuctionResult

    tids = [t.id for t in db.query(Tender).filter(Tender.tender_no.in_(AUCTION_TENDERS))]
    if not tids:
        return 0
    aids = [a.id for a in db.query(Auction).filter(Auction.tender_id.in_(tids))]
    if not aids:
        return 0
    db.query(AuctionBid).filter(AuctionBid.auction_id.in_(aids)).delete(synchronize_session=False)
    db.query(AuctionResult).filter(AuctionResult.auction_id.in_(aids)).delete(synchronize_session=False)
    db.query(AuctionParticipant).filter(AuctionParticipant.auction_id.in_(aids)).delete(synchronize_session=False)
    db.query(Auction).filter(Auction.id.in_(aids)).delete(synchronize_session=False)
    db.flush()
    return len(aids)


def _qualify(db, tender, officer, now):
    """Walk each application to TECHNICALLY_QUALIFIED via the real state machine."""
    qualified = 0
    for app in (db.query(Application)
                .filter(Application.tender_id == tender.id)
                .order_by(Application.application_no.asc()).all()):
        pl.init_pipeline(db, app)
        for _ in range(len(pl.STAGE_KEYS)):
            cur = pl.current_stage(db, app)
            if cur is None or cur in ("FINANCIAL", "APPROVAL"):
                break
            if cur == "SUBMISSION":
                payload = {"action": "ACKNOWLEDGE_SUBMISSION",
                           "remarks": "Submission acknowledged for auction demonstration."}
            elif cur == "BID_CLOSURE":
                payload = {"action": "OPEN_BIDS",
                           "remarks": "Bids opened by the authorised bid opening officer."}
            elif cur == "COMPLIANCE":
                payload = {"action": "VERIFIED", "finding": "VERIFIED",
                           "remarks": "Compliance verification completed; documents verified."}
            elif cur == "TECHNICAL":
                payload = {"action": "TECHNICALLY_QUALIFIED",
                           "outcome": "TECHNICALLY_QUALIFIED",
                           "remarks": "Meets the technical specification; qualified for the auction."}
            else:
                break
            try:
                pl.complete_stage(db, app, cur, officer, payload)
            except Exception as exc:  # stage rejected -> leave it, report honestly
                print(f"    {app.application_no}: stopped at {cur} ({exc})")
                break
        tech = pl.stage_rows(db, app.id).get("TECHNICAL")
        if tech is not None and tech.status == pl.COMPLETED \
                and (tech.payload or {}).get("outcome") == "TECHNICALLY_QUALIFIED":
            qualified += 1
    db.flush()
    return qualified


def _open_bids(db, tender, now):
    """Bid opening has occurred for an auction-phase tender."""
    if tender.bid_deadline is None or tender.bid_deadline > now:
        tender.bid_deadline = now - timedelta(hours=2)
    if tender.close_date is None or tender.close_date >= now.date():
        tender.close_date = (now - timedelta(days=1)).date()
    db.flush()


def _opening_bids(db, a, now):
    """A few real competitive bids so the room is alive on camera."""
    placed = 0
    parts = (db.query(AuctionParticipant)
             .filter(AuctionParticipant.auction_id == a.id,
                     AuctionParticipant.ra_eligible.is_(True)).all())
    if db.query(AuctionBid).filter(AuctionBid.auction_id == a.id).count():
        return 0
    reverse = a.auction_type == "REVERSE"
    inc = Decimal(str(a.min_increment))
    # Best initial price is the market reference; each bidder improves on it.
    prices = [p.initial_price for p in parts if p.initial_price is not None]
    if not prices:
        return 0
    market = min(prices) if reverse else max(prices)
    step = 0
    for p in parts:
        step += 1
        if step > 3:
            break
        user = db.get(User, p.bidder_user_id)
        amount = (market - inc * step) if reverse else (market + inc * step)
        if amount <= 0:
            continue
        try:
            auc.place_bid(db, a, p, user, amount=amount,
                          idempotency_key=f"demo-{a.auction_no}-{p.anonymous_id}-{step}")
            placed += 1
        except Exception as exc:
            print(f"    bid by {p.anonymous_id} rejected: {exc}")
    db.flush()
    return placed


def build(rebuild: bool = False) -> None:
    db = SessionLocal()
    try:
        now = utcnow()
        officer = _officer(db)
        if officer is None:
            print("Belagavi procurement officer not found - run the base seed first.")
            return

        if rebuild:
            n = _reset(db)
            print(f"Reset: removed {n} existing demo auction(s)")

        t3, created = _ensure_third_tender(db, now)
        if created:
            print(f"Created {t3.tender_no} (Hubli, reverse auction)")
            n = _clone_applications(db, t3, now)
            print(f"  seeded {n} applications with sealed quotations")
        n = _ensure_assignments(db, t3)
        if n:
            print(f"  assigned {n} officers to {t3.tender_no}")

        plan = [
            ("TDR-2026-010", "REVERSE", "Reverse Auction - Pipeline Valves and Fittings",
             Decimal("50000.00")),
            ("TDR-2026-011", "FORWARD", "Forward Auction - Disposal of Decommissioned Plant Assets",
             Decimal("25000.00")),
            ("TDR-2026-018", "REVERSE", "Reverse Auction - Fire Safety Equipment, Hubli Depot",
             Decimal("40000.00")),
        ]

        live = []
        for tender_no, kind, title, inc in plan:
            t = db.query(Tender).filter(Tender.tender_no == tender_no).first()
            if t is None:
                print(f"{tender_no}: missing, skipped")
                continue
            print(f"\n{tender_no} ({kind}, {t.district})")
            _open_bids(db, t, now)
            if _ensure_bidder_application(db, t, DEMO_BIDDER, now):
                print(f"  added {DEMO_BIDDER} to the field")
            q = _qualify(db, t, officer, now)
            print(f"  technically qualified: {q}")

            a = (db.query(Auction)
                 .filter(Auction.tender_id == t.id,
                         Auction.status.notin_(("CANCELLED", "COMPLETED"))).first())
            if a is None:
                a = auc.create_auction(
                    db, t, officer, auction_type=kind, title=title,
                    eligibility_rule="ALL_TECHNICALLY_QUALIFIED",
                    rule_params=None, min_increment=inc,
                    opening_reference_price=None,
                    auto_extend_enabled=True, extension_trigger_seconds=120,
                    extension_seconds=120, max_extensions=3,
                    scheduled_start_at=now - timedelta(minutes=5),
                    scheduled_end_at=now + timedelta(hours=RUN_HOURS),
                    notes="Live auction for demonstration.")
                print(f"  created {a.auction_no}")
            eligible = (db.query(AuctionParticipant)
                        .filter(AuctionParticipant.auction_id == a.id,
                                AuctionParticipant.ra_eligible.is_(True)).count())
            print(f"  eligible participants: {eligible}")

            if a.status in ("DRAFT", "SCHEDULED", "READY"):
                auc.schedule_auction(db, a, officer,
                                     scheduled_start_at=now - timedelta(minutes=5),
                                     scheduled_end_at=now + timedelta(hours=RUN_HOURS))
                auc.start_auction(db, a, officer)
                print(f"  started -> {a.status}")
            # Keep plenty of clock on the board for the demonstration.
            if a.status in ("LIVE", "EXTENDED") and (
                    a.effective_end_at is None or a.effective_end_at < now + timedelta(hours=1)):
                a.effective_end_at = now + timedelta(hours=RUN_HOURS)
            n = _opening_bids(db, a, now)
            if n:
                print(f"  opening bids placed: {n}")
            live.append((a, eligible))

        db.commit()

        print("\n" + "=" * 62)
        for a, eligible in live:
            db.refresh(a)
            bids = db.query(AuctionBid).filter(AuctionBid.auction_id == a.id).count()
            lead = auc.current_leading(db, a)
            print(f"{a.auction_no}  {a.auction_type:<7} {a.status:<8} "
                  f"participants={eligible} bids={bids} "
                  f"leading={lead['amount'] if lead else '-'}")
        print("=" * 62)
    finally:
        db.close()


def status() -> None:
    db = SessionLocal()
    try:
        rows = db.query(Auction).order_by(Auction.auction_no.asc()).all()
        if not rows:
            print("No auctions.")
            return
        now = utcnow()
        for a in rows:
            bids = db.query(AuctionBid).filter(AuctionBid.auction_id == a.id).count()
            eligible = (db.query(AuctionParticipant)
                        .filter(AuctionParticipant.auction_id == a.id,
                                AuctionParticipant.ra_eligible.is_(True)).count())
            lead = auc.current_leading(db, a)
            left = ""
            if a.effective_end_at:
                secs = int((a.effective_end_at - now).total_seconds())
                left = f" ends_in={secs // 3600}h{(secs % 3600) // 60:02d}m" if secs > 0 else " ENDED"
            print(f"{a.auction_no}  {a.tender.tender_no}  {a.auction_type:<7} {a.status:<8} "
                  f"participants={eligible} bids={bids} "
                  f"leading={lead['amount'] if lead else '-'}{left}")
    finally:
        db.close()


if __name__ == "__main__":
    import sys

    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "status":
        status()
    elif arg == "rebuild":
        build(rebuild=True)
    else:
        build()
