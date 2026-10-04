"""Verification-result cache (additive; ported from BidVerify-AI's 3-tier cache).

Doctrine checks:
- repeat verification within TTL skips the portal call (call counter proof)
- cached results carry `_cached` markers (audit transparency)
- deterministic results are unchanged by caching (same rule outcomes)
- transient failures (UNAVAILABLE) are NEVER cached
- TTL expiry + explicit admin clear (audited) work
"""
import time
import os

import httpx
import pytest
from conftest import H, login, open_bids


@pytest.fixture(autouse=True)
def _bids_open(client, seeded):
    """Verification flows assume bids are open (post-deadline)."""
    open_bids(client)

def _mock() -> str:
    return os.environ.get("MOCK_PORTAL_URL", "http://127.0.0.1:8001")
ADMIN = ("admin@cpcl.gov.in", "Admin@1234")
PROC_OFFICER = ("procurement.belagavi@cpcl.gov.in", "Officer@1234")
RAMESH = ("ramesh@bidder.in", "Bidder@1234")


def _stats() -> dict:
    r = httpx.get(f"{_mock()}/api/admin/stats", timeout=10)
    assert r.status_code == 200
    return r.json()


def _reset_stats() -> None:
    r = httpx.post(f"{_mock()}/api/admin/stats/reset", timeout=10)
    assert r.status_code == 200


def _app_id_for_bidder(bidder_email: str) -> str:
    from app.core.db import SessionLocal
    from app.models import Application, User
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.email == bidder_email).first()
        assert u is not None
        a = db.query(Application).filter(Application.bidder_user_id == u.id).first()
        assert a is not None
        return a.id
    finally:
        db.close()


def test_cache_hit_avoids_portal_call(client, seeded):
    _reset_stats()
    admin_tok = login(client, *ADMIN)
    # fresh state: module reseed wipes DB rows; the server's in-process LRU
    # may still hold entries from earlier modules — clear purges both tiers
    r = client.post("/admin/verify-cache/clear", json={}, headers=H(admin_tok))
    assert r.status_code == 200

    tok = login(client, *PROC_OFFICER)
    app_id = _app_id_for_bidder("ramesh@bidder.in")

    r1 = client.post(f"/officer/applications/{app_id}/run-verification", headers=H(tok))
    assert r1.status_code == 200, r1.text
    score_1 = r1.json()["compliance_score"]
    calls_after_1 = _stats()["total"]
    assert calls_after_1 > 0, "first run must hit the portal"

    r2 = client.post(f"/officer/applications/{app_id}/run-verification", headers=H(tok))
    assert r2.status_code == 200
    assert r2.json()["compliance_score"] == score_1, "caching must not change outcomes"
    calls_after_2 = _stats()["total"]
    assert calls_after_2 <= calls_after_1 + 1, (
        f"second run re-hit the portal: {calls_after_1} -> {calls_after_2}"
    )

    rep = client.get(f"/officer/applications/{app_id}/report", headers=H(tok)).json()
    src = rep["source_results"]
    cached = [s for s, o in src.items() if (o.get("data") or {}).get("_cached")]
    assert "GST" in cached and "UDYAM" in cached, f"expected cached sources, got {cached}"
    assert src["GST"]["data"].get("_cached_at"), "original retrieval timestamp preserved"
    assert src["GST"]["data"].get("_cache_age_seconds", -1) >= 0


