"""Add/update the payment demo policy on an existing GeM-Sentinel database.

Safe to run repeatedly. It does not delete or recreate any tender/application.
"""
from .core.db import Base, engine, SessionLocal, ensure_schema
from .models import Tender


def main() -> None:
    # Bring an existing database up to the current schema before querying tenders.
    Base.metadata.create_all(bind=engine)
    ensure_schema()
    db = SessionLocal()
    try:
        scenarios = {
            "TDR-2026-003": dict(
                application_fee_required=True,
                application_fee_amount=2500,
                application_fee_refundable=False,
                bid_security_required=True,
                bid_security_amount=None,
                bid_security_percent=2.0,
                bid_security_exemption_allowed=True,
            ),
            "TDR-2026-001": dict(
                application_fee_required=True,
                application_fee_amount=2500,
                application_fee_refundable=False,
                bid_security_required=True,
                bid_security_amount=None,
                bid_security_percent=1.0,
                bid_security_exemption_allowed=True,
            ),
        }
        for tender_no, values in scenarios.items():
            t = db.query(Tender).filter(Tender.tender_no == tender_no).first()
            if not t:
                print(f"skip {tender_no}: not found")
                continue
            for key, value in values.items():
                setattr(t, key, value)
            print(f"configured {tender_no}")
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    main()
