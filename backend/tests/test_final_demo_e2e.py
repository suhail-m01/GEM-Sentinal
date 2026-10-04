"""Final jury demo regression: payment-enabled tender -> bid -> verification -> award -> close."""
from conftest import H, login

OFF = {
    "OPEN": ("bidopening.karnataka@cpcl.gov.in", "Opening@1234"),
    "PROC": ("procurement.belagavi@cpcl.gov.in", "Officer@1234"),
    "TECH": ("technical.karnataka@cpcl.gov.in", "TechEval@1234"),
    "FIN": ("finance.karnataka@cpcl.gov.in", "L2Officer@1234"),
    "CFA": ("cfa.karnataka@cpcl.gov.in", "CFA@1234"),
    "CONTRACT": ("contract.karnataka@cpcl.gov.in", "Contract@1234"),
    "EXEC": ("contractexecution.karnataka@cpcl.gov.in", "Execution@1234"),
    "DELIVERY": ("inspection.karnataka@cpcl.gov.in", "Inspect@1234"),
    "PAYMENT": ("payment.karnataka@cpcl.gov.in", "Payment@1234"),
    "AUDIT": ("auditor@cpcl.gov.in", "Auditor@1234"),
    "ADMIN": ("admin@cpcl.gov.in", "Admin@1234"),
}


def test_payment_tender_to_award_and_audit(client, seeded):
    bidder = login(client, "ramesh@bidder.in", "Bidder@1234")
    toks = {k: login(client, *v) for k, v in OFF.items()}

    tenders = client.get("/bidder/tenders", headers=H(bidder)).json()["tenders"]
    t = next(x for x in tenders if x["tender_no"] == "TDR-2026-012")

    # The real gateway path is covered separately with a mocked Razorpay order + HMAC.
    # Here we place a verified payment row to exercise the real submission/payment gate.
    from app.core.db import SessionLocal, utcnow
    from app.models import TenderPayment, User
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.email == "ramesh@bidder.in").first()
        db.add(TenderPayment(tender_id=t["id"], bidder_user_id=u.id,
                             payment_type="APPLICATION_FEE_EMD", amount_paise=15250000,
                             razorpay_order_id="order_e2e_verified",
                             razorpay_payment_id="pay_e2e_verified",
                             razorpay_signature="verified-by-payment-unit-test",
                             status="VERIFIED", verified_at=utcnow()))
        db.commit()
    finally:
        db.close()

    detail = client.get(f"/bidder/tenders/{t['id']}", headers=H(bidder))
    assert detail.status_code == 200, detail.text
    req = detail.json()["payment_requirements"]
    assert req["application_fee"]["paid"] is True
    assert req["emd"]["required"] is True
    assert req["emd"]["paid"] is True
    assert req["checkout"]["amount"] == 152500.0
    assert req["all_satisfied"] is True

    body = {
        "declaration_text": "I declare that all submitted information is accurate and accept the tender conditions.",
        "consent_confirmed": True,
        "financial_bid": {
            "base_amount": "24500000.00", "tax_amount": "4410000.00", "other_charges": "0.00",
            "currency": "INR", "price_valid_days": 90,
            "commercial_declaration": "I confirm this commercial quotation is firm and valid for ninety days."
        }
    }
    r = client.post("/bidder/applications", params={"tender_id": t["id"]}, json=body, headers=H(bidder))
    assert r.status_code == 200, r.text
    app = r.json()
    aid = app["application_id"]

    # Prove sealed district behavior before the audited opening.
    r = client.get(f"/officer/applications/{aid}", headers=H(toks["PROC"]))
    assert r.status_code == 403 and r.json()["detail"]["code"] == "BIDS_SEALED"

    r = client.post(f"/admin/tenders/{t['id']}/open-bids", headers=H(toks["ADMIN"]))
    assert r.status_code == 200, r.text
    r = client.post(f"/officer/pipeline/{aid}/BID_CLOSURE/action", headers=H(toks["OPEN"]),
                    json={"action": "OPEN_BIDS", "remarks": "Bids opened after authorized tender opening."})
    assert r.status_code == 200, r.text

    r = client.post(f"/officer/applications/{aid}/run-verification", headers=H(toks["PROC"]))
    assert r.status_code == 200, r.text
    rep = client.get(f"/officer/applications/{aid}", headers=H(toks["PROC"])).json()["verification_report"]
    assert rep["eligibility_result"] in ("ELIGIBLE", "CONDITIONAL")
    finding = "VERIFIED" if rep["eligibility_result"] == "ELIGIBLE" else "MANUAL_REVIEW_REQUIRED"
    r = client.post(f"/officer/pipeline/{aid}/COMPLIANCE/action", headers=H(toks["PROC"]),
                    json={"action": finding, "finding": finding,
                          "remarks": f"AI verification recorded: {rep['compliance_score']}/100."})
    assert r.status_code == 200, r.text

    steps = [
        ("TECHNICAL", "TECH", {"action":"TECHNICALLY_QUALIFIED", "outcome":"TECHNICALLY_QUALIFIED", "remarks":"Technical requirements verified."}),
        ("FINANCIAL", "FIN", {"action":"PASS", "outcome":"PASS", "remarks":"Financial quotation evaluated."}),
        ("APPROVAL", "CFA", {"action":"APPROVE", "decision":"APPROVE", "remarks":"Competent authority awards the tender."}),
        ("CONTRACT", "CONTRACT", {"action":"ISSUE_CONTRACT", "contract_no":"CPCL/2026/DEMO/012", "po_no":"PO/DEMO/012", "remarks":"Contract issued."}),
        ("EXECUTION", "EXEC", {"action":"RECORD_PROGRESS", "milestones":[{"name":"Supply batch 1", "due":"2026-11-15", "status":"COMPLETED"}], "remarks":"Execution recorded."}),
        ("DELIVERY", "DELIVERY", {"action":"ACCEPTED", "outcome":"ACCEPTED", "batch_serial":"DEMO-BATCH-012", "remarks":"Delivery inspected and accepted."}),
        ("PAYMENT", "PAYMENT", {"action":"SETTLE_PAYMENT", "invoice_no":"INV-DEMO-012", "payment_ref":"NEFT-DEMO-012", "payment_amount":28910000, "remarks":"Invoice verified and settled."}),
        ("AUDIT", "AUDIT", {"action":"CLOSE_AUDIT", "remarks":"Audit chain verified and procurement closed."}),
    ]
    for stage, who, payload in steps:
        r = client.post(f"/officer/pipeline/{aid}/{stage}/action", headers=H(toks[who]), json=payload)
        assert r.status_code == 200, (stage, r.text)

    st = client.get(f"/officer/pipeline/{aid}", headers=H(toks["AUDIT"]))
    assert st.status_code == 200
    data = st.json()
    assert data["current_stage"] is None
    approval = next(x for x in data["stages"] if x["key"] == "APPROVAL")
    assert approval["payload"]["decision"] == "APPROVE"
    assert len(data["certificates"]) == 11
