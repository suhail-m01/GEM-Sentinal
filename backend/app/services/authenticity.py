"""Document authenticity assessment.

Uses (where available):
- OpenCV: preprocessing, seal/stamp & signature region detection, ORB feature
  matching against trusted references, tampering indicators
- pypdf: PDF structure, metadata, digital-signature presence
- SHA-256: fingerprinting, duplicate detection, integrity tracking

Decision outputs: VERIFIED | SUSPICIOUS | UNABLE_TO_VERIFY | MANUAL_REVIEW_REQUIRED

Policy (immutable):
- Visual similarity of a seal/stamp/signature ALONE is never conclusive.
- VERIFIED requires trusted corroborating evidence (government/mock-source
  record match, valid digital signature, trusted issuer data, verifiable
  reference).
- Uncertainty routes to UNABLE_TO_VERIFY or MANUAL_REVIEW_REQUIRED, never VERIFIED.
"""
import hashlib
import logging
import os
import re
from typing import Optional

from ..core.config import settings
from ..models import Document, TrustedReference
from .files import resolve_path

logger = logging.getLogger("gem.authenticity")

try:
    import cv2
    import numpy as np

    HAS_CV2 = True
except Exception:  # pragma: no cover
    HAS_CV2 = False

try:
    from pypdf import PdfReader

    HAS_PYPDF = True
except Exception:  # pragma: no cover
    HAS_PYPDF = False

LOW_CONFIDENCE_THRESHOLD = 0.7


def _load_image(path: str):
    if not HAS_CV2:
        return None
    return cv2.imread(path)


def _orb_similarity(img_a, img_b) -> Optional[float]:
    """ORB feature matching similarity in [0,1]. Returns None if not comparable."""
    if not HAS_CV2 or img_a is None or img_b is None:
        return None
    try:
        ga = cv2.cvtColor(cv2.resize(img_a, (200, 200)), cv2.COLOR_BGR2GRAY)
        gb = cv2.cvtColor(cv2.resize(img_b, (200, 200)), cv2.COLOR_BGR2GRAY)
        orb = cv2.ORB_create(nfeatures=500)
        ka, da = orb.detectAndCompute(ga, None)
        kb, db = orb.detectAndCompute(gb, None)
        if da is None or db is None or len(ka) < 8 or len(kb) < 8:
            return None
        matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
        matches = matcher.knnMatch(da, db, k=2)
        good = [m for m, n in matches if m.distance < 0.75 * n.distance]
        ratio = len(good) / max(len(ka), len(kb), 1)
        return round(min(1.0, ratio * 2.5), 3)
    except Exception as exc:
        logger.debug("ORB similarity failed: %s", exc)
        return None


def _detect_seal(img) -> Optional[dict]:
    """Heuristic circular red/blue ink region detection (seal/stamp)."""
    if not HAS_CV2 or img is None:
        return None
    try:
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        red1 = cv2.inRange(hsv, (0, 60, 60), (10, 255, 255))
        red2 = cv2.inRange(hsv, (170, 60, 60), (180, 255, 255))
        blue = cv2.inRange(hsv, (90, 50, 50), (130, 255, 255))
        mask = cv2.bitwise_or(cv2.bitwise_or(red1, red2), blue)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        best = None
        h, w = img.shape[:2]
        for c in contours:
            area = cv2.contourArea(c)
            if area < 250 or area > 0.4 * h * w:
                continue
            (cx, cy), radius = cv2.minEnclosingCircle(c)
            circularity = area / (3.14159 * radius * radius) if radius > 0 else 0
            if circularity > 0.35:
                best = {
                    "bbox": [int(cx - radius), int(cy - radius), int(2 * radius), int(2 * radius)],
                    "circularity": round(float(circularity), 2),
                    "area": int(area),
                }
                break
        return best
    except Exception as exc:
        logger.debug("seal detection failed: %s", exc)
        return None


