"""Deterministic extraction: never invents evidence.

Priority:
1. Gemini vision (if key present and succeeds)
2. Stored OCR data (from seeded/synced documents)
3. Local OCR (tesseract binary, if installed)
4. Empty result with low confidence → flagged for manual review.
"""
import os
import re
import shutil
import subprocess
from typing import Optional

from ..models import Document
from . import gemini

try:
    from PIL import Image, ImageFilter

    HAS_PIL = True
except Exception:
    HAS_PIL = False

ENGINE_NONE = "none"
ENGINE_STORED_OCR = "stored-ocr"
ENGINE_GEMINI = "gemini-vision"
ENGINE_TESSERACT = "tesseract-ocr"


def extraction_engine() -> str:
    if gemini._available():
        return ENGINE_GEMINI
    if shutil.which("tesseract"):
        return ENGINE_TESSERACT
    return ENGINE_STORED_OCR


def _tesseract_ocr(path: str) -> Optional[str]:
    tess = shutil.which("tesseract")
    if not tess or not path:
        return None
    try:
        out = subprocess.run(
            [tess, path, "stdout", "--psm", "6"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return out.stdout or None
    except Exception:
        return None


def _scan_fields(text: str) -> dict:
    fields: dict = {}
    patterns = {
        "gstin": r"\b[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]\b",
        "pan": r"\b[A-Z]{5}[0-9]{4}[A-Z]\b",
        "udyam_number": r"\bU[0-9]{4}[A-Z]{2}[0-9]{4}[A-Z0-9]{6,}\b",
        "certificate_number": r"\b(CERT|CERTIFICATE|REG)[\s/-]*[0-9A-Z]{4,}\b",
        "issue_date": r"\b(?:0[1-9]|[12][0-9]|3[01])[/\-](?:0[1-9]|1[0-2])[/\-](?:19|20)[0-9]{2}\b",
        "expiry_date": r"\b(?:0[1-9]|[12][0-9]|3[01])[/\-](?:0[1-9]|1[0-2])[/\-](?:19|20)[0-9]{2}\b",
    }
    for key, pat in patterns.items():
        m = re.search(pat, text)
        if m:
            fields[key] = m.group(0)
    if "issue_date" in fields and "expiry_date" in fields and fields["issue_date"] == fields["expiry_date"]:
        expiry = [d for d in re.findall(patterns["expiry_date"], text)]
        if len(expiry) > 1:
            fields["expiry_date"] = expiry[-1]
    words = set(re.findall(r"[A-Z][A-Za-z]{3,}", text.upper()))
    for candidate in words:
        if any(w in candidate for w in ("PVT", "LTD", "TRADERS", "ENGINEERING", "INDUSTRIES", "SUPPLIES", "POLYFAB")):
            fields["legal_name"] = candidate
            break
    return fields


def extract_document_fields(doc: Document, file_path: Optional[str] = None) -> tuple[dict, float, str]:
    """Returns (fields, confidence, engine). Low confidence is never upgraded."""
    # 1. Gemini vision
    if file_path and file_path.lower().endswith((".png", ".jpg", ".jpeg")):
        try:
            with open(file_path, "rb") as f:
                data = f.read()
            fields = gemini.extract_from_image(data, doc.doc_type)
            if fields:
                return fields, 0.8, ENGINE_GEMINI
        except Exception:
            pass

    # 2. stored OCR (seeded/synced)
    if doc.ocr_data:
        return doc.ocr_data, float(doc.ocr_confidence or 0.5), ENGINE_STORED_OCR

    # 3. local tesseract
    if file_path:
        text = _tesseract_ocr(file_path)
        if text:
            fields = _scan_fields(text)
            if fields:
                conf = min(0.65, 0.35 + 0.05 * len(fields))  # capped: heuristic OCR is low confidence
                return {**fields, "raw_text": text[:2000]}, conf, ENGINE_TESSERACT

    # 4. nothing reliable — do NOT invent
    return {}, 0.0, ENGINE_NONE
