"""The 11-stage procurement pipeline.

Covers: stage state machine + per-stage owning officer (role gate), role-based
document visibility (technical sees no PAN/GST/UDYAM; finance sees GST only),
readable officer summary + line-based explanation, per-stage certificates
(PDF), override accountability at award, terminal outcomes (NON_COMPLIANT
stops the pipeline), and the bidder's submission certificate.
"""
import json

import pytest
from conftest import H, login, open_bids

OFFICERS = {
    "BID_OPENING": ("bidopening.karnataka@cpcl.gov.in", "Opening@1234"),
    "PROCUREMENT": ("procurement.belagavi@cpcl.gov.in", "Officer@1234"),
    "TECHNICAL": ("technical.karnataka@cpcl.gov.in", "TechEval@1234"),
    "FINANCIAL": ("finance.karnataka@cpcl.gov.in", "L2Officer@1234"),
    "CFA": ("cfa.karnataka@cpcl.gov.in", "CFA@1234"),
    "CONTRACT": ("contract.karnataka@cpcl.gov.in", "Contract@1234"),
    "EXECUTION": ("contractexecution.karnataka@cpcl.gov.in", "Execution@1234"),
    "DELIVERY": ("inspection.karnataka@cpcl.gov.in", "Inspect@1234"),
    "PAYMENT": ("payment.karnataka@cpcl.gov.in", "Payment@1234"),
    "AUDIT": ("auditor@cpcl.gov.in", "Auditor@1234"),
}
PWD = {k: v[1] for k, v in OFFICERS.items()}


@pytest.fixture(scope="module")
def tokens(client, seeded):
    open_bids(client)
    return {k: login(client, *v) for k, v in OFFICERS.items()}


def _find_app(client, tok, company):
    r = client.get("/officer/applications", headers=H(tok))
    assert r.status_code == 200, r.text
    apps = r.json()["applications"]
    return next(a for a in apps if company.upper() in a["company"].upper())


def _pipeline(client, tok, app_id):
    r = client.get(f"/officer/pipeline/{app_id}", headers=H(tok))
    assert r.status_code == 200, r.text
    return r.json()


def _act(client, tok, app_id, stage, **body):
    return client.post(
        f"/officer/pipeline/{app_id}/{stage}/action", headers=H(tok), json=body
    )


def _cert(client, tok, app_id, stage):
    r = client.get(f"/officer/pipeline/{app_id}/certificate/{stage}", headers=H(tok))
    return r


