"""End-to-end matrix for the bidder financial quotation (bid value) feature.

Runs against the LIVE stack (API on :8000). Covers the full quote path:
submission gating, exact-decimal arithmetic, validation rejects, sealing,
officer blindness before opening, idempotency, and modification versioning.

Run:  python tests/test_financial_quote_matrix.py
"""
import json
import urllib.error
import urllib.request
import uuid
from decimal import Decimal

BASE = "http://localhost:8000"
PASS, FAIL = [], []


def call(method, path, body=None, token=None):
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(req, data) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw}


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" -> {detail}" if detail and not cond else ""))


def login(email, pw, attempts=8):
    """Officers sign in directly; bidder-side roles complete a console OTP.
    The login route is rate-limited, so back off and retry rather than
    reporting a false auth failure."""
    import time
    for i in range(attempts):
        st, r = call("POST", "/auth/login", {"email": email, "password": pw})
        if isinstance(r.get("detail"), str) and "wait" in r["detail"].lower():
            time.sleep(12)
            continue
        if r.get("access_token"):
            return r["access_token"]
        otp = r.get("dev_otp")
        if not otp:
            return None
        st2, r2 = call("POST", "/auth/verify-otp",
                       {"email": email, "otp": otp, "purpose": "login"})
        if r2.get("access_token"):
            return r2["access_token"]
        time.sleep(3)
    return None


