"""Local knowledge base for the Explainable Verification Engine.

Grounding corpus for RAG retrieval: statutory/policy clause language keyed by
the exact `clause_ref` values used in tender rules, plus condition-type
semantics and topic passages (blacklist, source availability, document
integrity, governance).

Prototype note: clause text is abridged, demonstrative statutory language for
the simulated GeM-like regime (see README honesty statements). Retrieval is
deterministic (no external embeddings/API) — grounded citations must match a
passage in this KB or a whitelisted instrument, enforced by the citation guard.
"""

PASSAGES: list[dict] = [
    # ---- GeM GTC family ----------------------------------------------------
    {
        "id": "ktc-2-1",
        "title": "GeM GTC §2.1 — Active registration at source",
        "refs": ["GeM GTC §2.1"],
        "applies_to": ["is_active", "registration_required", "UDYAM", "GST", "PAN", "MCA21", "EPFO", "ESIC", "BIS", "NSIC"],
        "text": (
            "A bidder must hold a registration or certification that is active (not suspended, "
            "cancelled, or lapsed) with the issuing authority as confirmed on the official source "
            "record at the time of verification. An inactive registration is a mandatory failure."
        ),
    },
    {
        "id": "ktc-2-1b",
        "title": "GeM GTC §2.1(b) — Statutory filings current",
        "refs": ["GeM GTC §2.1(b)"],
        "applies_to": ["filing_current"],
        "text": (
            "The bidder's statutory returns and filings (e.g., ITR, GST returns) must be current "
            "with no persistent default. A defaulting filing status renders the eligibility check "
            "failing until filings are regularised."
        ),
    },
    {
        "id": "ktc-2-4",
        "title": "GeM GTC §2.4 — MSME/UDYAM registration active",
        "refs": ["GeM GTC §2.4"],
        "applies_to": ["is_active", "UDYAM", "msme"],
        "text": (
            "For tenders with MSME preference, the bidder's Udyam/MSME registration must be active "
            "and the category claimed at application must match the registration category on the "
            "Udyam record."
        ),
    },
    # ---- ATC (Authorisation/Technical) family ------------------------------
    {
        "id": "atc-1",
        "title": "ATC-1 — Legal name consistency (profile vs source)",
        "refs": ["ATC-1"],
        "applies_to": ["name_match", "names_consistent"],
        "text": (
            "The legal name of the entity on the bidder profile must be consistent with the name "
            "recorded at the official source (e.g., Udyam/MCA). Material divergence between legal "
            "names indicates a possible identity substitution and must be resolved before "
            "qualification."
        ),
    },
    {
        "id": "atc-2",
        "title": "ATC-2 — Mandatory documents in the vault",
        "refs": ["ATC-2", "Documents §4.1", "Spec §3.1"],
        "applies_to": ["has_document"],
        "text": (
            "Every document listed as mandatory for the tender must be present in the bidder's "
            "document vault and linked to the application. A missing mandatory document is a "
            "mandatory failure subject to clarification if the tender terms allow cure."
        ),
    },
    {
        "id": "atc-4",
        "title": "ATC-4 — Minimum local content (Make in India)",
        "refs": ["ATC-4"],
        "applies_to": ["min_local_content"],
        "text": (
            "Where the tender mandates a minimum local-content percentage, the declared percentage "
            "must meet or exceed the threshold, supported by documentary evidence. Failure is a "
            "mandatory non-qualification."
        ),
    },
    # ---- Specification ------------------------------------------------------
    {
        "id": "spec-3-1",
        "title": "Specification §3.1 — Product/service conformity evidence",
        "refs": ["Spec §3.1"],
        "applies_to": ["has_document", "has_authorization"],
        "text": (
            "The bidder must submit conformity evidence (test certificates, OEM authorisation, or "
            "equivalent) demonstrating the offered product/service meets the specification. OEM "
            "authorisation is mandatory where third-party supply is involved."
        ),
    },
    {
        "id": "spec-3-2",
        "title": "Specification §3.2 — Valid, unexpired documents",
        "refs": ["Spec §3.2"],
        "applies_to": ["valid_unexpired"],
        "text": (
            "All submitted certificates and documents must be valid (within their stated validity "
            "period) at the time of bid opening. Expired documents are treated as missing for "
            "compliance purposes unless the tender expressly permits."
        ),
    },
    {
        "id": "spec-4-2",
        "title": "Specification §4.2 — Applicable BIS standard",
        "refs": ["Spec §4.2"],
        "applies_to": ["is_active", "BIS"],
        "text": (
            "The supplied goods must conform to the applicable BIS standard cited in the "
            "specification. Where a corrigendum revises the cited standard, compliance must be "
            "assessed against the revised standard and earlier verification results are stale."
        ),
    },
    # ---- Eligibility --------------------------------------------------------
    {
        "id": "elig-3-2",
        "title": "Eligibility §3.2 — Minimum annual turnover",
        "refs": ["Eligibility §3.2"],
        "applies_to": ["min_turnover", "turnover_gst_match"],
        "text": (
            "The bidder's average annual turnover for the preceding financial years must meet or "
            "exceed the minimum stipulated for the tender. Turnover claims are corroborated "
            "against GST-portal turnover data; material under-representation is a mandatory failure."
        ),
    },
    # ---- Integrity ----------------------------------------------------------
    {
        "id": "integrity-7-1",
        "title": "Integrity §7.1 — No debarment/blacklist",
        "refs": ["Integrity §7.1"],
        "applies_to": ["not_blacklisted", "blacklist"],
        "text": (
            "A bidder (or its directors/proprietors) that appears on an active debarment or "
            "blacklist entry is ineligible for the duration of the debarment. This is a hard "
            "disqualification: qualification may proceed only through the Override Accountability "
            "Engine with full accountability chain."
        ),
    },
    # ---- Labour -------------------------------------------------------------
    {
        "id": "labour-6-1",
        "title": "Labour compliance §6.1 — Statutory labour registrations active",
        "refs": ["Labour compliance §6.1"],
        "applies_to": ["is_active", "EPFO", "ESIC"],
        "text": (
            "The bidder must maintain active statutory labour registrations (EPFO/ESIC where "
            "applicable). Inactive labour compliance is a mandatory failure for tenders above the "
            "specified contract value."
        ),
    },
    # ---- Preference ---------------------------------------------------------
    {
        "id": "pref-8-3",
        "title": "Preference §8.3 — Startup recognition (preferred, not mandatory)",
        "refs": ["Preference §8.3"],
        "applies_to": ["startup_certified"],
        "text": (
            "Startup India recognition is a preference criterion: recognised startups receive "
            "preference in evaluation. Non-recognition is not a disqualification where the "
            "criterion is marked non-mandatory."
        ),
    },
    # ---- Cross-document identity (BidVerify port) ---------------------------
    {
        "id": "xdoc-consistency",
        "title": "Cross-document identity consistency",
        "refs": [],
        "applies_to": ["names_consistent", "gst_state_match", "pan_gst_match", "gstin_checksum_valid"],
        "text": (
            "Identity fields must be internally consistent across documents: the PAN embedded in a "
            "GSTIN must match the declared PAN, the GSTIN state code must match the declared "
            "jurisdiction, the GSTIN must satisfy its checksum, and the legal name must be "
            "consistent across submitted documents (ignoring legal-form tokens such as Pvt Ltd)."
        ),
    },
    # ---- Topics -------------------------------------------------------------
    {
        "id": "topic-source-unavailable",
        "title": "Source unavailability — UNABLE_TO_VERIFY doctrine",
        "refs": [],
        "applies_to": ["unable_to_verify", "source_unavailable"],
        "text": (
            "Where an official source cannot be reached or returns no record, the affected check is "
            "recorded as UNABLE_TO_VERIFY — never as PASS and never as FAIL. Such checks route to "
            "manual review with documented offline verification. This preserves the presumption "
            "against both wrongful rejection and wrongful qualification."
        ),
    },
    {
        "id": "topic-mismatch",
        "title": "Cross-verification mismatch",
        "refs": [],
        "applies_to": ["mismatch"],
        "text": (
            "A material discrepancy between the value declared by the bidder and the value recorded "
            "at the official source is a cross-verification mismatch. Each mismatch must be "
            "identified by field, both values, and the source consulted, and is grounds for "
            "clarification or rejection depending on materiality and tender terms."
        ),
    },
    {
        "id": "topic-doc-integrity",
        "title": "Document authenticity & integrity findings",
        "refs": [],
        "applies_to": ["authenticity", "document_integrity"],
        "text": (
            "Documents are assessed for authenticity (seal/signature consistency against trusted "
            "references, metadata, and visual anomalies). A SUSPICIOUS finding is a manual-review "
            "trigger, never a conclusive determination of forgery; visual similarity of a seal or "
            "signature alone is never conclusive."
        ),
    },
    {
        "id": "topic-remedy",
        "title": "Remediation and clarification path",
        "refs": [],
        "applies_to": ["clarification", "remediation"],
        "text": (
            "Where the tender terms permit cure, confirmed failures are communicated to the bidder "
            "as a clarification request with the specific deficiencies and a deadline. Corrected "
            "evidence must be re-verified before qualification."
        ),
    },
    {
        "id": "topic-governance",
        "title": "Decision governance — deterministic engine decides",
        "refs": [],
        "applies_to": ["governance"],
        "text": (
            "The verification outcome is decided exclusively by the deterministic rule engine. The "
            "AI component explains findings and must not decide, qualify, rank, or override. Final "
            "qualification authority rests with the designated procurement officers and committee; "
            "no bid is auto-awarded."
        ),
    },
]

_BY_ID: dict[str, dict] = {p["id"]: p for p in PASSAGES}


def get_passage(passage_id: str) -> dict | None:
    return _BY_ID.get(passage_id)
