"""End-to-end smoke test — Scenario A flow (TDR-2026-001, Ramesh):
eligibility pre-check → bid sealing gate → audited force-open → AI
compliance verification → readable report → clarification round-trip.
Run after a reseed, against the live API on :8000.
"""
import httpx, json, sys

BASE = "http://127.0.0.1:8000"
c = httpx.Client(timeout=120)

def login(email, pwd):
    r = c.post(f"{BASE}/auth/login", json={"email": email, "password": pwd})
    assert r.status_code == 200, f"login failed {email}: {r.text}"
    d = r.json()
    if d.get("otp_required"):
        r2 = c.post(f"{BASE}/auth/verify-otp", json={"email": email, "otp": d["dev_otp"], "purpose": "login"})
        assert r2.status_code == 200, f"otp failed {email}: {r2.text}"
        d = r2.json()
    return d["access_token"], d["user"]

def H(tok):
    return {"Authorization": f"Bearer {tok}"}

failures = []
def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name} {detail}")
    if not cond:
        failures.append(name)

# 1. health
r = c.get(f"{BASE}/health")
check("health", r.status_code == 200 and r.json()["database"] == "up")

# 2. 2FA logins
tok_ramesh, _ = login("ramesh@bidder.in", "Bidder@1234")
tok_officer, _ = login("procurement.belagavi@cpcl.gov.in", "Officer@1234")
tok_admin, _ = login("admin@cpcl.gov.in", "Admin@1234")
check("2FA logins (bidder + officer + admin)", True)

# 3. bidder: tenders + eligibility pre-check
r = c.get(f"{BASE}/bidder/tenders", headers=H(tok_ramesh))
tenders = r.json()["tenders"]
t1 = next(t for t in tenders if t["tender_no"] == "TDR-2026-001")
check("tenders list includes final demo set", len(tenders) >= 14, f"{len(tenders)} tenders")
r = c.get(f"{BASE}/bidder/tenders/{t1['id']}", headers=H(tok_ramesh))
el = r.json()["eligibility"]
check("pre-check t1: Ramesh CONDITIONAL (stale BIS + missing OEM)",
      el.get("result") == "CONDITIONAL" and el.get("compliance_score") == 65,
      f"result={el.get('result')} score={el.get('compliance_score')} risk={el.get('risk_level')}")
fails = [f for f in el.get("checklist", []) if f.get("result") == "FAIL"]
check("pre-check lists specific FAIL findings", len(fails) >= 2, str([f.get('label') for f in fails])[:120])

# 4. sealed state (before opening): identity hidden + detail blocked
apps = c.get(f"{BASE}/officer/applications", headers=H(tok_officer)).json()["applications"]
ra = next(a for a in apps if a["application_no"] == "APP-2026-000001")
check("sealed queue row (identity hidden)", ra.get("sealed") is True and ra.get("company") is None,
      f"sealed={ra.get('sealed')} company={ra.get('company')}")
r = c.get(f"{BASE}/officer/applications/{ra['id']}", headers=H(tok_officer))
check("sealed detail blocked (403 BIDS_SEALED)", r.status_code == 403 and r.json()["detail"]["code"] == "BIDS_SEALED")
r = c.post(f"{BASE}/officer/applications/{ra['id']}/run-verification", headers=H(tok_officer))
check("sealed verification blocked (403 BIDS_SEALED)", r.status_code == 403 and r.json()["detail"]["code"] == "BIDS_SEALED")

# 5. audited admin force-open (the demo's opening step)
r = c.post(f"{BASE}/admin/tenders/{t1['id']}/open-bids", headers=H(tok_admin))
check("admin force-open t1", r.status_code == 200, r.json().get("message", "")[:60])
logs = c.get(f"{BASE}/admin/audit-logs", params={"action": "tender_bids_force_opened"}, headers=H(tok_admin)).json()
check("force-open audited", any(e["action"] == "tender_bids_force_opened" for e in logs["logs"]))

# 6. after opening: detail visible + AI verification
r = c.get(f"{BASE}/officer/applications/{ra['id']}", headers=H(tok_officer))
check("unsealed detail (identity visible)", r.status_code == 200 and r.json()["application"].get("company"),
      r.json().get("application", {}).get("company", ""))
r = c.post(f"{BASE}/officer/applications/{ra['id']}/run-verification", headers=H(tok_officer))
check("AI verification ran", r.status_code == 200 and r.json().get("compliance_score") is not None,
      f"score={r.json().get('compliance_score')} risk={r.json().get('risk_level')} result={r.json().get('eligibility_result')}")
rv = c.get(f"{BASE}/officer/applications/{ra['id']}", headers=H(tok_officer)).json()
rep = rv.get("verification_report", {})
summ = rep.get("officer_summary", {})
check("readable explanation (verdict + one-line + narrative)",
      bool(summ.get("verdict")) and bool(summ.get("one_line")) and bool(rep.get("ai_explanation")))
check("role-scoped documents present", len(rv.get("documents", [])) > 0,
      f"{len(rv.get('documents', []))} docs scope={rv.get('doc_scope', {}).get('visible_types')}")

# 7. readable report endpoint
r = c.get(f"{BASE}/officer/applications/{ra['id']}/report", headers=H(tok_officer))
check("report endpoint", r.status_code == 200)

# 8. clarification round-trip (officer asks, bidder answers)
r = c.post(f"{BASE}/officer/applications/{ra['id']}/decision", headers=H(tok_officer),
           json={"action": "REQUEST_CLARIFICATION",
                 "remarks": "Please confirm the revised BIS certificate version.",
                 "clarification_reason": "BIS certificate shows the pre-2023 standard; the corrigendum requires IS 1141:2023."})
check("officer REQUEST_CLARIFICATION (cross-cutting)", r.status_code == 200, r.text[:80])
r = c.get(f"{BASE}/bidder/applications/{ra['id']}", headers=H(tok_ramesh))
clars = r.json().get("clarifications", [])
open_c = next((x for x in clars if x.get("status") == "OPEN"), None)
check("bidder sees OPEN clarification", bool(open_c))
if open_c:
    r = c.post(f"{BASE}/bidder/applications/{ra['id']}/clarification-response", headers=H(tok_ramesh),
               json={"clarification_id": open_c["id"],
                     "response_text": "Certificate renewal to IS 1141:2023 is in progress; certificate of conformity uploaded in the vault."})
    check("bidder responds to clarification", r.status_code == 200, r.text[:80])
    r = c.get(f"{BASE}/bidder/applications/{ra['id']}", headers=H(tok_ramesh))
    check("application resubmitted after response", r.json().get("status") == "Resubmitted", r.json().get("status", ""))

print()
if failures:
    print(f"SMOKE A: {len(failures)} FAILURES: {failures}")
    sys.exit(1)
print("SMOKE A (eligibility → sealing → opening → AI verification → clarification): ALL PASSED")
