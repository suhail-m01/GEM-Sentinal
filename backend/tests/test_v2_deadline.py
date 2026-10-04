"""Deadline-based bid confidentiality (bid sealing).

Doctrine: evaluation roles cannot see bid details, run verification, or make
decisions before the bid deadline. Sealed tenders expose aggregates only
(counts) — never identities, amounts, scores, or documents. Admin can
force-open (audited). Bidders always see their own data.
"""
from conftest import H, login, open_bids, ADMIN_CREDS

PROC = ("procurement.belagavi@cpcl.gov.in", "Officer@1234")
HUBLI = ("procurement.hubli@cpcl.gov.in", "Officer1@1234")
CFA = ("cfa.karnataka@cpcl.gov.in", "CFA@1234")
RAMESH = ("ramesh@bidder.in", "Bidder@1234")


def _admin_tok(client):
    return login(client, *ADMIN_CREDS)


def _future_utc(days=30):
    import datetime as _dt
    from zoneinfo import ZoneInfo
    ist = (_dt.datetime.now(ZoneInfo("Asia/Kolkata")) + _dt.timedelta(days=days)).replace(
        hour=18, minute=0, second=0, microsecond=0
    )
    return ist.astimezone(_dt.timezone.utc).replace(tzinfo=None).isoformat()


def _tender_body(t, bid_deadline):
    return {
        "title": t["title"], "description": t.get("description"),
        "department": t["department"], "organization": t.get("organization", "CPCL"),
        "category": t.get("category"), "product_service_category": t.get("product_service_category"),
        "district": t["district"], "state": t["state"], "jurisdiction_code": t["jurisdiction_code"],
        "value_min": t.get("value_min"), "value_max": t.get("value_max"),
        "close_date": t["close_date"], "open_date": t.get("open_date"),
        "bid_deadline": bid_deadline,
        "msme_preference": t.get("msme_preference", False),
        "make_in_india_required": t.get("make_in_india_required", False),
        "startup_eligible": t.get("startup_eligible", False),
        "required_turnover": t.get("required_turnover"),
        "min_local_content": t.get("min_local_content"),
        "required_document_types": t.get("required_document_types", []),
        "notes": t.get("notes"), "organization_id": t.get("organization_id"),
        "procurement_method": t.get("procurement_method", "GEM_BID"),
    }


def _app_ids(client, tok):
    r = client.get("/officer/applications", headers=H(tok))
    assert r.status_code == 200
    return r.json()["applications"]


