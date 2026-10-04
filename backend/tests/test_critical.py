"""Critical automated tests (per master prompt):
authentication, authorization, eligibility/rules, application, verification,
override, critical override. Run with the backend + mock portal + seeded DB.
"""
import httpx
import pytest

from conftest import H, login, open_bids


@pytest.fixture(autouse=True)
def _bids_open(client, seeded):
    """Evaluation flows assume bids are open (post-deadline)."""
    open_bids(client)

# V2.1 role migration: the legacy L1/L2 officer accounts are now explicit
# workflow roles. These tests exercise the still-present legacy endpoints
# during the migration window; they are superseded by the new
# evaluation/committee/CFA workflow tests (Slice 7).
ADMIN = ("admin@cpcl.gov.in", "Admin@1234")
L1 = ("procurement.belagavi@cpcl.gov.in", "Officer@1234")      # PROCUREMENT_OFFICER
L1HUB = ("procurement.hubli@cpcl.gov.in", "Officer1@1234")     # PROCUREMENT_OFFICER
L2 = ("finance.karnataka@cpcl.gov.in", "L2Officer@1234")       # FINANCE_EVALUATOR
RAMESH = ("ramesh@bidder.in", "Bidder@1234")
SITA = ("sita@bidder.in", "Bidder@1234")
VIKRAM = ("vikram@bidder.in", "Bidder@1234")
MEENA = ("meena@bidder.in", "Bidder@1234")


# ------------------------------ AUTHENTICATION ------------------------------

def test_valid_login(client, seeded):
    # Two-factor: credentials → OTP to registered mobile → session
    r = client.post("/auth/login", json={"email": RAMESH[0], "password": RAMESH[1]})
    assert r.status_code == 200
    step1 = r.json()
    assert step1["otp_required"] is True
    assert step1["mobile_masked"].startswith("+91")
    r2 = client.post("/auth/verify-otp", json={"email": RAMESH[0], "otp": step1["dev_otp"], "purpose": "login"})
    assert r2.status_code == 200
    body = r2.json()
    assert body["access_token"] and body["refresh_token"]
    assert body["user"]["role"] == "BIDDER"


def test_invalid_login(client, seeded):
    r = client.post("/auth/login", json={"email": RAMESH[0], "password": "wrong-password"})
    assert r.status_code == 401


def test_protected_route_requires_token(seeded, base_url):
    # fresh client: no headers AND no cookies (the shared session client
    # legitimately carries the gs_access session cookie from earlier logins)
    with httpx.Client(base_url=base_url, timeout=30) as fresh:
        r = fresh.get("/bidder/dashboard")
    assert r.status_code == 401


def test_invalid_token_rejected(client, seeded):
    r = client.get("/bidder/dashboard", headers=H("not.a.jwt"))
    assert r.status_code == 401


def test_expired_purpose_token_rejected(client, seeded):
    # refresh token must not work as access token
    tok = login(client, RAMESH[0], RAMESH[1])
    refresh = client.post("/auth/refresh", json={"refresh_token": tok})
    assert refresh.status_code == 401  # access token is not a refresh token


def test_refresh_flow(client, seeded):
    r = client.post("/auth/login", json={"email": RAMESH[0], "password": RAMESH[1]})
    r2v = client.post("/auth/verify-otp", json={"email": RAMESH[0], "otp": r.json()["dev_otp"], "purpose": "login"})
    refresh = r2v.json()["refresh_token"]
    r2 = client.post("/auth/refresh", json={"refresh_token": refresh})
    assert r2.status_code == 200 and r2.json()["access_token"]


def test_officer_cannot_self_activate(client, seeded):
    r = client.post("/auth/login", json={"email": "officer.new@cpcl.gov.in", "password": "Pending@1234"})
    assert r.status_code == 403
    assert "Admin approval" in r.json()["detail"]


# ------------------------------ AUTHORIZATION -------------------------------