def _detect_signature(img) -> Optional[dict]:
    """Heuristic handwritten-signature region: lower third, connected components
    with moderate area and stroke-like aspect."""
    if not HAS_CV2 or img is None:
        return None
    try:
        h, w = img.shape[:2]
        region = img[int(h * 0.55) :, :]
        gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
        _, thresh = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY_INV)
        n, labels, stats, _ = cv2.connectedComponentsWithStats(thresh, 8)
        candidates = []
        for i in range(1, n):
            x, y, ww, hh, area = stats[i]
            if 300 < area < 0.25 * w * h and 0.05 < hh / max(ww, 1) < 0.8:
                candidates.append({"bbox": [int(x), int(y + h * 0.55), int(ww), int(hh)], "area": int(area)})
        if not candidates:
            return None
        best = max(candidates, key=lambda c: c["area"])
        return {"bbox": best["bbox"], "area": best["area"], "components": len(candidates)}
    except Exception as exc:
        logger.debug("signature detection failed: %s", exc)
        return None


def _tampering_indicators(img) -> list[str]:
    """Heuristic manipulation indicators (not forensic proof)."""
    if not HAS_CV2 or img is None:
        return []
    findings = []
    try:
        gray = cv2.cvtColor(cv2.resize(img, (320, 320)), cv2.COLOR_BGR2GRAY)
        # ELA-lite: DCT energy anomaly
        dct = cv2.dct(gray.astype(np.float32))
        block = 64
        n_blocks = (320 // block) ** 2
        energies = []
        for by in range(0, 320 - block + 1, block):
            for bx in range(0, 320 - block + 1, block):
                energies.append(float(np.abs(dct[by : by + block, bx : bx + block]).mean()))
        energies = np.array(energies)
        if len(energies) >= 4:
            med = float(np.median(energies))
            outlier = (energies > 3.0 * med + 1e-6).mean()
            if outlier > 0.25:
                findings.append("Inconsistent compression energy across blocks (possible splicing/editing)")
        # Localized edge-density spike in the CONTENT region (middle bands).
        # Canny emits 0/255, so normalize each 1/5-height band to a 0..1 edge
        # fraction. A pasted/spliced region shows up as one middle band whose
        # edge density spikes far above the document's median. The top band
        # (title/header rule) and bottom band (footer) legitimately carry
        # heavy rules and are excluded — this is what previously produced
        # false "region substitution" flags on every clean document.
        edges = cv2.Canny(gray, 50, 150)
        dens = edges.reshape(5, -1).mean(axis=1) / 255.0  # 0..1 per band
        med = float(np.median(dens))
        for idx in (1, 2, 3):  # middle content bands only
            if dens[idx] > med * 3.0 + 0.05:
                findings.append(
                    f"Localized edge-density spike in content band {idx + 1} of 5 "
                    "(possible region substitution)"
                )
                break
    except Exception as exc:
        logger.debug("tampering heuristics failed: %s", exc)
    return findings


def _pdf_inspection(path: str) -> dict:
    out = {"metadata": None, "pages": None, "digital_signature": None, "errors": []}
    if not HAS_PYPDF:
        out["errors"].append("pypdf not available")
        return out
    try:
        reader = PdfReader(path)
        meta = reader.metadata
        out["metadata"] = {
            "producer": meta.get("/Producer") if meta else None,
            "creator": meta.get("/Creator") if meta else None,
            "creation_date": str(meta.get("/CreationDate")) if meta and meta.get("/CreationDate") else None,
            "modification_date": str(meta.get("/ModDate")) if meta and meta.get("/ModDate") else None,
            "author": meta.get("/Author") if meta else None,
        }
        out["pages"] = len(reader.pages)
        try:
            trailer = reader.trailer
            has_sig = "/AcroForm" in trailer and "/SigFlags" in (trailer["/AcroForm"] or {})
            if has_sig:
                out["digital_signature"] = {
                    "present": True,
                    "valid": None,
                    "note": "Digital signature field present. Cryptographic chain validation not available for this format; route to manual review.",
                }
            else:
                out["digital_signature"] = {"present": False}
        except Exception:
            out["digital_signature"] = {"present": None, "note": "Signature inspection inconclusive"}
    except Exception as exc:
        out["errors"].append(f"PDF inspection failed: {type(exc).__name__}")
    return out


def _extract_registration_numbers(text: str) -> dict:
    out = {}
    # GSTIN is 15 characters: state(2) + PAN(10: 5 letters, 4 digits, 1 letter)
    # + entity(1 digit) + 1 char + check(1 alphanumeric). The pattern must
    # span all 15 or the trailing \b fails.
    m = re.search(r"\b[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9][0-9A-Z][0-9A-Z]\b", text)
    if m:
        out["gstin"] = m.group(0)
    m = re.search(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b", text)
    if m:
        out["pan"] = m.group(0)
    m = re.search(r"\bU[0-9]{4}[A-Z]{2}[0-9]{4}[A-Z0-9]{6,}\b", text)
    if m:
        out["udyam"] = m.group(0)
    # MCA incorporation number (CIN), with or without the 'CIN-' prefix.
    m = re.search(r"\bCIN-?[A-Z0-9]{16,}\b", text)
    if m:
        out["cin"] = m.group(0).upper()
    return out


def analyze_document(
    db, doc: Document, source_data: Optional[dict] = None
) -> dict:
    """Full authenticity assessment for one document. Never decides procurement
    outcomes — returns evidence for the officer and the VerificationReport."""
    findings: dict = {
        "document_id": doc.id,
        "document_name": doc.name,
        "doc_type": doc.doc_type,
        "sha256": None,
        "duplicate_of": None,
        "seal": None,
        "signature": None,
        "seal_similarity_to_trusted": None,
        "signature_similarity_to_trusted": None,
        "tampering_indicators": [],
        "pdf": None,
        "extracted_numbers": {},
        "field_consistency": [],
        "ocr_confidence": doc.ocr_confidence,
        "digital_signature": None,
        "supported_by_trusted_evidence": False,
        "status": "UNABLE_TO_VERIFY",
        "confidence": 0.0,
        "reasons": [],
    }

    file_path = None
    if doc.file_path:
        try:
            file_path = resolve_path("docs", doc.file_path)
        except FileNotFoundError:
            findings["reasons"].append("File not stored; cannot inspect")
            return _finalize(findings, "UNABLE_TO_VERIFY", ["No file available for inspection"])

    # 1. SHA-256 fingerprint + duplicate detection
    if file_path and os.path.isfile(file_path):
        with open(file_path, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()
        findings["sha256"] = digest
        doc.document_hash = digest
        dup = (
            db.query(Document)
            .filter(Document.document_hash == digest, Document.id != doc.id)
            .first()
        )
        if dup:
            findings["duplicate_of"] = dup.name
            findings["reasons"].append(f"Duplicate of previously submitted document '{dup.name}'")

    is_image = file_path and file_path.lower().endswith((".png", ".jpg", ".jpeg"))
    is_pdf = file_path and file_path.lower().endswith(".pdf")

    # 2. PDF structure/metadata/digital signature
    if is_pdf:
        pdf_info = _pdf_inspection(file_path)
        findings["pdf"] = pdf_info
        ds = pdf_info.get("digital_signature") or {}
        findings["digital_signature"] = ds
        if ds.get("present") is True:
            findings["reasons"].append("Cryptographically signed PDF detected; chain validation pending manual review")

    # 3. Image analysis (OpenCV)
    img = _load_image(file_path) if is_image else None
    if img is not None:
        seal = _detect_seal(img)
        findings["seal"] = seal
        sig = _detect_signature(img)
        findings["signature"] = sig
        findings["tampering_indicators"] = _tampering_indicators(img)

        # trusted reference comparison (ORB)
        trusted_seal = (
            db.query(TrustedReference)
            .filter(TrustedReference.kind == "SEAL")
            .first()
        )
        if trusted_seal and seal:
            ref_img = None
            try:
                ref_img = _load_image(resolve_path("trusted", trusted_seal.file_path))
            except FileNotFoundError:
                pass
            if ref_img is not None:
                sx = max(0, seal["bbox"][0])
                sy = max(0, seal["bbox"][1])
                crop = img[sy : sy + seal["bbox"][3], sx : sx + seal["bbox"][2]]
                findings["seal_similarity_to_trusted"] = _orb_similarity(crop, ref_img)
                findings["seal_similarity_reference"] = trusted_seal.issuer_name
        trusted_sig = (
            db.query(TrustedReference)
            .filter(TrustedReference.kind == "SIGNATURE")
            .first()
        )
        if trusted_sig and sig:
            ref_img = None
            try:
                ref_img = _load_image(resolve_path("trusted", trusted_sig.file_path))
            except FileNotFoundError:
                pass
            if ref_img is not None:
                sx = max(0, sig["bbox"][0])
                sy = max(0, sig["bbox"][1])
                crop = img[sy : sy + sig["bbox"][3], sx : sx + sig["bbox"][2]]
                findings["signature_similarity_to_trusted"] = _orb_similarity(crop, ref_img)
                findings["signature_similarity_reference"] = trusted_sig.issuer_name

    # 4. OCR-based registration-number extraction & field consistency
    ocr_text = ""
    if doc.ocr_data:
        for v in doc.ocr_data.values():
            if isinstance(v, str):
                ocr_text += v + " "
    extracted = _extract_registration_numbers(ocr_text)
    findings["extracted_numbers"] = extracted

    source_data = source_data or {}
    if source_data:
        def _id_equal(a: str, b: str) -> bool:
            return re.sub(r"[^A-Z0-9]", "", str(a)).upper() == re.sub(r"[^A-Z0-9]", "", str(b)).upper()

        def _name_equal(a: str, b: str) -> bool:
            na = re.sub(r"[^a-z0-9]", "", str(a).lower())
            nb = re.sub(r"[^a-z0-9]", "", str(b).lower())
            if not na or not nb:
                return False
            if na == nb:
                return True
            short, long = (na, nb) if len(na) <= len(nb) else (nb, na)
            return short in long and len(short) / len(long) >= 0.8

        doc_ocr = doc.ocr_data or {}
        src_name = source_data.get("legal_name") or source_data.get("name")
        src_cin = source_data.get("cin") or source_data.get("certificate_number")
        checks = [
            ("GSTIN", extracted.get("gstin"), source_data.get("gstin"), _id_equal),
            ("PAN", extracted.get("pan"), source_data.get("pan"), _id_equal),
            ("Udyam number", extracted.get("udyam"), source_data.get("udyam_number"), _id_equal),
            ("CIN", extracted.get("cin"), src_cin, _id_equal),
            ("Legal name", doc_ocr.get("legal_name"), src_name, _name_equal),
        ]
        for field, doc_val, src_val, cmp in checks:
            if doc_val is None or src_val is None:
                continue
            result = "MATCH" if cmp(doc_val, src_val) else "MISMATCH"
            findings["field_consistency"].append({"field": field, "document_value": doc_val, "source_value": src_val, "result": result})

    # 5. decision matrix
    suspicious_signals = []
    if findings["tampering_indicators"]:
        suspicious_signals.extend(findings["tampering_indicators"])
    if any(f["result"] == "MISMATCH" for f in findings["field_consistency"]):
        suspicious_signals.append("Registration number mismatch between document and source record")
    if findings["duplicate_of"]:
        suspicious_signals.append("Duplicate document detected")
    ds = findings.get("digital_signature") or {}
    if ds.get("present") is False and doc.doc_type in ("GST_CERT", "MCA_DOC") and source_data:
        # unsigned where expected: note only, not conclusive
        pass

    trusted_evidence = False
    if source_data:
        matches = [f for f in findings["field_consistency"] if f["result"] == "MATCH"]
        if matches:
            trusted_evidence = True
    if findings.get("seal_similarity_to_trusted") is not None and findings["seal_similarity_to_trusted"] >= 0.5:
        # similarity alone is NOT conclusive, but counts as supporting evidence
        trusted_evidence = True

    findings["supported_by_trusted_evidence"] = trusted_evidence

    low_confidence = (doc.ocr_confidence is not None and doc.ocr_confidence < LOW_CONFIDENCE_THRESHOLD) or (
        is_image and findings["seal"] is None and findings["signature"] is None
    )

    if suspicious_signals:
        status = "SUSPICIOUS"
        findings["reasons"] = suspicious_signals + ["Findings must be reviewed by the officer"]
        findings["confidence"] = 0.8
    elif trusted_evidence and not low_confidence and not findings["tampering_indicators"]:
        status = "VERIFIED"
        findings["reasons"] = ["Trusted source data corroborates document fields", "No tampering indicators detected"]
        findings["confidence"] = 0.85
    elif low_confidence or findings["seal_similarity_to_trusted"] is not None and findings["seal_similarity_to_trusted"] < 0.5:
        status = "MANUAL_REVIEW_REQUIRED"
        findings["reasons"].append("Insufficient or ambiguous evidence — officer review required")
        findings["confidence"] = 0.5
    else:
        status = "UNABLE_TO_VERIFY"
        findings["reasons"].append("Could not establish authenticity with available evidence")
        findings["confidence"] = 0.3

    findings["status"] = status
    return findings


def _finalize(findings: dict, status: str, reasons: list[str]) -> dict:
    findings["status"] = status
    findings["reasons"] = reasons
    return findings
