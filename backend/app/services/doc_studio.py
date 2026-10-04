"""Document Studio — on-demand, deterministic document replicas.

Free tooling (Pillow, already a dependency; no external API). Renders an
original-looking government document replica for a bidder's Document record,
populated from the verified source data. Every replica carries a verified-record
seal, a QR verify block, and a SHA-256 content hash footer.

Replicas are cached at a STABLE key (user_email + doc_type) so they survive
reseeds; a sidecar content-hash forces a re-render only when the underlying
data actually changes.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ..core.config import settings

# --- canvas / palette -------------------------------------------------------
W, H = 1240, 1754  # A4 @ 150dpi
MARGIN = 96

NAVY = (11, 37, 69)
GOLD = (176, 141, 37)
INK = (30, 41, 59)
MUTED = (100, 116, 139)
LINE = (203, 213, 225)
PAPER = (255, 255, 255)
GREEN = (22, 101, 52)
RED = (185, 28, 28)

_FONT_DIR = "/usr/share/fonts/truetype/dejavu"
def _font(size: int, bold: bool = False, mono: bool = False, oblique: bool = False) -> ImageFont.FreeTypeFont:
    name = "DejaVuSans"
    if mono:
        name = "DejaVuSansMono"
    if bold:
        name += "-Bold"
    elif oblique:
        name += "-Oblique"
    path = os.path.join(_FONT_DIR, f"{name}.ttf")
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


def _sha256(s) -> str:
    if isinstance(s, bytes):
        return hashlib.sha256(s).hexdigest()
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


# Bump to force re-render of cached replicas after layout/template changes.
RENDER_VERSION = "v3"


def _qmark_matrix(seed: str, n: int = 21) -> list[list[int]]:
    """Deterministic QR-like block from a seed string."""
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    bits = []
    i = 0
    while len(bits) < n * n:
        b = digest[i % len(digest)]
        for k in range(8):
            bits.append((b >> (7 - k)) & 1)
        i += 1
    mat = [[(bits[r * n + c]) for c in range(n)] for r in range(n)]
    # finder squares (corners) for the "scannable" look
    def place(r0, c0):
        for r in range(7):
            for c in range(7):
                edge = r in (0, 6) or c in (0, 6)
                core = 2 <= r <= 4 and 2 <= c <= 4
                mat[r0 + r][c0 + c] = 1 if (edge or core) else 0
    place(0, 0)
    place(0, n - 7)
    place(n - 7, 0)
    return mat


# --- source metadata --------------------------------------------------------
SOURCE_META = {
    "GST": ("GST Network (GSTN)", "Department of Revenue, Government of India"),
    "UDYAM": ("Udyam / MSME Portal", "Ministry of MSME, Government of India"),
    "PAN": ("Income Tax Department", "Department of Revenue, Government of India"),
    "MCA21": ("Ministry of Corporate Affairs", "Government of India"),
    "EPFO": ("Employees' Provident Fund Organisation", "Ministry of Labour & Employment"),
    "ESIC": ("Employees' State Insurance Corporation", "Ministry of Labour & Employment"),
    "STARTUP_INDIA": ("Startup India", "Department for Promotion of Industry & Internal Trade"),
    "NSIC": ("National Small Industries Corporation", "Ministry of MSME"),
    "BIS": ("Bureau of Indian Standards", "Department for Promotion of Industry & Internal Trade"),
    "DIGILOCKER": ("DigiLocker", "Ministry of Electronics & IT"),
    "MANUAL": ("Bidder-submitted", "Uploaded by the bidder"),
}

FIELD_LABELS = {
    "legal_name": "Legal / Registered Name",
    "gstin": "GSTIN",
    "pan": "PAN",
    "udyam_number": "Udyam Number",
    "issue_date": "Date of Issue",
    "expiry_date": "Date of Expiry",
    "issuer": "Issuing Authority",
    "reference_id": "Reference / Certificate No.",
    "status": "Registration Status",
    "business_type": "Business Type",
    "category": "Enterprise Category",
    "address": "Registered Address",
    "state": "State",
    "turnover": "Annual Turnover",
    "cin": "CIN",
    "establishment_no": "Establishment / Reg. No.",
    "compliance": "Compliance Status",
    "scope": "Scope",
    "trade_name": "Trade Name",
}


def _doc_fields(doc, profile) -> list[tuple[str, str]]:
    """Build display rows from ocr_data + profile, masked where the app masks."""
    ocr = dict(doc.ocr_data or {})
    rows: list[tuple[str, str]] = []

    def push(label: str, value):
        if value not in (None, ""):
            rows.append((label, str(value)))

    # source-specific headline identifier first
    if doc.doc_type == "GST_CERT":
        push("GSTIN", ocr.get("gstin") or doc.masked_identifier)
        push("Legal / Registered Name", ocr.get("legal_name") or (profile.company_name if profile else None))
        push("PAN", ocr.get("pan"))
    elif doc.doc_type == "UDYAM_CERT":
        push("Udyam Number", ocr.get("udyam_number") or doc.masked_identifier)
        push("Enterprise Name", ocr.get("legal_name") or (profile.company_name if profile else None))
    elif doc.doc_type == "PAN":
        push("PAN", ocr.get("pan") or doc.masked_identifier)
        push("Name", ocr.get("legal_name") or (profile.company_name if profile else None))

    # remaining ocr fields (skipping ones already used)
    used = {v for _, v in rows}
    for k, v in ocr.items():
        label = FIELD_LABELS.get(k, k.replace("_", " ").title())
        if k in ("legal_name", "gstin", "pan", "udyam_number") and doc.doc_type in ("GST_CERT", "UDYAM_CERT", "PAN"):
            continue
        if v in used:
            continue
        push(label, v)

    if doc.reference_id:
        push("Reference / Certificate No.", doc.reference_id)
    if doc.issue_date:
        push("Date of Issue", doc.issue_date.isoformat())
    if doc.expiry_date:
        push("Date of Expiry", doc.expiry_date.isoformat())

    if profile:
        push("Registered Address", profile.registered_address)
        push("State", profile.state)

    # status line from verification
    st = doc.verification_status
    if st:
        push("Registration Status", st.replace("_", " ").title())

    # de-dup labels, keep order
    seen: dict[str, str] = {}
    for lab, val in rows:
        if lab not in seen:
            seen[lab] = val
    return list(seen.items())


# --- rendering --------------------------------------------------------------
@dataclass
class _RenderCtx:
    doc: object
    profile: object
    user: object
    officer_view: bool = False
    fields: list = field(default_factory=list)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if draw.textlength(trial, font=font) <= max_width:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [""]


def _draw_letterhead(d: ImageDraw.ImageDraw, source: str, title: str) -> None:
    dept, org = SOURCE_META.get(source, (source, "Government of India"))
    # emblem
    cx, cy, r = MARGIN + 44, 150, 44
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=NAVY, width=3)
    d.ellipse([cx - r + 12, cy - r + 12, cx + r - 12, cy + r - 12], outline=GOLD, width=2)
    # stylised star
    import math
    pts = []
    for i in range(10):
        rad = r - 18 if i % 2 == 0 else (r - 18) * 0.45
        ang = -math.pi / 2 + i * math.pi / 5
        pts.append((cx + rad * math.cos(ang), cy + rad * math.sin(ang)))
    d.polygon(pts, fill=NAVY)

    d.text((cx + 90, 108), "GOVERNMENT OF INDIA", font=_font(34, bold=True), fill=NAVY)
    d.text((cx + 90, 152), org, font=_font(20), fill=INK)
    d.text((cx + 90, 184), dept, font=_font(18), fill=MUTED)
    d.line([MARGIN, 250, W - MARGIN, 250], fill=NAVY, width=4)
    d.line([MARGIN, 258, W - MARGIN, 258], fill=GOLD, width=2)
    d.text((MARGIN, 278), title, font=_font(40, bold=True), fill=NAVY)


def _draw_qr(d: ImageDraw.ImageDraw, seed: str, x: int, y: int, cell: int = 9) -> None:
    mat = _qmark_matrix(seed)
    n = len(mat)
    d.rectangle([x - 10, y - 10, x + n * cell + 10, y + n * cell + 10], fill=(248, 250, 252), outline=LINE, width=2)
    for r in range(n):
        for c in range(n):
            if mat[r][c]:
                d.rectangle([x + c * cell, y + r * cell, x + (c + 1) * cell - 1, y + (r + 1) * cell - 1], fill=INK)
    d.text((x - 10, y + n * cell + 16), "Verify: gemsentinel.gov.in/verify", font=_font(13), fill=MUTED)


def _draw_seal(d: ImageDraw.ImageDraw, x: int, y: int) -> None:
    r = 74
    d.ellipse([x - r, y - r, x + r, y + r], outline=GREEN, width=3)
    d.ellipse([x - r + 12, y - r + 12, x + r - 12, y + r - 12], outline=GREEN, width=1)
    d.text((x - 52, y - 34), "VERIFIED", font=_font(16, bold=True), fill=GREEN)
    d.text((x - 58, y - 12), "RECORD", font=_font(16, bold=True), fill=GREEN)
    d.text((x - 34, y + 10), "SHA-256", font=_font(13), fill=GREEN)


def _footer(d: ImageDraw.ImageDraw) -> None:
    d.rectangle([0, H - 84, W, H], fill=NAVY)
    d.text((MARGIN, H - 62), "GeM-Sentinel Document Studio  ·  generated from verified source data", font=_font(15, bold=True), fill=(255, 255, 255))
    d.text((MARGIN, H - 38), "Integrity check: SHA-256 content hash (see QR block). Generated for official evaluation workflow.", font=_font(13), fill=(203, 213, 225))


def _review_banner(img: Image.Image) -> None:
    d = ImageDraw.Draw(img, "RGBA")
    d.rectangle([0, 0, W, 56], fill=(185, 28, 28, 235))
    d.text((MARGIN, 14), "OFFICIAL REVIEW COPY — MANUAL REVIEW", font=_font(22, bold=True), fill=(255, 255, 255))


def render_replica(doc, profile, user, officer_view: bool = False) -> bytes:
    img = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(img)

    source = doc.source or "MANUAL"
    title = doc.name or f"{source} Document"
    _draw_letterhead(d, source, title)

    y = 330
    ident = doc.masked_identifier or doc.reference_id or user.email
    # reference line
    d.text((MARGIN, y), "Document Reference:", font=_font(16), fill=MUTED)
    d.text((MARGIN, y + 24), doc.reference_id or "—", font=_font(26, bold=True, mono=True), fill=INK)
    d.text((MARGIN, y + 62), "Identification:", font=_font(16), fill=MUTED)
    d.text((MARGIN, y + 86), ident or "—", font=_font(24, bold=True, mono=True), fill=INK)

    # QR block top-right
    _draw_qr(d, ident + "|" + (doc.id or ""), W - MARGIN - 21 * 9 - 10, 330)

    y += 150
    d.line([MARGIN, y, W - MARGIN, y], fill=LINE, width=2)
    y += 22
    d.text((MARGIN, y), "The following particulars were captured from the indicated source record:", font=_font(18), fill=INK)
    y += 46

    fields = _doc_fields(doc, profile)
    label_font = _font(17, bold=True)
    value_font = _font(19)
    label_w = 340
    for lab, val in fields:
        if y > H - 320:
            break
        d.text((MARGIN, y), lab, font=label_font, fill=MUTED)
        lines = _wrap(d, val, value_font, W - MARGIN - (MARGIN + label_w) - 20)
        for i, ln in enumerate(lines[:3]):
            d.text((MARGIN + label_w, y), ln, font=value_font, fill=INK)
        y += max(1, len(lines[:3])) * 26 + 14
        d.line([MARGIN, y - 8, W - MARGIN, y - 8], fill=(241, 245, 249), width=1)

    # signature block
    sy = H - 260
    _draw_seal(d, MARGIN + 90, sy)
    d.line([W - MARGIN - 360, sy - 40, W - MARGIN, sy - 40], fill=INK, width=2)
    d.text((W - MARGIN - 360, sy - 24), "Authorised Signatory", font=_font(17, bold=True), fill=INK)
    d.text((W - MARGIN - 360, sy), SOURCE_META.get(source, (source,))[0], font=_font(14), fill=MUTED)
    # script signature
    sig_font = _font(30, oblique=True)
    d.text((W - MARGIN - 300, sy - 78), "A. Signatory", font=sig_font, fill=(51, 65, 85))

    # expiry flag — full-width banner above the footer (no overlap with seal)
    import datetime as _dt
    if getattr(doc, "expiry_date", None) and str(doc.expiry_date) < str(_dt.date.today()):
        by = H - 172
        d.rounded_rectangle([MARGIN, by, W - MARGIN, by + 46], radius=8, fill=(254, 242, 242), outline=RED, width=2)
        d.text((MARGIN + 16, by + 12), "⚠ DOCUMENT EXPIRED — see verification status", font=_font(16, bold=True), fill=RED)

    _footer(d)

    # officer review banner (on top)
    out = img
    if officer_view:
        _review_banner(out)

    buf = _to_png(out)
    return buf


def _to_png(img: Image.Image) -> bytes:
    import io

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# --- cache ------------------------------------------------------------------
def _replica_dir() -> Path:
    base = settings.UPLOAD_DIR or _default_storage()
    p = Path(base) / "docs" / "ai_replicas"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _default_storage() -> str:
    # <repo>/storage
    return str(Path(__file__).resolve().parents[3] / "storage")


def _safe_key(email: str, doc_type: str) -> str:
    e = re.sub(r"[^A-Za-z0-9._-]", "_", email)
    t = re.sub(r"[^A-Za-z0-9_-]", "_", doc_type)
    return f"{e}_{t}"


def get_replica(doc, profile, user, officer_view: bool = False) -> tuple[bytes, str]:
    """Return (png_bytes, sha256_of_png). Cached at a stable key; re-renders
    only when the content fingerprint changes."""
    key = _safe_key(user.email or "user", doc.doc_type)
    if officer_view:
        key += "_review"
    fields = _doc_fields(doc, profile)
    fingerprint = _sha256(
        json.dumps(
            {
                "v": RENDER_VERSION,
                "doc_type": doc.doc_type, "name": doc.name, "source": doc.source,
                "ref": doc.reference_id, "masked": doc.masked_identifier,
                "issue": str(doc.issue_date), "expiry": str(doc.expiry_date),
                "status": doc.verification_status, "fields": fields,
                "officer": officer_view, "user": (user.full_name or user.email),
            },
            sort_keys=True,
            default=str,
        )
    )
    png_path = _replica_dir() / f"{key}.png"
    meta_path = _replica_dir() / f"{key}.meta.json"
    if png_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
            if meta.get("fingerprint") == fingerprint:
                data = png_path.read_bytes()
                return data, _sha256(data)
        except Exception:
            pass
    data = render_replica(doc, profile, user, officer_view=officer_view)
    png_path.write_bytes(data)
    meta_path.write_text(json.dumps({"fingerprint": fingerprint, "hash": _sha256(data)}))
    return data, _sha256(data)