def test_sealed_state_hides_details_from_officers(client, seeded):
    # After a fresh reseed the demo deadlines are today 18:00 IST. Force a
    # definitely-future deadline so the sealed assertions are deterministic
    # no matter the wall clock.
    admin_tok = _admin_tok(client)
    tenders = client.get("/admin/tenders", headers=H(admin_tok)).json()["tenders"]
    # push every published tender's deadline far into the future
    import datetime as _dt
    from zoneinfo import ZoneInfo
    future_ist = (_dt.datetime.now(ZoneInfo("Asia/Kolkata")) + _dt.timedelta(days=30)).replace(
        hour=18, minute=0, second=0, microsecond=0
    )
    future_utc = future_ist.astimezone(_dt.timezone.utc).replace(tzinfo=None).isoformat()
    for t in tenders:
        if t["status"] != "PUBLISHED":
            continue
        body = {
            "title": t["title"], "description": t.get("description"),
            "department": t["department"], "organization": t.get("organization", "CPCL"),
            "category": t.get("category"), "product_service_category": t.get("product_service_category"),
            "district": t["district"], "state": t["state"], "jurisdiction_code": t["jurisdiction_code"],
            "value_min": t.get("value_min"), "value_max": t.get("value_max"),
            "close_date": t["close_date"], "open_date": t.get("open_date"),
            "bid_deadline": future_utc,
            "msme_preference": t.get("msme_preference", False),
            "make_in_india_required": t.get("make_in_india_required", False),
            "startup_eligible": t.get("startup_eligible", False),
            "required_turnover": t.get("required_turnover"),
            "min_local_content": t.get("min_local_content"),
            "required_document_types": t.get("required_document_types", []),
            "notes": t.get("notes"), "organization_id": t.get("organization_id"),
            "procurement_method": t.get("procurement_method", "GEM_BID"),
        }
        r = client.put(f"/admin/tenders/{t['id']}", json=body, headers=H(admin_tok))
        assert r.status_code == 200, r.text[:200]
    # confirm sealed
    tenders = client.get("/admin/tenders", headers=H(admin_tok)).json()["tenders"]
    assert any(t["bids_open"] is False for t in tenders if t["status"] == "PUBLISHED")

    tok = login(client, *PROC)
    apps = _app_ids(client, tok)
    # Belagavi officer sees t1's apps; they must be sealed aggregates only
    sealed = [a for a in apps if a.get("sealed")]
    assert sealed, "expected at least one sealed application in officer queue"
    for a in sealed:
        assert a["company"] is None, "bidder identity must be hidden while sealed"
        assert a["compliance_score"] is None
        assert a["eligibility_result"] is None
        assert a["submitted_at"] is None

    # detail + verification must be blocked with BIDS_SEALED
    a0 = sealed[0]
    r = client.get(f"/officer/applications/{a0['id']}", headers=H(tok))
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "BIDS_SEALED"
    r = client.post(f"/officer/applications/{a0['id']}/run-verification", headers=H(tok))
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "BIDS_SEALED"
    r = client.get(f"/officer/applications/{a0['id']}/report", headers=H(tok))
    assert r.status_code == 403

    # CFA (L2-behavior) is equally gated
    cfa_tok = login(client, *CFA)
    r = client.get(f"/l2/applications/{a0['id']}", headers=H(cfa_tok))
    assert r.status_code == 403 and r.json()["detail"]["code"] == "BIDS_SEALED"
    l2apps = client.get("/l2/applications", headers=H(cfa_tok)).json()["applications"]
    # identity is hidden only for tenders that are still sealed (the Hubli
    # demo tender has per-tender sealing disabled — see test below)
    sealed_nos = {t["tender_no"] for t in tenders if t["status"] == "PUBLISHED" and t["bids_open"] is False}
    for a in l2apps:
        if a["tender_no"] in sealed_nos:
            assert a["company"] is None

    # bidder still sees their own data while sealed
    bid_tok = login(client, *RAMESH)
    r = client.get("/bidder/applications", headers=H(bid_tok))
    assert r.status_code == 200 and len(r.json()["applications"]) >= 1
    myapp = r.json()["applications"][0]
    r = client.get(f"/bidder/applications/{myapp['id']}", headers=H(bid_tok))
    assert r.status_code == 200
    r = client.get("/bidder/documents", headers=H(bid_tok))
    assert r.status_code == 200

    # admin management view still works (aggregates + deadline), not blocked
    r = client.get("/admin/tenders", headers=H(admin_tok))
    assert r.status_code == 200


