"""V2.1 Slice 1 — identity, roles, organization isolation, IP-aware audit."""
import json

import httpx
import pytest

from conftest import H, login

ADMIN = ("admin@cpcl.gov.in", "Admin@1234")
AUDITOR = ("auditor@cpcl.gov.in", "Auditor@1234")
PROC_OFFICER = ("procurement.belagavi@cpcl.gov.in", "Officer@1234")
FIN_EVALUATOR = ("finance.karnataka@cpcl.gov.in", "L2Officer@1234")
RAMESH = ("ramesh@bidder.in", "Bidder@1234")
SITA = ("sita@bidder.in", "Bidder@1234")
PREPARER = ("preparer@rameshtraders.in", "Preparer@1234")


# --------------------------- IP context (unit) ----------------------------

def test_ip_normalization():
    from app.ipcontext import normalize_ip, mask_ip
    assert normalize_ip("203.0.113.7") == "203.0.113.7"
    assert normalize_ip("::ffff:203.0.113.7") == "203.0.113.7"
    assert normalize_ip("FE80::1%eth0".upper()) == "fe80::1"
    assert normalize_ip("garbage") is None
    assert mask_ip("203.0.113.7") == "203.0.*.*"
    assert mask_ip(None) == "—"


def test_ip_spoofed_forwarded_from_untrusted_peer_is_ignored(monkeypatch):
    from app import ipcontext
    monkeypatch.setattr(ipcontext.settings, "TRUSTED_PROXY_CIDRS", "")
    # Attacker puts a fake X-Forwarded-For; peer is the direct socket — must win.
    ip, src = ipcontext.resolve_client_ip("198.51.100.9", x_forwarded_for="1.2.3.4, 5.6.7.8")
    assert ip == "198.51.100.9" and src == "DIRECT"


def test_ip_trusted_proxy_chain(monkeypatch):
    from app import ipcontext
    monkeypatch.setattr(ipcontext.settings, "TRUSTED_PROXY_CIDRS", "10.0.0.0/8")
    # Direct peer is a trusted proxy; XFF: client, trusted-proxy
    ip, src = ipcontext.resolve_client_ip("10.1.2.3", x_forwarded_for="192.0.2.55, 10.9.9.9")
    assert ip == "192.0.2.55" and src == "TRUSTED_PROXY"
    # Entire chain trusted → no client address (malformed/unknown)
    ip2, src2 = ipcontext.resolve_client_ip("10.1.2.3", x_forwarded_for="10.9.9.9, 10.8.8.8")
    assert ip2 is None and src2 == "MALFORMED"


# --------------------------- role model ------------------------------------

def test_new_roles_login(client, seeded):
    for creds in (PROC_OFFICER, FIN_EVALUATOR, AUDITOR):
        tok = login(client, *creds)
        me = client.get("/auth/me", headers=H(tok)).json()
        assert me["role"] != "L1_OFFICER" and me["role"] != "L2_OFFICER"


def test_admin_cannot_be_procurement_decision_maker(client, seeded):
    """ADMIN has no tender assignment → require_tender_role must 403."""
    from app.core.db import SessionLocal
    from app.models import User, Tender
    from app.policies.permissions import require_tender_role

    db = SessionLocal()
    try:
        admin = db.query(User).filter(User.email == ADMIN[0]).first()
        tender = db.query(Tender).filter(Tender.tender_no == "TDR-2026-001").first()
        with pytest.raises(Exception) as ei:
            require_tender_role(db, admin, tender.id, "PROCUREMENT_OFFICER")
        assert "403" in str(ei.value.status_code)
        # Assigned procurement officer passes
        proc = db.query(User).filter(User.email == PROC_OFFICER[0]).first()
        role = require_tender_role(db, proc, tender.id, "PROCUREMENT_OFFICER", "CFA")
        assert role == "PROCUREMENT_OFFICER"
    finally:
        db.close()


# --------------------------- organization isolation ------------------------

def test_bidder_org_scoped_access(client, seeded):
    tok_ramesh = login(client, *RAMESH)
    me = client.get("/auth/me", headers=H(tok_ramesh)).json()
    assert any(o["trade_name"] == "Ramesh Traders" for o in me["organizations"])
    org_id = me["organizations"][0]["id"]
    # Ramesh (owner) sees members incl. preparer & signatory
    members = client.get(f"/organizations/{org_id}/members", headers=H(tok_ramesh)).json()
    roles = {m["org_role"] for m in members["members"]}
    assert {"OWNER", "PREPARER", "SIGNATORY"} <= roles

    # Sita has no membership in Ramesh's org → policy-safe 404 (no metadata)
    tok_sita = login(client, *SITA)
    r = client.get(f"/organizations/{org_id}/members", headers=H(tok_sita))
    assert r.status_code == 404

    # Preparer can view the org but not manage members
    tok_prep = login(client, *PREPARER)
    assert client.get(f"/organizations/{org_id}/members", headers=H(tok_prep)).status_code == 200
    r = client.post(f"/organizations/{org_id}/members", headers=H(tok_prep),
                    json={"email": "x@y.com", "full_name": "X Y", "password": "password123", "org_role": "PREPARER"})
    assert r.status_code == 403