def test_bidder_isolation(client, seeded):
    t_ramesh = login(client, *RAMESH)
    t_sita = login(client, *SITA)
    apps_r = client.get("/bidder/applications", headers=H(t_ramesh)).json()["applications"]
    ramesh_ids = {a["id"] for a in apps_r}
    assert ramesh_ids
    # sita must not fetch ramesh's application
    a_id = next(iter(ramesh_ids))
    r = client.get(f"/bidder/applications/{a_id}", headers=H(t_sita))
    assert r.status_code == 404


def test_l1_jurisdiction_isolation(client, seeded):
    t_l1_bel = login(client, *L1)
    t_l1_hub = login(client, *L1HUB)
    apps_bel = client.get("/officer/applications", headers=H(t_l1_bel)).json()["applications"]
    apps_hub = client.get("/officer/applications", headers=H(t_l1_hub)).json()["applications"]
    bel_nos = {a["application_no"] for a in apps_bel}
    hub_nos = {a["application_no"] for a in apps_hub}
    # Belagavi apps must not be visible to Hubli officer
    assert "APP-2026-000001" in bel_nos
    assert "APP-2026-000001" not in hub_nos
    # and direct access is 403
    app_obj = next(a for a in apps_bel if a["application_no"] == "APP-2026-000001")
    r = client.get(f"/officer/applications/{app_obj['id']}", headers=H(t_l1_hub))
    assert r.status_code == 403


def test_l2_scope_and_admin_restriction(client, seeded):
    t_l2 = login(client, *L2)
    t_ramesh = login(client, *RAMESH)
    # bidder cannot access admin APIs
    r = client.get("/admin/dashboard", headers=H(t_ramesh))
    assert r.status_code == 403
    # bidder cannot access L2 APIs
    r = client.get("/l2/dashboard", headers=H(t_ramesh))
    assert r.status_code == 403
    # L2 can access its own dashboard
    r = client.get("/l2/dashboard", headers=H(t_l2))
    assert r.status_code == 200


def test_admin_only_endpoints(client, seeded):
    t_l1 = login(client, *L1)
    assert client.get("/admin/audit-logs", headers=H(t_l1)).status_code == 403
    assert client.get("/admin/users", headers=H(t_l1)).status_code == 403


# --------------------------- ELIGIBILITY / RULES ----------------------------

def _tender1(client, tok):
    ts = client.get("/bidder/tenders", headers=H(tok)).json()["tenders"]
    return next(t for t in ts if t["tender_no"] == "TDR-2026-001")


def test_rule_pass_clean_bidder(client, seeded):
    tok = login(client, *RAMESH)
    t1 = _tender1(client, tok)
    el = client.get(f"/bidder/tenders/{t1['id']}", headers=H(tok)).json()["eligibility"]
    # Two-layer compliance model:
    #  Layer 1 (STATUTORY) — general statutory & GeM rules: a clean bidder passes all of them.
    #  Layer 2 (SECTOR_TECHNICAL) — tender-specific petroleum technical & safety rules:
    #  Ramesh's mock BIS/OEM records are missing, so these resolve to manual review
    #  (never auto-FAIL to NOT_ELIGIBLE) -> CONDITIONAL, and Apply stays enabled.
    statutory = [r for r in el["checklist"] if r.get("compliance_layer") != "SECTOR_TECHNICAL"]
    sector = [r for r in el["checklist"] if r.get("compliance_layer") == "SECTOR_TECHNICAL"]
    assert statutory and all(r["result"] == "PASS" for r in statutory)
    assert sector, "t1 must carry tender-specific sector technical & safety rules"
    assert el["result"] == "CONDITIONAL"
    assert el["apply_allowed"] is True


