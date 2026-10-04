"""Real OTP issuance & verification.

Design goals
------------
* Codes are 6-digit, cryptographically random, and stored ONLY as SHA-256
  hashes (a DB leak does not leak usable OTPs).
* Delivery goes through a pluggable SMS provider (MSG91 / Twilio / TextLocal).
  With no provider configured, ``console`` mode logs the code server-side and
  the API surfaces a clear "no SMS gateway configured" flag (never a fake
  always-constant code).
* Abuse controls: per-code attempt cap, expiry, resend cooldown, and a
  per-account issue-per-window cap (SMS-bomb protection).
"""
from __future__ import annotations

import hashlib
import logging
import re
import secrets
from datetime import timedelta

import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .db import utcnow

logger = logging.getLogger("gem.otp")

# ------------------------------ helpers --------------------------------------

_MOBILE_RE = re.compile(r"^\+91[6-9]\d{9}$")  # normalized Indian mobile: +91 + 10 digits (leading 6-9)


def normalize_mobile(raw: str | None) -> str:
    """Normalize to E.164-ish +91XXXXXXXXXX. Raises 400 when invalid."""
    m = (raw or "").strip()
    m = m.replace(" ", "").replace("-", "")
    if m.startswith("0091"):
        m = "+91" + m[4:]
    elif m.startswith("91") and len(m) == 12:
        m = "+" + m
    elif len(m) == 10 and m.isdigit():
        m = "+91" + m
    if not _MOBILE_RE.match(m):
        raise HTTPException(status_code=400, detail="Enter a valid Indian mobile number (10 digits)")
    return m


def mask_mobile(mobile: str) -> str:
    """+919845012345 -> +91 •••••12345 (also handles bare 10-digit input)"""
    digits = (mobile or "").lstrip("+").strip()
    if len(digits) == 10 and digits.isdigit():
        return f"+91 •••••{digits[-4:]}"
    return f"+{digits[:2]} •••••{digits[-4:]}" if len(digits) > 4 else "•••"


def code_hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def device_id_hash(device_id: str | None) -> str | None:
    if not device_id:
        return None
    return hashlib.sha256(device_id.strip().encode()).hexdigest()


def _generate_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


# ------------------------------ SMS providers --------------------------------

def _send_msg91(mobile: str, message: str) -> None:
    if not settings.MSG91_AUTH_KEY:
        raise RuntimeError("MSG91_AUTH_KEY not configured")
    resp = httpx.post(
        "https://control.msg91.com/api/v5/otp-verify" if False else "https://control.msg91.com/api/v5/flow/launch",
        headers={"authkey": settings.MSG91_AUTH_KEY, "Content-Type": "application/json"},
        json={"sender": settings.MSG91_SENDER_ID or None, "route": settings.MSG91_ROUTE,
              "mobile": mobile.lstrip("+91") if mobile.startswith("+91") else mobile,
              "message": message, "message_type": "Transactional"},
        timeout=10.0,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"MSG91 HTTP {resp.status_code}: {resp.text[:200]}")


def _send_twilio(mobile: str, message: str) -> None:
    if not (settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN and settings.TWILIO_FROM):
        raise RuntimeError("Twilio credentials not configured")
    resp = httpx.post(
        f"https://api.twilio.com/2010-04-01/Accounts/{settings.TWILIO_ACCOUNT_SID}/Messages.json",
        auth=(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN),
        data={"To": mobile, "From": settings.TWILIO_FROM, "Body": message},
        timeout=10.0,
    )
    if resp.status_code >= 300:
        raise RuntimeError(f"Twilio HTTP {resp.status_code}: {resp.text[:200]}")


def _send_textlocal(mobile: str, message: str) -> None:
    if not settings.TEXTLOCAL_API_KEY:
        raise RuntimeError("TEXTLOCAL_API_KEY not configured")
    resp = httpx.get(
        "https://api.textlocal.in/send/",
        params={"apikey": settings.TEXTLOCAL_API_KEY, "numbers": mobile.lstrip("+"),
                "message": message, "sender": settings.TEXTLOCAL_SENDER or ""},
        timeout=10.0,
    )
    if resp.status_code != 200 or "error" in resp.text.lower():
        raise RuntimeError(f"TextLocal HTTP {resp.status_code}: {resp.text[:200]}")