def test_member_add_and_revoke_by_owner(client, seeded):
    tok_ramesh = login(client, *RAMESH)
    org_id = client.get("/auth/me", headers=H(tok_ramesh)).json()["organizations"][0]["id"]
    # add
    r = client.post(f"/organizations/{org_id}/members", headers=H(tok_ramesh),
                    json={"email": "newsignatory@rameshtraders.in", "full_name": "New Signatory",
                          "password": "Signatory@9", "org_role": "SIGNATORY", "mobile": "9900011223"})
    assert r.status_code == 201, r.text
    new_id = r.json()["user_id"]
    # new member can log in and sees the org
    tok_new = login(client, "newsignatory@rameshtraders.in", "Signatory@9")
    assert client.get("/organizations", headers=H(tok_new)).json()["organizations"]
    # owner cannot revoke self
    me = client.get("/auth/me", headers=H(tok_ramesh)).json()
    r = client.delete(f"/organizations/{org_id}/members/{me['id']}", headers=H(tok_ramesh))
    assert r.status_code == 400
    # owner revokes the new member
    r = client.delete(f"/organizations/{org_id}/members/{new_id}", headers=H(tok_ramesh))
    assert r.status_code == 204
    r = client.get("/organizations", headers=H(tok_new))
    assert r.json()["organizations"] == []


# --------------------------- IP-aware audit --------------------------------

def test_audit_login_event_carries_ip_and_request_id(client, seeded):
    login(client, *PROC_OFFICER)
    tok = login(client, *ADMIN)
    rows = client.get("/admin/audit-logs", headers=H(tok), params={"action": "login", "limit": 5}).json()["logs"]
    assert rows, "expected login audit events"
    recent = rows[0]
    assert recent["source_ip"] is not None
    assert recent["ip_source"] in ("DIRECT", "TRUSTED_PROXY")
    assert recent["request_id"]


def test_audit_chain_includes_ip_and_verifies(client, seeded):
    from app.core.db import SessionLocal
    from app.audit import verify_chain
    login(client, *PROC_OFFICER)
    db = SessionLocal()
    try:
        res = verify_chain(db)
        assert res["valid"] is True, res
    finally:
        db.close()


def test_reveal_ip_requires_reauth_and_is_audited(client, seeded):
    tok = login(client, *ADMIN)
    rows = client.get("/admin/audit-logs", headers=H(tok), params={"limit": 10}).json()["logs"]
    target = next(r for r in rows if r["source_ip"])
    # wrong password → 401
    r = client.post("/admin/audit-logs/reveal-ip", headers=H(tok),
                    json={"event_id": target["id"], "password": "Wrong@123"})
    assert r.status_code == 401
    # correct password → full IP + the reveal is itself audited
    r = client.post("/admin/audit-logs/reveal-ip", headers=H(tok),
                    json={"event_id": target["id"], "password": ADMIN[1]})
    assert r.status_code == 200
    assert r.json()["source_ip"] == target["source_ip"]
    revealed = client.get("/admin/audit-logs", headers=H(tok), params={"action": "audit_ip_revealed"}).json()["logs"]
    assert revealed and revealed[0]["payload"]["target_event"] == target["id"]


def test_masked_ip_default_and_auditor_access(client, seeded):
    login(client, *PROC_OFFICER)
    tok_auditor = login(client, *AUDITOR)
    rows = client.get("/admin/audit-logs", headers=H(tok_auditor), params={"limit": 5}).json()["logs"]
    assert rows  # auditor can read audit
    with_ip = next(r for r in rows if r["source_ip"] not in (None, "—"))
    assert "*" in with_ip["source_ip"]  # masked for auditor (default)
    # Same event: admin holding VIEW_FULL_IP sees the full address.
    tok_admin = login(client, *ADMIN)
    rows2 = client.get("/admin/audit-logs", headers=H(tok_admin), params={"limit": 5}).json()["logs"]
    same = next(r for r in rows2 if r["id"] == with_ip["id"])
    assert "*" not in same["source_ip"]
    assert same["source_ip"].startswith(with_ip["source_ip"].split(".*.*")[0])


# --------------------------- session rotation ------------------------------

def test_session_rotation_on_login_and_refresh(client, seeded):
    r = client.post("/auth/login", json={"email": RAMESH[0], "password": RAMESH[1]})
    r = client.post("/auth/verify-otp", json={"email": RAMESH[0], "otp": r.json()["dev_otp"], "purpose": "login"})
    t1 = r.json()
    r2 = client.post("/auth/refresh", json={"refresh_token": t1["refresh_token"]})
    t2 = r2.json()
    import base64, json as _json

    def sid(tok):
        part = tok.split(".")[1]
        part += "=" * (-len(part) % 4)
        return _json.loads(base64.urlsafe_b64decode(part))

    s1, s2 = sid(t1["access_token"]), sid(t2["access_token"])
    assert "sid" in s1 and "jti" in s1
    assert s2["sid"] != s1["sid"]  # rotated on refresh
    assert s1["jti"] != s2["jti"]
