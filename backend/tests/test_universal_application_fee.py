"""Regression tests for universal application/platform maintenance fee."""

def test_every_seeded_tender_requires_application_fee(seeded):
    from app.core.config import settings
    from app.core.db import SessionLocal
    from app.models import Tender, User
    from app.routers.bidder import _payment_requirements

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "ramesh@bidder.in").first()
        tenders = db.query(Tender).all()
        assert tenders
        for tender in tenders:
            req = _payment_requirements(db, user, tender)
            assert req["application_fee"]["required"] is True, tender.tender_no
            assert req["application_fee"]["amount"] > 0, tender.tender_no
            assert req["application_fee"]["refundable"] is False, tender.tender_no
    finally:
        db.close()
