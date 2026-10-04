from fastapi import APIRouter
from sqlalchemy import text

from ..core.config import settings
from ..core.db import engine

router = APIRouter(tags=["health"])


@router.get("/health")
def health():
    db_ok = False
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    return {
        "service": "gem-sentinel-backend",
        "status": "ok" if db_ok else "degraded",
        "database": "up" if db_ok else "down",
        "environment": settings.ENVIRONMENT,
        "sms_provider": (settings.SMS_PROVIDER or "console").lower(),
        "demo_sms": bool(
            settings.TWILIO_DEMO_SMS and settings.TWILIO_VERIFY_SERVICE_SID
            and settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN
        ),
        "principle": "The Rule Engine Decides. AI Only Explains.",
    }
