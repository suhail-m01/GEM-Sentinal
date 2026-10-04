"""SENTINEL WATCH — continuous compliance monitoring (spec §8) + evidence
strength ladder / coverage (§13) + reproducibility replay (§12.3).

Flow (order matters — state accumulates per module):
  1. officer verifies a bidder  -> report carries strength ladder + coverage,
     a ComplianceWatch is auto-registered
  2. replay proves determinism  -> bit-identical before anything changes
  3. bidder self-service        -> compliance-health own-data view
  4. RBAC                       -> bidders cannot read the watch tower
  5. SIMULATED drift            -> GST ACTIVE -> CANCELLED at the mock portal
     -> tick detects CRITICAL DEGRADED, alert routed, score drops,
     commitment gate blocks, CRITICAL can't be snoozed
  6. acknowledge                -> gate unblocks (other decision rules apply)
  7. restore                    -> IMPROVED drift, gate stays open
"""
import httpx
import pytest

from conftest import H, login, open_bids

ADMIN = ("admin@cpcl.gov.in", "Admin@1234")
PO = ("procurement.belagavi@cpcl.gov.in", "Officer@1234")       # PROCUREMENT_OFFICER
L2 = ("finance.karnataka@cpcl.gov.in", "L2Officer@1234")        # FINANCE_EVALUATOR
RAMESH = ("ramesh@bidder.in", "Bidder@1234")


@pytest.fixture(scope="module", autouse=True)
def _bids_open(client, seeded):
    """Module-scoped so bids are open BEFORE the app_a fixture verifies."""
    open_bids(client)


@pytest.fixture(scope="module")
def app_a(client, seeded, _bids_open):
    """Verify APP-2026-000001 (Ramesh) once for the module; return its id."""
    tok = login(client, *PO)
    r = client.get("/officer/applications", headers=H(tok))
    a = next(x for x in r.json()["applications"] if x["application_no"] == "APP-2026-000001")
    r = client.post(f"/officer/applications/{a['id']}/run-verification", headers=H(tok))
    assert r.status_code == 200, r.text[:300]
    return a


def _watch_for(client, tok, application_no):
    r = client.get("/watch/watches", headers=H(tok))
    assert r.status_code == 200, r.text[:300]
    return next((w for w in r.json()["watches"] if w["application_no"] == application_no), None)


# ----------------------- strength ladder + coverage (§13) -----------------------

def test_report_has_strength_and_coverage(client, seeded, app_a):
    tok = login(client, *PO)
    r = client.get(f"/officer/applications/{app_a['id']}", headers=H(tok))
    assert r.status_code == 200
    rep = r.json()["verification_report"]
    assert rep is not None
    # every evaluated rule carries an evidence-strength grade
    graded = [x for x in rep["rule_results"] if x.get("evidence_strength")]
    assert graded, "rule results missing evidence_strength"
    assert all(x["evidence_strength"] in ("S0", "S1", "S2", "S3", "S4") for x in graded)
    # live-authoritative evidence must actually exist (S3 present in a clean verify)
    assert any(x["evidence_strength"] == "S3" for x in graded), "expected S3 (authoritative) evidence"
    # report-level coverage + determinism hash
    assert rep.get("coverage_pct") is not None and 0 <= rep["coverage_pct"] <= 100
    assert rep.get("strength_distribution"), "strength_distribution missing"
    assert rep.get("determinism_hash"), "determinism_hash missing"


# ------------------------------- watch registration ------------------------------

def test_watch_auto_registered(client, seeded, app_a):
    tok = login(client, *PO)
    w = _watch_for(client, tok, "APP-2026-000001")
    assert w is not None, "no ComplianceWatch registered after verification"
    assert w["active"] is True
    assert set(w["watched_attributes"]) >= {"BLACKLIST", "GST", "UDYAM", "PAN", "EPFO", "MCA21"}
    assert w["baseline"] and "GST" in w["baseline"]
    assert w["baseline"]["GST"]["value"].get("inner_status") == "ACTIVE"


# ------------------------------ determinism replay ------------------------------

def test_replay_identical_before_any_change(client, seeded, app_a):
    tok = login(client, *L2)
    r = client.get(f"/l2/applications/{app_a['id']}/replay", headers=H(tok))
    assert r.status_code == 200, r.text[:300]
    body = r.json()
    assert body["stored_hash"] and body["recomputed_hash"]
    assert body["identical"] is True, f"replay not bit-identical: {body}"
    assert body["score_stored"] == body["score_recomputed"]


# ----------------------------- bidder self-service ------------------------------

def test_compliance_health_own_data(client, seeded, app_a):
    tok = login(client, *RAMESH)
    r = client.get("/bidder/compliance-health", headers=H(tok))
    assert r.status_code == 200, r.text[:300]
    body = r.json()
    attrs = {i["attribute"] for i in body["items"]}
    assert {"GST", "PAN", "UDYAM", "EPFO", "MCA21", "BLACKLIST"} <= attrs
    gst = next(i for i in body["items"] if i["attribute"] == "GST")
    assert gst["status"] == "OK" and gst["value"] == "ACTIVE"
    assert body["watch_active"] is True
    assert body["score_at_verification"] is not None


# ------------------------------------- RBAC -------------------------------------

def test_bidder_cannot_read_watch_tower(client, seeded, app_a):
    tok = login(client, *RAMESH)
    assert client.get("/watch/watches", headers=H(tok)).status_code == 403
    assert client.get("/watch/drift", headers=H(tok)).status_code == 403
    assert client.get("/watch/alerts", headers=H(tok)).status_code == 403
    # and cannot run actions
    assert client.post("/watch/run-now", headers=H(tok)).status_code == 403


# ------------------------------ drift: CRITICAL beat ----------------------------

