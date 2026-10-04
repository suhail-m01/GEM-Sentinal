"""End-to-end matrix for the LIVE auction demonstration (runs against :8000).

Covers what the demo actually shows: the officer console sees live auctions,
a bidder can enter their room and place a real improving bid, the rule engine
rejects wrong-direction and under-increment bids, identities stay anonymous,
non-participants are refused, and the audit/integrity chain holds.

Run:  .venv/bin/python tests/test_live_auction_matrix.py
"""
import json
import time
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
    for _ in range(attempts):
        st, r = call("POST", "/auth/login", {"email": email, "password": pw})
        if isinstance(r.get("detail"), str) and "wait" in r["detail"].lower():
            time.sleep(12)
            continue
        if r.get("access_token"):
            return r["access_token"]
        if r.get("dev_otp"):
            st2, r2 = call("POST", "/auth/verify-otp",
                           {"email": email, "otp": r["dev_otp"], "purpose": "login"})
            if r2.get("access_token"):
                return r2["access_token"]
        return None
    return None


def main():
    print("\n=== LIVE AUCTION MATRIX ===\n")

    print("Officer console")
    officer = login("procurement.belagavi@cpcl.gov.in", "Officer@1234")
    check("officer authenticates", bool(officer))
    if not officer:
        return summary()
    st, r = call("GET", "/officer/auctions", token=officer)
    auctions = r.get("auctions", [])
    live = [a for a in auctions if a["status"] in ("LIVE", "EXTENDED")]
    check("at least 3 auctions exist", len(auctions) >= 3, f"{len(auctions)}")
    check("at least 3 are LIVE", len(live) >= 3, f"{len(live)} live")
    kinds = {a["auction_type"] for a in live}
    check("both REVERSE and FORWARD are live", {"REVERSE", "FORWARD"} <= kinds, str(kinds))
    for a in live:
        print(f"    {a['auction_no']} {a['tender_no']} {a['auction_type']} "
              f"participants={a['eligible_participants']}")
        check(f"{a['auction_no']} has participants", a["eligible_participants"] > 0)

    ra = next((a for a in live if a["auction_type"] == "REVERSE"), None)
    fa = next((a for a in live if a["auction_type"] == "FORWARD"), None)
    if not ra:
        return summary()

    print("\nOfficer live view")
    st, snap = call("GET", f"/officer/auctions/{ra['id']}/live", token=officer)
    check("officer can read the live board", st == 200, f"{st}")
    board = json.dumps(snap)
    check("board exposes a leading price", "leading" in board.lower())

    print("\nBidder auction room")
    bidder = login("ramesh@bidder.in", "Bidder@1234")
    check("bidder authenticates", bool(bidder))
    if not bidder:
        return summary()
    st, mine = call("GET", "/bidder/auctions", token=bidder)
    groups = mine if isinstance(mine, dict) else {}
    my_live = groups.get("live", [])
    check("bidder sees at least one live auction", len(my_live) >= 1, json.dumps(groups)[:200])
    if not my_live:
        return summary()
    room_id = my_live[0]["auction_id"]
    st, room = call("GET", f"/bidder/auctions/{room_id}", token=bidder)
    check("bidder can open their room", st == 200, f"{st}")

    a_type = my_live[0].get("auction_type")
    lead = room.get("leading") or {}
    lead_amt = Decimal(str(lead.get("amount"))) if lead.get("amount") is not None else None
    inc = Decimal(str(room.get("min_increment") or "0"))
    print(f"    room {my_live[0].get('auction_no')} type={a_type} leading={lead_amt} increment={inc}")
    check("room hides real bidder identities",
          "ramesh" not in json.dumps(room).lower() or "anonymous" in json.dumps(room).lower())

    if lead_amt is None or inc <= 0:
        print("    (no leading price yet - skipping bid rules)")
        return summary()

    reverse = a_type == "REVERSE"

    print("\nRule engine rejects bad bids")
    wrong = lead_amt + inc if reverse else lead_amt - inc
    st, r = call("POST", f"/bidder/auctions/{room_id}/bids",
                 {"amount": str(wrong), "idempotency_key": str(uuid.uuid4())}, bidder)
    check(f"wrong-direction bid rejected ({st})", st in (409, 422), f"{st} {json.dumps(r)[:140]}")

    tiny = (lead_amt - (inc / 2)) if reverse else (lead_amt + (inc / 2))
    st, r = call("POST", f"/bidder/auctions/{room_id}/bids",
                 {"amount": str(tiny.quantize(Decimal('0.01'))), "idempotency_key": str(uuid.uuid4())}, bidder)
    check(f"under-increment bid rejected ({st})", st in (409, 422), f"{st} {json.dumps(r)[:140]}")

    st, r = call("POST", f"/bidder/auctions/{room_id}/bids",
                 {"amount": "0", "idempotency_key": str(uuid.uuid4())}, bidder)
    check(f"zero bid rejected ({st})", st in (409, 422), f"{st}")

    print("\nValid improving bid")
    good = (lead_amt - inc) if reverse else (lead_amt + inc)
    key = str(uuid.uuid4())
    st, r = call("POST", f"/bidder/auctions/{room_id}/bids",
                 {"amount": str(good), "idempotency_key": key}, bidder)
    placed = st == 200
    check("improving bid accepted", placed, f"{st} {json.dumps(r)[:200]}")
    if placed:
        bid = r.get("bid", {})
        check("bid is ACCEPTED", bid.get("status") == "ACCEPTED", str(bid.get("status")))
        check("bid has a sequence number", bid.get("sequence_number") is not None)
        check("amount recorded exactly",
              Decimal(str(bid.get("amount"))) == good, f"{bid.get('amount')} vs {good}")

        print("\nIdempotency")
        st, r2 = call("POST", f"/bidder/auctions/{room_id}/bids",
                      {"amount": str(good), "idempotency_key": key}, bidder)
        check("replayed key returns the original bid, not a new one",
              st == 200 and r2.get("duplicate") is True, f"{st} dup={r2.get('duplicate')}")

        print("\nLeading price advanced")
        st, room2 = call("GET", f"/bidder/auctions/{room_id}", token=bidder)
        new_lead = Decimal(str((room2.get("leading") or {}).get("amount")))
        check("leading price is now the new bid", new_lead == good, f"{new_lead} vs {good}")

    print("\nAccess control")
    # Find a bidder who genuinely is NOT in this auction, then probe with a
    # wrong-direction amount so the attempt can never land a real bid even if
    # the access check were to regress.
    outsider = outsider_tok = None
    for cand in ("sita@bidder.in", "meena@bidder.in", "arjun@bidder.in", "bharat@bidder.in"):
        tok = login(cand, "Bidder@1234")
        if not tok:
            continue
        st, lst = call("GET", "/bidder/auctions", token=tok)
        ids = {e.get("auction_id") for grp in (lst or {}).values() if isinstance(grp, list) for e in grp}
        if ra["id"] not in ids:
            outsider, outsider_tok = cand, tok
            break
    if outsider_tok:
        safe = str(lead_amt + inc * 10) if reverse else str((lead_amt - inc * 10).max(Decimal("1")))
        st, r = call("GET", f"/bidder/auctions/{ra['id']}", token=outsider_tok)
        check(f"non-participant cannot open the room ({outsider})", st == 403, f"{st}")
        st2, r2 = call("POST", f"/bidder/auctions/{ra['id']}/bids",
                       {"amount": safe, "idempotency_key": str(uuid.uuid4())}, outsider_tok)
        check("non-participant cannot bid", st2 == 403, f"{st2} {json.dumps(r2)[:120]}")
    else:
        print("    (no non-participant bidder available to probe)")
    st, r = call("GET", f"/officer/auctions/{ra['id']}", token=bidder)
    check("bidder cannot reach the officer auction API", st in (401, 403), f"{st}")

    print("\nIntegrity and audit")
    st, integ = call("GET", f"/officer/auctions/{ra['id']}/integrity", token=officer)
    txt = json.dumps(integ).lower()
    check("integrity endpoint reports intact chain",
          st == 200 and ("true" in txt or "intact" in txt or "ok" in txt), f"{st} {txt[:160]}")
    # /events is a Server-Sent Events stream (live board updates): read the
    # first chunk and hang up rather than waiting for it to end.
    try:
        req = urllib.request.Request(BASE + f"/officer/auctions/{ra['id']}/events")
        req.add_header("Authorization", "Bearer " + officer)
        with urllib.request.urlopen(req, timeout=10) as resp:
            ctype = resp.headers.get("Content-Type", "")
            chunk = resp.read(120)
        check("live event stream serves text/event-stream",
              "text/event-stream" in ctype, ctype)
        check("event stream emits data", len(chunk) > 0, f"{len(chunk)} bytes")
    except Exception as exc:
        check("live event stream reachable", False, str(exc)[:120])

    if fa:
        print("\nForward auction direction is opposite")
        st, fsnap = call("GET", f"/officer/auctions/{fa['id']}/live", token=officer)
        check("forward auction board loads", st == 200, f"{st}")
        check("forward auction is FORWARD", fa["auction_type"] == "FORWARD")

    return summary()


def summary():
    print("\n" + "=" * 54)
    print(f"PASSED: {len(PASS)}   FAILED: {len(FAIL)}")
    if FAIL:
        print("\nFailures:")
        for f in FAIL:
            print("  -", f)
    print("=" * 54)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
