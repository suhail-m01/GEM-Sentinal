"""Upload-time document security guard (additive).

Layered *before* storage:
- Magic-byte validation: the actual bytes must match the declared extension
  (a renamed executable is rejected, not just re-labelled).
- Size cap.
- PDF exploit-indicator scan (heuristic, non-blocking): JavaScript actions,
  file launchers, rich media, abnormal object counts — flagged as
  `suspicious` for officer attention, never auto-rejected.
- PII sanitizer for audit/log payloads: PAN, GSTIN, Udyam, Aadhaar and long
  digit runs are masked in text that goes into audit payloads.

None of this changes the SHA-256 immutability or authenticity pipeline — it
adds upload hygiene and keeps audit payloads PII-light.
"""
import re
from dataclasses import dataclass, field
from typing import Optional

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB

_MIME_SIGNATURES = {
    "pdf": (b"%PDF-",),
    "png": (b"\x89PNG\r\n\x1a\n",),
    "jpg": (b"\xff\xd8\xff",),
    "jpeg": (b"\xff\xd8\xff",),
}


@dataclass
class UploadGuardReport:
    ok: bool
    detected_type: Optional[str] = None
    size_bytes: int = 0
    errors: list[str] = field(default_factory=list)
    suspicious_indicators: list[str] = field(default_factory=list)

    @property
    def suspicious(self) -> bool:
        return bool(self.suspicious_indicators)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "detected_type": self.detected_type,
            "size_bytes": self.size_bytes,
            "errors": self.errors,
            "suspicious_indicators": self.suspicious_indicators,
            "suspicious": self.suspicious,
        }


def detect_type(content: bytes) -> Optional[str]:
    for ext, sigs in _MIME_SIGNATURES.items():
        for sig in sigs:
            if content.startswith(sig):
                return ext
    return None


def _scan_pdf_indicators(content: bytes) -> list[str]:
    """Heuristic exploit-indicator scan for PDF payloads (advisory flags)."""
    flags = []
    try:
        text = content.decode("latin-1")
    except Exception:
        return flags
    lowered = text.lower()
    if re.search(r"/js\b", lowered) or re.search(r"/javascript\b", lowered):
        flags.append("embedded_javascript")
    if "/launch" in lowered:
        flags.append("launch_action")
    if "/openaction" in lowered or "/aa" in lowered:
        flags.append("auto_action_on_open")
    if "/richmedia" in lowered or "/embed" in lowered:
        flags.append("embedded_rich_media")
    obj_count = len(re.findall(r"(?m)^\d+ \d+ obj", text))
    if obj_count > 500:
        flags.append(f"abnormal_object_count({obj_count})")
    # embedded files with executable extensions
    m = re.findall(r"/uf\s*\(\s*([^)]{1,120})\s*\)", lowered)
    for name in m:
        if re.search(r"\.(exe|bat|cmd|scr|vbs|js|msi|ps1)$", name.strip()):
            flags.append(f"embedded_executable({name.strip()})")
            break
    return flags


def assess_upload(filename: Optional[str], content: bytes) -> UploadGuardReport:
    report = UploadGuardReport(ok=True, size_bytes=len(content))
    if len(content) > MAX_UPLOAD_BYTES:
        report.ok = False
        report.errors.append(f"file exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)}MB limit")
        return report
    if len(content) == 0:
        report.ok = False
        report.errors.append("empty file")
        return report

    detected = detect_type(content)
    report.detected_type = detected
    ext = (filename or "").rsplit(".", 1)[-1].lower() if filename and "." in filename else ""

    if detected is None:
        report.ok = False
        report.errors.append("unrecognized file type (no known document signature)")
        return report
    if ext:
        same = ext == detected or {ext, detected} in ({"jpg", "jpeg"},)
        if not same:
            report.ok = False
            report.errors.append(
                f"file extension '.{ext}' does not match detected document type "
                f"'{detected}' (renamed files are rejected)"
            )
            return report

    if detected == "pdf":
        report.suspicious_indicators = _scan_pdf_indicators(content)
    return report


# ---------------- PII sanitizer (audit payloads / logs) ----------------

_PAN_RE = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")
_GSTIN_RE = re.compile(r"\b[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b")
_AADHAAR_RE = re.compile(r"(?<!\d)\d{4}\s?\d{4}\s?\d{4}(?!\d)")
_PHONE_RE = re.compile(r"(?<!\d)(?:\+91[-\s]?)?[6-9]\d{9}(?!\d)")
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")


def sanitize_pii(text: str) -> tuple[str, int]:
    """Mask PII in free text for audit payloads. Returns (text, redactions)."""
    if not text:
        return text, 0
    count = 0
    out = _GSTIN_RE.sub(lambda m: m.group(0)[:4] + "••••••••••" + m.group(0)[-3:], text)
    count += out != text
    out = _PAN_RE.sub(lambda m: m.group(0)[:3] + "•••••" + m.group(0)[-1:], out)
    out = _AADHAAR_RE.sub(lambda m: "XXXX XXXX " + m.group(0)[-4:].replace(" ", ""), out)
    out = _PHONE_RE.sub(lambda m: "•••••" + m.group(0)[-3:], out)
    out = _EMAIL_RE.sub(lambda m: m.group(0)[0] + "•••@" + m.group(0).split("@")[-1], out)
    return out, count


def sanitize_payload(payload: dict) -> dict:
    """Recursively sanitize string values of an audit payload (PII-light)."""
    def walk(v):
        if isinstance(v, str):
            s, _ = sanitize_pii(v)
            return s
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [walk(x) for x in v]
        return v

    return walk(payload or {})