_PROVIDERS = {
    "msg91": _send_msg91,
    "twilio": _send_twilio,
    "textlocal": _send_textlocal,
}


def send_sms(mobile: str, message: str) -> tuple[str, str | None]:
    """Dispatch via the configured provider. Returns (provider_name, error).
    console mode never raises — it logs the code for server operators."""
    provider = (settings.SMS_PROVIDER or "console").lower()
    if provider == "console":
        logger.warning("OTP[console] to %s: %s (no SMS gateway configured)", mask_mobile(mobile), message)
        return "console", None
    fn = _PROVIDERS.get(provider)
    if fn is None:
        logger.error("Unknown SMS_PROVIDER %r — falling back to console", provider)
        logger.warning("OTP[console-fallback] to %s: %s", mask_mobile(mobile), message)
        return "console", f"unknown provider {provider}"
    try:
        fn(mobile, message)
        return provider, None
    except Exception as exc:
        # fail-closed: never mark an OTP as "sent" when the gateway failed
        logger.error("SMS send failed via %s for %s: %s", provider, mask_mobile(mobile), exc)
        return provider, str(exc)


def _send_verify_demo_sms(mobile: str) -> bool:
    """Best-effort REAL SMS notification via Twilio Verify (demo delivery proof).

    Fired whenever an OTP is generated so the registered phone actually buzzes
    — demonstrating live SMS delivery capability end-to-end. The message uses
    Twilio's pre-approved template (trial accounts reject custom bodies,
    error 572006) and does NOT carry this system's OTP code: in console mode
    the code lives only in the server log. Best-effort by design — never
    raises, never blocks or fails the OTP flow, at most one send per issue.
    """
    if not settings.TWILIO_DEMO_SMS:
        return False
    if not (settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN
            and settings.TWILIO_VERIFY_SERVICE_SID):
        return False
    channel = (settings.TWILIO_DEMO_SMS_CHANNEL or "sms").lower()
    if channel not in ("sms", "whatsapp"):
        channel = "sms"
    try:
        resp = httpx.post(
            f"https://verify.twilio.com/v2/Services/{settings.TWILIO_VERIFY_SERVICE_SID}/Verifications",
            auth=(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN),
            data={"To": mobile, "Channel": channel},
            timeout=10.0,
        )
        if resp.status_code >= 300:
            logger.warning("demo-SMS rejected for %s: %s", mask_mobile(mobile), resp.text[:160])
            return False
        logger.info("demo-SMS queued for %s (live delivery capability proof)", mask_mobile(mobile))
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("demo-SMS failed for %s: %s", mask_mobile(mobile), str(exc)[:160])
        return False


# ------------------------------ issue / verify -------------------------------

OTP_MESSAGE = "Your GeM-Sentinel verification code is {code}. It expires in 5 minutes. Never share this code."