# ---------------------------------------------------------------------------
# 1. full 11-stage walk (each stage acted by its owning officer)
# ---------------------------------------------------------------------------
def test_full_walk_all_stages_and_certificates(client, tokens):
    tok = tokens["PROCUREMENT"]
    meena = _find_app(client, tok, "MEENA POLYFAB")
    aid = meena["id"]

    st = _pipeline(client, tok, aid)
    assert st["current_stage"] == "BID_CLOSURE"
    assert st["stages"][0]["status"] == "COMPLETED"  # SUBMISSION at submission
    assert st["stages"][0]["certificate_no"].startswith("CERT-SUB-")

    r = _act(client, tokens["BID_OPENING"], aid, "BID_CLOSURE",
             action="OPEN_BIDS", remarks="Bids opened at closing time in committee presence.")
    assert r.status_code == 200, r.text

    r = client.post(f"/officer/applications/{aid}/run-verification", headers=H(tok))
    assert r.status_code == 200, r.text

    # compliance stage records the AI outcome
    r = _act(client, tok, aid, "COMPLIANCE",
             action="MANUAL_REVIEW_REQUIRED", finding="MANUAL_REVIEW_REQUIRED",
             remarks="AI score on file; one unverified source — manual review noted.")
    assert r.status_code == 200, r.text
    assert r.json()["certificate_no"].startswith("CERT-COMP-")

    r = _act(client, tokens["TECHNICAL"], aid, "TECHNICAL",
             action="TECHNICALLY_QUALIFIED", outcome="TECHNICALLY_QUALIFIED",
             remarks="Technical specs and certifications verified.")
    assert r.status_code == 200, r.text

    r = _act(client, tokens["FINANCIAL"], aid, "FINANCIAL",
             action="PASS", outcome="PASS", amount=74100000, tax_percent=18,
             remarks="Price evaluated; GST verified; L-rank recorded.")
    assert r.status_code == 200, r.text
    assert r.json()["certificate_no"].startswith("CERT-FINA-")

    # CFA award — Meena has no blocking findings, so direct approval passes
    r = _act(client, tokens["CFA"], aid, "APPROVAL",
             action="APPROVE", decision="APPROVE",
             remarks="Competent authority awards to the evaluated bidder.")
    assert r.status_code == 200, r.text

    r = _act(client, tokens["CONTRACT"], aid, "CONTRACT",
             action="ISSUE_CONTRACT", contract_no="CPCL/CON/2026-27/014", po_no="PO/2026-27/014",
             remarks="Contract issued on standard terms.")
    assert r.status_code == 200, r.text

    r = _act(client, tokens["EXECUTION"], aid, "EXECUTION",
             action="RECORD_PROGRESS",
             milestones=[{"name": "Foundation complete", "due": "2026-11-01", "status": "COMPLETED"}],
             remarks="Progress per schedule.")
    assert r.status_code == 200, r.text

    r = _act(client, tokens["DELIVERY"], aid, "DELIVERY",
             action="ACCEPTED", outcome="ACCEPTED", batch_serial="Batch 1 · SN 4500-4512",
             remarks="Inspection passed; goods conform.")
    assert r.status_code == 200, r.text

    r = _act(client, tokens["PAYMENT"], aid, "PAYMENT",
             action="SETTLE_PAYMENT", invoice_no="INV/2026/014", payment_ref="NEFT/CPCL/88231",
             payment_amount=74100000, remarks="Invoice verified; payment settled.")
    assert r.status_code == 200, r.text

    r = _act(client, tokens["AUDIT"], aid, "AUDIT",
             action="CLOSE_AUDIT", remarks="Hash chain verified; trail complete.")
    assert r.status_code == 200, r.text

    st = _pipeline(client, tokens["AUDIT"], aid)
    assert st["current_stage"] is None
    assert len(st["certificates"]) == 11
    for c in st["certificates"]:
        r = _cert(client, tokens["AUDIT"], aid, c["stage"])
        assert r.status_code == 200, (c["stage"], r.text[:200])
        assert r.content[:4] == b"%PDF", f"{c['stage']} certificate is not a PDF"

    # completed stages are immutable (409 on re-act)
    r = _act(client, tokens["PAYMENT"], aid, "PAYMENT",
             action="SETTLE_PAYMENT", payment_ref="X", remarks="double")
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# 2. role gate: only the owning officer (or admin) may act on a stage
# ---------------------------------------------------------------------------
def test_role_gate_blocks_non_owner(client, tokens):
    tok = tokens["PROCUREMENT"]
    ramesh = _find_app(client, tok, "RAMESH")
    aid = ramesh["id"]

    # procurement officer is NOT the bid-opening officer
    r = _act(client, tok, aid, "BID_CLOSURE", action="OPEN_BIDS", remarks="not my stage")
    assert r.status_code == 403, r.text
    # technical officer cannot act on the financial stage
    r = _act(client, tokens["TECHNICAL"], aid, "FINANCIAL",
             action="PASS", outcome="PASS", amount=1, remarks="not my stage")
    assert r.status_code == 403, r.text
    # and a completed-elsewhere stage is not the current stage for a new app
    st = _pipeline(client, tok, aid)
    assert st["current_stage"] == "BID_CLOSURE"


