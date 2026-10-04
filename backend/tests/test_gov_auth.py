"""Government-portal sign-in: Govt Unique ID + OTP to the registered phone.

Covers:
* seeded ACTIVE employee signs in with Govt ID + OTP (tokens, correct user)
* unknown Govt ID → 400
* pending-approval employee → OTP verifies but NO token + pending message
* admin employee master: create (PENDING_APPROVAL) → approve → sign-in works
* admin suspends → sign-in blocked (403)
* list endpoint masks the phone; create duplicate Govt ID → 409
* wrong OTP → 400/429, not a token
"""
import sys

sys.path.insert(0, ".")
from conftest import H, login, gov_login, ADMIN_CREDS  # noqa: E402


def test_seeded_gov_login_works(client, seeded):
    r = gov_login(client, "GOI-PNG-2018-0492")
    assert r.get("access_token"), r
    assert r.get("refresh_token")
    user = r["user"]
    assert user["email"] == "procurement.belagavi@cpcl.gov.in"
    assert user["role"] == "PROCUREMENT_OFFICER"
    assert user["status"] == "ACTIVE"


def test_gov_login_unknown_id(client, seeded):
    r = client.post("/auth/gov-login", json={"gov_id": "NOPE-999"})
    assert r.status_code == 400, r.text


def test_gov_login_pending_no_token(client, seeded):
    # GOI-PNG-2025-1102 is seeded PENDING_APPROVAL — the OTP must reach the phone and
    # verify, but a token must NOT be issued until the System Admin approves.
    r = client.post("/auth/gov-login", json={"gov_id": "GOI-PNG-2025-1102"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("pending_approval") is True
    dev = body.get("dev_otp")
    assert dev, "console mode required"
    r2 = client.post("/auth/gov-verify", json={"gov_id": "GOI-PNG-2025-1102", "otp": dev})
    assert r2.status_code == 200, r2.text
    v = r2.json()
    assert v.get("pending_approval") is True
    assert "access_token" not in v, v
    assert "user" not in v or not v.get("user")


def test_admin_create_approve_suspend_flow(client, seeded):
    admin = login(client, *ADMIN_CREDS)
    ah = H(admin)

    # 1. create — pending approval by design
    r = client.post(
        "/admin/gov-employees",
        json={
            "gov_id": "GOI-PNG-2024-8899",
            "full_name": "Test Officer",
            "phone": "+919811112222",
            "role": "TECHNICAL_EVALUATOR",
            "department": "QA",
        },
        headers=ah,
    )
    assert r.status_code == 201, r.text
    emp = r.json()
    assert emp["status"] == "PENDING_APPROVAL"
    emp_id = emp["id"]

    # 2. pending: sign-in must not yield a token
    r = client.post("/auth/gov-login", json={"gov_id": "GOI-PNG-2024-8899"})
    assert r.status_code == 200 and r.json().get("pending_approval") is True
    dev = r.json().get("dev_otp")
    assert dev
    r2 = client.post("/auth/gov-verify", json={"gov_id": "GOI-PNG-2024-8899", "otp": dev})
    assert r2.status_code == 200
    assert r2.json().get("pending_approval") is True
    assert "access_token" not in r2.json()

    # 3. approve
    r = client.post(f"/admin/gov-employees/{emp_id}/approve", headers=ah)
    assert r.status_code == 200, r.text

    # 4. now sign-in works
    r = gov_login(client, "GOI-PNG-2024-8899")
    assert r.get("access_token"), r
    assert r["user"]["role"] == "TECHNICAL_EVALUATOR"
    assert r["user"]["email"] == "goi-png-2024-8899@gov.in"

    # 5. suspend → sign-in blocked
    r = client.post(f"/admin/gov-employees/{emp_id}/suspend", headers=ah)
    assert r.status_code == 200, r.text
    r = client.post("/auth/gov-login", json={"gov_id": "GOI-PNG-2024-8899"})
    assert r.status_code == 403, r.text


def test_gov_list_masks_phone_and_duplicate_rejected(client, seeded):
    admin = login(client, *ADMIN_CREDS)
    ah = H(admin)

    r = client.get("/admin/gov-employees", headers=ah)
    assert r.status_code == 200, r.text
    rows = r.json()["employees"]
    assert len(rows) >= 15
    by_id = {e["gov_id"]: e for e in rows}
    # phone must be masked, never the raw E.164 value
    e3 = by_id["GOI-PNG-2018-0492"]
    assert "phone_masked" in e3
    assert e3["phone_masked"] != "+919164346113"
    assert "phone" not in e3  # raw phone never returned
    assert e3["user_email"] == "procurement.belagavi@cpcl.gov.in"

    # duplicate Govt ID rejected
    r = client.post(
        "/admin/gov-employees",
        json={
            "gov_id": "goi-png-2018-0492",  # case-insensitive duplicate
            "full_name": "Dup",
            "phone": "+919999999999",
            "role": "CFA",
        },
        headers=ah,
    )
    assert r.status_code == 409, r.text


def test_gov_wrong_otp_rejected(client, seeded):
    r = client.post("/auth/gov-login", json={"gov_id": "GOI-PNG-2012-0349"})
    assert r.status_code == 200, r.text
    r2 = client.post("/auth/gov-verify", json={"gov_id": "GOI-PNG-2012-0349", "otp": "000000"})
    assert r2.status_code in (400, 429), r2.text
    body = r2.json() if r2.headers.get("content-type", "").startswith("application/json") else {}
    assert "access_token" not in (body or {})


def test_test_env_never_fires_demo_sms(client, seeded):
    """The suite must run against a server with the Twilio demo-SMS proof
    disabled — otherwise every OTP issue in the suite would fire a real SMS
    at a real phone."""
    h = client.get("/health").json()
    assert h.get("demo_sms") is False, (
        f"test server has demo_sms enabled ({h.get('demo_sms')}) — tests would "
        "fire real SMS at real phones"
    )