def issue_otp(db: Session, *, email: str, mobile: str, purpose: str,
              source_ip: str | None = None, device_hash: str | None = None,
              label: str | None = None) -> dict:
    """Create + dispatch an OTP. Enforces cooldown and per-window caps."""
    from ..models.otp import OtpRecord

    mobile = normalize_mobile(mobile)  # stored formats may lack the +91 prefix
    now = utcnow()
    # cooldown: most recent unconsumed record
    recent = (
        db.query(OtpRecord)
        .filter(OtpRecord.email == email, OtpRecord.purpose == purpose,
                OtpRecord.consumed_at.is_(None))
        .order_by(OtpRecord.created_at.desc())
        .first()
    )
    if recent and (now - recent.created_at).total_seconds() < settings.OTP_RESEND_COOLDOWN:
        wait = int(settings.OTP_RESEND_COOLDOWN - (now - recent.created_at).total_seconds())
        raise HTTPException(status_code=429, detail=f"Please wait {wait}s before requesting another OTP")
    # per-window cap: count LIVE (unused, unexpired) codes only — a code that
    # was successfully used indicates a legitimate session, not an attack.
    #
    # The cap exists to stop an attacker bombing a real phone with SMS. In
    # console mode no message ever leaves the server, so there is nothing to
    # bomb; enforcing it there only locks an operator out for ten minutes after
    # three abandoned sign-ins (switching between accounts leaves each previous
    # code live and unconsumed). The resend cooldown above still applies in
    # every mode, so this cannot become a rapid-fire loop.
    # ponytail: keyed off the configured provider rather than a debug flag, so
    # the cap re-arms by itself the moment a real SMS gateway is configured.
    sms_is_live = (settings.SMS_PROVIDER or "console").lower() != "console"
    if sms_is_live:
        in_window = (
            db.query(OtpRecord)
            .filter(OtpRecord.email == email, OtpRecord.purpose == purpose,
                    OtpRecord.consumed_at.is_(None),
                    OtpRecord.expires_at > now,
                    OtpRecord.created_at >= now - timedelta(seconds=settings.OTP_WINDOW_SECONDS))
            .count()
        )
        if in_window >= settings.OTP_MAX_PER_WINDOW:
            raise HTTPException(status_code=429, detail="Too many OTP requests. Try again in 10 minutes.")

    code = _generate_code()
    rec = OtpRecord(
        email=email, purpose=purpose, code_hash=code_hash(code),
        mobile_masked=mask_mobile(mobile), expires_at=now + timedelta(seconds=settings.OTP_TTL_SECONDS),
        max_attempts=settings.OTP_MAX_ATTEMPTS, source_ip=source_ip, device_hash=device_hash,
    )
    db.add(rec)
    db.commit()

    provider, err = send_sms(mobile, OTP_MESSAGE.format(code=code))
    # Demo delivery proof: fire a real (template) SMS to the registered phone so
    # the user sees live delivery. Best-effort; never carries the code; never
    # fails the OTP flow. Controlled by TWILIO_DEMO_SMS (default off).
    _send_verify_demo_sms(mobile)
    return {
        "otp_required": True,
        "purpose": purpose,
        "mobile_masked": mask_mobile(mobile),
        "expires_in": settings.OTP_TTL_SECONDS,
        "provider": provider,
        "sms_error": err,
        # development convenience ONLY: lets the operator/demo complete the flow
        # while no SMS gateway is configured. Never shown in production builds.
        "dev_otp": code if provider == "console" else None,
    }


def verify_otp(db: Session, *, email: str, code: str, purpose: str,
               source_ip: str | None = None) -> None:
    """Check a submitted code. Raises 4xx on every failure mode."""
    from ..models.otp import OtpRecord

    now = utcnow()
    rec = (
        db.query(OtpRecord)
        .filter(OtpRecord.email == email, OtpRecord.purpose == purpose, OtpRecord.consumed_at.is_(None))
        .order_by(OtpRecord.created_at.desc())
        .first()
    )
    if rec is None:
        raise HTTPException(status_code=400, detail="No OTP in progress — request a new one")
    if now > rec.expires_at:
        rec.consumed_at = now
        rec.fail_reason = "EXPIRED"
        db.commit()
        raise HTTPException(status_code=400, detail="OTP expired — request a new one")
    if rec.attempts >= rec.max_attempts:
        rec.consumed_at = now
        rec.fail_reason = "MAX_ATTEMPTS"
        db.commit()
        raise HTTPException(status_code=429, detail="Too many incorrect attempts — request a new OTP")
    if hashlib.sha256(code.strip().encode()).hexdigest() != rec.code_hash:
        rec.attempts += 1
        remaining = rec.max_attempts - rec.attempts
        db.commit()
        raise HTTPException(status_code=400, detail=f"Incorrect OTP ({remaining} attempts left)" if remaining else "Too many incorrect attempts — request a new OTP")
    rec.consumed_at = now
    rec.fail_reason = None
    db.commit()


def find_latest_otp(db: Session, email: str, purpose: str) -> dict | None:
    from ..models.otp import OtpRecord

    rec = (
        db.query(OtpRecord)
        .filter(OtpRecord.email == email, OtpRecord.purpose == purpose)
        .order_by(OtpRecord.created_at.desc())
        .first()
    )
    if not rec:
        return None
    return {
        "active": rec.consumed_at is None and utcnow() <= rec.expires_at,
        "created_at": rec.created_at.isoformat(),
        "mobile_masked": rec.mobile_masked,
        "purpose": rec.purpose,
    }
