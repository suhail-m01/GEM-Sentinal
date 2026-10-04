"""Deterministic rule engine.

The rule engine decides. AI only explains.

Each rule is evaluated against an evidence context built from:
- bidder profile data
- bidder documents (vault)
- mock/government source responses (fetched live, consent-gated)

Every rule yields one of: PASS, FAIL, UNABLE_TO_VERIFY (or N/A when disabled).
Unavailable sources never become PASS.
"""
import re
from datetime import date, datetime
from typing import Optional

from sqlalchemy.orm import Session

from .models import BidderProfile, BlacklistEntry, ConsentRecord, Document, Tender, User
from .scoring import (
    CONDITIONAL,
    ELIGIBLE,
    NOT_ELIGIBLE,
    determine_eligibility,
    determine_risk,
    compute_score,
    STRENGTH_RANK,
    compute_coverage,
    strength_distribution,
    determinism_hash,
)

PASS = "PASS"
FAIL = "FAIL"
UNABLE = "UNABLE_TO_VERIFY"
NA = "N/A"


# ---------------- normalization helpers ----------------

def norm_name(value: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def norm_id(value: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").upper().lower())


def names_match(a: Optional[str], b: Optional[str]) -> bool:
    """Lenient matching: exact, or containment where the shorter name covers
    at least 80% of the longer (allows 'works'/'pvt ltd' style suffixes) but
    catches genuine mismatches like 'Sita Engineering' vs 'Sita Engineering Works'."""
    na, nb = norm_name(a), norm_name(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    short, long = (na, nb) if len(na) <= len(nb) else (nb, na)
    if short in long:
        return len(short) / len(long) >= 0.8
    return False


# Legal-form tokens ignored when comparing entity names across documents
# (GST certificate vs Udyam vs profile commonly differ only in these).
_LEGAL_FORM_TOKENS = {
    "pvt", "private", "ltd", "limited", "llp", "partnership", "co", "corp",
    "corporation", "india", "of", "the",
}


def names_consistent_across_docs(a: Optional[str], b: Optional[str]) -> bool:
    """Cross-document entity-name consistency: tolerant of legal-form suffix
    differences ('Ramesh Traders' ≡ 'Ramesh Traders Pvt Ltd'), strict on the
    core business name."""
    def core(v: str) -> str:
        tokens = re.findall(r"[a-z0-9]+", (v or "").lower())
        core_tokens = [t for t in tokens if t not in _LEGAL_FORM_TOKENS]
        return " ".join(core_tokens or tokens)

    ca, cb = core(a or ""), core(b or "")
    if not ca or not cb:
        return False
    if ca == cb:
        return True
    short, long = (ca, cb) if len(ca) <= len(cb) else (cb, ca)
    if short in long:
        return len(short) / len(long) >= 0.6
    return False


def addresses_match(a: Optional[str], b: Optional[str]) -> bool:
    """Address comparison tolerant of house-number/pin-code additions.

    'Industrial Layout, Belagavi, Karnataka' matches
    '12, Industrial Layout, Belagavi, Karnataka 590016' — but
    'Ring Road' vs 'MIDC Phase II' does not. Comparison is on the
    alpha-only core; the shorter core must cover ≥ 60% of the longer.
    """
    ca = re.sub(r"[^a-z]", "", (a or "").lower())
    cb = re.sub(r"[^a-z]", "", (b or "").lower())
    if not ca or not cb:
        return False
    if ca == cb:
        return True
    short, long = (ca, cb) if len(ca) <= len(cb) else (cb, ca)
    if short in long:
        return len(short) / len(long) >= 0.6
    return False


# ---------------- Indian identifier validation (deterministic) --------------

MOD36_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

GST_STATE_CODES = {
    "01": "Jammu & Kashmir", "02": "Himachal Pradesh", "03": "Punjab",
    "04": "Chandigarh", "05": "Uttarakhand", "06": "Haryana", "07": "Delhi",
    "08": "Rajasthan", "09": "Uttar Pradesh", "10": "Bihar", "11": "Sikkim",
    "12": "Arunachal Pradesh", "13": "Nagaland", "14": "Manipur",
    "15": "Mizoram", "16": "Tripura", "17": "Meghalaya", "18": "Assam",
    "19": "West Bengal", "20": "Jharkhand", "21": "Odisha",
    "22": "Chhattisgarh", "23": "Madhya Pradesh", "24": "Gujarat",
    "27": "Maharashtra", "29": "Karnataka", "30": "Goa", "32": "Kerala",
    "33": "Tamil Nadu", "36": "Telangana", "37": "Andhra Pradesh",
}


def validate_gstin_checksum(gstin: Optional[str]) -> tuple[bool, str]:
    """Strict official Luhn Mod-36 GSTIN checksum (15 chars, last char Z).

    Deliberately strict: a checksum mismatch is a FAIL, not a pass-with-note.
    """
    if not gstin:
        return False, "no GSTIN"
    g = str(gstin).strip().upper()
    if len(g) != 15:
        return False, f"expected 15 characters, got {len(g)}"
    if not re.fullmatch(r"[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]", g):
        return False, "does not match GSTIN structure (state+PAN+entity+Z+check)"
    if g[:2] not in GST_STATE_CODES:
        return False, f"invalid state code '{g[:2]}'"
    total = 0
    factor = 1
    for i in range(14):
        code_point = MOD36_CHARS.index(g[i])
        digit = factor * code_point
        factor = 2 if factor == 1 else 1
        total += (digit // 36) + (digit % 36)
    expected = MOD36_CHARS[(36 - (total % 36)) % 36]
    if g[14] == expected:
        return True, "valid GSTIN structure and mod-36 checksum"
    return False, f"checksum mismatch (expected trailing check character {expected})"


# ---------------- evidence context ----------------

SOURCE_DOC_TYPE = {
    "UDYAM": "UDYAM_CERT",
    "GST": "GST_CERT",
    "PAN": "PAN",
    "EPFO": "EPFO_DOC",
    "ESIC": "ESIC_DOC",
    "MCA21": "MCA_DOC",
    "STARTUP_INDIA": "STARTUP_CERT",
    "NSIC": "NSIC_CERT",
    "BIS": "BIS_CERT",
}

IDENTIFIER_FOR_SOURCE = {
    "UDYAM": "udyam",
    "GST": "gstin",
    "PAN": "pan",
    "DIGILOCKER": "pan",
    "MCA21": "pan",
    "EPFO": "pan",
    "ESIC": "pan",
    "STARTUP_INDIA": "pan",
    "NSIC": "pan",
    "BIS": "pan",
    "BLACKLIST": "pan",
    "LOCAL_CONTENT": "pan",
}


def _get_profile(db: Session, user: User) -> Optional[BidderProfile]:
    return db.query(BidderProfile).filter(BidderProfile.user_id == user.id).first()


def _consents(db: Session, user_id: str) -> set[str]:
    rows = (
        db.query(ConsentRecord)
        .filter(ConsentRecord.user_id == user_id, ConsentRecord.status == "GRANTED")
        .all()
    )
    cats = set()
    for r in rows:
        cats.add(r.source_category.upper())
    return cats


def _has_consent(consents: set[str], source: str) -> bool:
    return "ALL" in consents or source.upper() in consents


def build_evidence_context(
    db: Session,
    user: User,
    tender: Optional[Tender],
    fetch_sources: bool = True,
) -> dict:
    from .services.portal import fetch_source  # local import to avoid cycle

    profile = _get_profile(db, user)
    consents = _consents(db, user.id)
    documents = (
        db.query(Document).filter(Document.user_id == user.id).all() if user.role == "BIDDER" else []
    )

    identifiers = {
        "pan": profile.pan_plain if (profile and profile.pan_plain) else None,
        "gstin": profile.gstin_plain if (profile and profile.gstin_plain) else None,
        "udyam": profile.udyam_plain if (profile and profile.udyam_plain) else None,
        "company_name": profile.company_name if profile else None,
    }

    needed = set()
    if tender is not None:
        rules = [r for r in tender.rules if r.enabled]
        needed.update(r.source.upper() for r in rules)
    # Sources that corroborate the bidder's vault documents (authenticity
    # assessment compares document fields against the issuing record — e.g.
    # an MCA incorporation certificate vs the MCA21 registry) must be
    # fetched too, even when no rule references the source directly.
    needed.update(
        {(d.source or "").upper() for d in documents if (d.source or "").upper() in SOURCE_DOC_TYPE}
    )
    if fetch_sources:
        needed.add("BLACKLIST")

    source_outcomes: dict[str, dict] = {}
    for source in sorted(needed):
        if source in ("DOCUMENT", "PROFILE"):
            continue
        if not fetch_sources:
            source_outcomes[source] = {"status": "NOT_FETCHED", "message": "pre-check: sources not fetched"}
            continue
        if source != "BLACKLIST" and not _has_consent(consents, source):
            source_outcomes[source] = {
                "status": "CONSENT_REQUIRED",
                "message": "Bidder consent not granted for this source category",
                "retrieved_at": None,
                "data": {},
            }
            continue
        identifier = identifiers.get(IDENTIFIER_FOR_SOURCE.get(source, "pan"), None)
        extra = {}
        if source == "BLACKLIST":
            extra = {
                "gstin": identifiers.get("gstin"),
                "company_name": identifiers.get("company_name"),
            }
        res = fetch_source(db, source, identifier=identifier, extra=extra or None)
        source_outcomes[source] = {
            "status": res.status,
            "message": res.message,
            "data": res.data,
            "retrieved_at": res.retrieved_at.isoformat() if res.retrieved_at else None,
            "latency_ms": res.latency_ms,
        }

    # local blacklist reference DB (admin-maintained, authoritative reference)
    local_blacklist = _local_blacklist_hits(db, identifiers)

    return {
        "profile": profile,
        "consents": consents,
        "documents": documents,
        "sources": source_outcomes,
        "identifiers": identifiers,
        "local_blacklist": local_blacklist,
        "today": date.today(),
    }


def _local_blacklist_hits(db: Session, identifiers: dict) -> list[dict]:
    today = date.today()
    hits = []
    q = db.query(BlacklistEntry).filter(BlacklistEntry.status == "ACTIVE")
    entries = q.all()
    for e in entries:
        if e.valid_until and e.valid_until < today:
            e.status = "EXPIRED"
            continue
        reasons = []
        if identifiers.get("pan") and e.pan and norm_id(e.pan) == norm_id(identifiers["pan"]):
            reasons.append("PAN match")
        if identifiers.get("gstin") and e.gstin and norm_id(e.gstin) == norm_id(identifiers["gstin"]):
            reasons.append("GSTIN match")
        if identifiers.get("company_name") and names_match(e.entity_name, identifiers["company_name"]):
            reasons.append("Entity name match")
        if reasons:
            hits.append(
                {
                    "entry_id": e.id,
                    "entity_name": e.entity_name,
                    "matched_on": ", ".join(reasons),
                    "reason": e.reason,
                    "source_authority": e.source_authority,
                    "official_order_reference": e.official_order_reference,
                    "valid_from": e.valid_from.isoformat() if e.valid_from else None,
                    "valid_until": e.valid_until.isoformat() if e.valid_until else None,
                }
            )
    return hits


# ---------------- rule evaluation ----------------

def _doc_for_type(documents: list[Document], doc_type: str) -> Optional[Document]:
    matches = [d for d in documents if d.doc_type == doc_type]
    return max(matches, key=lambda d: d.version) if matches else None


def _source_outcome(ctx: dict, source: str) -> dict:
    return ctx["sources"].get(source.upper(), {"status": "NOT_FETCHED", "message": "source not fetched", "data": {}})


def _eval_condition(rule, ctx: dict) -> tuple[str, str, Optional[dict]]:
    """Returns (result, message, evidence)."""
    profile = ctx["profile"]
    docs = ctx["documents"]
    today: date = ctx["today"]

    def src(source: str) -> dict:
        return _source_outcome(ctx, source)

    ct = rule.condition_type
    cv = (rule.condition_value or "").strip()

    if ct == "is_active":
        out = src(rule.source)
        st = out.get("status")
        if st == "CONSENT_REQUIRED":
            return UNABLE, "Consent not granted; source not consulted (never treated as PASS)", {"source": rule.source, "status": st}
        if st == "UNAVAILABLE":
            return UNABLE, f"Source unavailable: {out.get('message')}", {"source": rule.source, "status": st, "message": out.get("message")}
        data = out.get("data", {})
        if st == "NOT_FOUND":
            return FAIL, "No active registration found at source", {"source": rule.source, "status": st}
        if st == "EXPIRED":
            return FAIL, f"Registration expired per source: {out.get('message')}", {"source": rule.source, "status": st}
        if st == "VALID":
            status_field = str(data.get("status", "ACTIVE")).upper()
            if status_field in ("ACTIVE", "VALID", "OK", "CURRENT"):
                return PASS, "Active at source", {"source": rule.source, "status": status_field, "reference_id": data.get("reference_id")}
            return FAIL, f"Source status not active: {status_field}", {"source": rule.source, "status": status_field}
        return UNABLE, f"Source returned {st}", {"source": rule.source, "status": st}

    if ct == "valid_unexpired":
        out = src(rule.source)
        st = out.get("status")
        if st == "CONSENT_REQUIRED":
            return UNABLE, "Consent not granted; source not consulted", {"source": rule.source, "status": st}
        if st in ("UNAVAILABLE", "ERROR", "NOT_FETCHED"):
            return UNABLE, f"Source unavailable: {out.get('message')}", {"source": rule.source, "status": st}
        data = out.get("data", {})
        expiry = data.get("expiry_date")
        if st == "EXPIRED" or (expiry and date.fromisoformat(str(expiry)[:10]) < today):
            return FAIL, f"Document/registration expired on {str(expiry)[:10]}", {"source": rule.source, "expiry_date": str(expiry)[:10]}
        return PASS, "Valid and unexpired", {"source": rule.source, "expiry_date": expiry}

    if ct == "min_turnover":
        if profile is None or profile.annual_turnover is None:
            return UNABLE, "Turnover not provided in bidder profile", {"source": "PROFILE"}
        threshold = int(cv or 0)
        if profile.annual_turnover >= threshold:
            return PASS, f"Turnover ₹{profile.annual_turnover:,} ≥ ₹{threshold:,}", {"source": "PROFILE", "turnover": profile.annual_turnover, "threshold": threshold}
        return FAIL, f"Turnover ₹{profile.annual_turnover:,} < required ₹{threshold:,}", {"source": "PROFILE", "turnover": profile.annual_turnover, "threshold": threshold}

    if ct in ("has_document", "has_authorization"):
        doc_type = cv or ("OEM_AUTHORIZATION" if ct == "has_authorization" else None)
        doc = _doc_for_type(docs, doc_type)
        if doc is None:
            return FAIL, f"Required document not found in Document Vault: {doc_type}", {"source": "DOCUMENT", "doc_type": doc_type}
        st = doc.verification_status
        if st == "VERIFIED":
            return PASS, f"Document present and verified from source ({doc.name})", {"source": "DOCUMENT", "doc_type": doc_type, "document_id": doc.id, "verification_status": st}
        if st in ("EXPIRED", "MISMATCH"):
            return FAIL, f"Document {doc.name} is {st.lower()}", {"source": "DOCUMENT", "doc_type": doc_type, "document_id": doc.id, "verification_status": st}
        if st == "MANUAL_UPLOADED":
            return PASS, f"Manually uploaded document present ({doc.name}) — subject to officer review", {"source": "DOCUMENT", "doc_type": doc_type, "document_id": doc.id, "verification_status": st}
        return UNABLE, f"Document {doc.name} is {st.lower()}", {"source": "DOCUMENT", "doc_type": doc_type, "document_id": doc.id, "verification_status": st}

    if ct == "not_blacklisted":
        local_hits = ctx["local_blacklist"]
        out = src("BLACKLIST")
        if local_hits:
            return FAIL, f"ACTIVE debarment record in Reference Blacklist Database ({local_hits[0]['matched_on']})", {"source": "BLACKLIST", "local_hits": local_hits}
        st = out.get("status")
        if st == "BLACKLIST":
            return FAIL, f"Blacklist hit at source: {out.get('message')}", {"source": "BLACKLIST", "status": st, "data": out.get("data")}
        if st in ("UNAVAILABLE", "ERROR", "NOT_FETCHED", "CONSENT_REQUIRED"):
            return UNABLE, f"Blacklist source unavailable; only local reference DB checked (no hit): {out.get('message')}", {"source": "BLACKLIST", "status": st}
        if st == "NOT_FOUND":
            return PASS, "No debarment record at source or in reference DB", {"source": "BLACKLIST", "status": st}
        if st == "VALID":
            return PASS, "No debarment record at source or in reference DB", {"source": "BLACKLIST", "status": st}
        return UNABLE, f"Blacklist source returned {st}", {"source": "BLACKLIST", "status": st}

    if ct == "name_match":
        # format: "profile.company_name:UDYAM.legal_name"
        parts = cv.split(":")
        if len(parts) != 2:
            return UNABLE, "Malformed name_match condition", {"condition": cv}
        left_field, right = parts[0].split(".")[1], parts[1]
        src_name, field = right.split(".")
        left_value = getattr(profile, left_field, None) if profile else None
        if left_value is not None and left_field.endswith("_encrypted"):
            from .core.security import decrypt_field
            try:
                left_value = decrypt_field(left_value)
            except Exception:
                left_value = None
        out = src(src_name)
        data = out.get("data", {})
        right_value = data.get(field)
        st = out.get("status")
        if st in ("UNAVAILABLE", "ERROR", "NOT_FETCHED", "CONSENT_REQUIRED", "NOT_FOUND") or left_value is None or right_value is None:
            return UNABLE, f"Could not compare names: left={'missing' if left_value is None else left_value}, right={'missing' if right_value is None else str(right_value)[:60]}", {"source": src_name, "field": field, "status": st}
        if names_match(left_value, right_value):
            return PASS, "Names match across profile and source", {"source": src_name, "field": field, "bidder_value": _mask(left_value), "source_value": _mask(right_value)}
        return FAIL, f"Name mismatch: profile '{_mask(left_value)}' vs source '{_mask(right_value)}'", {"source": src_name, "field": field, "bidder_value": _mask(left_value), "source_value": _mask(right_value)}

    if ct == "filing_current":
        out = src(rule.source)
        st = out.get("status")
        if st in ("UNAVAILABLE", "ERROR", "NOT_FETCHED", "CONSENT_REQUIRED", "NOT_FOUND"):
            return UNABLE, f"Source unavailable for filing status: {out.get('message')}", {"source": rule.source, "status": st}
        data = out.get("data", {})
        filing = str(data.get("filing_status", "")).upper()
        if filing in ("CURRENT", "OK", "ACTIVE"):
            return PASS, "Filing current at source", {"source": rule.source, "filing_status": filing, "last_return_date": data.get("last_return_date")}
        if st == "EXPIRED" or filing in ("EXPIRED", "DEFAULTED", "SUSPENDED", "LAPSED"):
            return FAIL, f"Filing not current at source: {filing or st}", {"source": rule.source, "filing_status": filing, "last_return_date": data.get("last_return_date")}
        return UNABLE, f"Indeterminable filing status: {filing or st}", {"source": rule.source, "filing_status": filing}

    if ct == "registration_required":
        out = src(rule.source)
        st = out.get("status")
        if st in ("UNAVAILABLE", "ERROR", "NOT_FETCHED", "CONSENT_REQUIRED"):
            return UNABLE, f"Source unavailable: {out.get('message')}", {"source": rule.source, "status": st}
        if st == "NOT_FOUND":
            return FAIL, "Required registration not found at source", {"source": rule.source, "status": st}
        if st in ("VALID", "EXPIRED"):
            note = " (expired)" if st == "EXPIRED" else ""
            return PASS, f"Registration found at source{note}", {"source": rule.source, "status": st}
        return UNABLE, f"Source returned {st}", {"source": rule.source, "status": st}

    if ct == "min_local_content":
        value = None
        profile = ctx["profile"]
        if profile is not None and profile.local_content_percent is not None:
            value = profile.local_content_percent
        out = src("LOCAL_CONTENT")
        if out.get("status") == "VALID" and out.get("data", {}).get("percent") is not None:
            value = int(out["data"]["percent"])
        if value is None:
            return UNABLE, "Local-content data not available", {"source": "LOCAL_CONTENT"}
        threshold = int(cv or 0)
        if value >= threshold:
            return PASS, f"Local content {value}% ≥ required {threshold}%", {"source": "LOCAL_CONTENT", "percent": value, "threshold": threshold}
        return FAIL, f"Local content {value}% < required {threshold}%", {"source": "LOCAL_CONTENT", "percent": value, "threshold": threshold}

    if ct == "startup_certified":
        out = src("STARTUP_INDIA")
        st = out.get("status")
        if st in ("UNAVAILABLE", "ERROR", "NOT_FETCHED", "CONSENT_REQUIRED"):
            return UNABLE, f"Startup India source unavailable: {out.get('message')}", {"source": "STARTUP_INDIA", "status": st}
        if st == "NOT_FOUND":
            return FAIL, "No Startup India recognition found", {"source": "STARTUP_INDIA", "status": st}
        if st == "EXPIRED":
            return FAIL, "Startup India recognition expired", {"source": "STARTUP_INDIA", "status": st}
        return PASS, "Startup India recognition active", {"source": "STARTUP_INDIA", "status": st}

    # ---- cross-document identity consistency (deterministic) ----

    if ct == "gstin_checksum_valid":
        gstin = profile.gstin_plain if profile else None
        if not gstin:
            return UNABLE, "No GSTIN in bidder profile", {"source": "PROFILE"}
        ok, msg = validate_gstin_checksum(gstin)
        if ok:
            return PASS, msg, {"source": "PROFILE", "gstin": _mask(gstin)}
        return FAIL, f"GSTIN failed deterministic validation: {msg}", {"source": "PROFILE", "gstin": _mask(gstin)}

    if ct == "gst_pan_embedded_match":
        gstin = profile.gstin_plain if profile else None
        pan = profile.pan_plain if profile else None
        if not gstin or len(str(gstin).strip()) < 15:
            return UNABLE, "GSTIN not available in bidder profile", {"source": "PROFILE"}
        if not pan:
            return UNABLE, "PAN not available in bidder profile", {"source": "PROFILE"}
        embedded = str(gstin).strip().upper()[2:12]
        declared = str(pan).strip().upper()
        if embedded == declared:
            return PASS, "PAN embedded in GSTIN (chars 2–12) matches declared PAN", {
                "source": "PROFILE", "embedded_pan": _mask(embedded), "declared_pan": _mask(declared)}
        return FAIL, f"PAN embedded in GSTIN ('{_mask(embedded)}') contradicts declared PAN ('{_mask(declared)}') — possible forged or mis-assigned GSTIN", {
            "source": "PROFILE", "embedded_pan": _mask(embedded), "declared_pan": _mask(declared)}

    if ct == "gst_state_match":
        gstin = profile.gstin_plain if profile else None
        if not gstin or len(str(gstin).strip()) < 2:
            return UNABLE, "GSTIN not available in bidder profile", {"source": "PROFILE"}
        code = str(gstin).strip().upper()[:2]
        gst_state = GST_STATE_CODES.get(code)
        if gst_state is None:
            return FAIL, f"GSTIN state code '{code}' is not a valid state code", {"source": "PROFILE", "state_code": code}
        expected = (cv or "").strip() or (profile.state or "")
        if not expected:
            return UNABLE, "No declared state to compare against", {"source": "PROFILE", "gst_state": gst_state}
        a = re.sub(r"[^a-z]", "", str(expected).lower().replace("&", "and"))
        b = re.sub(r"[^a-z]", "", gst_state.lower().replace("&", "and"))
        if a == b or a in b or b in a:
            return PASS, f"GSTIN state code {code} matches declared state ({gst_state})", {"source": "PROFILE", "state_code": code, "gst_state": gst_state}
        return FAIL, f"GSTIN state code {code} ('{gst_state}') does not match declared state ('{expected}')", {
            "source": "PROFILE", "state_code": code, "gst_state": gst_state, "declared_state": expected}

    if ct == "cross_doc_name_consistency":
        name = profile.company_name if profile else None
        if not name:
            return UNABLE, "No company name in bidder profile", {"source": "PROFILE"}
        checked, results = [], []
        for source_name in ("GST", "UDYAM"):
            out = src(source_name)
            st = out.get("status")
            data = out.get("data", {})
            if st in ("UNAVAILABLE", "ERROR", "NOT_FETCHED", "CONSENT_REQUIRED"):
                return UNABLE, f"{source_name} source unavailable — cross-document name consistency could not be established", {
                    "source": source_name, "status": st}
            if st == "NOT_FOUND":
                results.append({"source": source_name, "result": "UNABLE_TO_VERIFY", "detail": "no registration at source"})
                checked.append(source_name)
                continue
            source_name_val = data.get("legal_name") or data.get("trade_name")
            if not source_name_val:
                results.append({"source": source_name, "result": "UNABLE_TO_VERIFY", "detail": "no name field returned"})
                checked.append(source_name)
                continue
            ok = names_consistent_across_docs(name, str(source_name_val))
            results.append({"source": source_name, "result": "MATCH" if ok else "MISMATCH",
                            "bidder_value": _mask(name), "source_value": _mask(str(source_name_val))})
            checked.append(source_name)
        if not checked:
            return UNABLE, "No document source consulted for cross-document name check", {"source": "PROFILE"}
        if any(r["result"] == "MISMATCH" for r in results):
            bad = [r for r in results if r["result"] == "MISMATCH"]
            return FAIL, "Entity name inconsistent across documents: " + "; ".join(
                f"{r['source']} has '{r['source_value']}'" for r in bad), {
                "source": "PROFILE+DOCUMENT", "results": results}
        if any(r["result"] == "UNABLE_TO_VERIFY" for r in results):
            return UNABLE, "Cross-document name check incomplete: " + "; ".join(
                f"{r['source']}: {r['detail']}" for r in results if r["result"] == "UNABLE_TO_VERIFY"), {"source": "PROFILE+DOCUMENT", "results": results}
        return PASS, "Entity name consistent across profile and " + "+".join(checked), {"source": "PROFILE+DOCUMENT", "results": results}

    return UNABLE, f"Unsupported condition type: {ct}", {"condition_type": ct}


# ---------------- evidence strength ladder (spec §4) ----------------------
# Deterministic derivation from WHERE the evidence came from. Format validity
# (checksums/patterns) never raises strength above S1.

def _derive_strength(result: str, condition_type: str, evidence: Optional[dict]) -> str:
    from .scoring import STRENGTH_S0, STRENGTH_S1, STRENGTH_S2, STRENGTH_S3

    ev = evidence or {}
    st = str(ev.get("status") or "").upper()

    if result == "UNABLE_TO_VERIFY":
        return STRENGTH_S0

    # Source-authoritative evidence (live issuing-system / simulated adapter)
    if ev.get("source") not in (None, "DOCUMENT", "PROFILE") and st in (
        "VALID", "ACTIVE", "NOT_FOUND", "EXPIRED", "BLACKLIST", "CURRENT", "OK", "VALID",
    ):
        return STRENGTH_S3
    if ev.get("local_hits"):  # admin-maintained authoritative reference DB
        return STRENGTH_S3

    if result == "PASS":
        if ev.get("source") == "DOCUMENT":
            vst = str(ev.get("verification_status") or "").upper()
            if vst == "VERIFIED":
                return STRENGTH_S3      # document verified against issuing source
            if vst == "MANUAL_UPLOADED":
                return STRENGTH_S1      # self-declared upload only
            return STRENGTH_S0
        if condition_type == "name_match":
            return STRENGTH_S2          # declared value agrees with independent source
        if ev.get("source") == "PROFILE":
            return STRENGTH_S1          # self-declared field
        return STRENGTH_S1

    # FAIL: absence (missing document) is S0; source-proven failure is S3;
    # document-verified failure is S2; declared-data failure is S1.
    if ev.get("source") == "DOCUMENT":
        if ev.get("doc_type") and not ev.get("document_id"):
            return STRENGTH_S0
        return STRENGTH_S2
    return STRENGTH_S1


def _mask(value: Optional[str]) -> str:
    from .core.security import mask_gstin, mask_pan, mask_udyam
    v = (value or "").upper()
    if re.fullmatch(r"[A-Z0-9]{15}", v):
        return mask_gstin(v)
    if re.fullmatch(r"[A-Z]{5}[0-9]{4}[A-Z]", v):
        return mask_pan(v)
    if len(v) >= 16 and "U" in v:
        return mask_udyam(v)
    return value or ""


def evaluate_tender(
    db: Session, user: User, tender: Tender, ctx: Optional[dict] = None
) -> dict:
    """Run the full deterministic evaluation for a tender+bidder.

    Returns a dict with rule_results, cross-verification, mismatches,
    missing/expired documents, blacklist findings, low-confidence fields,
    score, risk, eligibility. No AI is involved.
    """
    if ctx is None:
        ctx = build_evidence_context(db, user, tender)

    profile = ctx["profile"]
    docs = ctx["documents"]
    today: date = ctx["today"]

    rules = sorted([r for r in tender.rules if r.enabled], key=lambda r: r.sort_order)
    rule_results = []
    for rule in rules:
        result, message, evidence = _eval_condition(rule, ctx)
        strength = _derive_strength(result, rule.condition_type, evidence)
        min_strength = getattr(rule, "min_evidence_strength", None) or "S1"
        # Hard rule: a PASS on evidence weaker than the rule's minimum is
        # UNABLE_TO_VERIFY — never PASS (spec §4).
        if (
            result == "PASS"
            and STRENGTH_RANK.get(strength, 0) < STRENGTH_RANK.get(min_strength, 1)
        ):
            result = "UNABLE_TO_VERIFY"
            message = (
                f"Met condition on evidence of strength {strength}, but the rule "
                f"requires at least {min_strength} — treated as UNABLE_TO_VERIFY, never PASS"
            )
        rule_results.append(
            {
                "rule_id": rule.id,
                "rule_key": rule.rule_key,
                "label": rule.label,
                "source": rule.source,
                "mandatory": rule.mandatory,
                "condition_type": rule.condition_type,
                "result": result,
                "weight": rule.weight,
                "weight_applied": rule.weight,
                "failure_action": rule.failure_action,
                "allows_manual_review": rule.allows_manual_review,
                "hard_disqualification": rule.hard_disqualification,
                "message": message,
                "evidence": evidence,
                "evidence_strength": strength,
                "min_evidence_strength": min_strength,
                "compliance_layer": getattr(rule, "compliance_layer", None) or "STATUTORY",
                "clause_ref": getattr(rule, "clause_ref", None),
            }
        )

    blacklist_findings = list(ctx["local_blacklist"])
    bl_out = ctx["sources"].get("BLACKLIST", {})
    if bl_out.get("status") == "BLACKLIST" and not blacklist_findings:
        blacklist_findings.append(
            {
                "entry_id": None,
                "entity_name": bl_out.get("data", {}).get("entity_name", "Unknown"),
                "matched_on": "portal blacklist match",
                "reason": bl_out.get("data", {}).get("reason", ""),
                "source_authority": bl_out.get("data", {}).get("source_authority", ""),
                "official_order_reference": bl_out.get("data", {}).get("order_ref", ""),
            }
        )

    # missing mandatory documents
    missing_docs = []
    for dt in (tender.required_document_types or []):
        if _doc_for_type(docs, dt) is None:
            missing_docs.append(dt)

    # expired documents
    expired_docs = []
    for d in docs:
        if d.expiry_date and d.expiry_date < today:
            expired_docs.append({"document_id": d.id, "name": d.name, "expiry_date": d.expiry_date.isoformat()})

    # cross-verification table
    cross = build_cross_verification(ctx)

    mismatches = [
        {"field": c["field"], "bidder_value": c["bidder_value"], "source_value": c["source_value"], "source": c["source"]}
        for c in cross
        if c["result"] == "MISMATCH"
    ]

    # low-confidence OCR fields
    low_conf = []
    for d in docs:
        if d.ocr_confidence is not None and d.ocr_confidence < 0.7:
            low_conf.append(
                {"document_id": d.id, "name": d.name, "confidence": round(float(d.ocr_confidence), 3)}
            )

    score, manual_review = compute_score(rule_results)
    risk = determine_risk(rule_results, blacklist_findings, score)
    eligibility = determine_eligibility(rule_results, blacklist_findings, missing_docs)

    from .scoring import apply_button_allowed
    apply_allowed, apply_reasons = apply_button_allowed(rule_results, blacklist_findings, missing_docs)

    # Evidence-strength distribution + verified coverage (reported alongside
    # the score — a high score on thin evidence must look different).
    coverage = compute_coverage(rule_results)
    dist = strength_distribution(rule_results)
    det_hash = determinism_hash(rule_results, score, risk, eligibility)

    return {
        "rule_results": rule_results,
        "cross_verification": cross,
        "mismatches": mismatches,
        "missing_documents": missing_docs,
        "expired_documents": expired_docs,
        "blacklist_findings": blacklist_findings,
        "low_confidence_fields": low_conf,
        "source_results": ctx["sources"],
        "compliance_score": score,
        "manual_review_required": manual_review,
        "risk_level": risk,
        "eligibility_result": eligibility,
        "apply_allowed": apply_allowed,
        "apply_blocked_reasons": apply_reasons,
        "coverage_pct": coverage,
        "strength_distribution": dist,
        "determinism_hash": det_hash,
    }


def build_cross_verification(ctx: dict) -> list[dict]:
    """Field-level bidder vs government source comparison table.

    Officer-facing: values are shown in full (the reviewing officer has
    authority to see bid details; masked values make the comparison
    unreadable). Each row is decided on the raw values and the outcome is
    labelled MATCH / MISMATCH / UNABLE_TO_VERIFY.
    """
    profile = ctx["profile"]
    sources = ctx["sources"]
    rows = []

    def add(field: str, bidder_value, source_value, source: str, compare=None):
        cmp = compare or names_match
        if bidder_value is None or source_value is None:
            result = "UNABLE_TO_VERIFY"
        elif cmp(str(bidder_value), str(source_value)):
            result = "MATCH"
        else:
            result = "MISMATCH"
        rows.append(
            {
                "field": field,
                "bidder_value": str(bidder_value) if bidder_value is not None else None,
                "source_value": str(source_value) if source_value is not None else None,
                "source": source,
                "result": result,
            }
        )

    gst = sources.get("GST", {}).get("data", {})
    udyam = sources.get("UDYAM", {}).get("data", {})
    pan = sources.get("PAN", {}).get("data", {})

    if profile is None:
        return rows

    add("Legal name", profile.company_name, gst.get("legal_name") or udyam.get("legal_name"), "GST/UDYAM")
    add("Trade name", profile.trade_name, gst.get("trade_name"), "GST")
    add("PAN", profile.pan_plain, pan.get("pan"), "PAN")
    add("GSTIN", profile.gstin_plain, gst.get("gstin"), "GST")
    add("Udyam number", profile.udyam_plain, udyam.get("udyam_number"), "UDYAM")
    add("Registered address", profile.registered_address, gst.get("address"), "GST", compare=addresses_match)
    add("Authorized representative", profile.authorized_rep_name, gst.get("authorized_rep"), "GST")
    add("Annual turnover (₹)", str(profile.annual_turnover) if profile.annual_turnover is not None else None, str(gst.get("turnover")) if gst.get("turnover") is not None else None, "GST")
    add("Filing status", profile.filing_status, gst.get("filing_status"), "GST")
    return rows
