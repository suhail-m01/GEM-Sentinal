"""Additive demo extension — district sealing showcase + a fuller portal.

Run AFTER a fresh reseed and AFTER the last test pass (the test suite's
``open_bids`` helper force-opens every tender, which unseals t1's
sealed-bid demo state):

    cd backend && .venv/bin/python -c "from app.seed_demo_districts import extend; extend()"

Legacy additive extension. The final main seed now creates TDR-2026-012..017 directly; therefore this is normally a no-op on the final build.

What it does
  1. Restores t1 (TDR-2026-001, Belagavi) sealed-bid demo deadline to
     +3 days 18:00 IST — the demo's opening step stays the audited admin
     force-open (POST /admin/tenders/{id}/open-bids).
  2. Adds six tenders in the two demo districts ONLY:
       Belagavi (sealed district):  TDR-2026-012, TDR-2026-013 PUBLISHED
                                    (sealing on, future deadlines),
                                    TDR-2026-016 CLOSED (history)
       Hubli (unsealed district):   TDR-2026-014, TDR-2026-015 PUBLISHED
                                    (sealing disabled per-tender),
                                    TDR-2026-017 CLOSED (history)
     Each gets the full pipeline role assignments and the standard
     statutory compliance rule set.
  3. Adds applications from the existing demo bidders (documents
     auto-attached, like the main seed):
       - sealed Belagavi queue shows anonymized "Sealed" aggregate rows
         (identity hidden until the audited opening);
       - Hubli rows are open, and 014/015 apps are pre-verified so the
         list shows scores and risk immediately.
"""
from datetime import date, datetime, timedelta, timezone, time as _t

import zoneinfo

from .core.db import SessionLocal, utcnow
from .models import (
    Application,
    ApplicationDocument,
    ApplicationStatusHistory,
    Document,
    Tender,
    TenderAssignment,
    TenderRule,
    User,
)

IST = zoneinfo.ZoneInfo("Asia/Kolkata")

# standard statutory rule set (mirrors the first 9 rows of t1 in the main seed)
_STD_RULES = [
    ("gst_active", "GST registration active", "GST", True, "is_active", None, 20, "FAIL", False, False, "STATUTORY", "GeM GTC §2.1"),
    ("udyam_active", "Udyam/MSME registration active", "UDYAM", True, "is_active", None, 15, "FAIL", False, False, "STATUTORY", "GeM GTC §2.4"),
    ("min_turnover", "Minimum annual turnover ₹2.0 Cr", "PROFILE", True, "min_turnover", "20000000", 15, "FAIL", False, False, "STATUTORY", "Eligibility §3.2"),
    ("epfo_compliant", "EPFO compliance current", "EPFO", True, "is_active", None, 10, "FAIL", False, False, "STATUTORY", "Labour compliance §6.1"),
    ("gst_filing_current", "GST filings current", "GST", True, "filing_current", None, 10, "FAIL", False, False, "STATUTORY", "GeM GTC §2.1(b)"),
    ("legal_name_match", "Legal name consistent (profile vs Udyam)", "UDYAM", True, "name_match", "profile.company_name:UDYAM.legal_name", 10, "FAIL", False, False, "STATUTORY", "ATC-1"),
    ("gst_document", "GST certificate on file", "DOCUMENT", True, "has_document", "GST_CERT", 10, "FAIL", False, False, "STATUTORY", "Documents §4.1"),
    ("udyam_document", "Udyam certificate on file", "DOCUMENT", True, "has_document", "UDYAM_CERT", 10, "FAIL", False, False, "STATUTORY", "Documents §4.1"),
    ("not_blacklisted", "Not blacklisted/debarred", "BLACKLIST", True, "not_blacklisted", None, 100, "FAIL", False, True, "STATUTORY", "Integrity §7.1"),
]

