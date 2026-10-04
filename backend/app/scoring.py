"""Deterministic compliance scoring and risk determination.

The score and risk are computed ONLY from deterministic rule results.
AI/LLM output never modifies them.
"""
from typing import Optional

RISK_LOW = "LOW"
RISK_MEDIUM = "MEDIUM"
RISK_HIGH = "HIGH"
RISK_CRITICAL = "CRITICAL"

ELIGIBLE = "ELIGIBLE"
CONDITIONAL = "CONDITIONAL"
NOT_ELIGIBLE = "NOT_ELIGIBLE"
# Debarment/blacklist hit — the single label for a blacklisted bidder on every
# tender (a blacklist in one domain disables bidding in all domains).
BLACKLISTED = "BLACKLISTED"
# Mandatory tender documents not yet in the Document Vault — actionable, and
# reported as such rather than the generic "Not Eligible".
MISSING_DOCUMENTS = "MISSING_DOCUMENTS"

RISK_LABELS = {
    RISK_LOW: "Low Risk — Compliant",
    RISK_MEDIUM: "Medium Risk — Review Required",
    RISK_HIGH: "High Risk — Major Concern",
    RISK_CRITICAL: "Critical — Blacklisted/Debarred or Confirmed Mandatory Failure",
}

# ---------------------------------------------------------------------------
# Evidence strength ladder (spec §4) — UNVERIFIED IS NEVER COMPLIANT
# ---------------------------------------------------------------------------
STRENGTH_S0 = "S0"  # UNVERIFIED: missing / unreadable / stale / source unavailable
STRENGTH_S1 = "S1"  # DECLARED: bidder document/self-declared only
STRENGTH_S2 = "S2"  # CORROBORATED: agrees with independent source or 2+ documents
STRENGTH_S3 = "S3"  # AUTHORITATIVE: live issuing-system (or simulated adapter) response
STRENGTH_S4 = "S4"  # CRYPTOGRAPHIC: valid digital signature / issuer-attested chain

STRENGTH_RANK = {STRENGTH_S0: 0, STRENGTH_S1: 1, STRENGTH_S2: 2, STRENGTH_S3: 3, STRENGTH_S4: 4}

STRENGTH_LABELS = {
    STRENGTH_S0: "Unverified",
    STRENGTH_S1: "Declared only",
    STRENGTH_S2: "Corroborated",
    STRENGTH_S3: "Authoritative source",
    STRENGTH_S4: "Cryptographic",
}

STRENGTH_TOOLTIP = (
    "Format validity (checksums, patterns) never raises evidence above S1 — "
    "a well-formed number is not a verified number. S3 requires a live issuing-system "
    "response; S4 an issuer-attested cryptographic proof."
)


def compute_coverage(rule_results: list[dict]) -> float:
    """% of applicable requirements actually verified.

    UNABLE_TO_VERIFY contributes 0 to coverage and is reported separately —
    a high score on thin evidence must look different from a high score on
    complete evidence (spec §9)."""
    if not rule_results:
        return 100.0
    verified = sum(1 for r in rule_results if r["result"] != "UNABLE_TO_VERIFY")
    return round(100.0 * verified / len(rule_results), 1)


def strength_distribution(rule_results: list[dict]) -> dict:
    """e.g. {'S3': 7, 'S2': 2, 'S1': 3} — only requirements with ≥ S1 evidence."""
    dist: dict[str, int] = {}
    for r in rule_results:
        s = r.get("evidence_strength") or STRENGTH_S0
        if STRENGTH_RANK.get(s, 0) >= 1:
            dist[s] = dist.get(s, 0) + 1
    return dict(sorted(dist.items(), reverse=True))