def main():
    print("\n=== FINANCIAL QUOTATION (BID VALUE) MATRIX ===\n")

    print("Auth")
    bidder = login("ramesh@bidder.in", "Bidder@1234")
    check("bidder authenticates", bool(bidder))
    officer = login("procurement.hubli@cpcl.gov.in", "Officer1@1234")
    check("officer authenticates", bool(officer))
    if not bidder or not officer:
        return summary()

    # Pick an open tender the bidder can still apply to (Hubli is unsealed).
    st, tl = call("GET", "/bidder/tenders", token=bidder)
    tenders = tl.get("tenders", tl if isinstance(tl, list) else [])
    target = None
    for t in tenders:
        st2, det = call("GET", f"/bidder/tenders/{t['id']}", token=bidder)
        # Must also be a tender this bidder is actually allowed to bid on:
        # the integrity gate (hard requirement failures) fires before the
        # quote is ever validated, by design.
        if (det.get("existing_application") is None
                and det.get("financial_bid_required")
                and det.get("eligibility", {}).get("apply_allowed")):
            target = det
            break
    check("found an open tender requiring a quote", target is not None)
    if not target:
        return summary()
    tid = target["id"]
    print(f"  using tender {target['tender_no']}")

    print("\nTender detail exposes the requirement")
    check("financial_bid_required is True", target["financial_bid_required"] is True)

    decl = "I confirm the submitted information and documents are true and complete in all respects."
    comm = "Price is firm, inclusive of quoted taxes, and valid for the stated period."

    def apply_body(**over):
        fb = {"base_amount": "9500000.00", "tax_amount": "1710000.00",
              "other_charges": "50000.00", "currency": "INR",
              "price_valid_days": 90, "commercial_declaration": comm,
              "idempotency_key": str(uuid.uuid4())}
        fb.update(over.pop("financial_bid", {}))
        b = {"declaration_text": decl, "consent_confirmed": True, "financial_bid": fb}
        b.update(over)
        return b

    print("\nRejects: submission without a quote")
    st, r = call("POST", f"/bidder/applications?tender_id={tid}",
                 {"declaration_text": decl, "consent_confirmed": True}, bidder)
    code = (r.get("detail") or {}).get("code") if isinstance(r.get("detail"), dict) else None
    check("missing quote -> 400 FINANCIAL_BID_REQUIRED", st == 400 and code == "FINANCIAL_BID_REQUIRED",
          f"{st} {r}")

    print("\nRejects: invalid amounts (server-side, exact Decimal)")
    cases = [
        ("zero base", {"base_amount": "0"}, "greater than zero"),
        ("negative base", {"base_amount": "-100"}, None),
        ("negative tax", {"tax_amount": "-1"}, "negative"),
        ("bad currency", {"currency": "XYZ"}, "not supported"),
        ("validity 0 days", {"price_valid_days": 0}, "validity"),
        ("validity 400 days", {"price_valid_days": 400}, "validity"),
        ("non-numeric base", {"base_amount": "abc"}, None),
    ]
    for label, over, frag in cases:
        st, r = call("POST", f"/bidder/applications?tender_id={tid}", apply_body(financial_bid=over), bidder)
        msg = json.dumps(r).lower()
        ok = st in (400, 422) and (frag is None or frag.lower() in msg)
        check(f"rejects {label} ({st})", ok, f"{st} {r}")

    print("\nRejects: short commercial declaration")
    st, r = call("POST", f"/bidder/applications?tender_id={tid}",
                 apply_body(financial_bid={"commercial_declaration": "too short"}), bidder)
    check("declaration < 20 chars -> 422", st == 422, f"{st}")

    print("\nHappy path: submit with a valid quote")
    body = apply_body()
    idem = body["financial_bid"]["idempotency_key"]
    st, r = call("POST", f"/bidder/applications?tender_id={tid}", body, bidder)
    check("application accepted (200)", st == 200, f"{st} {r}")
    if st != 200:
        return summary()
    app_id, app_no = r["application_id"], r["application_no"]
    env = r.get("financial_envelope", {})
    print(f"  created {app_no}")
    check("envelope returned SEALED", env.get("status") == "SEALED", str(env.get("status")))
    check("envelope has integrity hash", bool(env.get("financial_bid_hash")))

    print("\nExact arithmetic (no float drift)")
    expected = Decimal("9500000.00") + Decimal("1710000.00") + Decimal("50000.00")
    got = Decimal(str(env.get("total_quoted_amount")))
    check(f"total == {expected} (got {got})", got == expected)
    check("base stored exactly", Decimal(str(env.get("base_amount"))) == Decimal("9500000.00"))

    print("\nIdempotency: replaying the same key must not seal a second envelope")
    body2 = apply_body()
    body2["financial_bid"]["idempotency_key"] = idem
    st, r2 = call("POST", f"/bidder/applications?tender_id={tid}", body2, bidder)
    check("duplicate key rejected (409) or deduped", st in (409, 200), f"{st}")
    if st == 409:
        check("409 names DUPLICATE_BID_REQUEST",
              "DUPLICATE" in json.dumps(r2).upper(), json.dumps(r2)[:120])

    print("\nBidder can read back their own sealed quote")
    st, det = call("GET", f"/bidder/applications/{app_id}", token=bidder)
    fe = det.get("financial_envelope", {})
    check("owner sees total_quoted_amount", fe.get("total_quoted_amount") is not None)
    check("owner sees status SEALED", fe.get("status") == "SEALED", str(fe.get("status")))

    print("\nOfficer is blind to the amount before authorised opening")
    st, of = call("GET", f"/officer/applications/{app_id}/financial-bid", token=officer)
    blind = (st in (403, 404)) or (isinstance(of, dict) and of.get("total_quoted_amount") is None)
    check("officer cannot read amount pre-opening", blind, f"{st} {json.dumps(of)[:160]}")

    print("\nModification: re-quote creates a new version")
    st, r3 = call("PUT", f"/bidder/applications/{app_id}/financial-bid",
                  {"base_amount": "9400000.00", "tax_amount": "1692000.00",
                   "other_charges": "50000.00", "currency": "INR", "price_valid_days": 90,
                   "commercial_declaration": comm, "idempotency_key": str(uuid.uuid4())}, bidder)
    check("re-quote accepted (200)", st == 200, f"{st} {json.dumps(r3)[:200]}")
    if st == 200:
        newv = r3.get("version") or r3.get("financial_envelope", {}).get("version")
        check("version incremented to 2", newv == 2, f"version={newv}")
        st, det2 = call("GET", f"/bidder/applications/{app_id}", token=bidder)
        tot = det2.get("financial_envelope", {}).get("total_quoted_amount")
        check("active quote reflects new total 11142000.00",
              Decimal(str(tot)) == Decimal("11142000.00"), str(tot))

    print("\nAudit trail records the sealing (hash only, never the amount)")
    admin = login("admin@cpcl.gov.in", "Admin@1234")
    check("admin authenticates", bool(admin))
    st, aud = call("GET", "/admin/audit-logs?limit=100", token=admin)
    txt = json.dumps(aud)
    check("financial_bid_sealed event recorded", "financial_bid_sealed" in txt, f"status={st}")
    check("plaintext amount NOT written to audit", "9500000.00" not in txt)
    st, ver = call("GET", "/admin/audit-logs/verify", token=admin)
    ok = st == 200 and (ver.get("valid") is True or ver.get("intact") is True
                        or "ok" in json.dumps(ver).lower())
    check("audit hash-chain verifies intact", ok, f"{st} {json.dumps(ver)[:160]}")

    return summary()


def summary():
    print("\n" + "=" * 52)
    print(f"PASSED: {len(PASS)}   FAILED: {len(FAIL)}")
    if FAIL:
        print("\nFailures:")
        for f in FAIL:
            print("  -", f)
    print("=" * 52)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
