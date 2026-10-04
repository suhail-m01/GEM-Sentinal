import hashlib
import hmac

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from conftest import login, H


def _request():
    return Request({"type": "http", "method": "POST", "path": "/test", "headers": [],
                    "client": ("127.0.0.1", 12345), "scheme": "http", "server": ("test", 80)})


def test_six_demo_tender_payment_matrix(client, seeded):
    tok = login(client, "ramesh@bidder.in", "Bidder@1234")
    r = client.get("/bidder/tenders", headers=H(tok))
    assert r.status_code == 200
    tenders = {t["tender_no"]: t for t in r.json()["tenders"]}
    for no in ("TDR-2026-012", "TDR-2026-013", "TDR-2026-014", "TDR-2026-015", "TDR-2026-016", "TDR-2026-017"):
        assert no in tenders

    from app.core.db import SessionLocal
    from app.models import Tender, User
    from app.routers.bidder import _payment_requirements
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "ramesh@bidder.in").first()
        rows = {t.tender_no: t for t in db.query(Tender).filter(Tender.tender_no.in_(list(tenders))).all()}
        # TDR-2026-012/015 deliberately require EMD even for this demo bidder,
        # while other tenders retain exemption/no-EMD scenarios.
        assert rows["TDR-2026-012"].application_fee_required is True
        assert rows["TDR-2026-012"].bid_security_required is True
        assert rows["TDR-2026-013"].application_fee_required is True
        assert rows["TDR-2026-013"].bid_security_percent == 2
        assert rows["TDR-2026-014"].sealing_enabled is False
        assert rows["TDR-2026-015"].application_fee_amount == 2500
        assert rows["TDR-2026-016"].bid_security_required is False
        req17 = _payment_requirements(db, user, rows["TDR-2026-017"])
        assert req17["application_fee"]["required"] is True
        assert req17["application_fee"]["paid"] is False
        assert req17["emd"]["required"] is False
        assert req17["all_satisfied"] is False
    finally:
        db.close()


def test_razorpay_order_and_signature_verification(monkeypatch, seeded):
    from app.core.config import settings
    from app.core.db import SessionLocal
    from app.models import Tender, TenderPayment, User
    from app.routers import bidder as br

    monkeypatch.setattr(settings, "ENABLE_RAZORPAY_PAYMENTS", True)
    monkeypatch.setattr(settings, "RAZORPAY_KEY_ID", "rzp_test_unit_key")
    monkeypatch.setattr(settings, "RAZORPAY_KEY_SECRET", "unit_test_secret")

    class FakeResponse:
        def raise_for_status(self):
            return None
        def json(self):
            return {"id": "order_unit_001", "amount": 15250000, "currency": "INR"}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def post(self, url, json=None, auth=None):
            assert url == "https://api.razorpay.com/v1/orders"
            assert json["amount"] == 15250000  # ₹2,500 + ₹1,50,000 EMD
            assert auth == ("rzp_test_unit_key", "unit_test_secret")
            return FakeResponse()

    monkeypatch.setattr(br.httpx, "Client", FakeClient)

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "ramesh@bidder.in").first()
        tender = db.query(Tender).filter(Tender.tender_no == "TDR-2026-012").first()
        out = br.create_payment_order(tender.id, br.PaymentOrderIn(payment_type="AUTO"),
                                      _request(), user=user, db=db)
        assert out["order_id"] == "order_unit_001"
        assert out["amount"] == 15250000
        assert out["payment_type"] == "APPLICATION_FEE_EMD"
        assert out["breakdown"] == {"application_fee": 2500.0, "emd": 150000.0}
        rec = db.query(TenderPayment).filter(TenderPayment.razorpay_order_id == "order_unit_001").first()
        assert rec is not None and rec.status == "CREATED" and rec.id

        payment_id = "pay_unit_001"
        sig = hmac.new(b"unit_test_secret", f"order_unit_001|{payment_id}".encode(), hashlib.sha256).hexdigest()
        verified = br.verify_payment(
            tender.id,
            br.PaymentVerifyIn(razorpay_order_id="order_unit_001", razorpay_payment_id=payment_id,
                               razorpay_signature=sig),
            _request(), user=user, db=db,
        )
        assert verified["verified"] is True
        db.refresh(rec)
        assert rec.status == "VERIFIED" and rec.razorpay_payment_id == payment_id
    finally:
        db.close()


def test_bad_razorpay_signature_is_rejected(monkeypatch, seeded):
    from app.core.config import settings
    from app.core.db import SessionLocal
    from app.models import Tender, TenderPayment, User
    from app.routers import bidder as br

    monkeypatch.setattr(settings, "RAZORPAY_KEY_SECRET", "unit_test_secret")
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "ramesh@bidder.in").first()
        tender = db.query(Tender).filter(Tender.tender_no == "TDR-2026-016").first()
        rec = TenderPayment(tender_id=tender.id, bidder_user_id=user.id, payment_type="APPLICATION_FEE",
                            amount_paise=250000, razorpay_order_id="order_bad_sig", status="CREATED")
        db.add(rec); db.commit()
        with pytest.raises(HTTPException) as exc:
            br.verify_payment(
                tender.id,
                br.PaymentVerifyIn(razorpay_order_id="order_bad_sig", razorpay_payment_id="pay_bad",
                                   razorpay_signature="not-a-valid-signature"),
                _request(), user=user, db=db,
            )
        assert exc.value.status_code == 400
        db.refresh(rec)
        assert rec.status == "FAILED"
    finally:
        db.close()
