"""V2.1 enhancements — cross-document identity rules, portal resiliency,
document security guard, citation guard, tender-scoped collusion indicators.

Adopted from the BidVerify-AI reference (key features only), adapted to the
GeM-Sentinel doctrine: deterministic engine decides, AI explains, humans act.
"""
import time
from types import SimpleNamespace

import pytest
from conftest import H, login, open_bids


@pytest.fixture(autouse=True)
def _bids_open(client, seeded):
    """Evaluation-scoped endpoints assume bids are open (post-deadline)."""
    open_bids(client)

ADMIN = ("admin@cpcl.gov.in", "Admin@1234")
PROC_OFFICER = ("procurement.belagavi@cpcl.gov.in", "Officer@1234")
RAMESH = ("ramesh@bidder.in", "Bidder@1234")


# =========================== rule engine: identity =========================

def test_gstin_checksum_strict():
    from app.rules_engine import validate_gstin_checksum
    assert validate_gstin_checksum("33AAACC1206D1ZN")[0] is True
    assert validate_gstin_checksum("27AAACR5055K1Z7")[0] is True
    assert validate_gstin_checksum("33AAACC1206D1ZM")[0] is False  # typo
    assert validate_gstin_checksum("33AAACC1206D1Z")[0] is False   # short
    assert validate_gstin_checksum("XXAAACC1206D1ZN")[0] is False  # bad state


def _fake_rule(ct, source="PROFILE", cv=""):
    return SimpleNamespace(condition_type=ct, source=source, condition_value=cv)


def _ctx_with(profile, sources=None):
    from datetime import date
    return {
        "profile": profile,
        "consents": {"ALL"},
        "documents": [],
        "sources": sources or {},
        "identifiers": {},
        "local_blacklist": [],
        "today": date.today(),
    }


def test_gst_pan_embedded_match_rule():
    from app.rules_engine import _eval_condition
    ok = _ctx_with(SimpleNamespace(gstin_plain="29AAAPS5678K1Z2", pan_plain="AAAPS5678K", state="Karnataka"))
    res, msg, _ = _eval_condition(_fake_rule("gst_pan_embedded_match"), ok)
    assert res == "PASS", msg
    bad = _ctx_with(SimpleNamespace(gstin_plain="29AAAPS5678K1Z2", pan_plain="AAQPS5678K", state="Karnataka"))
    res2, msg2, _ = _eval_condition(_fake_rule("gst_pan_embedded_match"), bad)
    assert res2 == "FAIL", msg2
    missing = _ctx_with(SimpleNamespace(gstin_plain=None, pan_plain="AAAPS5678K"))
    assert _eval_condition(_fake_rule("gst_pan_embedded_match"), missing)[0] == "UNABLE_TO_VERIFY"


def test_gstin_checksum_rule():
    from app.rules_engine import _eval_condition
    good = _ctx_with(SimpleNamespace(gstin_plain="33AAACC1206D1ZN", pan_plain="AAACC1206D"))
    assert _eval_condition(_fake_rule("gstin_checksum_valid"), good)[0] == "PASS"
    bad = _ctx_with(SimpleNamespace(gstin_plain="33AAACC1206D1ZM", pan_plain="AAACC1206D"))
    assert _eval_condition(_fake_rule("gstin_checksum_valid"), bad)[0] == "FAIL"
    none = _ctx_with(SimpleNamespace(gstin_plain=None))
    assert _eval_condition(_fake_rule("gstin_checksum_valid"), none)[0] == "UNABLE_TO_VERIFY"


def test_gst_state_match_rule():
    from app.rules_engine import _eval_condition
    good = _ctx_with(SimpleNamespace(gstin_plain="29AAAPS5678K1Z2", state="Karnataka"))
    assert _eval_condition(_fake_rule("gst_state_match"), good)[0] == "PASS"
    bad = _ctx_with(SimpleNamespace(gstin_plain="29AAAPS5678K1Z2", state="Delhi"))
    assert _eval_condition(_fake_rule("gst_state_match"), bad)[0] == "FAIL"
    badstate = _ctx_with(SimpleNamespace(gstin_plain="99AAAPS5678K1Z2", state="Karnataka"))
    assert _eval_condition(_fake_rule("gst_state_match"), badstate)[0] == "FAIL"


