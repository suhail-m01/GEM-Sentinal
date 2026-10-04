"""Competitive e-auctions: reverse (bids decrease) + forward (bids increase).

Covers: mode gates in both directions, creation prerequisites (no qualified
bidders, bids not open, auction-already-exists), disqualification exclusion,
live bidding rules (min increment, own-price floor, invalid 422s, disqualified
403), idempotency, pause/resume with reauth, anti-sniping extension,
server-authoritative close + deterministic ranking (L1 is NOT auto-awarded),
CFA result approval, direction-aware financial prefill + L-ranking,
bid-hash chain integrity, audit coverage, report PDF, and RBAC.
"""

import time

import pytest
from conftest import H, login

OFFICERS = {
    "OPEN": ("bidopening.karnataka@cpcl.gov.in", "Opening@1234"),
    "PROC": ("procurement.belagavi@cpcl.gov.in", "Officer@1234"),
    "TECH": ("technical.karnataka@cpcl.gov.in", "TechEval@1234"),
    "FIN": ("finance.karnataka@cpcl.gov.in", "L2Officer@1234"),
    "CFA": ("cfa.karnataka@cpcl.gov.in", "CFA@1234"),
    "ADMIN": ("admin@cpcl.gov.in", "Admin@1234"),
}
BID = {
    "A": ("arjun@bidder.in", "Bidder@1234"),
    "B": ("bharat@bidder.in", "Bidder@1234"),
    "C": ("chetan@bidder.in", "Bidder@1234"),
    "D": ("delta@bidder.in", "Bidder@1234"),
}
COMPANY = {"A": "ARJUN", "B": "BHARAT", "C": "CHETAN", "D": "DELTA"}

STATE = {}  # module-scoped carry-over between ordered tests


@pytest.fixture(scope="module")
def tokens(client, seeded):
    return {k: login(client, *v) for k, v in OFFICERS.items()} | {
        f"BID_{k}": login(client, *v) for k, v in BID.items()
    }


def _tender(client, toks, no):
    r = client.get("/admin/tenders", headers=H(toks["ADMIN"]))
    assert r.status_code == 200, r.text
    return next(t for t in r.json()["tenders"] if t["tender_no"] == no)


def _app(client, toks, tender_no, company):
    r = client.get("/officer/applications", headers=H(toks["PROC"]))
    assert r.status_code == 200, r.text
    return next(a for a in r.json()["applications"]
                if a.get("tender_no") == tender_no and a["company"].upper().startswith(company))


def _reauth(client, toks, key):
    r = client.post("/auth/reauth", headers=H(toks[key]), json={"password": OFFICERS[key][1]})
    assert r.status_code == 200, r.text
    return r.json()["reauth_token"]


def _workflow(client, toks, tender_no, letters):
    """Drive each bidder through closure -> verification -> compliance -> technical."""
    out = {}
    for k in letters:
        app = _app(client, toks, tender_no, COMPANY[k])
        r = client.post(f"/officer/pipeline/{app['id']}/BID_CLOSURE/action", headers=H(toks["OPEN"]),
                        json={"action": "OPEN_BIDS", "remarks": "Bid deadline reached; bids opened per tender schedule."})
        assert r.status_code == 200, (k, r.text)
        r = client.post(f"/officer/applications/{app['id']}/run-verification", headers=H(toks["PROC"]))
        assert r.status_code == 200, (k, r.text)
        rep = client.get(f"/officer/applications/{app['id']}", headers=H(toks["PROC"])).json()["verification_report"]
        if k == "C":
            assert rep["eligibility_result"] == "NOT_ELIGIBLE", rep
        else:
            assert rep["eligibility_result"] == "ELIGIBLE", (k, rep)
        finding = "NON_COMPLIANT" if k == "C" else "VERIFIED"
        r = client.post(f"/officer/pipeline/{app['id']}/COMPLIANCE/action", headers=H(toks["PROC"]),
                        json={"action": finding, "finding": finding,
                              "remarks": f"AI verification {rep['compliance_score']}/100, risk {rep['risk_level']}."})
        assert r.status_code == 200, (k, r.text)
        if k != "C":
            r = client.post(f"/officer/pipeline/{app['id']}/TECHNICAL/action", headers=H(toks["TECH"]),
                            json={"action": "TECHNICALLY_QUALIFIED", "outcome": "TECHNICALLY_QUALIFIED",
                                  "remarks": "Technical specifications met; vendor capability verified."})
            assert r.status_code == 200, (k, r.text)
        out[k] = app
    return out


