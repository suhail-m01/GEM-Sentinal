"""Security primitives: JWT, bcrypt hashing, AES-256 field encryption, masking."""
import base64
import hashlib
import os
import uuid
from datetime import timedelta
from typing import Optional

from cryptography.fernet import Fernet
from jose import JWTError, jwt
from passlib.context import CryptContext

from .config import settings
from .db import utcnow

pwd_context = CryptContext(schemes=["bcrypt", "pbkdf2_sha256"], deprecated="auto")

# ---------------- Passwords ----------------

def hash_password(password: str) -> str:
    # Production/default remains bcrypt. The pure-Python PBKDF2 option exists
    # only for isolated CI/sandbox regression runs where a native bcrypt
    # wheel is unavailable; existing bcrypt hashes remain fully verifiable.
    if os.getenv("GEM_SENTINEL_TEST_PBKDF2", "").lower() in {"1", "true", "yes"}:
        return pwd_context.hash(password, scheme="pbkdf2_sha256")
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return pwd_context.verify(plain, hashed)
    except Exception:
        return False


# ---------------- JWT ----------------

def create_token(subject: str, role: str, purpose: str, minutes: int, session_id: str | None = None) -> str:
    now = utcnow()
    payload = {
        "sub": subject,
        "role": role,
        "purpose": purpose,
        "jti": uuid.uuid4().hex,  # unique per token (audit correlation)
        "iat": int(now.timestamp()),
        "exp": now + timedelta(minutes=minutes),
    }
    if session_id:
        payload["sid"] = session_id  # rotating session id (same for the session's token pair)
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str, purpose: str) -> Optional[dict]:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        if payload.get("purpose") != purpose:
            return None
        return payload
    except JWTError:
        return None


# ---------------- AES-256 field encryption (sensitive identifiers) ----------------

def _fernet() -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(settings.ENCRYPTION_KEY.encode()).digest())
    return Fernet(key)


_fernet = _fernet()


def encrypt_field(value: str) -> str:
    return _fernet.encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_field(value: str) -> str:
    return _fernet.decrypt(value.encode("ascii")).decode("utf-8")


# ---------------- Masking for normal UI/API responses ----------------

def mask_pan(value: str) -> str:
    v = (value or "").strip().upper()
    if len(v) < 5:
        return "***"
    return f"{v[:2]}***{v[-3:]}"


def mask_gstin(value: str) -> str:
    v = (value or "").strip().upper()
    if len(v) < 8:
        return "***"
    return f"{v[:2]}{'*' * (len(v) - 6)}{v[-4:]}"


def mask_udyam(value: str) -> str:
    v = (value or "").strip()
    if len(v) < 8:
        return "***"
    return f"{v[:4]}{'*' * (len(v) - 8)}{v[-4:]}"


def mask_mobile(value: str) -> str:
    v = (value or "").strip()
    if len(v) < 7:
        return "***"
    return f"{v[:3]}***{v[-3:]}"