def test_force_open_unseals_and_allows_verification(client, seeded):
    admin_tok = _admin_tok(client)
    tenders = client.get("/admin/tenders", headers=H(admin_tok)).json()["tenders"]
    published = [t for t in tenders if t["status"] == "PUBLISHED"]
    assert published
    # ensure sealed first
    import datetime as _dt
    from zoneinfo import ZoneInfo
    future_ist = (_dt.datetime.now(ZoneInfo("Asia/Kolkata")) + _dt.timedelta(days=30)).replace(
        hour=18, minute=0, second=0, microsecond=0
    )
    future_utc = future_ist.astimezone(_dt.timezone.utc).replace(tzinfo=None).isoformat()
    # pick a tender with applications inside the Belagavi officer's jurisdiction
    t0 = next(t for t in published if t["application_count"] > 0 and t["district"] == "Belagavi")
    body = {
        "title": t0["title"], "description": t0.get("description"),
        "department": t0["department"], "organization": t0.get("organization", "CPCL"),
        "category": t0.get("category"), "product_service_category": t0.get("product_service_category"),
        "district": t0["district"], "state": t0["state"], "jurisdiction_code": t0["jurisdiction_code"],
        "value_min": t0.get("value_min"), "value_max": t0.get("value_max"),
        "close_date": t0["close_date"], "open_date": t0.get("open_date"),
        "bid_deadline": future_utc,
        "msme_preference": t0.get("msme_preference", False),
        "make_in_india_required": t0.get("make_in_india_required", False),
        "startup_eligible": t0.get("startup_eligible", False),
        "required_turnover": t0.get("required_turnover"),
        "min_local_content": t0.get("min_local_content"),
        "required_document_types": t0.get("required_document_types", []),
        "notes": t0.get("notes"), "organization_id": t0.get("organization_id"),
        "procurement_method": t0.get("procurement_method", "GEM_BID"),
    }
    assert client.put(f"/admin/tenders/{t0['id']}", json=body, headers=H(admin_tok)).status_code == 200

    # now force-open
    r = client.post(f"/admin/tenders/{t0['id']}/open-bids", headers=H(admin_tok))
    assert r.status_code == 200
    # audit trail recorded
    logs = client.get("/admin/audit-logs", params={"action": "tender_bids_force_opened"}, headers=H(admin_tok)).json()
    assert any(e["action"] == "tender_bids_force_opened" for e in logs["logs"])

    # unsealed: officer can now see + verify
    tok = login(client, *PROC)
    apps = [a for a in _app_ids(client, tok) if a["tender_no"] == t0["tender_no"]]
    assert apps
    assert all(a["sealed"] is False for a in apps)
    assert all(a["company"] is not None for a in apps), "identity visible once open"
    r = client.get(f"/officer/applications/{apps[0]['id']}", headers=H(tok))
    assert r.status_code == 200
    r = client.post(f"/officer/applications/{apps[0]['id']}/run-verification", headers=H(tok))
    assert r.status_code == 200, r.text[:200]
    r = client.get(f"/officer/applications/{apps[0]['id']}/report", headers=H(tok))
    assert r.status_code == 200


def test_sealing_is_per_tender(client, seeded):
    """Demo district split: Belagavi (TDR-2026-001) stays sealed; Hubli
    (TDR-2026-002) is open per-tender and evaluable before any deadline or
    force-open, whatever the wall clock says."""
    admin_tok = _admin_tok(client)
    # deterministic sealed state: push the Belagavi demo tender's opening out
    tenders = client.get("/admin/tenders", headers=H(admin_tok)).json()["tenders"]
    t1 = next(t for t in tenders if t["tender_no"] == "TDR-2026-001")
    assert client.put(
        f"/admin/tenders/{t1['id']}", json=_tender_body(t1, _future_utc()), headers=H(admin_tok)
    ).status_code == 200

    tenders = client.get("/admin/tenders", headers=H(admin_tok)).json()["tenders"]
    by_no = {t["tender_no"]: t for t in tenders}
    assert by_no["TDR-2026-001"]["bids_open"] is False, "Belagavi demo tender must be sealed"
    assert by_no["TDR-2026-002"]["bids_open"] is True, "Hubli AI-verification demo tender must be open"

    # the Hubli application (routed to the Hubli L1) is evaluable pre-deadline
    tok = login(client, *HUBLI)
    apps = [a for a in _app_ids(client, tok) if a["tender_no"] == "TDR-2026-002"]
    assert apps, "expected the seeded Hubli application"
    assert all(a["sealed"] is False and a["company"] is not None for a in apps)
    assert client.get(f"/officer/applications/{apps[0]['id']}", headers=H(tok)).status_code == 200