# ---------------------------------------------------------------------------
# 3. role-based document visibility
# ---------------------------------------------------------------------------
def test_role_scoped_documents(client, tokens):
    proc = tokens["PROCUREMENT"]
    ramesh = _find_app(client, proc, "RAMESH")
    aid = ramesh["id"]

    # full-scope role sees everything
    r = client.get(f"/officer/applications/{aid}", headers=H(proc))
    assert r.status_code == 200, r.text
    d = r.json()
    full_types = {x["doc_type"] for x in d["documents"]}
    assert d["doc_scope"]["hidden_count"] == 0
    assert "PAN" in full_types and "GST_CERT" in full_types

    # technical evaluator: technical/certification docs only — Ramesh's
    # statutory identity docs must all be hidden
    r = client.get(f"/officer/applications/{aid}", headers=H(tokens["TECHNICAL"]))
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["my_role"] == "TECHNICAL_EVALUATOR"
    vis = {x["doc_type"] for x in d["documents"]}
    assert not (vis & {"PAN", "GST_CERT", "UDYAM_CERT", "EPFO_DOC", "MCA_DOC"}), vis
    assert vis <= {"OEM_AUTHORIZATION", "BIS_CERT", "STARTUP_CERT", "NSIC_CERT",
                   "LOCAL_CONTENT_DECLARATION", "TECH_SPEC", "QUOTATION"}, vis
    assert d["doc_scope"]["hidden_count"] >= 4, d["doc_scope"]

    # a bidder with sector documents (Meena) sees exactly those in technical view
    meena = _find_app(client, proc, "MEENA POLYFAB")
    r = client.get(f"/officer/applications/{meena['id']}", headers=H(tokens["TECHNICAL"]))
    assert r.status_code == 200, r.text
    vis = {x["doc_type"] for x in r.json()["documents"]}
    assert {"BIS_CERT", "OEM_AUTHORIZATION", "TECH_SPEC"} <= vis, vis
    assert not (vis & {"PAN", "GST_CERT", "UDYAM_CERT", "EPFO_DOC"}), vis

    # financial evaluator: GST + commercial only
    r = client.get(f"/officer/applications/{aid}", headers=H(tokens["FINANCIAL"]))
    assert r.status_code == 200, r.text
    d = r.json()
    vis = {x["doc_type"] for x in d["documents"]}
    assert not (vis & {"PAN", "UDYAM_CERT", "EPFO_DOC", "MCA_DOC", "OEM_AUTHORIZATION", "TECH_SPEC"}), vis
    assert "GST_CERT" in vis


# ---------------------------------------------------------------------------
# 4. readable officer summary + line-based explanation
# ---------------------------------------------------------------------------
def test_officer_summary_and_readable_explanation(client, tokens):
    tok = tokens["PROCUREMENT"]
    ramesh = _find_app(client, tok, "RAMESH")
    aid = ramesh["id"]
    r = client.post(f"/officer/applications/{aid}/run-verification", headers=H(tok))
    assert r.status_code == 200, r.text

    r = client.get(f"/officer/applications/{aid}", headers=H(tok))
    assert r.status_code == 200
    rep = r.json()["verification_report"]
    summ = rep["officer_summary"]
    assert summ["verdict"], "summary verdict required"
    assert summ["one_line"]
    assert summ["checks"]["total"] == summ["checks"]["passed"] + \
        summ["checks"]["failed"] + summ["checks"]["unverified"]
    assert isinstance(summ["documents"]["missing"], list)

    # the explanation is now line-oriented (skimmable), not a single wall of prose
    lines = (rep["ai_explanation"] or "").splitlines()
    assert any(l.startswith("VERDICT:") for l in lines)
    assert any("RECOMMENDED ACTION:" in l for l in lines)
    assert len(lines) >= 3, "explanation should be multi-line"
    for item in summ["must_fix"]:
        assert item["action"], "every must-fix finding needs a concrete remedy action"


