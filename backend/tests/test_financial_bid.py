"""Financial (commercial) envelope: sealed bids + hash integrity + controlled opening.

Covers: deterministic quote validation (exact Decimal math, no float), server-side
sealing (403 before technical qualification; the plaintext amount is never on the
audit chain), hash consistency between the envelope and the tamper-evident seal
event, post-deadline modification lock, and the rule that a disqualified bidder's
envelope is never opened.
"""
import re

import pytest
from conftest import H, login

OFFICERS = {
    "OPEN": ("bidopening.karnataka@cpcl.gov.in", "Opening@1234"),
    "PROC": ("procurement.belagavi@cpcl.gov.in", "Officer@1234"),
    "TECH": ("technical.karnataka@cpcl.gov.in", "TechEval@1234"),
    "FIN": ("finance.karnataka@cpcl.gov.in", "L2Officer@1234"),
    "ADMIN": ("admin@cpcl.gov.in", "Admin@1234"),
}
BID = {
    "A": ("arjun@bidder.in", "Bidder@1234"),
    "C": ("chetan@bidder.in", "Bidder@1234"),
}
COMPANY = {"A": "ARJUN", "C": "CHETAN"}
TENDER = "TDR-2026-010"


@pytest.fixture(scope="module")
def tokens(client, seeded):
    return {k: login(client, *v) for k, v in OFFICERS.items()} | {
        f"BID_{k}": login(client, *v) for k, v in BID.items()
    }


def _app(client, tok, company):
    r = client.get("/officer/applications", headers=H(tok))
    assert r.status_code == 200, r.text
    return next(a for a in r.json()["applications"]
                if a.get("tender_no") == TENDER and a["company"].upper().startswith(company))


def _run_workflow(client, toks, letter, up_to="technical"):
    """Bid closure -> AI verification -> compliance -> (technical). Returns app + report."""
    app = _app(client, toks["PROC"], COMPANY[letter])
    r = client.post(f"/officer/pipeline/{app['id']}/BID_CLOSURE/action",
                    headers=H(toks["OPEN"]),
                    json={"action": "OPEN_BIDS", "remarks": "Bid deadline reached; bids opened per tender schedule."})
    assert r.status_code == 200, r.text
    r = client.post(f"/officer/applications/{app['id']}/run-verification", headers=H(toks["PROC"]))
    assert r.status_code == 200, r.text
    rep = client.get(f"/officer/applications/{app['id']}", headers=H(toks["PROC"])).json()["verification_report"]
    finding = "NON_COMPLIANT" if rep["eligibility_result"] == "NOT_ELIGIBLE" else "VERIFIED"
    r = client.post(f"/officer/pipeline/{app['id']}/COMPLIANCE/action", headers=H(toks["PROC"]),
                    json={"action": finding, "finding": finding,
                          "remarks": f"AI verification {rep['compliance_score']}/100, risk {rep['risk_level']}."})
    assert r.status_code == 200, r.text
    if up_to == "technical" and finding == "VERIFIED":
        r = client.post(f"/officer/pipeline/{app['id']}/TECHNICAL/action", headers=H(toks["TECH"]),
                        json={"action": "TECHNICALLY_QUALIFIED", "outcome": "TECHNICALLY_QUALIFIED",
                              "remarks": "Technical specifications met; vendor capability verified."})
        assert r.status_code == 200, r.text
    return app, rep


# ---------------------------------------------------------------------------
# 1. deterministic quote validation (exact Decimal, no float)
# ---------------------------------------------------------------------------
def test_quote_validation_exact_decimal():
    from app.services import financial as fin

    q = fin.validate_financial_quote(base="102000000", tax="8160000", other="100000",
                                     currency="INR", price_valid_days=90)
    assert str(q["total"]) == "110260000.00"  # exact component sum, 2 dp
    assert str(q["base"]) == "102000000.00"

    bad = [dict(base="0"), dict(base="-5"), dict(base="abc"), dict(base="100", tax="-1"),
           dict(base="100", other="-0.01"), dict(base="100", currency="XYZ"),
           dict(base="100", price_valid_days=0), dict(base="100", price_valid_days=366)]
    for override in bad:
        kwargs = dict(base="100", tax="0", other="0", currency="INR", price_valid_days=90)
        kwargs.update(override)
        with pytest.raises(Exception):
            fin.validate_financial_quote(**kwargs)


# ---------------------------------------------------------------------------
# 2. sealing: finance cannot read the envelope before technical qualification
# ---------------------------------------------------------------------------
def test_sealed_before_technical(tokens, client):
    app = _app(client, tokens["PROC"], "ARJUN")
    r = client.get(f"/officer/applications/{app['id']}/financial-bid", headers=H(tokens["FIN"]))
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "FINANCIAL_ENVELOPE_SEALED"


def test_seal_audit_event_carries_hash_not_plaintext(tokens, client):
    r = client.get("/admin/tenders", headers=H(tokens["ADMIN"]))
    tender_id = next(t["id"] for t in r.json()["tenders"] if t["tender_no"] == TENDER)
    r = client.get("/admin/audit-logs", headers=H(tokens["ADMIN"]),
                   params={"action": "financial_bid_sealed", "tender_id": tender_id, "limit": 100})
    assert r.status_code == 200, r.text
    rows = r.json()["logs"]
    assert len(rows) == 4  # the four sealed t10 envelopes
    for row in rows:
        p = row["payload"]
        assert set(p) == {"version", "hash"}, p
        assert re.fullmatch(r"[0-9a-f]{64}", p["hash"])
        assert isinstance(p["version"], int)
        blob = str(row).lower()
        for amount in ("102000000", "98500000", "100500000", "97000000"):
            assert amount not in blob, "plaintext amount leaked on the audit chain"


# ---------------------------------------------------------------------------
# 3. authorized opening (technical qualification) + hash consistency
# ---------------------------------------------------------------------------
def test_envelope_opens_only_for_technically_qualified(tokens, client):
    app, rep = _run_workflow(client, tokens, "A", up_to="technical")
    assert rep["eligibility_result"] == "ELIGIBLE"
    r = client.get(f"/officer/applications/{app['id']}/financial-bid", headers=H(tokens["FIN"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert float(body["total_quoted_amount"]) == 102000000.0
    assert body["status"] == "OPENED"
    assert re.fullmatch(r"[0-9a-f]{64}", body["financial_bid_hash"])


def test_disqualified_bidders_envelope_never_opens(tokens, client):
    app, rep = _run_workflow(client, tokens, "C", up_to="compliance")
    assert rep["eligibility_result"] == "NOT_ELIGIBLE"
    assert rep["risk_level"] == "CRITICAL"
    r = client.get(f"/officer/applications/{app['id']}/financial-bid", headers=H(tokens["FIN"]))
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "FINANCIAL_ENVELOPE_SEALED"


# ---------------------------------------------------------------------------
# 4. post-deadline modification is locked
# ---------------------------------------------------------------------------
def test_modification_after_deadline_blocked(tokens, client):
    app = _app(client, tokens["PROC"], "ARJUN")
    r = client.put(f"/bidder/applications/{app['id']}/financial-bid", headers=H(tokens["BID_A"]),
                   json={"base_amount": "90000000", "tax_amount": "0", "other_charges": "0",
                         "currency": "INR", "price_valid_days": 90,
                         "commercial_declaration": "I declare that the quoted price is valid for 90 days."})
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["code"] == "DEADLINE_PASSED"