def test_rule_fail_and_blacklist_hard_fail(client, seeded):
    tok = login(client, *VIKRAM)
    t1 = _tender1(client, tok)
    el = client.get(f"/bidder/tenders/{t1['id']}", headers=H(tok)).json()["eligibility"]
    # A blacklist hit is labelled BLACKLISTED — the one true label, never the
    # generic "Not Eligible" — and disables application for every tender.
    assert el["result"] == "BLACKLISTED"
    assert el["risk_level"] == "CRITICAL"
    bl = next(r for r in el["checklist"] if r["rule_key"] == "not_blacklisted")
    assert bl["result"] == "FAIL" and bl["hard_disqualification"]
    assert el["apply_allowed"] is False
    assert any("Blacklist" in reason or "blacklist" in reason.lower() for reason in el["apply_blocked_reasons"])


def test_source_unavailable_never_pass(client, seeded):
    tok = login(client, *MEENA)
    t1 = _tender1(client, tok)
    el = client.get(f"/bidder/tenders/{t1['id']}", headers=H(tok)).json()["eligibility"]
    epfo = next(r for r in el["checklist"] if r["rule_key"] == "epfo_compliant")
    assert epfo["result"] == "UNABLE_TO_VERIFY"
    assert el["result"] == "CONDITIONAL"  # manual review, never PASS/eligible


def test_blacklist_submitted_app_is_critical_on_verification(client, seeded):
    tok_l1 = login(client, *L1)
    apps = client.get("/officer/applications", headers=H(tok_l1)).json()["applications"]
    app_c = next(a for a in apps if a["application_no"] == "APP-2026-000003")
    r = client.post(f"/officer/applications/{app_c['id']}/run-verification", headers=H(tok_l1))
    assert r.status_code == 200
    assert r.json()["risk_level"] == "CRITICAL"
    rep = client.get(f"/officer/applications/{app_c['id']}/report", headers=H(tok_l1)).json()
    assert rep["blacklist_findings"], "blacklist finding must be recorded with evidence"
    assert rep["rule_results"], "rule evidence must be persisted"


# ------------------------------ APPLICATION ---------------------------------

def test_eligible_submission_and_routing(client, seeded):
    # seed already contains APP-2026-000001 routed to Belagavi L1; verify routing recorded
    tok_l1 = login(client, *L1)
    apps = client.get("/officer/applications", headers=H(tok_l1)).json()["applications"]
    app = next(a for a in apps if a["application_no"] == "APP-2026-000001")
    review = client.get(f"/officer/applications/{app['id']}", headers=H(tok_l1)).json()
    assert review["application"]["assigned_l1"] == "Anita Deshmukh"
    assert review["application"]["routing_method"] == "EXACT_JURISDICTION"
    assert review["application"]["status"] == "AI Verification Not Run"


def test_hard_fail_submission_prevented(client, seeded):
    """A fresh blacklisted bidder cannot submit a new application."""
    # register a new blacklisted bidder (same PAN as debarred entity) so the
    # submission reaches the blacklist hard-fail check.
    r = client.post("/auth/register", json={
        "email": "newblack@bidder.in", "password": "Black@1234", "full_name": "Vikram Rao",
        "mobile": "9900009999",
        "company_name": "VIKRAM INDUSTRIAL SUPPLIES", "pan": "AAVCV9012L",
        "gstin": "29AAVCV9012L1Z7", "district": "Belagavi", "state": "Karnataka",
    })
    assert r.status_code == 200, r.text[:300]
    r = client.post("/auth/verify-otp", json={"email": "newblack@bidder.in", "otp": r.json()["dev_otp"], "purpose": "register"})
    assert r.status_code == 200, r.text[:300]
    tok = r.json()["access_token"]
    client.post("/bidder/consent", json={"sources": ["ALL"]}, headers=H(tok))
    t1 = _tender1(client, tok)
    r = client.post(
        f"/bidder/applications?tender_id={t1['id']}",
        headers=H(tok),
        json={"declaration_text": "I hereby declare that the information furnished in this application is true and complete.",
              "consent_confirmed": True},
    )
    assert r.status_code == 409
    assert "APPLICATION_BLOCKED" in r.json()["detail"]["code"]