# ---------------------------------------------------------------------------
# 5. award with blocking findings → override accountability flow
# ---------------------------------------------------------------------------
def test_override_flow_on_blocking_findings(client, tokens):
    proc = tokens["PROCUREMENT"]
    sita = _find_app(client, proc, "Sita")
    aid = sita["id"]

    r = _act(client, tokens["BID_OPENING"], aid, "BID_CLOSURE",
             action="OPEN_BIDS", remarks="Bids opened.")
    assert r.status_code == 200, r.text
    r = client.post(f"/officer/applications/{aid}/run-verification", headers=H(proc))
    assert r.status_code == 200, r.text
    # officer records manual review rather than auto-reject
    r = _act(client, proc, aid, "COMPLIANCE",
             action="MANUAL_REVIEW_REQUIRED", finding="MANUAL_REVIEW_REQUIRED",
             remarks="Lapsed GST + udyam mismatch — escalated for committee review.")
    assert r.status_code == 200, r.text
    r = _act(client, tokens["TECHNICAL"], aid, "TECHNICAL",
             action="TECHNICALLY_QUALIFIED", outcome="TECHNICALLY_QUALIFIED",
             remarks="Technical requirements met.")
    assert r.status_code == 200, r.text
    r = _act(client, tokens["FINANCIAL"], aid, "FINANCIAL",
             action="PASS", outcome="PASS", amount=75400000,
             remarks="Price within budget.")
    assert r.status_code == 200, r.text

    # Sita has blocking (hard-FAIL) findings → CFA approval is challenged (409)
    r = _act(client, tokens["CFA"], aid, "APPROVAL",
             action="APPROVE", decision="APPROVE", remarks="Award per committee.")
    assert r.status_code == 409, f"expected override challenge, got {r.status_code}"
    ch = r.json()["detail"]
    assert ch.get("override_required") and ch["challenge"]

    # re-authenticate + accountable override
    r = client.post("/auth/reauth", headers=H(tokens["CFA"]), json={"password": PWD["CFA"]})
    assert r.status_code == 200, r.text
    reauth = r.json()["reauth_token"]
    r = client.post(f"/officer/applications/{aid}/override", headers=H(tokens["CFA"]),
                    json={"challenge": ch["challenge"],
                          "reason_category": "EXCEPTIONAL_APPROVAL_UNDER_TENDER_CLAUSE",
                          "justification": "Committee accepted residual risk with compensating controls "
                                           "and a corrective-action deadline for GST reactivation.",
                          "reauth_token": reauth})
    assert r.status_code == 200, r.text

    # now the award proceeds
    r = _act(client, tokens["CFA"], aid, "APPROVAL",
             action="APPROVE", decision="APPROVE",
             remarks="Award after accepted, co-signed override.")
    assert r.status_code == 200, r.text
    st = _pipeline(client, tokens["CFA"], aid)
    appr = next(s for s in st["stages"] if s["key"] == "APPROVAL")
    assert appr["status"] == "COMPLETED" and appr["certificate_no"]


# ---------------------------------------------------------------------------
# 6. terminal outcome: NON_COMPLIANT at screening stops the pipeline
# ---------------------------------------------------------------------------
def test_terminal_outcome_terminates_pipeline(client, tokens):
    proc = tokens["PROCUREMENT"]
    vikram = _find_app(client, proc, "VIKRAM")
    aid = vikram["id"]
    r = _act(client, tokens["BID_OPENING"], aid, "BID_CLOSURE",
             action="OPEN_BIDS", remarks="Bids opened.")
    assert r.status_code == 200, r.text
    r = client.post(f"/officer/applications/{aid}/run-verification", headers=H(proc))
    assert r.status_code == 200, r.text
    r = _act(client, proc, aid, "COMPLIANCE",
             action="NON_COMPLIANT", finding="NON_COMPLIANT",
             remarks="Active debarment on file — bid rejected at screening.")
    assert r.status_code == 200, r.text

    st = _pipeline(client, proc, aid)
    comp = next(s for s in st["stages"] if s["key"] == "COMPLIANCE")
    assert comp["status"] == "TERMINATED"
    # the terminated app cannot proceed to technical evaluation
    r = _act(client, tokens["TECHNICAL"], aid, "TECHNICAL",
             action="TECHNICALLY_QUALIFIED", outcome="TECHNICALLY_QUALIFIED",
             remarks="should be blocked")
    assert r.status_code == 409, r.text
    # but the screening certificate is still downloadable
    r = _cert(client, proc, aid, "COMPLIANCE")
    assert r.status_code == 200 and r.content[:4] == b"%PDF"


# ---------------------------------------------------------------------------
# 7. bidder's submission certificate
# ---------------------------------------------------------------------------
def test_bidder_submission_certificate(client, tokens):
    r = client.post("/auth/login", json={"email": "meena@bidder.in", "password": "Bidder@1234"})
    assert r.status_code == 200, r.text
    dev = r.json().get("dev_otp")
    assert dev, "console-mode dev_otp expected"
    r2 = client.post("/auth/verify-otp",
                     json={"email": "meena@bidder.in", "otp": dev, "purpose": "login"})
    assert r2.status_code == 200, r2.text
    bid_tok = r2.json()["access_token"]

    r = client.get("/bidder/applications", headers=H(bid_tok))
    assert r.status_code == 200, r.text
    body = r.json()
    apps = body["applications"] if isinstance(body, dict) else body
    mine = next(a for a in apps if a["tender_no"] == "TDR-2026-001")
    r = client.get(f"/bidder/applications/{mine['id']}/certificate/submission", headers=H(bid_tok))
    assert r.status_code == 200, r.text
    assert r.content[:4] == b"%PDF"