def test_cross_doc_name_consistency_rule():
    from app.rules_engine import _eval_condition
    prof = SimpleNamespace(company_name="Ramesh Traders Pvt Ltd")
    good_sources = {
        "GST": {"status": "VALID", "data": {"legal_name": "Ramesh Traders Pvt Ltd"}, "message": ""},
        "UDYAM": {"status": "VALID", "data": {"legal_name": "Ramesh Traders"}, "message": ""},
    }
    res, msg, _ = _eval_condition(_fake_rule("cross_doc_name_consistency"), _ctx_with(prof, good_sources))
    assert res == "PASS", msg

    bad_sources = {
        "GST": {"status": "VALID", "data": {"legal_name": "Totally Different Corp"}, "message": ""},
        "UDYAM": {"status": "VALID", "data": {"legal_name": "Ramesh Traders"}, "message": ""},
    }
    assert _eval_condition(_fake_rule("cross_doc_name_consistency"), _ctx_with(prof, bad_sources))[0] == "FAIL"

    unable_sources = {"GST": {"status": "UNAVAILABLE", "data": {}, "message": "down"}, "UDYAM": {}}
    assert _eval_condition(_fake_rule("cross_doc_name_consistency"), _ctx_with(prof, unable_sources))[0] == "UNABLE_TO_VERIFY"


# =============================== resiliency ================================

def test_circuit_breaker_state_machine():
    from app.services.resiliency import CircuitBreaker
    b = CircuitBreaker("TEST", failure_threshold=3, recovery_timeout=0.2)
    assert b.allow() is True
    for _ in range(3):
        assert b.allow() is True
        b.report_failure()
    assert b.state == CircuitBreaker.OPEN
    assert b.allow() is False  # fail fast
    time.sleep(0.25)
    assert b.allow() is True   # half-open probe
    b.report_success()
    assert b.state == CircuitBreaker.CLOSED
    st = b.status()
    assert st["source"] == "TEST" and st["state"] == "CLOSED"


def test_backoff_respects_retry_after():
    from app.services.resiliency import backoff_delay
    for _ in range(50):
        d = backoff_delay(0, retry_after=2.0)
        assert d >= 2.0
    for _ in range(50):
        d = backoff_delay(0)
        assert 0 <= d <= 1.0  # base 0.5 * 2^0, full jitter
    d = backoff_delay(10)  # capped
    assert d <= 8.0


def test_swr_store_ttl():
    from app.services.resiliency import SwrStore
    s = SwrStore(ttl_seconds=1)
    s.put("GST", "abc", data={"k": 1}, status="VALID")
    got = s.get("GST", "abc")
    assert got and got["data"] == {"k": 1} and got["age_hours"] == 0.0
    time.sleep(1.2)
    assert s.get("GST", "abc") is None
    s.clear()


def test_fetch_source_stale_snapshot_and_circuit(monkeypatch):
    import httpx
    from app.core.db import SessionLocal
    from app.services import portal, resiliency
    resiliency.reset_resiliency()
    # prime the SWR snapshot
    resiliency.swr_store.put("UDYAM", "UDYAM-KA-01-0000001", data={"legal_name": "Old Co"}, status="VALID")
    monkeypatch.setattr(portal.settings, "MOCK_PORTAL_URL", "http://127.0.0.1:9")  # dead port
    db = SessionLocal()
    try:
        # 1st failure: live call fails, stale snapshot attached (reference only)
        r = portal.fetch_source(db, "UDYAM", identifier="UDYAM-KA-01-0000001")
        assert r.status == "UNAVAILABLE"
        assert r.data.get("_stale") is True
        assert "stale snapshot" in r.message
        # keep failing until the breaker opens
        for _ in range(6):
            r = portal.fetch_source(db, "UDYAM", identifier="UDYAM-KA-01-0000001")
        assert "circuit breaker OPEN" in r.message
        db.rollback()
    finally:
        db.close()
    resiliency.reset_resiliency()


# ============================ document guard ================================

def test_doc_guard_magic_bytes():
    from app.services.doc_guard import assess_upload
    pdf = b"%PDF-1.7\n1 0 obj\nendobj\ntrailer\n<<>>\n%%EOF"
    assert assess_upload("doc.pdf", pdf).ok
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    assert assess_upload("doc.png", png).ok
    # renamed executable disguised as PDF
    assert not assess_upload("doc.pdf", png).ok
    # unknown binary
    assert not assess_upload("shell.exe", b"MZ\x90\x00" + b"\x00" * 64).ok
    # oversized
    assert not assess_upload("big.pdf", b"%PDF-" + b"x" * (11 * 1024 * 1024)).ok