# ------------------------------ VERIFICATION --------------------------------

def test_verification_report_persisted_with_evidence(client, seeded):
    tok_l1 = login(client, *L1)
    apps = client.get("/officer/applications", headers=H(tok_l1)).json()["applications"]
    app_a = next(a for a in apps if a["application_no"] == "APP-2026-000001")
    if app_a["status"] != "Approved":
        r = client.post(f"/officer/applications/{app_a['id']}/run-verification", headers=H(tok_l1))
        assert r.status_code == 200
    rep = client.get(f"/officer/applications/{app_a['id']}/report", headers=H(tok_l1)).json()
    assert rep["compliance_score"] is not None
    assert rep["risk_level"] in ("LOW", "MEDIUM", "HIGH", "CRITICAL")
    assert rep["rule_results"], "rule results with evidence"
    assert rep["cross_verification"], "cross-verification table"
    assert rep["source_results"], "source timestamps"
    assert rep["ai_explanation"], "AI explanation"
    assert rep["model_identifier"], "model/provider identifier stored"


def test_suspicious_document_flagged_not_verified(client, seeded):
    tok_l1 = login(client, *L1)
    apps = client.get("/officer/applications", headers=H(tok_l1)).json()["applications"]
    app_b = next(a for a in apps if a["application_no"] == "APP-2026-000002")
    r = client.post(f"/officer/applications/{app_b['id']}/run-verification", headers=H(tok_l1))
    assert r.status_code == 200
    rep = client.get(f"/officer/applications/{app_b['id']}/report", headers=H(tok_l1)).json()
    udyam = next(a for a in rep["authenticity"] if "Udyam" in (a.get("document_name") or ""))
    assert udyam["status"] in ("SUSPICIOUS", "MANUAL_REVIEW_REQUIRED")
    assert udyam["status"] != "VERIFIED", "mismatched document must never be VERIFIED"


def test_authenticity_uncertainty_routes_to_manual_review(client, seeded):
    tok_l1 = login(client, *L1)
    apps = client.get("/officer/applications", headers=H(tok_l1)).json()["applications"]
    app_d = next(a for a in apps if a["application_no"] == "APP-2026-000004")
    r = client.post(f"/officer/applications/{app_d['id']}/run-verification", headers=H(tok_l1))
    assert r.status_code == 200
    assert r.json()["eligibility_result"] != "ELIGIBLE", "unverifiable source must not yield clean eligibility"
    rep = client.get(f"/officer/applications/{app_d['id']}/report", headers=H(tok_l1)).json()
    assert any(s.get("status") == "UNAVAILABLE" for s in rep["source_results"].values())


# --------------------------- OVERRIDE ACCOUNTABILITY -------------------------

def _get_app(client, tok_l1, app_no):
    apps = client.get("/officer/applications", headers=H(tok_l1)).json()["applications"]
    return next(a for a in apps if a["application_no"] == app_no)