_TENDERS = [
    # (tender_no, district, org, status, sealing, deadline offset days, close offset days, title, category, psc, v_min, v_max)
    ("TDR-2026-012", "Belagavi", "CPCL", "PUBLISHED", True, 3, 3,
     "Supply of Laboratory Chemicals & Reagents — CPCL Belagavi", "Chemicals & Reagents", "Lab Chemicals", 5000000, 30000000),
    ("TDR-2026-013", "Belagavi", "CPCL", "PUBLISHED", True, 5, 5,
     "Supply of Industrial Safety Footwear & Hand Tools — CPCL Belagavi", "Safety Equipment", "PPE", 3000000, 18000000),
    ("TDR-2026-014", "Hubli", "IOC", "PUBLISHED", False, 1, 3,
     "Supply of Field Communication Equipment — IOC Hubli", "Communication Systems", "Radio & Comms", 8000000, 42000000),
    ("TDR-2026-015", "Hubli", "IOC", "PUBLISHED", False, 1, 4,
     "Supply of Workshop Power Tools & Spares — IOC Hubli", "Workshop Equipment", "Power Tools", 4000000, 22000000),
    ("TDR-2026-016", "Belagavi", "CPCL", "CLOSED", True, -6, -6,
     "Supply of Boiler Water Treatment Chemicals — CPCL Belagavi", "Chemicals & Reagents", "Water Treatment", 6000000, 35000000),
    ("TDR-2026-017", "Hubli", "IOC", "CLOSED", False, -4, -4,
     "Supply of Welding Consumables & Accessories — IOC Hubli", "Fabrication Materials", "Welding", 2500000, 14000000),
]

# (tender_no, [bidder emails])
_APPS = [
    ("TDR-2026-012", ["ramesh@bidder.in", "bharat@bidder.in", "delta@bidder.in"]),
    ("TDR-2026-013", ["sita@bidder.in", "vikram@bidder.in", "meena@bidder.in"]),
    ("TDR-2026-014", ["ramesh@bidder.in", "bharat@bidder.in", "arjun@bidder.in", "chetan@bidder.in"]),
    ("TDR-2026-015", ["delta@bidder.in", "meena@bidder.in", "sita@bidder.in"]),
    ("TDR-2026-016", ["bharat@bidder.in", "delta@bidder.in"]),
    ("TDR-2026-017", ["ramesh@bidder.in", "arjun@bidder.in"]),
]

_VERIFY_TENDERS = {"TDR-2026-014", "TDR-2026-015"}  # unsealed district: pre-verified, scored rows


def _at(days_offset: int, clock: _t) -> datetime:
    ist = datetime.combine(date.today() + timedelta(days=days_offset), clock, tzinfo=IST)
    return ist.astimezone(timezone.utc).replace(tzinfo=None)


def _mk_tender(db, row, orgs, admin, l1s, l2, today):
    tender_no, district, org, status, sealing, dl_days, close_days, title, cat, psc, vmin, vmax = row
    # Both districts share the existing Oil & Gas jurisdiction codes (IOC's
    # Hubli tenders are routed through KARNATAKA-HUBLI-OILGAS, like t2).
    jur_code = "KARNATAKA-BELAGAVI-OILGAS" if district == "Belagavi" else "KARNATAKA-HUBLI-OILGAS"
    t = Tender(
        tender_no=tender_no, title=title,
        description=f"{title}. Demonstration tender for the {district} district portal.",
        department="Oil & Natural Gas", organization=org, category=cat,
        product_service_category=psc, district=district, state="Karnataka",
        jurisdiction_code=jur_code, value_min=vmin, value_max=vmax,
        open_date=today - timedelta(days=10), close_date=today + timedelta(days=close_days),
        bid_deadline=_at(dl_days, _t(18, 0)),
        status=status, msme_preference=True, created_by=admin.id,
        organization_id=orgs[org].id, procurement_method="GEM_BID",
        sealing_enabled=sealing,
    )
    db.add(t)
    db.flush()

    for i, r in enumerate(_STD_RULES):
        db.add(TenderRule(
            tender_id=t.id, rule_key=r[0], label=r[1], source=r[2], mandatory=r[3],
            condition_type=r[4], condition_value=r[5], weight=r[6],
            failure_action=r[7], allows_manual_review=r[8],
            hard_disqualification=r[9], sort_order=i, version=1,
            compliance_layer=r[10], clause_ref=r[11],
        ))

    for u in (l1s.get(district), l2):
        if u is not None:
            db.add(TenderAssignment(tender_id=t.id, user_id=u.id,
                                    workflow_role="PROCUREMENT_OFFICER" if u is l1s.get(district) else "FINANCE_EVALUATOR",
                                    assigned_by=admin.id))
    return t


