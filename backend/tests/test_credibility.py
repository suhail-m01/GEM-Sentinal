"""Credibility & label precision (2026-09-20 user request):

1. Blacklisted bidder: every tender is disabled and labelled BLACKLISTED
   (a debarment in one domain disables bidding in all domains).
2. Missing mandatory documents are reported as MISSING_DOCUMENTS — not as
   the generic NOT_ELIGIBLE.
3. Officer verification output is meaningful: documents submitted by a
   compliant bidder verify VERIFIED against their issuing source, the
   cross-verification table shows readable (unmasked) values, and no
   bogus tampering flags fire on clean documents.
"""
import httpx
import pytest

from conftest import H, login, open_bids


@pytest.fixture(autouse=True)
def _bids_open(client, seeded):
    open_bids(client)


L1 = ("procurement.belagavi@cpcl.gov.in", "Officer@1234")
RAMESH = ("ramesh@bidder.in", "Bidder@1234")
VIKRAM = ("vikram@bidder.in", "Bidder@1234")


# --------------------------- 1. BLACKLIST LABEL -----------------------------

def test_blacklisted_bidder_all_tenders_disabled_and_labelled(client, seeded):
    tok = login(client, *VIKRAM)
    r = client.get("/bidder/tenders", headers=H(tok))
    assert r.status_code == 200
    tenders = r.json()["tenders"]
    assert tenders, "bidder must see open tenders"
    for t in tenders:
        assert t["eligibility_badge"] == "BLACKLISTED", (
            f"{t['tender_no']}: a debarred bidder must see BLACKLISTED on every tender "
            f"(got {t['eligibility_badge']})"
        )
        assert t["apply_allowed"] is False, "blacklisted bidder must not be able to apply"
        assert any("Blacklist" in x or "blacklist" in x.lower() for x in t["apply_blocked_reasons"])

    # the block also holds at submission time for ANY tender
    t1 = next(t for t in tenders if t["tender_no"] == "TDR-2026-001")
    r = client.post(
        f"/bidder/applications?tender_id={t1['id']}",
        headers=H(tok),
        json={
            "declaration_text": "I hereby declare that the information is true.",
            "consent_confirmed": True,
        },
    )
    assert r.status_code == 409
    assert "APPLICATION_BLOCKED" in r.json()["detail"]["code"]


def test_blacklisted_label_not_generic_not_eligible(client, seeded):
    tok = login(client, *VIKRAM)
    t1 = next(
        t for t in client.get("/bidder/tenders", headers=H(tok)).json()["tenders"]
        if t["tender_no"] == "TDR-2026-001"
    )
    el = client.get(f"/bidder/tenders/{t1['id']}", headers=H(tok)).json()["eligibility"]
    assert el["result"] == "BLACKLISTED"
    assert "Blacklist" in el["label"]


# --------------------------- 2. MISSING DOCUMENTS ----------------------------

def test_missing_documents_labelled_not_not_eligible(client, seeded):
    # TDR-2026-002 requires OEM_AUTHORIZATION, which no demo bidder has —
    # the tender must be labelled "missing documents", not "not eligible".
    tok = login(client, *RAMESH)
    tenders = client.get("/bidder/tenders", headers=H(tok)).json()["tenders"]
    t2 = next(t for t in tenders if t["tender_no"] == "TDR-2026-002")
    assert t2["eligibility_badge"] == "MISSING_DOCUMENTS", (
        f"missing mandatory document must be labelled MISSING_DOCUMENTS (got {t2['eligibility_badge']})"
    )
    assert t2["apply_allowed"] is False
    assert any("OEM_AUTHORIZATION" in x for x in t2["apply_blocked_reasons"])

    el = client.get(f"/bidder/tenders/{t2['id']}", headers=H(tok)).json()["eligibility"]
    assert el["result"] == "MISSING_DOCUMENTS"
    assert "document" in el["label"].lower()
    assert el["apply_allowed"] is False


# --------------------------- 3. CREDIBLE VERIFICATION ------------------------

def test_verification_report_is_meaningful(client, seeded):
    tok_l1 = login(client, *L1)
    apps = client.get("/officer/applications", headers=H(tok_l1)).json()["applications"]
    app_a = next(a for a in apps if a["application_no"] == "APP-2026-000001")

    r = client.post(f"/officer/applications/{app_a['id']}/run-verification", headers=H(tok_l1))
    assert r.status_code == 200, r.text[:300]
    rep = client.get(f"/officer/applications/{app_a['id']}/report", headers=H(tok_l1)).json()

    # --- document authenticity: compliant bidder's docs must VERIFY ---------
    auth = {a["doc_type"]: a for a in rep["authenticity"]}
    for dt in ("GST_CERT", "UDYAM_CERT", "MCA_DOC"):
        a = auth[dt]
        assert a["status"] == "VERIFIED", (
            f"{dt} ({a['document_name']}) must be VERIFIED against its issuing source "
            f"(got {a['status']}: {a.get('reasons')})"
        )
        assert not a.get("tampering_indicators"), (
            f"{dt}: no tampering indicators may fire on a clean rendered document "
            f"(got {a['tampering_indicators']})"
        )
    # the GST certificate's GSTIN must be extracted and matched at source
    gst = auth["GST_CERT"]
    assert gst["extracted_numbers"].get("gstin") == "29ABRCT1234F1Z5"
    assert any(
        f["field"] == "GSTIN" and f["result"] == "MATCH" for f in gst["field_consistency"]
    ), gst["field_consistency"]

    # --- cross-verification: readable values, consistent story --------------
    cross = {c["field"]: c for c in rep["cross_verification"]}
    # full, unmasked values (officer-facing)
    assert cross["GSTIN"]["bidder_value"] == "29ABRCT1234F1Z5"
    assert cross["PAN"]["bidder_value"] == "ABRCT1234F"
    # address agrees with the source record (seeded consistent)
    assert cross["Registered address"]["result"] == "MATCH", cross["Registered address"]
    # the nonsense 'product category vs MSME size class' row is gone
    assert "Business category" not in cross
    # no mismatches for the clean bidder
    assert rep["mismatches"] == [], rep["mismatches"]

    # the engine's remaining findings are specific and explained
    fails = [x for x in rep["rule_results"] if x["result"] == "FAIL"]
    assert {x["rule_key"] for x in fails} == {"bis_lubricants_std", "oem_authorization"}


def test_no_unable_to_verify_wall_for_clean_bidder(client, seeded):
    """The report must not read as 'unable to verify' all over the place:
    rule results for a clean bidder are decisive (PASS/FAIL), and the
    recommendation names the specific remediable gaps."""
    tok_l1 = login(client, *L1)
    apps = client.get("/officer/applications", headers=H(tok_l1)).json()["applications"]
    app_a = next(a for a in apps if a["application_no"] == "APP-2026-000001")
    r = client.post(f"/officer/applications/{app_a['id']}/run-verification", headers=H(tok_l1))
    assert r.status_code == 200
    rep = client.get(f"/officer/applications/{app_a['id']}/report", headers=H(tok_l1)).json()

    results = [x["result"] for x in rep["rule_results"]]
    assert "UNABLE_TO_VERIFY" not in results, (
        "a clean bidder with all sources reachable must get decisive rule results"
    )
    assert "BIS" in rep["recommendation"] or "OEM" in rep["recommendation"], (
        "the recommendation must name the concrete gaps"
    )