def test_override_interception_and_full_chain(client, seeded):
    tok_l1 = login(client, *L1)
    tok_l2 = login(client, *L2)
    tok_admin = login(client, *ADMIN)
    app_c = _get_app(client, tok_l1, "APP-2026-000003")

    # interception
    r = client.post(f"/officer/applications/{app_c['id']}/decision", headers=H(tok_l1),
                    json={"action": "RECOMMEND_QUALIFY", "remarks": "try"})
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["override_required"] is True
    assert detail["finding"]["blacklisted"] is True
    challenge = detail["challenge"]

    # justification validation
    reauth = client.post("/auth/reauth", headers=H(tok_l1), json={"password": L1[1]}).json()["reauth_token"]
    r = client.post(f"/officer/applications/{app_c['id']}/override", headers=H(tok_l1),
                    json={"challenge": challenge, "reason_category": "FALSE_POSITIVE",
                          "justification": "too short", "reauth_token": reauth})
    assert r.status_code == 400

    # wrong password rejected
    r = client.post("/auth/reauth", headers=H(tok_l1), json={"password": "bad-password-123"})
    assert r.status_code == 401

    # correct re-auth accepted
    reauth = client.post("/auth/reauth", headers=H(tok_l1), json={"password": L1[1]}).json()["reauth_token"]
    r = client.post(f"/officer/applications/{app_c['id']}/override", headers=H(tok_l1),
                    json={"challenge": challenge,
                          "reason_category": "PORTAL_DATA_MISMATCH_MANUALLY_VERIFIED",
                          "justification": "Debarment order withdrawn per CPCL revision 88/2026; offline verification with regional office on 2026-09-10; full documentation attached.",
                          "reauth_token": reauth})
    assert r.status_code == 200
    od = r.json()
    assert od["severity"] == "CRITICAL"
    assert od["admin_cosign_required"] is True
    assert od["vigilance_referral_id"]
    assert od["application_status"] == "Admin Co-sign Required"

    # L2 cannot finalize before Admin co-sign
    r = client.post(f"/l2/applications/{app_c['id']}/decision", headers=H(tok_l2),
                    json={"action": "APPROVE", "remarks": "early approve"})
    assert r.status_code == 409

    # admin sees override + co-signs
    rows = client.get("/admin/overrides", headers=H(tok_admin)).json()["overrides"]
    row = next(o for o in rows if o["application_no"] == "APP-2026-000003")
    assert row["severity"] == "CRITICAL"
    r = client.post(f"/admin/overrides/{row['id']}/cosign", headers=H(tok_admin))
    assert r.status_code == 200

    # now L2 can approve
    r = client.post(f"/l2/applications/{app_c['id']}/decision", headers=H(tok_l2),
                    json={"action": "APPROVE", "remarks": "Approved with full accountability chain"})
    assert r.status_code == 200 and r.json()["status"] == "Approved"

    # vigilance referral exists and is trackable
    refs = client.get("/admin/vigilance-referrals", headers=H(tok_admin)).json()["referrals"]
    ref = next(x for x in refs if x["application_no"] == "APP-2026-000003")
    assert ref["status"] in ("DRAFT", "REVIEWED", "READY_FOR_CVC_FILING", "FILED_MANUALLY")

    # admin notification generated
    notifs = client.get("/notifications", headers=H(tok_admin)).json()["notifications"]
    assert any(n["event_type"] in ("admin_cosign_required", "vigilance_referral_generated", "override_alert") for n in notifs)


def test_audit_chain_integrity_after_override_flow(client, seeded):
    tok_admin = login(client, *ADMIN)
    r = client.get("/admin/audit-logs/verify", headers=H(tok_admin))
    assert r.json()["valid"] is True
    logs = client.get("/admin/audit-logs", headers=H(tok_admin)).json()["logs"]
    actions = {x["action"] for x in logs}
    assert "override_created" in actions
    assert "admin_cosign_completed" in actions
    # every record has prev + current hash
    assert all(x["record_hash"] and x["prev_hash"] for x in logs)


def test_blacklist_db_no_delete_and_labels(client, seeded):
    tok_admin = login(client, *ADMIN)
    r = client.get("/admin/blacklist", headers=H(tok_admin))
    assert r.json()["label"].startswith("Reference Blacklist Database")
    entries = r.json()["entries"]
    assert any(e["entity_name"] == "VIKRAM INDUSTRIAL SUPPLIES" for e in entries)
    # status transitions allowed, but no DELETE endpoint
    e = next(x for x in entries if x["entity_name"] == "VIKRAM INDUSTRIAL SUPPLIES")
    r = client.post(f"/admin/blacklist/{e['id']}/status", headers=H(tok_admin),
                    json={"status": "APPEAL_PENDING", "review_status": "appeal filed"})
    assert r.status_code == 200
    assert client.delete(f"/admin/blacklist/{e['id']}", headers=H(tok_admin)).status_code in (404, 405)