def extend() -> None:
    db = SessionLocal()
    try:
        if db.query(Tender).filter(Tender.tender_no == "TDR-2026-012").first():
            print("Final demo tenders already exist in the main seed (TDR-2026-012..017) — nothing to do.")
            return

        admin = db.query(User).filter(User.role == "ADMIN").first()
        today = date.today()
        from .models import ProcuringOrganization
        t1 = db.query(Tender).filter(Tender.tender_no == "TDR-2026-001").first()
        orgs = {o.short_code: o for o in db.query(ProcuringOrganization).all()}
        l1s = {
            "Belagavi": db.query(User).filter(User.email == "procurement.belagavi@cpcl.gov.in").first(),
            "Hubli": db.query(User).filter(User.email == "procurement.hubli@cpcl.gov.in").first(),
        }
        l2 = db.query(User).filter(User.email == "finance.karnataka@cpcl.gov.in").first()
        pipeline_roles = [
            "bidopening.karnataka@cpcl.gov.in", "technical.karnataka@cpcl.gov.in",
            "committee.chair@cpcl.gov.in", "committee.member@cpcl.gov.in",
            "cfa.karnataka@cpcl.gov.in", "contract.karnataka@cpcl.gov.in",
            "contractexecution.karnataka@cpcl.gov.in", "inspection.karnataka@cpcl.gov.in",
            "payment.karnataka@cpcl.gov.in",
        ]
        role_users = {u.email: u for u in db.query(User).filter(User.email.in_(pipeline_roles)).all()}
        # workflow role per email (mirrors the main seed assignment block)
        email_role = {
            "bidopening.karnataka@cpcl.gov.in": "BID_OPENING_OFFICER",
            "technical.karnataka@cpcl.gov.in": "TECHNICAL_EVALUATOR",
            "committee.chair@cpcl.gov.in": "COMMITTEE_CHAIR",
            "committee.member@cpcl.gov.in": "COMMITTEE_MEMBER",
            "cfa.karnataka@cpcl.gov.in": "CFA",
            "contract.karnataka@cpcl.gov.in": "CONTRACT_MANAGER",
            "contractexecution.karnataka@cpcl.gov.in": "CONTRACT_OFFICER",
            "inspection.karnataka@cpcl.gov.in": "INSPECTION_OFFICER",
            "payment.karnataka@cpcl.gov.in": "PAYMENT_OFFICER",
        }
        bidders = {u.email: u for u in
                   db.query(User).filter(User.email.like("%@bidder.in"), User.role == "BIDDER").all()}

        # ---- 1. restore t1 sealed demo deadline (+3 days 18:00 IST) ----
        t1.bid_deadline = _at(3, _t(18, 0))
        t1.sealing_enabled = True

        # ---- 2. new tenders (Belagavi + Hubli only) ----
        new_tenders: dict[str, Tender] = {}
        for row in _TENDERS:
            new_tenders[row[0]] = _mk_tender(db, row, orgs, admin, l1s, l2, today)
        for t in new_tenders.values():
            for email, u in role_users.items():
                db.add(TenderAssignment(tender_id=t.id, user_id=u.id,
                                        workflow_role=email_role[email], assigned_by=admin.id))
        db.flush()

        # ---- 3. applications (documents auto-attached) ----
        seq = int(db.query(Application.application_no)
                  .filter(Application.application_no.like("APP-2026-%"))
                  .order_by(Application.application_no.desc()).first()[0][-6:]) + 1
        to_verify: list[Application] = []
        for tender_no, emails in _APPS:
            t = new_tenders[tender_no]
            district = t.district
            l1 = l1s[district]
            for email in emails:
                b = bidders[email]
                submitted = utcnow()
                a = Application(
                    tender_id=t.id, bidder_user_id=b.id, application_no=f"APP-2026-{seq:06d}",
                    status="AI Verification Not Run", eligibility_result=None,
                    declaration_text=("I hereby declare that the information furnished in this application and the "
                                      "documents provided are true, complete and accurate to the best of my knowledge. "
                                      "I understand that false declaration may lead to disqualification and legal action."),
                    declaration_at=submitted, submitted_at=submitted, snapshot_frozen_at=submitted,
                    assigned_l1_id=l1.id, assigned_l2_id=l2.id,
                    routing_method="EXACT_JURISDICTION", routing_fallback=False,
                    sla_due_at=utcnow() + timedelta(days=5),
                )
                db.add(a)
                db.flush()
                db.add(ApplicationStatusHistory(application_id=a.id, status="Submitted",
                                                actor_id=b.id, actor_role="BIDDER", remarks="Application submitted"))
                db.add(ApplicationStatusHistory(application_id=a.id, status="AI Verification Not Run",
                                                actor_id=None, actor_role="SYSTEM",
                                                remarks=f"Routed to L1 (EXACT_JURISDICTION)"))
                for d in db.query(Document).filter(Document.user_id == b.id).all():
                    db.add(ApplicationDocument(
                        application_id=a.id, document_id=d.id, name=d.name, doc_type=d.doc_type,
                        file_path=d.file_path, purpose="ORIGINAL", uploaded_by=b.id,
                    ))
                if t.tender_no in _VERIFY_TENDERS:
                    to_verify.append(a)
                seq += 1
        db.commit()

        # ---- 4. pre-verify the unsealed (Hubli) PUBLISHED apps ----
        from .services.verification import run_verification
        hubli_l1 = l1s["Hubli"]
        for a in to_verify:
            report = run_verification(db, a, hubli_l1, language="en")
            db.commit()
            print(f"  verified {a.application_no} ({a.tender.tender_no}, "
                  f"{a.bidder_user.full_name}): score={report.compliance_score} "
                  f"risk={report.risk_level} result={report.eligibility_result}")

        print("Demo district extension complete:")
        print("  Belagavi (sealed):  TDR-2026-001 (restored), TDR-2026-012, TDR-2026-013 PUBLISHED + sealed, TDR-2026-016 CLOSED")
        print("  Hubli (unsealed):   TDR-2026-014, TDR-2026-015 PUBLISHED + open (pre-verified), TDR-2026-017 CLOSED")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _backfill_financial_quotes(db, now) -> int:
    """Give every tender a price requirement and every application a sealed quote.

    A tender application without a quoted price is not a real bid, so the demo
    data must not contain one: each tender is set to require a financial bid,
    and every application that lacks an active envelope gets one sealed through
    the same service path (and therefore the same canonical hash) a live
    submission would use.

    Amounts are derived deterministically from the tender's estimated value and
    the bidder id, so the spread is stable across restores and the L1/L2/L3
    price ranking is meaningful rather than random.

    ponytail: deterministic derivation beats storing a fixture table - no extra
    data file to keep in sync. If a specific scripted price is ever needed for a
    scene, override that one application after calling this.
    """
    from decimal import Decimal
    from .models import FinancialBid
    from .services import financial as fin_svc

    for t in db.query(Tender).all():
        t.financial_bid_required = True
    db.flush()

    sealed = 0
    for app in db.query(Application).all():
        active = (db.query(FinancialBid)
                  .filter(FinancialBid.application_id == app.id,
                          FinancialBid.status.in_(("SEALED", "OPENED")))
                  .first())
        if active is not None:
            continue
        tender = app.tender
        # Estimated value anchors the quote; bidders land within -8%..+6% of it.
        anchor = tender.value_max or tender.value_min or 5_000_000
        spread = (int(app.bidder_user_id.replace("-", "")[:8], 16) % 15) - 8  # -8..+6
        base = (Decimal(str(anchor)) * (Decimal(100 + spread) / Decimal(100))
                ).quantize(Decimal("0.01"))
        tax = (base * Decimal("0.18")).quantize(Decimal("0.01"))
        quote = fin_svc.validate_financial_quote(
            base=base, tax=tax, other=Decimal("0.00"),
            currency="INR", price_valid_days=90)
        fin_svc.create_financial_bid(
            db, app=app, tender=tender, user=app.bidder_user, quote=quote,
            commercial_declaration=(
                "Price is firm, inclusive of the quoted taxes, and held valid "
                "for 90 days from the date of submission."),
            submitted_at=now, commit=False)
        sealed += 1
    db.flush()
    return sealed


