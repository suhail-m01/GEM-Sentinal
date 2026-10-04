from functools import lru_cache
from typing import List
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    APP_NAME: str = "GeM-Sentinel"
    APP_TAGLINE: str = "Verify Once. Trust Always."
    ENVIRONMENT: str = "development"
    # V2.1 — production disables demo provisioning, Document Studio exports, etc.
    APP_ENV: str = "development"  # development | production
    DEMO_MODE: bool = True  # hard-off when APP_ENV == production

    # Trusted reverse proxies allowed to contribute X-Forwarded-For (CIDR list)
    TRUSTED_PROXY_CIDRS: str = ""

    # Database (PostgreSQL per specification)
    DATABASE_URL: str = "postgresql+psycopg2://gemsentinel:gemsentinel123@127.0.0.1:5432/gem_sentinel"

    # JWT / secrets (never hardcode real secrets; these are development defaults)
    JWT_SECRET: str = "dev-only-jwt-secret-change-in-production"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_MINUTES: int = 30
    REFRESH_TOKEN_DAYS: int = 7
    REAUTH_TOKEN_MINUTES: int = 10

    # AES-256 field encryption (Fernet key derived from this secret)
    ENCRYPTION_KEY: str = "dev-only-encryption-key-change-in-production"

    # AI — pluggable, provider-neutral. Any/none may be configured; the
    # deterministic local fallback keeps the demo fully functional offline.
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-1.5-flash"
    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "llama-3.1-8b-instant"
    HF_API_TOKEN: str = ""
    AI_PROVIDER: str = "auto"  # auto | gemini | groq | huggingface | local

    # Financial envelope encryption (Fernet / AES-128-CBC+HMAC — key material
    # derived from this secret; envelope fields are never serialized generically)
    ENVELOPE_ENCRYPTION_KEY: str = "dev-only-envelope-key-change-in-production"

    # Email provider — SIMULATED (in-app outbox) unless SMTP is configured
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = ""
    SMTP_TLS: bool = True
    EMAIL_MODE: str = "simulated"  # simulated | smtp

    # Mock government portals
    MOCK_PORTAL_URL: str = "http://127.0.0.1:8001"
    PORTAL_TIMEOUT_SECONDS: float = 2.5

    # Verification-result cache (additive; BidVerify-AI 3-tier cache port)
    # In-process LRU → Postgres. Only deterministic source states are cached;
    # UNAVAILABLE/ERROR are never cached (UNABLE_TO_VERIFY doctrine preserved).
    VERIFY_CACHE_ENABLED: bool = True
    VERIFY_CACHE_TTL_SECONDS: int = 900
    VERIFY_CACHE_MAX_ENTRIES: int = 512

    # File storage
    UPLOAD_DIR: str = ""  # defaults to <repo>/storage

    # Connection pool (tuned for high-concurrency traffic)
    DB_POOL_SIZE: int = 20        # steady-state connections per worker
    DB_MAX_OVERFLOW: int = 40     # burst capacity on top of pool_size
    DB_POOL_RECYCLE: int = 1800   # seconds; recycle before server-side idle timeouts
    MAX_UPLOAD_MB: int = 10
    ALLOWED_UPLOAD_EXTENSIONS: str = ".pdf,.png,.jpg,.jpeg"

    # SLA (business days simplified to calendar days for prototype)
    L1_SLA_DAYS: int = 5
    L2_SLA_DAYS: int = 3

    # Prototype OTP (static/mock OTP permitted for prototype)
    MOCK_OTP_CODE: str = "123456"

    # --- Real OTP / SMS delivery -------------------------------------------
    # SMS_PROVIDER: console (default; logs the code, no SMS) | msg91 | twilio | textlocal
    SMS_PROVIDER: str = "console"
    # MSG91 (Indian govt-standard transactional SMS; requires DLT-registered template)
    MSG91_AUTH_KEY: str = ""
    MSG91_SENDER_ID: str = ""
    MSG91_ROUTE: str = "4"
    # Twilio
    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    TWILIO_FROM: str = ""
    # Twilio Verify service used for the best-effort "demo SMS" notification:
    # whenever an OTP is generated, a real SMS (pre-approved template, trial-safe)
    # is fired to the registered phone to demonstrate live delivery capability.
    # The OTP code itself is NOT in that message and is never sent there — in
    # console mode the code lives only in the server log.
    TWILIO_VERIFY_SERVICE_SID: str = ""
    TWILIO_DEMO_SMS: bool = False
    # Channel for the demo-SMS proof: "sms" | "whatsapp" (WhatsApp only reaches
    # numbers that have previously messaged the Twilio account — trial rule).
    TWILIO_DEMO_SMS_CHANNEL: str = "sms"
    # TextLocal
    TEXTLOCAL_API_KEY: str = ""
    TEXTLOCAL_SENDER: str = ""
    # OTP policy
    OTP_TTL_SECONDS: int = 300        # code valid for 5 minutes
    OTP_MAX_ATTEMPTS: int = 5         # then the code is invalidated
    OTP_RESEND_COOLDOWN: int = 60     # seconds between re-issues
    OTP_MAX_PER_WINDOW: int = 3       # max issues per window (SMS-bomb protection)
    OTP_WINDOW_SECONDS: int = 600     # ...per this window
    TRUSTED_DEVICE_DAYS: int = 30     # "trust this device" duration

    # Override justification policy
    MIN_JUSTIFICATION_LENGTH: int = 30
    MIN_OTHER_JUSTIFICATION_LENGTH: int = 100

    # Rate limiting (login attempt monitoring)
    RATE_LIMIT_LOGIN: str = "30/minute"

    # Sentinel Watch — continuous compliance monitoring
    WATCH_ENABLED: bool = True
    WATCH_TICK_SECONDS: int = 20      # scheduler cadence (jittered); demo-friendly
    # Bid-sealing toggle (demo/testing override). True (default) = bids sealed
    # until the bid deadline (spec behavior). Set BID_SEALING_ENABLED=false in
    # .env to run AI verification at any time (evaluation always open).
    BID_SEALING_ENABLED: bool = True

    # Financial bidding / auction feature flags (additive). Each can be
    # disabled without affecting the rest of the platform: the app still
    # launches, all existing flows still work, and the disabled endpoint
    # returns a controlled 409/403 message.
    ENABLE_FINANCIAL_BIDDING: bool = True
    ENABLE_REVERSE_AUCTION: bool = True
    ENABLE_FORWARD_AUCTION: bool = True

    # Razorpay Standard Checkout (test keys are recommended for SIH/demo).
    # Secret is backend-only; only RAZORPAY_KEY_ID is returned to the browser.
    RAZORPAY_KEY_ID: str = ""
    RAZORPAY_KEY_SECRET: str = ""
    RAZORPAY_WEBHOOK_SECRET: str = ""
    ENABLE_RAZORPAY_PAYMENTS: bool = True
    # Universal non-refundable platform/application fee charged to every bidder submission.
    APPLICATION_FEE_AMOUNT: float = 2500.0
    # Bid placement rate limit (slowapi, per user+auction) — anti-flooding.
    RATE_LIMIT_AUCTION_BID: str = "50/minute"
    WATCH_FLAP_CONFIRMATIONS: int = 2 # consistent observations before a CRITICAL fires

    @model_validator(mode="after")
    def _production_safety(self):
        """Fail closed on production deployments that still use demo secrets.

        Development remains zero-config, but APP_ENV=production must never
        silently run with the repository defaults. Demo-only functionality is
        also forced off regardless of an accidentally-set DEMO_MODE flag.
        """
        if (self.APP_ENV or "").strip().lower() == "production":
            self.DEMO_MODE = False
            insecure = []
            defaults = {
                "JWT_SECRET": "dev-only-jwt-secret-change-in-production",
                "ENCRYPTION_KEY": "dev-only-encryption-key-change-in-production",
                "ENVELOPE_ENCRYPTION_KEY": "dev-only-envelope-key-change-in-production",
            }
            for name, default in defaults.items():
                value = (getattr(self, name, "") or "").strip()
                if not value or value == default:
                    insecure.append(name)
            if insecure:
                raise ValueError(
                    "Production startup refused: replace insecure/missing secrets: "
                    + ", ".join(insecure)
                )
        return self

    @property
    def allowed_upload_extensions(self) -> List[str]:
        return [e.strip().lower() for e in self.ALLOWED_UPLOAD_EXTENSIONS.split(",") if e.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
