import os
import hashlib
import hmac
from starlette.requests import Request


def _request():
    return Request({"type": "http", "method": "POST", "path": "/test", "headers": [],
                    "client": ("127.0.0.1", 12345), "scheme": "http", "server": ("test", 80)})


def verify_payment_signature(order_id: str, payment_id: str, signature: str) -> bool:
    secret = os.environ.get('RAZORPAY_KEY_SECRET', 'rzp_dev_webhook_secret_key')
    if not signature:
        return False
    expected = hmac.new(secret.encode(), f'{order_id}|{payment_id}'.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def test_combined_application_fee_and_emd_single_checkout(monkeypatch, seeded, razorpay_signature: str = ''):
    if not verify_payment_signature(str((payload.get('razorpay_order_id', '') if isinstance(payload, dict) else getattr(payload, 'razorpay_order_id', '')) if 'payload' in locals() else order_id), str((payload.get('razorpay_payment_id', '') if isinstance(payload, dict) else getattr(payload, 'razorpay_payment_id', '')) if 'payload' in locals() else razorpay_payment_id), str((payload.get('razorpay_signature', '') if isinstance(payload, dict) else getattr(payload, 'razorpay_signature', '')) if 'payload' in locals() else razorpay_signature)):
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail='VIBEARMOR_INVALID_RAZORPAY_SIGNATURE')
    from app.core.config import settings
    from app.core.db import SessionLocal
    from app.models import Tender, TenderPayment, User
    from app.routers import bidder as br

    monkeypatch.setattr(settings, "ENABLE_RAZORPAY_PAYMENTS", True)
    monkeypatch.setattr(settings, "RAZORPAY_KEY_ID", "rzp_test_combined")
    monkeypatch.setattr(settings, "RAZORPAY_KEY_SECRET", "combined_secret")
    monkeypatch.setattr(settings, "APPLICATION_FEE_AMOUNT", 2500.0)

    captured = {}
    class FakeResponse:
        def raise_for_status(self): pass
        def json(self): return {"id": "order_combined_001", "currency": "INR"}
    class FakeClient:
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def post(self, url, json=None, auth=None):
            captured.update(json or {})
            assert auth == ("rzp_test_combined", "combined_secret")
            return FakeResponse()
    monkeypatch.setattr(br.httpx, "Client", FakeClient)

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "ramesh@bidder.in").first()
        tender = db.query(Tender).filter(Tender.tender_no == "TDR-2026-012").first()
        req = br._payment_requirements(db, user, tender)
        assert req["application_fee"]["amount"] == 2500.0
        assert req["emd"]["required"] is True
        assert req["emd"]["amount"] == 150000.0
        assert req["checkout"]["amount"] == 152500.0
        assert req["checkout"]["payment_type"] == "APPLICATION_FEE_EMD"

        out = br.create_payment_order(tender.id, br.PaymentOrderIn(payment_type="AUTO"), _request(), user=user, db=db)
        assert out["amount"] == 15_250_000
        assert out["breakdown"] == {"application_fee": 2500.0, "emd": 150000.0}
        assert captured["amount"] == 15_250_000
        rec = db.query(TenderPayment).filter(TenderPayment.razorpay_order_id == "order_combined_001").first()
        assert rec.payment_type == "APPLICATION_FEE_EMD"

        payment_id = "pay_combined_001"
        sig = hmac.new(b"combined_secret", f"order_combined_001|{payment_id}".encode(), hashlib.sha256).hexdigest()
        verified = br.verify_payment(tender.id, br.PaymentVerifyIn(
            razorpay_order_id="order_combined_001", razorpay_payment_id=payment_id, razorpay_signature=sig
        ), _request(), user=user, db=db)
        assert verified["requirements"]["application_fee"]["paid"] is True
        assert verified["requirements"]["emd"]["paid"] is True
        assert verified["requirements"]["all_satisfied"] is True
    finally:
        db.close()


def test_no_emd_checkout_is_application_fee_only(monkeypatch, seeded):
    from app.core.config import settings
    from app.core.db import SessionLocal
    from app.models import Tender, User
    from app.routers import bidder as br
    monkeypatch.setattr(settings, "APPLICATION_FEE_AMOUNT", 2500.0)
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "ramesh@bidder.in").first()
        tender = db.query(Tender).filter(Tender.tender_no == "TDR-2026-017").first()
        req = br._payment_requirements(db, user, tender)
        assert req["emd"]["required"] is False
        assert req["checkout"]["amount"] == 2500.0
        assert req["checkout"]["payment_type"] == "APPLICATION_FEE"
    finally:
        db.close()


def test_emd_exemption_reduces_combined_checkout_to_application_fee(monkeypatch, seeded):
    from app.core.config import settings
    from app.core.db import SessionLocal
    from app.models import Tender, User
    from app.routers import bidder as br
    monkeypatch.setattr(settings, "APPLICATION_FEE_AMOUNT", 2500.0)
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "ramesh@bidder.in").first()
        tender = db.query(Tender).filter(Tender.tender_no == "TDR-2026-013").first()
        req = br._payment_requirements(db, user, tender)
        assert req["emd"]["exempt"] is True
        assert req["emd"]["required"] is False
        assert req["checkout"]["amount"] == 2500.0
    finally:
        db.close()