def align_demo_state() -> None:
    """Align the live demo data with the district story.

    Call this after any test pass (the suite re-seeds per module and its
    ``open_bids`` helper force-opens every tender, unsealing the sealed
    district), or whenever the demo state drifts:

        .venv/bin/python -m app.seed_demo_districts restore

    1. Belagavi district = SEALED uniformly: every Belagavi tender gets
       sealing ON + one uniform future deadline (+2 days 18:00 IST), so
       all of its bid rows show sealed (identity hidden, BIDS_SEALED).
       The demo's opening step is the audited admin force-open.
    2. Hubli district = UNSEALED uniformly: sealing stays OFF (bids open,
       live AI-verification demo).
    3. Every application's submitted/declared/frozen timestamps are set to
       the real current time.
    """
    db = SessionLocal()
    try:
        now = utcnow()
        deadline = _at(2, _t(18, 0))
        # Tenders hosting a live auction are past bid opening - re-sealing them
        # would break their auction (an auction cannot exist on sealed bids).
        from .seed_demo_auctions import AUCTION_TENDERS

        bel = [t for t in db.query(Tender).filter(Tender.district == "Belagavi").all()
               if t.tender_no not in AUCTION_TENDERS]
        for t in bel:
            t.sealing_enabled = True
            t.force_opened = False
            t.bid_deadline = deadline
        hub = [t for t in db.query(Tender).filter(Tender.district == "Hubli").all()
               if t.tender_no not in AUCTION_TENDERS]
        for t in hub:
            t.sealing_enabled = False
            t.force_opened = False
        # t2 is the live Hubli-verification demo tender: keep it PUBLISHED
        # with a future deadline (its seeded deadline passes today and the
        # scheduler would auto-close it).
        t2 = next((t for t in hub if t.tender_no == "TDR-2026-002"), None)
        if t2 is not None:
            t2.status = "PUBLISHED"
            t2.bid_deadline = _at(1, _t(18, 0))
        # Any PUBLISHED tender whose deadline has already passed would still be
        # listed to bidders (the list filters on close_date) but reject every
        # submission with DEADLINE_PASSED. Push those onto the same future
        # deadline so nothing in the portal is advertised-but-unbiddable.
        stale = [t for t in db.query(Tender).filter(Tender.status == "PUBLISHED").all()
                 if (t.bid_deadline is None or t.bid_deadline <= now)
                 and t.tender_no not in AUCTION_TENDERS]
        for t in stale:
            t.bid_deadline = deadline
            if t.close_date is not None and t.close_date < deadline.date():
                t.close_date = deadline.date()
        db.query(Application).update(
            {"submitted_at": now, "declaration_at": now, "snapshot_frozen_at": now},
            synchronize_session=False,
        )
        db.flush()
        quoted = _backfill_financial_quotes(db, now)
        db.commit()
        print(f"Demo state aligned: {len(bel)} Belagavi tenders sealed "
              f"(uniform deadline {deadline.isoformat()}), {len(hub)} Hubli tenders unsealed; "
              f"application timestamps set to real time ({now.isoformat()}); "
              f"{quoted} application(s) backfilled with a sealed financial quotation; "
              f"{len(stale)} stale deadline(s) pushed forward.")
    finally:
        db.close()


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "restore":
        align_demo_state()
    else:
        extend()
