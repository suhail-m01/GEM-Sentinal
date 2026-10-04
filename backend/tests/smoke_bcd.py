"""End-to-end smoke tests — Scenarios B, C, D (critical acceptance flows):
B = compliance gap (Sita, 33/HIGH), C = blacklist (Vikram, all tenders),
D = unable-to-verify (Meena, source unavailable), plus the per-district
sealing split (Hubli t2 open), watch tower and the hash-chained audit log.
Run after a reseed (and after smoke_a, which opens the sealed tender),
against the live API on :8000.
"""
import httpx, sys

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
    return d["access_token"]

def H(tok):
    return {"Authorization": f"Bearer {tok}"}

failures = []
def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name} {detail}")
    if not cond:
        failures.append(name)

tok_officer = login("procurement.belagavi@cpcl.gov.in", "Officer@1234")
tok_hubli = login("procurement.hubli@cpcl.gov.in", "Officer1@1234")
tok_admin = login("admin@cpcl.gov.in", "Admin@1234")
tok_sita = login("sita@bidder.in", "Bidder@1234")
tok_vikram = login("vikram@bidder.in", "Bidder@1234")
tok_meena = login("meena@bidder.in", "Bidder@1234")
check("2FA logins (5 users)", True)

tenders = c.get(f"{BASE}/admin/tenders", headers=H(tok_admin)).json()["tenders"]
t1 = next(t for t in tenders if t["tender_no"] == "TDR-2026-001")
t2 = next(t for t in tenders if t["tender_no"] == "TDR-2026-002")
t5 = next(t for t in tenders if t["tender_no"] == "TDR-2026-005")

# make sure the sealed Belagavi demo tender is open for evaluation checks
# (smoke_a does this; idempotent here for standalone runs)
if not t1["bids_open"]:
    r = c.post(f"{BASE}/admin/tenders/{t1['id']}/open-bids", headers=H(tok_admin))
    check("force-open t1 (if sealed)", r.status_code == 200)

apps1 = c.get(f"{BASE}/officer/applications", headers=H(tok_officer)).json()["applications"]
app_sita = next(a for a in apps1 if a["application_no"] == "APP-2026-000002")
app_meena = next(a for a in apps1 if a["application_no"] == "APP-2026-000004")

# ---- C: blacklist — every tender blacklisted, application blocked ----
vt = c.get(f"{BASE}/bidder/tenders", headers=H(tok_vikram)).json()["tenders"]
published = [t for t in vt if t.get("status") == "PUBLISHED" or t["tender_no"] in (t1["tender_no"],)]
check("C: every tender BLACKLISTED for Vikram",
      len(vt) > 0 and all(t.get("eligibility_badge") == "BLACKLISTED" for t in vt),
      str({t["tender_no"]: t.get("eligibility_badge") for t in vt})[:150])
r = c.post(f"{BASE}/bidder/applications?tender_id={t5['id']}", headers=H(tok_vikram),
           json={"declaration_text": "I hereby declare that the information furnished in this application is true and complete.",
                 "consent_confirmed": True})
check("C: application blocked for blacklisted bidder",
      r.status_code in (400, 403, 409) and "lacklist" in r.text.lower(), f"status={r.status_code}")

# ---- B: compliance gap — Sita's verification shows real FAIL findings ----
r = c.post(f"{BASE}/officer/applications/{app_sita['id']}/run-verification", headers=H(tok_officer))
check("B: Sita verification (gap) ran", r.status_code == 200 and r.json().get("compliance_score") is not None,
      f"score={r.json().get('compliance_score')} risk={r.json().get('risk_level')} result={r.json().get('eligibility_result')}")
rep = c.get(f"{BASE}/officer/applications/{app_sita['id']}", headers=H(tok_officer)).json().get("verification_report", {})
fails = [f for f in rep.get("rule_results", []) if f.get("result") in ("FAIL", "NOT_ELIGIBLE")]
check("B: verification lists concrete FAIL findings", len(fails) >= 1,
      str([f.get('label') for f in fails])[:120])
r = c.get(f"{BASE}/officer/applications/{app_sita['id']}/report", headers=H(tok_officer))
check("B: readable report", r.status_code == 200)

# ---- D: unable to verify — Meena's report carries UNABLE_TO_VERIFY items ----
r = c.post(f"{BASE}/officer/applications/{app_meena['id']}/run-verification", headers=H(tok_officer))
check("D: Meena verification ran", r.status_code == 200 and r.json().get("compliance_score") is not None,
      f"score={r.json().get('compliance_score')} result={r.json().get('eligibility_result')}")
rep = c.get(f"{BASE}/officer/applications/{app_meena['id']}", headers=H(tok_officer)).json().get("verification_report", {})
unver = [f for f in rep.get("rule_results", []) if f.get("result") == "UNABLE_TO_VERIFY"]
check("D: UNABLE_TO_VERIFY findings present (source unavailable)", len(unver) >= 1,
      str([f.get('label') for f in unver])[:120])

# ---- per-district split: Hubli t2 open from submission (AI-verification demo) ----
apps2 = c.get(f"{BASE}/officer/applications", headers=H(tok_hubli)).json()["applications"]
t2a = next(a for a in apps2 if a["tender_no"] == "TDR-2026-002")
check("district split: Hubli app open pre-deadline (identity visible)",
      t2a.get("sealed") is False and t2a.get("company"), f"sealed={t2a.get('sealed')} company={t2a.get('company')}")
r = c.get(f"{BASE}/officer/applications/{t2a['id']}", headers=H(tok_hubli))
check("district split: Hubli app detail readable", r.status_code == 200)
r = c.post(f"{BASE}/officer/applications/{t2a['id']}/run-verification", headers=H(tok_hubli))
check("district split: Hubli AI verification runs live", r.status_code == 200,
      f"result={r.json().get('verification_report', {}).get('eligibility_result')}")

# ---- watch tower ----
r = c.get(f"{BASE}/watch/watches", headers=H(tok_officer))
check("watch tower (watches)", r.status_code == 200)
r = c.get(f"{BASE}/watch/drift", headers=H(tok_officer))
check("watch tower (drift feed)", r.status_code == 200)

# ---- hash-chained audit log ----
r = c.get(f"{BASE}/admin/audit-logs", headers=H(tok_admin))
check("audit log", r.status_code == 200 and len(r.json().get("logs", [])) > 0, f"{len(r.json().get('logs', []))} rows")
r = c.get(f"{BASE}/admin/audit-logs/verify", headers=H(tok_admin))
check("audit chain valid (hash chain)", r.status_code == 200 and r.json().get("valid") is True)

print()
if failures:
    print(f"SMOKE B/C/D: {len(failures)} FAILURES: {failures}")
    sys.exit(1)
print("SMOKE B/C/D (gap, blacklist, unverifiable, district split, watch, audit): ALL PASSED")