def test_simulated_drift_detected_critical(client, seeded, app_a):
    tok = login(client, *PO)
    r = client.post("/watch/demo/simulate-drift",
                    json={"application_id": app_a["id"], "to_status": "CANCELLED"}, headers=H(tok))
    assert r.status_code == 200, r.text[:300]
    assert r.json()["tick"]["drifts_detected"] >= 1

    r = client.get("/watch/drift", headers=H(tok))
    drifts = [d for d in r.json()["drifts"] if d["application_no"] == "APP-2026-000001" and d["attribute"] == "GST"]
    assert drifts, "no GST drift detected"
    d = drifts[0]
    assert d["severity"] == "CRITICAL" and d["direction"] == "DEGRADED"
    assert d["old_value"]["inner_status"] == "ACTIVE" and d["new_value"]["inner_status"] == "CANCELLED"
    assert d["score_before"] is not None and d["score_after"] is not None
    assert d["score_after"] < d["score_before"]
    assert d["cause_summary"]
    # drift must NOT mutate history — it creates a report revision
    assert d["report_revision_id"] and d["original_report_id"]

    # alert fired + ownership resolution recorded
    r = client.get("/watch/alerts", headers=H(tok))
    alerts = [a for a in r.json()["alerts"] if a["application_no"] == "APP-2026-000001" and a["severity"] == "CRITICAL"]
    assert alerts and alerts[0]["status"] == "OPEN"
    assert alerts[0]["resolved_recipient_id"] and alerts[0]["resolution_reason"]

    # THEN vs NOW shows the drop
    r = client.get(f"/watch/applications/{app_a['id']}/comparison", headers=H(tok))
    c = r.json()
    assert c["score_now"] < c["score_then"]
    assert any(x["attribute"] == "GST" and x["to"] == "CANCELLED" for x in c["changed_attributes"])


def test_gate_blocks_commitment_during_critical(client, seeded, app_a):
    tok = login(client, *L2)
    r = client.post(f"/l2/applications/{app_a['id']}/decision",
                    json={"action": "RECOMMEND_QUALIFY", "remarks": "test gate"}, headers=H(tok))
    assert r.status_code == 409, f"expected gate block, got {r.status_code}: {r.text[:200]}"
    detail = r.json().get("detail")
    assert isinstance(detail, dict) and detail.get("code") == "WATCH_GATE_BLOCKED", r.text[:300]
    assert detail.get("blocking_alerts")


def test_critical_alert_cannot_be_snoozed(client, seeded, app_a):
    tok = login(client, *PO)
    r = client.get("/watch/alerts", headers=H(tok))
    a = next(x for x in r.json()["alerts"]
             if x["application_no"] == "APP-2026-000001" and x["severity"] == "CRITICAL" and x["status"] == "OPEN")
    r = client.post(f"/watch/alerts/{a['id']}/snooze",
                    json={"reason": "please wait", "hours": 2}, headers=H(tok))
    assert r.status_code == 409
    assert "never be snoozed" in r.text


def test_drift_report_pdf(client, seeded, app_a):
    tok = login(client, *PO)
    r = client.get("/watch/drift", headers=H(tok))
    d = next(x for x in r.json()["drifts"] if x["application_no"] == "APP-2026-000001" and x["attribute"] == "GST")
    r = client.get(f"/watch/drift/{d['id']}/report.pdf", headers=H(tok))
    assert r.status_code == 200 and r.content[:4] == b"%PDF" and len(r.content) > 5000


def test_acknowledge_unblocks_gate(client, seeded, app_a):
    tok = login(client, *PO)
    r = client.get("/watch/alerts", headers=H(tok))
    a = next(x for x in r.json()["alerts"]
             if x["application_no"] == "APP-2026-000001" and x["severity"] == "CRITICAL" and x["status"] == "OPEN")
    r = client.post(f"/watch/alerts/{a['id']}/acknowledge", headers=H(tok))
    assert r.status_code == 200 and r.json()["status"] == "ACKNOWLEDGED"

    # gate check no longer blocks
    r = client.post(f"/watch/applications/{app_a['id']}/gate-check", headers=H(tok))
    assert r.status_code == 200 and r.json()["blocked"] is False

    # L2 decision must not be refused BY THE WATCH GATE any more
    tok_l2 = login(client, *L2)
    r = client.post(f"/l2/applications/{app_a['id']}/decision",
                    json={"action": "RECOMMEND_QUALIFY", "remarks": "after ack"}, headers=H(tok_l2))
    if r.status_code == 409:
        detail = r.json().get("detail")
        code = detail.get("code") if isinstance(detail, dict) else None
        assert code != "WATCH_GATE_BLOCKED", f"gate still blocking after ack: {r.text[:300]}"


# ---------------------------------- restore beat ---------------------------------

def test_restore_produces_improved_drift(client, seeded, app_a):
    tok = login(client, *PO)
    r = client.post("/watch/demo/restore-drift", json={"application_id": app_a["id"]}, headers=H(tok))
    assert r.status_code == 200, r.text[:300]

    r = client.get("/watch/drift", headers=H(tok))
    improved = [d for d in r.json()["drifts"]
                if d["application_no"] == "APP-2026-000001" and d["attribute"] == "GST" and d["direction"] == "IMPROVED"]
    assert improved, "no IMPROVED drift after restore"

    r = client.post(f"/watch/applications/{app_a['id']}/gate-check", headers=H(tok))
    assert r.json()["blocked"] is False
    assert r.json()["score_now"] is not None


def test_source_health_populated(client, seeded, app_a):
    tok = login(client, *PO)
    r = client.get("/watch/source-health", headers=H(tok))
    assert r.status_code == 200
    src = r.json()["sources"]
    assert "GST" in src and src["GST"]["last_poll"] in ("UP", "DOWN")
