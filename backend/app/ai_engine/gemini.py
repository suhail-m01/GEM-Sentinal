"""Gemini (free tier) client. Returns None on any failure so callers fall
back to the deterministic path. AI output is advisory explanation only."""
import base64
import logging
import time
from typing import Optional

import httpx

from ..core.config import settings

logger = logging.getLogger("gem.ai")

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def _available() -> bool:
    return bool(settings.GEMINI_API_KEY)


def _call(parts: list[dict], prompt: str, _attempt: int = 0) -> Optional[str]:
    if not _available():
        return None
    try:
        resp = httpx.post(
            GEMINI_URL.format(model=settings.GEMINI_MODEL),
            headers={"x-goog-api-key": settings.GEMINI_API_KEY},
            json={
                "contents": [{"parts": [{"text": prompt}, *parts]}],
                "generationConfig": {"temperature": 0.2, "maxOutputTokens": 2048},
            },
            timeout=60.0,
        )
        if resp.status_code in (429, 503) and _attempt < 3:
            # Transient free-tier overload/rate limit — bounded retries, 2/4/6s backoff.
            time.sleep(2.0 * (_attempt + 1))
            return _call(parts, prompt, _attempt + 1)
        if resp.status_code != 200:
            logger.warning("Gemini error: HTTP %s", resp.status_code)
            return None
        data = resp.json()
        content = (data["candidates"][0].get("content") or {})
        # 3.x models may include thought parts without text — join text parts only.
        text = "".join(p.get("text", "") for p in content.get("parts", []) if p.get("text")).strip()
        return text or None
    except Exception as exc:
        logger.warning("Gemini call failed: %s", exc)
        return None


def explain_structured_findings(findings: dict, language: str) -> Optional[str]:
    """Ask Gemini to explain pre-computed deterministic findings (advisory)."""
    if not _available():
        return None
    lang = "Hindi" if language == "hi" else "English"
    prompt = (
        f"You are a compliance explanation assistant for a government procurement "
        f"decision-support system. The deterministic rule engine has ALREADY decided "
        f"the outcome; your job is ONLY to explain the findings in plain {lang}. "
        f"Do not change, soften, or override the findings. Keep it under 180 words. "
        f"Findings (JSON): {findings}"
    )
    return _call([], prompt)


def extract_from_image(image_bytes: bytes, doc_type: str) -> Optional[dict]:
    """Vision extraction via Gemini. Returns None on any failure (fallback path)."""
    if not _available():
        return None
    prompt = (
        "Extract structured fields from this procurement document "
        f"(expected type: {doc_type}). Return ONLY a JSON object with keys among: "
        "legal_name, trade_name, pan, gstin, udyam_number, certificate_number, "
        "issue_date, expiry_date, issuer, registration_date, category, status, "
        "address, turnover, raw_text. Use null for anything you cannot read with "
        "high confidence. Do not guess."
    )
    b64 = base64.b64encode(image_bytes).decode()
    parts = [{"inlineData": {"mimeType": "image/jpeg", "data": b64}}]
    text = _call(parts, prompt)
    if not text:
        return None
    try:
        import json
        start = text.find("{")
        end = text.rfind("}")
        return json.loads(text[start : end + 1])
    except Exception:
        return None