def test_doc_guard_pdf_exploit_flags():
    from app.services.doc_guard import assess_upload
    evil = b"%PDF-1.7\n<< /JS (this.app) >> /Launch /OpenAction"
    rep = assess_upload("evil.pdf", evil)
    assert rep.ok and rep.suspicious
    assert "embedded_javascript" in rep.suspicious_indicators
    assert "launch_action" in rep.suspicious_indicators
    clean = b"%PDF-1.7\n1 0 obj\n<< /Type /Page >>\nendobj"
    assert assess_upload("clean.pdf", clean).suspicious is False


def test_pii_sanitizer():
    from app.services.doc_guard import sanitize_pii, sanitize_payload
    text = ("PAN AAAPS5678K, GSTIN 29AAAPS5678K1Z2, phone +91 9845012345, "
            "email ramesh@bidder.in, aadhaar 1234 5678 9012")
    out, n = sanitize_pii(text)
    assert "AAAPS5678K" not in out
    assert "29AAAPS5678K1Z2" not in out
    assert "9845012345" not in out
    assert "ramesh@bidder.in" not in out
    assert "9012" in out  # aadhaar keeps last 4
    assert n > 0
    payload = {"name": "X", "pan": "AAAPS5678K", "list": ["9845012345"]}
    p = sanitize_payload(payload)
    assert "AAAPS5678K" not in p["pan"] and "9845012345" not in p["list"][0]


# ============================ citation guard ================================

def test_citation_guard_removes_unverifiable():
    from app.ai_engine.citation_guard import sanitize_explanation
    evaluation = {"rule_results": [{"label": "GST registration active", "result": "PASS"}]}
    text = ("Verification complete. Under Rule 4.7 of the Procurement Manual the bid fails. "
            "As per GFR 144 and CVC guidelines, the bidder is reviewed. "
            "Per Rule Zebra-99 the tender is cancelled.")
    cleaned, meta = sanitize_explanation(text, evaluation, model_id="gemini:test")
    # the *body* must be clean; removed citations may be listed in the guard note
    body = cleaned.split(" (Citation guard:")[0]
    assert "Zebra-99" not in body
    assert "Procurement Manual" not in body
    assert "GFR 144" in body and "CVC" in body
    assert meta["mode"] == "applied" and meta["sentences_removed"] == 2

    # template (deterministic) explanation passes clean
    t2, m2 = sanitize_explanation(
        "Deterministic verification completed. Compliance score: 80/100. "
        "Failed mandatory checks — GST registration active: expired.",
        evaluation, model_id="deterministic-template-1.0")
    assert m2["sentences_removed"] == 0

    # a citation matching a real rule label is kept
    t3, m3 = sanitize_explanation(
        "As Rule GST registration active states, the check failed.",
        evaluation, model_id="gemini:test")
    assert "GST registration active" in t3


# ====================== collusion indicator (service+API) ===================

def _make_bidder(db, email, phone, name, domain):
    from app.core.security import hash_password
    from app.models import BidderProfile, User
    u = User(email=email, password_hash=hash_password("Test@12345"),
             full_name=name, role="BIDDER", status="ACTIVE", is_verified=True)
    db.add(u)
    db.flush()
    p = BidderProfile(user_id=u.id, company_name=name,
                      registered_address=f"12 Test Street, Belagavi", pincode="590001",
                      state="Karnataka", authorized_rep_phone=phone,
                      authorized_rep_email=f"contact@{domain}")
    db.add(p)
    db.flush()
    return u


def _make_application(db, tender_id, bidder_id, app_no):
    from app.models import Application
    a = Application(tender_id=tender_id, bidder_user_id=bidder_id,
                    application_no=app_no, status="Submitted")
    db.add(a)
    db.flush()
    return a