def _bid(client, toks, auction_id, letter, amount, key=None):
    body = {"amount": amount}
    if key:
        body["idempotency_key"] = key
    return client.post(f"/bidder/auctions/{auction_id}/bids", headers=H(toks[f"BID_{letter}"]), json=body)


# ---------------------------------------------------------------------------
# 1. mode gates + creation prerequisites
# ---------------------------------------------------------------------------
def test_mode_gate_and_prerequisites(tokens, client):
    t10 = _tender(client, tokens, "TDR-2026-010")
    t11 = _tender(client, tokens, "TDR-2026-011")
    t1 = _tender(client, tokens, "TDR-2026-001")

    r = client.post("/officer/auctions", headers=H(tokens["PROC"]),
                    json={"tender_id": t10["id"], "auction_type": "FORWARD", "min_increment": "100000"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "MODE_MISMATCH", r.text

    r = client.post("/officer/auctions", headers=H(tokens["PROC"]),
                    json={"tender_id": t11["id"], "auction_type": "REVERSE", "min_increment": "100000"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "MODE_MISMATCH", r.text

    # a standard e-bid tender is not an auction tender at all — the mode
    # gate is enforced before any other prerequisite
    r = client.post("/officer/auctions", headers=H(tokens["PROC"]),
                    json={"tender_id": t1["id"], "auction_type": "REVERSE", "min_increment": "100000"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "MODE_MISMATCH", r.text

    r = client.post("/officer/auctions", headers=H(tokens["PROC"]),
                    json={"tender_id": t10["id"], "auction_type": "REVERSE", "min_increment": "100000"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "NO_QUALIFIED_BIDDERS", r.text


# ---------------------------------------------------------------------------
# 2. full reverse lifecycle (bids DECREASE, lowest wins)
# ---------------------------------------------------------------------------
def test_full_reverse_lifecycle(tokens, client):
    t10 = _tender(client, tokens, "TDR-2026-010")
    apps = _workflow(client, tokens, "TDR-2026-010", ("A", "B", "D", "C"))

    # ---- create: qualified only, anonymous, sealed quotes as initial prices
    r = client.post("/officer/auctions", headers=H(tokens["PROC"]), json={
        "tender_id": t10["id"], "auction_type": "REVERSE",
        "min_increment": "100000",
        "auto_extend_enabled": True, "extension_trigger_seconds": 120,
        "extension_seconds": 120, "max_extensions": 3,
        "notes": "Authorized reverse auction per tender conditions."})
    assert r.status_code == 200, r.text
    aid = r.json()["auction_id"]
    assert r.json()["status"] == "DRAFT"

    r = client.get(f"/officer/auctions/{aid}", headers=H(tokens["PROC"]))
    assert r.status_code == 200, r.text
    det = r.json()
    eligible_prices = [p["initial_price"] for p in det["participants"] if p["ra_eligible"]]
    assert {int(float(x)) for x in eligible_prices} == {102000000, 98500000, 97000000}
    excluded = [p for p in det["participants"] if not p["ra_eligible"]]
    assert len(excluded) == 1 and excluded[0]["qualification_reason"]

    # ---- duplicate creation is rejected
    r = client.post("/officer/auctions", headers=H(tokens["PROC"]),
                    json={"tender_id": t10["id"], "auction_type": "REVERSE", "min_increment": "100000"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "AUCTION_ALREADY_EXISTS", r.text

    # ---- schedule + pre-start rejection
    t0 = time.time()
    start = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t0 + 8))
    end = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t0 + 68))
    r = client.post(f"/officer/auctions/{aid}/schedule", headers=H(tokens["PROC"]),
                    json={"scheduled_start_at": start, "scheduled_end_at": end})
    assert r.status_code == 200 and r.json()["status"] == "SCHEDULED", r.text
    r = _bid(client, tokens, aid, "D", "96000000", "pre-start")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "AUCTION_NOT_STARTED", r.text
    time.sleep(8)
    r = client.post(f"/officer/auctions/{aid}/start", headers=H(tokens["PROC"]))
    assert r.status_code == 200 and r.json()["status"] == "LIVE", r.text

    # ---- invalid bids
    r = _bid(client, tokens, aid, "D", "96950000", "neg-small-decrement")
    assert r.status_code == 422 and r.json()["detail"]["code"] == "INVALID_REVERSE_AUCTION_BID", r.text
    r = _bid(client, tokens, aid, "D", "97000000", "neg-not-lower")
    assert r.status_code == 422, r.text
    r = _bid(client, tokens, aid, "C", "90000000", "neg-ineligible")
    assert r.status_code == 403 and r.json()["detail"]["code"] == "NOT_ELIGIBLE", r.text

    # ---- valid decrements (anti-sniping extends on the first bid)
    r = _bid(client, tokens, aid, "D", "96000000", "bid-D-1")
    assert r.status_code == 200 and r.json()["bid"]["status"] == "ACCEPTED", r.text
    assert r.json()["extended"] is True  # inside the 120s trigger window
    for letter, amount, key in (("A", "95500000", "bid-A-1"), ("D", "94200000", "bid-D-2")):
        r = _bid(client, tokens, aid, letter, amount, key)
        assert r.status_code == 200 and r.json()["bid"]["status"] == "ACCEPTED", (letter, r.text)
    # idempotency: identical retry returns the original outcome
    r = _bid(client, tokens, aid, "D", "94200000", "bid-D-2")
    assert r.status_code == 200 and r.json()["duplicate"] is True, r.text

    # ---- pause / resume (reauth) with bid blocking
    tok = _reauth(client, tokens, "PROC")
    r = client.post(f"/officer/auctions/{aid}/pause", headers=H(tokens["PROC"]),
                    json={"reason": "Temporary pause for participant network verification.", "reauth_token": tok})
    assert r.status_code == 200 and r.json()["status"] == "PAUSED", r.text
    r = _bid(client, tokens, aid, "A", "93000000", "neg-paused")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "AUCTION_PAUSED", r.text
    r = client.post(f"/officer/auctions/{aid}/resume", headers=H(tokens["PROC"]),
                    json={"reauth_token": tok, "remarks": "Resume authorized."})
    assert r.status_code == 200 and r.json()["status"] == "LIVE", r.text

    # ---- close -> deterministic ranking (B never bid -> initial price)
    r = client.post(f"/officer/auctions/{aid}/close", headers=H(tokens["PROC"]))
    assert r.status_code == 200 and r.json()["status"] == "RESULT_PENDING", r.text
    res = client.get(f"/officer/auctions/{aid}/live", headers=H(tokens["PROC"])).json()["result"]
    rank = {str(e["rank"]): e for e in res["ranking"]}
    assert len(rank) == 3  # disqualified bidder never ranked
    amounts = {int(float(rank[k]["amount"])) for k in rank}
    assert amounts == {94200000, 95500000, 98500000}
    assert float(res["initial_lowest"]) == 97000000 and float(res["final_lowest"]) == 94200000
    assert float(res["savings_amount"]) == 2800000 and res["tie"] is False
    assert res["accepted_bid_count"] == 3  # B never revised -> keeps initial price
    l1 = rank["1"]
    assert l1["price_source"] == "REVERSE_AUCTION_FINAL" and float(l1["amount"]) == 94200000
    l3 = rank["3"]
    assert l3["price_source"] == "INITIAL_QUALIFYING_BID" and float(l3["amount"]) == 98500000
    STATE["ra"] = {"auction_id": aid, "apps": apps, "rank": rank}

    # ---- CFA approves (L1 is NOT auto-awarded)
    rtok = _reauth(client, tokens, "CFA")
    r = client.post(f"/officer/auctions/{aid}/approve-result", headers=H(tokens["CFA"]),
                    json={"decision": "APPROVE",
                          "remarks": "Auction result verified; L1 approved at the approved auction price.",
                          "reauth_token": rtok})
    assert r.status_code == 200 and r.json()["status"] == "COMPLETED", r.text

    # ---- financial stage: RA final price prefill + L-ranking
    # D = L1 (94.2M), A = L2 (95.5M), B = L3 (98.5M initial)
    expect = {"D": 1, "A": 2, "B": 3}
    for k in ("D", "A", "B"):
        r = client.post(f"/officer/pipeline/{apps[k]['id']}/FINANCIAL/action", headers=H(tokens["FIN"]),
                        json={"action": "PASS", "outcome": "PASS",
                              "remarks": "Financial evaluation per approved reverse auction result."})
        assert r.status_code == 200, (k, r.text)
        st = client.get(f"/officer/pipeline/{apps[k]['id']}", headers=H(tokens["FIN"])).json()
        pay = next(x for x in st["stages"] if x["key"] == "FINANCIAL").get("payload") or {}
        assert pay.get("rank") == expect[k], (k, pay)
        assert pay.get("evaluation_basis") == "Approved reverse auction final price", pay


# ---------------------------------------------------------------------------
# 3. full forward lifecycle (bids INCREASE, highest wins)
# ---------------------------------------------------------------------------
def test_full_forward_lifecycle(tokens, client):
    t11 = _tender(client, tokens, "TDR-2026-011")
    apps = _workflow(client, tokens, "TDR-2026-011", ("A", "B", "D", "C"))

    r = client.post("/officer/auctions", headers=H(tokens["PROC"]), json={
        "tender_id": t11["id"], "auction_type": "FORWARD",
        "min_increment": "1000000",
        "auto_extend_enabled": True, "extension_trigger_seconds": 120,
        "extension_seconds": 120, "max_extensions": 3,
        "notes": "Authorized forward e-auction per tender conditions."})
    assert r.status_code == 200, r.text
    aid = r.json()["auction_id"]

    r = client.get(f"/officer/auctions/{aid}", headers=H(tokens["PROC"]))
    eligible = {int(float(p["initial_price"])): p for p in r.json()["participants"] if p["ra_eligible"]}
    assert set(eligible) == {50000000, 52500000, 55000000}

    t0 = time.time()
    start = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t0 + 8))
    end = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t0 + 68))
    r = client.post(f"/officer/auctions/{aid}/schedule", headers=H(tokens["PROC"]),
                    json={"scheduled_start_at": start, "scheduled_end_at": end})
    assert r.status_code == 200, r.text
    time.sleep(8)
    r = client.post(f"/officer/auctions/{aid}/start", headers=H(tokens["PROC"]))
    assert r.status_code == 200 and r.json()["status"] == "LIVE", r.text

    # invalid: increment below minimum / not higher than leading / disqualified
    r = _bid(client, tokens, aid, "D", "55500000", "neg-small-increment")
    assert r.status_code == 422 and r.json()["detail"]["code"] == "INVALID_FORWARD_AUCTION_BID", r.text
    r = _bid(client, tokens, aid, "A", "55000000", "neg-not-higher")
    assert r.status_code == 422, r.text
    r = _bid(client, tokens, aid, "C", "40000000", "neg-ineligible")
    assert r.status_code == 403 and r.json()["detail"]["code"] == "NOT_ELIGIBLE", r.text

    for letter, amount, key in (("A", "56000000", "bid-A-1"), ("B", "58000000", "bid-B-1"),
                                ("D", "60000000", "bid-D-1")):
        r = _bid(client, tokens, aid, letter, amount, key)
        assert r.status_code == 200 and r.json()["bid"]["status"] == "ACCEPTED", (letter, r.text)
    r = _bid(client, tokens, aid, "D", "60000000", "bid-D-1")
    assert r.status_code == 200 and r.json()["duplicate"] is True, r.text

    r = client.post(f"/officer/auctions/{aid}/close", headers=H(tokens["PROC"]))
    assert r.status_code == 200 and r.json()["status"] == "RESULT_PENDING", r.text
    res = client.get(f"/officer/auctions/{aid}/live", headers=H(tokens["PROC"])).json()["result"]
    rank = {str(e["rank"]): e for e in res["ranking"]}
    assert len(rank) == 3
    # highest valid bid wins: D 60M > B 58M > A 56M
    assert float(rank["1"]["amount"]) == 60000000 and rank["1"]["price_source"] == "FORWARD_AUCTION_FINAL"
    assert float(rank["2"]["amount"]) == 58000000
    assert float(rank["3"]["amount"]) == 56000000
    assert float(res["initial_lowest"]) == 55000000 and float(res["final_lowest"]) == 60000000
    assert float(res["savings_amount"]) == 5000000 and res["tie"] is False  # premium above initial

    rtok = _reauth(client, tokens, "CFA")
    r = client.post(f"/officer/auctions/{aid}/approve-result", headers=H(tokens["CFA"]),
                    json={"decision": "APPROVE",
                          "remarks": "Forward e-auction result verified; highest bid approved.",
                          "reauth_token": rtok})
    assert r.status_code == 200 and r.json()["status"] == "COMPLETED", r.text

    # direction-aware L-ranking: highest bid = L1
    expect = {"D": 1, "B": 2, "A": 3}
    for k in ("D", "B", "A"):
        r = client.post(f"/officer/pipeline/{apps[k]['id']}/FINANCIAL/action", headers=H(tokens["FIN"]),
                        json={"action": "PASS", "outcome": "PASS",
                              "remarks": "Financial evaluation per approved forward e-auction result."})
        assert r.status_code == 200, (k, r.text)
        st = client.get(f"/officer/pipeline/{apps[k]['id']}", headers=H(tokens["FIN"])).json()
        pay = next(x for x in st["stages"] if x["key"] == "FINANCIAL").get("payload") or {}
        assert pay.get("rank") == expect[k], (k, pay)
        assert pay.get("evaluation_basis") == "Approved forward e-auction final price", pay
    STATE["fa"] = {"auction_id": aid, "apps": apps}


# ---------------------------------------------------------------------------
# 4. integrity + report endpoints + audit coverage (uses the RA auction)
# ---------------------------------------------------------------------------
def test_integrity_report_audit_and_rbac(tokens, client):
    aid = STATE["ra"]["auction_id"]
    t10 = _tender(client, tokens, "TDR-2026-010")

    r = client.get(f"/officer/auctions/{aid}/integrity", headers=H(tokens["PROC"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["bid_chain"]["valid"] is True and body["bid_chain"]["bids_checked"] == 3
    assert body["audit"]["audit_chain_valid"] is True
    assert body["audit"]["auction_events"] >= 12
    assert body["result_hash"]
    assert body["collusion_signals"]["note"].startswith("Advisory")

    r = client.get(f"/officer/auctions/{aid}/report", headers=H(tokens["CFA"]))
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf", r.text
    assert r.content[:4] == b"%PDF" and len(r.content) > 15000

    # RBAC: a role without auction-management authority is denied
    r = client.get(f"/officer/auctions/{aid}/report", headers=H(tokens["FIN"]))
    assert r.status_code == 403, r.text

    # every auction event on the tamper-evident chain
    r = client.get("/admin/audit-logs", headers=H(tokens["ADMIN"]),
                   params={"tender_id": t10["id"], "limit": 1000})
    actions = [x["action"] for x in r.json()["logs"] if x.get("entity_id") == aid]
    for e in ("AUCTION_CREATED", "RA_ELIGIBILITY_CALCULATED", "AUCTION_SCHEDULED", "AUCTION_STARTED",
              "AUCTION_PAUSED", "AUCTION_RESUMED", "AUCTION_CLOSED", "RANKING_CALCULATED",
              "RESULT_APPROVED", "AUCTION_AUTO_EXTENDED"):
        assert e in actions, (e, actions)
    assert actions.count("BID_PLACED") == 3

    r = client.get("/admin/audit-logs/verify", headers=H(tokens["ADMIN"]))
    assert r.json()["valid"] is True, r.text
