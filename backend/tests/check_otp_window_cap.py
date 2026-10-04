"""Check: the OTP per-window cap is a real-SMS protection, not a demo blocker.

Console mode sends nothing to the phone, so abandoned sign-ins must not lock an
operator out. The moment a real gateway is configured the cap must come back.

Run: cd backend && PYTHONPATH=. .venv/bin/python tests/check_otp_window_cap.py
"""
from fastapi import HTTPException

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.otp import issue_otp

EMAIL = "otp-window-check@example.invalid"
MOBILE = "+919000000001"
PURPOSE = "LOGIN"


def _burst(db, n):
    """Issue n codes back to back, bypassing only the resend cooldown."""
    from app.models.otp import OtpRecord
    from datetime import timedelta

    issued = 0
    for _ in range(n):
        try:
            issue_otp(db, email=EMAIL, mobile=MOBILE, purpose=PURPOSE)
            issued += 1
        except HTTPException as exc:
            if exc.status_code == 429 and "Too many" in str(exc.detail):
                return issued, "CAPPED"
            raise
        # age the record past the cooldown so we isolate the window cap
        rec = (db.query(OtpRecord)
               .filter(OtpRecord.email == EMAIL)
               .order_by(OtpRecord.created_at.desc()).first())
        rec.created_at = rec.created_at - timedelta(seconds=settings.OTP_RESEND_COOLDOWN + 1)
        db.commit()
    return issued, "UNCAPPED"


def main():
    db = SessionLocal()
    from app.models.otp import OtpRecord
    original = settings.SMS_PROVIDER
    failures = 0
    try:
        over = settings.OTP_MAX_PER_WINDOW + 2

        # console: nothing is dispatched, so no cap
        db.query(OtpRecord).filter(OtpRecord.email == EMAIL).delete()
        db.commit()
        settings.SMS_PROVIDER = "console"
        n, verdict = _burst(db, over)
        ok = verdict == "UNCAPPED" and n == over
        print(f"[{'PASS' if ok else 'FAIL'}] console mode issues {n}/{over} codes "
              f"without locking out ({verdict})")
        failures += 0 if ok else 1

        # real gateway: the SMS-bomb cap must fire
        db.query(OtpRecord).filter(OtpRecord.email == EMAIL).delete()
        db.commit()
        settings.SMS_PROVIDER = "msg91"
        n, verdict = _burst(db, over)
        ok = verdict == "CAPPED" and n == settings.OTP_MAX_PER_WINDOW
        print(f"[{'PASS' if ok else 'FAIL'}] live gateway caps at "
              f"{settings.OTP_MAX_PER_WINDOW} (issued {n}, {verdict})")
        failures += 0 if ok else 1
    finally:
        settings.SMS_PROVIDER = original
        db.query(OtpRecord).filter(OtpRecord.email == EMAIL).delete()
        db.commit()
        db.close()

    print("OK" if not failures else f"{failures} FAILED")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