def test_failures_are_never_cached(client, seeded):
    """A faulted (UNAVAILABLE) result must not be cached; after the fault is
    cleared the portal is called again and the fresh VALID result is returned."""
    admin_tok = login(client, *ADMIN)
    # fresh slate so no cached GST result from earlier tests is in play
    assert client.post("/admin/verify-cache/clear", json={}, headers=H(admin_tok)).status_code == 200

    from app.core.db import SessionLocal
    from app.models import BidderProfile, User
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.email == "ramesh@bidder.in").first()
        gstin = db.query(BidderProfile).filter(BidderProfile.user_id == u.id).first().gstin_plain
    finally:
        db.close()
    assert gstin

    # 1) inject a fault on the GST portal for this GSTIN
    r = httpx.post(f"{_mock()}/api/admin/fault", json={"source": "gst", "mode": "unavailable", "identifier": gstin}, timeout=10)
    assert r.status_code == 200

    tok = login(client, *PROC_OFFICER)
    app_id = _app_id_for_bidder("ramesh@bidder.in")
    _reset_stats()
    r1 = client.post(f"/officer/applications/{app_id}/run-verification", headers=H(tok))
    assert r1.status_code == 200
    calls_after_1 = _stats()["total"]
    assert calls_after_1 > 0
    rep1 = client.get(f"/officer/applications/{app_id}/report", headers=H(tok)).json()
    assert rep1["source_results"]["GST"]["status"] == "UNAVAILABLE"

    # 2) clear the fault and re-run: must re-hit the portal (no cached failure)
    r = httpx.delete(f"{_mock()}/api/admin/fault/gst", timeout=10)
    assert r.status_code == 200
    r2 = client.post(f"/officer/applications/{app_id}/run-verification", headers=H(tok))
    assert r2.status_code == 200
    calls_after_2 = _stats()["total"]
    assert calls_after_2 > calls_after_1, "fault-cleared re-verification must call the portal"
    rep2 = client.get(f"/officer/applications/{app_id}/report", headers=H(tok)).json()
    assert rep2["source_results"]["GST"]["status"] == "VALID"
    assert not (rep2["source_results"]["GST"].get("data") or {}).get("_cached"), \
        "a freshly fetched result must not be marked cached"


def test_cache_admin_endpoints_and_audit(client, seeded):
    tok_bidder = login(client, *RAMESH)
    assert client.get("/admin/verify-cache", headers=H(tok_bidder)).status_code == 403

    admin_tok = login(client, *ADMIN)
    r = client.get("/admin/verify-cache", headers=H(admin_tok))
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is True and body["ttl_seconds"] == 900
    assert body["cacheable_statuses"] == ["BLACKLIST", "EXPIRED", "MISMATCH", "NOT_FOUND", "VALID"]

    r = client.post("/admin/verify-cache/clear", json={"source": "GST"}, headers=H(admin_tok))
    assert r.status_code == 200
    # per-source clear leaves other sources' rows intact (if any exist)
    after = client.get("/admin/verify-cache", headers=H(admin_tok)).json()
    assert all(row["source"] != "GST" for row in after["by_source"])

    # audited action
    logs = client.get("/admin/audit-logs", params={"action": "verify_cache_cleared"}, headers=H(admin_tok)).json()
    assert any(e.get("action") == "verify_cache_cleared" for e in logs.get("logs", [])), \
        "cache clear must be written to the audit trail"


def test_ttl_expiry_unit(monkeypatch):
    from app.core import config
    from app.core.db import SessionLocal, utcnow
    from app.services import verify_cache

    monkeypatch.setattr(config.settings, "VERIFY_CACHE_TTL_SECONDS", 1)
    db = SessionLocal()
    try:
        verify_cache.invalidate(db)
        db.commit()
        ok = verify_cache.put(db, "GST", "CACHE-TTL-TEST", None, "VALID", {"x": 1}, "ok", utcnow())
        assert ok
        db.commit()
        hit = verify_cache.get(db, "GST", "CACHE-TTL-TEST", None)
        assert hit is not None and hit["data"] == {"x": 1}
        time.sleep(1.2)
        assert verify_cache.get(db, "GST", "CACHE-TTL-TEST", None) is None
        # non-cacheable statuses are rejected outright
        assert verify_cache.put(db, "GST", "CACHE-TTL-TEST2", None, "UNAVAILABLE", {}, "x", utcnow()) is False
        db.commit()
    finally:
        verify_cache.invalidate(db)
        db.commit()
        db.close()
