"""Role-scoped document visibility + the officer-readable summary.

Visibility: an officer sees ONLY the documents (and rule sections) relevant
to their stage's job. Example: the Technical Evaluation Officer cannot act on
PAN/GST/Udyam/EPFO records, so those documents are hidden from their review.
"""
from typing import Optional

# role -> (allowed doc_types or None=all, allowed rule scope or None=all)
# rule scope: "SECTOR_TECHNICAL" (layer filter) | set of rule keys | None (all)
DOC_POLICY: dict[str, tuple] = {
    # full record: compliance screening + authority + post-award management
    "PROCUREMENT_OFFICER": (None, None),
    "BID_OPENING_OFFICER": (None, None),
    "CFA": (None, None),
    "COMMITTEE_CHAIR": (None, None),
    "COMMITTEE_MEMBER": (None, None),
    "CONTRACT_MANAGER": (None, None),
    "CONTRACT_OFFICER": (None, None),
    "INSPECTION_OFFICER": (None, None),
    "PAYMENT_OFFICER": (None, None),
    "ADMIN": (None, None),
    "AUDITOR": (None, None),
    # scoped roles -----------------------------------------------------------
    # Technical: certifications & technical credentials only (NO PAN/GST/UDYAM/EPFO/ESIC)
    "TECHNICAL_EVALUATOR": (
        {"OEM_AUTHORIZATION", "BIS_CERT", "STARTUP_CERT", "NSIC_CERT",
         "LOCAL_CONTENT_DECLARATION", "TECH_SPEC", "QUOTATION"},
        "SECTOR_TECHNICAL",
    ),
    # Financial: tax compliance (GST) + commercial documents (NO PAN/UDYAM/EPFO)
    "FINANCE_EVALUATOR": (
        {"GST_CERT", "QUOTATION", "COMMERCIAL", "TAX"},
        {"gst_active", "gst_filing_current", "min_turnover", "gstin_checksum_valid",
         "gst_state_match", "gst_pan_embedded_match"},
    ),
}

SCOPE_NOTES = {
    "TECHNICAL_EVALUATOR": "Technical evaluation view \u2014 technical & certification documents only. "
                           "Statutory identity documents (PAN/GST/Udyam/EPFO) are handled at the "
                           "compliance stage and are not shown here.",
    "FINANCE_EVALUATOR": "Financial evaluation view \u2014 tax-compliance (GST) and commercial documents. "
                         "Identity/MSME/labour documents are handled at the compliance stage.",
}


def doc_scope_for_role(role: Optional[str]) -> tuple[Optional[set], object, Optional[str]]:
    docs, rules = DOC_POLICY.get(role or "", (None, None))
    return (set(docs) if docs else None), rules, SCOPE_NOTES.get(role or "")


def filter_docs_for_role(docs: list[dict], role: Optional[str]) -> list[dict]:
    allowed, _, _ = doc_scope_for_role(role)
    if allowed is None:
        return docs
    return [d for d in docs if d.get("doc_type") in allowed]


def filter_rule_results(rule_results: list[dict], role: Optional[str]) -> list[dict]:
    _, scope, _ = doc_scope_for_role(role)
    if scope is None:
        return rule_results
    if scope == "SECTOR_TECHNICAL":
        return [r for r in rule_results if (r.get("compliance_layer") or "STATUTORY") == "SECTOR_TECHNICAL"]
    return [r for r in rule_results if r.get("rule_key") in scope]


def doc_scope_info(docs: list[dict], role: Optional[str]) -> dict:
    visible = filter_docs_for_role(docs, role)
    visible_types = sorted({d.get("doc_type") for d in visible if d.get("doc_type")})
    all_types = sorted({d.get("doc_type") for d in docs if d.get("doc_type")})
    hidden_types = [t for t in all_types if t not in visible_types]
    return {
        "role": role,
        "scope_label": SCOPE_NOTES.get(role or ""),
        "note": SCOPE_NOTES.get(role or ""),
        "visible_count": len(visible),
        "total_count": len(docs),
        "visible_types": visible_types,
        "hidden_types": hidden_types,
        "hidden_count": len(hidden_types),
        "scoped": DOC_POLICY.get(role or "", (None, None))[0] is not None,
    }


# ---------------------------------------------------------------------------
# officer-readable summary (deterministic — built from rule results only)
# ---------------------------------------------------------------------------

def build_officer_summary(report) -> dict:
    """A skimmable, precise summary at the top of every report:
    verdict, one line, what to fix (with the concrete action), watch items,
    document state, and the single recommended action."""
    from .ai_engine.explainer import remedy_for

    rr = report.rule_results or []
    fails = [r for r in rr if r["result"] == "FAIL"]
    unables = [r for r in rr if r["result"] == "UNABLE_TO_VERIFY"]
    passed = [r for r in rr if r["result"] == "PASS"]
    blacklist = report.blacklist_findings or []

    if blacklist:
        verdict = "NON-COMPLIANT \u2014 DEBARRED BIDDER"
    elif report.eligibility_result in ("NOT_ELIGIBLE", "BLACKLISTED"):
        verdict = "NON-COMPLIANT"
    elif unables or [r for r in fails if r.get("allows_manual_review")]:
        verdict = "MANUAL REVIEW REQUIRED"
    elif fails:
        verdict = "NON-COMPLIANT"
    else:
        verdict = "COMPLIANCE VERIFIED"

    n = len(rr)
    if blacklist:
        one_line = (f"Active debarment record on file ({blacklist[0].get('source_authority', 'reference register')}) "
                    f"\u2014 the bid cannot proceed regardless of other checks.")
    else:
        one_line = f"{len(passed)} of {n} checks passed"
        if fails:
            one_line += f", {len(fails)} failed"
        if unables:
            one_line += f", {len(unables)} could not be verified"
        one_line += ". "
        if not fails and not unables:
            one_line += "No findings requiring action."
        else:
            one_line += "Items below need your action before the bid can proceed."

    def _item(r, tag):
        return {
            "check": r["label"],
            "status": tag,
            "finding": r.get("message"),
            "clause": r.get("clause_ref"),
            "action": remedy_for(r),
        }

    must_fix = [_item(r, "FAILED") for r in fails]
    watch_items = [_item(r, "UNVERIFIED") for r in unables]

    auth = report.authenticity or []
    docs_verified = sum(1 for a in auth if a.get("status") == "VERIFIED")
    docs_flagged = [a.get("document_name") or a.get("name") for a in auth if a.get("status") not in (None, "VERIFIED")]
    missing = report.missing_documents or []

    summary = {
        "verdict": verdict,
        "score": report.compliance_score,
        "risk": report.risk_level,
        "one_line": one_line,
        "must_fix": must_fix,
        "watch_items": watch_items,
        "documents": {
            "verified": docs_verified,
            "flagged": docs_flagged,
            "missing": missing,
        },
        "recommended_action": report.recommendation,
        "checks": {"total": n, "passed": len(passed), "failed": len(fails), "unverified": len(unables)},
    }
    return summary