def test_collusion_detects_shared_phone_and_persists(client, seeded):
    from app.core.db import SessionLocal
    from app.models import Tender
    tok = login(client, *ADMIN)
    db = SessionLocal()
    try:
        tender = db.query(Tender).filter(Tender.tender_no == "TDR-2026-002").first()
        assert tender is not None
        u1 = _make_bidder(db, "collud1@testmail.in", "9876500001", "Alpha Supply", "sharedcorp.co.in")
        u2 = _make_bidder(db, "collud2@testmail.in", "9876500001", "Beta Supply", "sharedcorp.co.in")
        u3 = _make_bidder(db, "collud3@testmail.in", "9876500099", "Gamma Supply", "othermail.co.in")
        _make_application(db, tender.id, u1.id, "APP-COL-001")
        _make_application(db, tender.id, u2.id, "APP-COL-002")
        _make_application(db, tender.id, u3.id, "APP-COL-003")
        db.commit()
        tender_id = tender.id
    finally:
        db.close()

    r = client.get(f"/collusion/tenders/{tender_id}", headers=H(tok))
    assert r.status_code == 200, r.text
    data = r.json()
    signals = {(i["signal"]) for i in data["indicators"]}
    # shared phone + shared custom domain between Alpha & Beta
    assert "shared_phone" in signals
    assert "shared_email_domain" in signals
    pair = next(i for i in data["indicators"] if i["signal"] == "shared_phone")
    assert {"Alpha Supply", "Beta Supply"} == {pair["bidder_a"], pair["bidder_b"]}
    # no leak of raw PII in evidence
    assert "9876500001" not in str(data["indicators"])
    assert data["risk_level"] in ("MEDIUM", "HIGH", "LOW")
    assert "advisory" in data["note"].lower()

    # persisted + history + audited
    h = client.get(f"/collusion/tenders/{tender_id}/history", headers=H(tok)).json()
    assert len(h["runs"]) >= 1 and h["runs"][0]["bidders_analyzed"] >= 3
    ev = client.get("/admin/audit-logs", headers=H(tok),
                    params={"action": "collusion_analysis_run"}).json()["logs"]
    assert ev and ev[0]["payload"]["advisory_only"] is True


def test_collusion_no_false_positive_on_seeded_tender(client, seeded):
    from app.core.db import SessionLocal
    from app.models import Tender
    tok = login(client, *PROC_OFFICER)
    db = SessionLocal()
    try:
        tid = db.query(Tender).filter(Tender.tender_no == "TDR-2026-001").first().id
    finally:
        db.close()
    r = client.get(f"/collusion/tenders/{tid}", headers=H(tok))
    assert r.status_code == 200
    data = r.json()
    # seeded bidders have distinct phones/emails/addresses
    assert data["indicators"] == [], data["indicators"]
    assert data["risk_level"] == "NONE"
    # common @bidder.in domain (4 bidders) must be excluded, not flagged
    assert any(e["signal"] == "shared_email_domain" for e in data.get("excluded_common", []))


def test_collusion_single_bidder_not_applicable(client, seeded):
    from app.core.db import SessionLocal
    from app.models import Tender
    tok = login(client, *ADMIN)
    db = SessionLocal()
    try:
        from app.models import User
        admin = db.query(User).filter(User.email == ADMIN[0]).first()
        t = Tender(tender_no="TDR-COLL-SINGLE", title="Single bidder tender",
                   department="Petroleum", district="Belagavi", state="Karnataka",
                   jurisdiction_code="KARNATAKA-BELAGAVI", status="PUBLISHED",
                   created_by=admin.id)
        db.add(t)
        db.flush()
        u = _make_bidder(db, "single@testmail.in", "9811100002", "Solo Supply", "solomail.co.in")
        _make_application(db, t.id, u.id, "APP-COL-SINGLE-1")
        db.commit()
        tid = t.id
    finally:
        db.close()
    r = client.get(f"/collusion/tenders/{tid}", headers=H(tok))
    assert r.status_code == 200
    assert r.json()["risk_level"] == "NONE"
    assert "not applicable" in r.json()["note"].lower()


def test_collusion_role_gating(client, seeded):
    from app.core.db import SessionLocal
    from app.models import Tender
    tok_bidder = login(client, *RAMESH)
    db = SessionLocal()
    try:
        tid = db.query(Tender).filter(Tender.tender_no == "TDR-2026-001").first().id
    finally:
        db.close()
    r = client.get(f"/collusion/tenders/{tid}", headers=H(tok_bidder))
    assert r.status_code == 403


def test_admin_source_resiliency_endpoint(client, seeded):
    from app.services import resiliency
    resiliency.reset_resiliency()
    tok = login(client, *ADMIN)
    r = client.get("/admin/source-resiliency", headers=H(tok))
    assert r.status_code == 200
    srcs = {s["source"]: s for s in r.json()["sources"]}
    assert "GST" in srcs and srcs["GST"]["state"] == "CLOSED"
    # bidder must not access
    tok_b = login(client, *RAMESH)
    assert client.get("/admin/source-resiliency", headers=H(tok_b)).status_code == 403