def determinism_hash(rule_results: list[dict], score: int, risk: str, eligibility: str) -> str:
    """SHA-256 over the canonical deterministic results.

    Identical inputs MUST produce an identical hash (spec §9/§12.3). The replay
    endpoint re-runs the deterministic portion and asserts bit-identity."""
    import hashlib
    import json

    canonical = {
        "score": score,
        "risk": risk,
        "eligibility": eligibility,
        "rule_results": [
            {k: r[k] for k in ("rule_key", "result", "weight_applied", "evidence_strength")}
            for r in rule_results
        ],
    }
    return hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def compute_score(rule_results: list[dict]) -> tuple[int, bool]:
    """Return (score, manual_review_required).

    - FAIL: full weight deducted.
    - UNABLE_TO_VERIFY: half weight deducted and manual review required.
    - A source being unavailable is never treated as PASS.
    """
    score = 100
    manual_review = False
    for r in rule_results:
        w = int(r.get("weight_applied", r.get("weight", 0)) or 0)
        if r["result"] == "FAIL":
            score -= w
            if r.get("allows_manual_review"):
                manual_review = True
        elif r["result"] == "UNABLE_TO_VERIFY":
            score -= max(1, w // 2)
            manual_review = True
    return max(0, min(100, score)), manual_review


def _hard_fail(rule_results: list[dict]) -> bool:
    return any(
        r["result"] == "FAIL" and r.get("hard_disqualification") for r in rule_results
    )


def _mandatory_fail(rule_results: list[dict]) -> bool:
    return any(r["result"] == "FAIL" and r.get("mandatory") for r in rule_results)


def determine_risk(rule_results: list[dict], blacklist_findings: list, score: int) -> str:
    if _hard_fail(rule_results) or blacklist_findings:
        return RISK_CRITICAL
    if _mandatory_fail(rule_results) or score < 60:
        return RISK_HIGH
    unable_mandatory = any(
        r["result"] == "UNABLE_TO_VERIFY" and r.get("mandatory") for r in rule_results
    )
    if unable_mandatory or score < 80:
        return RISK_MEDIUM
    return RISK_LOW


def determine_eligibility(
    rule_results: list[dict], blacklist_findings: list, missing_docs: Optional[list] = None
) -> str:
    # 1. A blacklist/debarment hit is the one true label: it disables bidding
    #    for every tender, in every domain, and is reported as BLACKLISTED
    #    (never the generic "Not Eligible").
    if blacklist_findings:
        return BLACKLISTED
    # 2. Missing mandatory tender documents are reported as such (actionable:
    #    upload the documents and re-check) — not as a blanket "Not Eligible".
    if missing_docs:
        return MISSING_DOCUMENTS
    # 3. Any other confirmed hard failure is a genuine disqualification.
    if _hard_fail(rule_results):
        return NOT_ELIGIBLE
    conditional = any(
        (r["result"] == "UNABLE_TO_VERIFY")
        or (r["result"] == "FAIL" and r.get("allows_manual_review") and not r.get("hard_disqualification"))
        for r in rule_results
    )
    return CONDITIONAL if conditional else ELIGIBLE


def risk_label(risk: str) -> str:
    return RISK_LABELS.get(risk or "", risk or "")


def eligibility_label(result: str) -> str:
    return {
        ELIGIBLE: "Eligible to Apply",
        CONDITIONAL: "Conditionally Eligible / Manual Review Required",
        NOT_ELIGIBLE: "Not Eligible",
        BLACKLISTED: "Blacklisted — debarment active; application disabled for all tenders",
        MISSING_DOCUMENTS: "Missing mandatory documents — upload to apply",
    }.get(result or "", result or "")


def apply_button_allowed(
    rule_results: list[dict], blacklist_findings: list, missing_docs: list
) -> tuple[bool, list[str]]:
    """Apply is disabled ONLY for confirmed hard failures / blacklist / missing mandatory docs."""
    reasons = []
    for r in rule_results:
        if r["result"] == "FAIL" and r.get("hard_disqualification"):
            reasons.append(f"Hard requirement failed: {r['label']}")
        elif r["result"] == "FAIL" and r.get("mandatory") and not r.get("allows_manual_review"):
            reasons.append(f"Confirmed mandatory requirement failed: {r['label']}")
    if blacklist_findings:
        reasons.append(
            "Blacklisted — active debarment record in the Reference Blacklist Database; "
            "application is disabled for ALL tenders, in every domain"
        )
    for d in missing_docs:
        reasons.append(f"Mandatory document missing: {d}")
    return (len(reasons) == 0), reasons
