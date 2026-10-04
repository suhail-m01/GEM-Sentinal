"""Explainable Verification Engine (XAI).

Architecture:
- The deterministic rule engine ALREADY decided; this module only explains.
- RAG retrieval (local KB, deterministic scoring) grounds every explanation in
  statutory clause language + the evaluation's own evidence.
- Output is two-layer:
  * `explanation_detail` — structured, sectioned, citation-bearing (JSON;
    rendered in the review UI and the PDF).
  * `ai_explanation` — professional prose narrative (Gemini when configured,
    grounded on the retrieved passages; deterministic template otherwise).
- Every citation must match a retrieved passage or whitelisted instrument —
  the citation guard strips unverifiable ones (anti-hallucination).
"""
import json
from typing import Optional

from . import gemini
from . import rag
from .citation_guard import sanitize_explanation

MODEL_FALLBACK = "deterministic-xai-2.0"

_RISK_LABELS = {
    "LOW": "Low Risk — Compliant",
    "MEDIUM": "Medium Risk — Review Required",
    "HIGH": "High Risk — Material Non-Compliance",
    "CRITICAL": "Critical — Debarment or Confirmed Mandatory Failure",
}
_RESULT_IMPACT = {
    "FAIL": "confirmed non-compliance",
    "UNABLE_TO_VERIFY": "unverifiable — routes to manual review (never treated as pass)",
}

# ----------------------------- structured layer ----------------------------


def _clause_passage(evaluation: dict, clause_ref: str, condition_type: str) -> Optional[dict]:
    """Best grounding passage for one rule result."""
    scored = []
    for p in evaluation.get("_retrieved", []):
        s = 0.0
        if clause_ref and p.get("refs") and any(
            clause_ref == r or clause_ref.startswith(r) or r.startswith(clause_ref) for r in p["refs"]
        ):
            s += 4.0
        if condition_type and condition_type in p.get("applies_to", []):
            s += 3.0
        if s:
            scored.append((s, p))
    if not scored:
        return None
    scored.sort(key=lambda x: -x[0])
    return scored[0][1]


def structured_explanation(evaluation: dict, language: str = "en") -> dict:
    """Build the sectioned, citation-bearing explanation object."""
    retrieved = evaluation.get("_retrieved") or rag.retrieve_for(evaluation)
    rules = evaluation.get("rule_results") or []
    fails = [r for r in rules if r.get("result") == "FAIL"]
    unables = [r for r in rules if r.get("result") == "UNABLE_TO_VERIFY"]
    passes = [r for r in rules if r.get("result") == "PASS"]
    n_a = [r for r in rules if r.get("result") == "N/A"]

    # ---- basis of decision: every failing/unverified mandatory-relevant check
    import re as _re

    def _clean_msg(m: Optional[str]) -> Optional[str]:
        if not m:
            return m
        # strip operational cache markers from evidence text (freshness is
        # carried by evidence_trail.cached instead)
        return _re.sub(r"^\[cache hit: source verified \d+s ago\]\s*", "", m)

    basis = []
    for r in sorted(fails + unables, key=lambda x: (not x.get("mandatory"), -int(x.get("weight") or 0))):
        passage = _clause_passage(evaluation, r.get("clause_ref") or "", r.get("condition_type") or "")
        basis.append({
            "rule": r.get("label"),
            "rule_key": r.get("rule_key"),
            "result": r.get("result"),
            "mandatory": bool(r.get("mandatory")),
            "clause_ref": r.get("clause_ref") or None,
            "statutory_basis": passage.get("text") if passage else None,
            "cited_passage_id": passage.get("id") if passage else None,
            "evidence": {
                "message": _clean_msg(r.get("message")),
                "source": (r.get("evidence") or {}).get("source"),
                "detail": r.get("evidence"),
            },
            "weight": r.get("weight"),
            "impact": _RESULT_IMPACT.get(r.get("result"), r.get("result")),
        })

    # ---- mismatches
    mismatches = []
    for m in evaluation.get("mismatches") or []:
        mismatches.append({
            "field": m.get("field"),
            "bidder_value": m.get("bidder_value"),
            "source_value": m.get("source_value"),
            "source": m.get("source"),
            "cited_passage_id": "topic-mismatch",
        })

    # ---- document findings
    doc_findings = []
    for name in evaluation.get("missing_documents") or []:
        doc_findings.append({"document": name, "finding": "MISSING",
                             "detail": "Mandatory document not present in the vault/application.",
                             "cited_passage_id": "atc-2"})
    for d in evaluation.get("expired_documents") or []:
        doc_findings.append({"document": d.get("name"), "finding": "EXPIRED",
                             "detail": "Document validity ended before bid evaluation.",
                             "cited_passage_id": "spec-3-2"})
    for a in evaluation.get("authenticity_summary") or []:
        if a.get("status") and a.get("status") != "VERIFIED":
            doc_findings.append({"document": a.get("name"), "finding": a.get("status"),
                                 "detail": "Authenticity assessment flagged for review.",
                                 "cited_passage_id": "topic-doc-integrity"})

    # ---- evidence trail (per government source)
    trail = []
    for s, v in (evaluation.get("source_results") or {}).items():
        trail.append({
            "source": s,
            "status": v.get("status"),
            "retrieved_at": v.get("retrieved_at"),
            "cached": bool((v.get("data") or {}).get("_cached")),
            "message": _clean_msg(v.get("message")),
        })

    # ---- remediation (actionable, tender-cure-aware)
    remediation = []
    for r in fails:
        rem = _remedy_for(r)
        if rem:
            remediation.append({"rule": r.get("label"), "action": rem,
                                "cited_passage_id": "topic-remedy"})
    for r in unables:
        remediation.append({"rule": r.get("label"),
                            "action": "Offline verification with documented evidence required; source must be re-checked or an alternative proof submitted.",
                            "cited_passage_id": "topic-source-unavailable"})
    if mismatches:
        remediation.append({"rule": "Cross-verification",
                            "action": "Reconcile the declared values with the official source records and resubmit corrected evidence where the declaration was in error.",
                            "cited_passage_id": "topic-mismatch"})

    # ---- citations actually used
    cited_ids = []
    for b in basis:
        if b.get("cited_passage_id"):
            cited_ids.append(b["cited_passage_id"])
    for m in mismatches:
        cited_ids.append(m["cited_passage_id"])
    for d in doc_findings:
        cited_ids.append(d["cited_passage_id"])
    for x in remediation:
        cited_ids.append(x["cited_passage_id"])
    cited_ids.append("topic-governance")
    citations = []
    seen = set()
    for pid in cited_ids:
        if pid in seen:
            continue
        seen.add(pid)
        p = next((x for x in retrieved if x["id"] == pid), None)
        if p is None:
            from . import knowledge_base as kb
            p = kb.get_passage(pid)
        if p:
            citations.append({"passage_id": p["id"], "title": p["title"],
                              "refs": p.get("refs") or []})

    # ---- executive summary
    headline = []
    if evaluation.get("blacklist_findings"):
        headline.append("An active debarment/blacklist match was confirmed (hard disqualification).")
    if fails:
        headline.append(f"{len(fails)} mandatory/compliance check(s) failed.")
    if unables:
        headline.append(f"{len(unables)} check(s) could not be verified (manual review required).")
    if not headline:
        headline.append("All deterministic checks passed; no material exceptions identified.")

    return {
        "version": "2.0",
        "language": language,
        "executive_summary": {
            "compliance_score": evaluation.get("compliance_score"),
            "risk_level": evaluation.get("risk_level"),
            "risk_label": _RISK_LABELS.get(evaluation.get("risk_level") or "", evaluation.get("risk_level")),
            "eligibility_result": evaluation.get("eligibility_result"),
            "headline": headline,
            "counts": {"passed": len(passes), "failed": len(fails),
                       "unverified": len(unables), "not_applicable": len(n_a)},
        },
        "basis_of_decision": basis,
        "cross_verification": mismatches,
        "document_findings": doc_findings,
        "evidence_trail": trail,
        "remediation": remediation,
        "citations": citations,
        "governance": {
            "decided_by": "Deterministic rule engine (tender rule set, version-controlled)",
            "ai_role": "Explanation and retrieval grounding only — the AI does not decide, qualify, rank, or override",
            "retrieval": "Deterministic local RAG over the statutory knowledge base (no external models used for grounding)",
            "final_authority": "Designated procurement officers and the Tender Committee; no bid is auto-awarded",
            "retrieved_passages": [
                {"id": p["id"], "title": p["title"], "score": p.get("score"),
                 "matched_on": p.get("matched_on", [])}
                for p in retrieved
            ],
        },
    }


def _remedy_for(rule: dict) -> Optional[str]:
    return _remedy_for_impl(rule)


def remedy_for(rule: dict) -> Optional[str]:
    """Public alias used by the pipeline officer summary."""
    return _remedy_for_impl(rule)


def _remedy_for_impl(rule: dict) -> Optional[str]:
    ct = rule.get("condition_type")
    label = rule.get("label") or "the requirement"
    if ct == "has_document":
        return f"Submit the missing mandatory document ({label}) to the document vault and link it to this application."
    if ct in ("is_active", "registration_required", "valid_unexpired"):
        return f"Renew/reactivate the lapsed registration or certificate so that {label} is active at the official source, then request re-verification."
    if ct == "min_turnover":
        return "Provide certified turnover evidence (ITR/GST portal data) demonstrating the required minimum, or correct the declared figure."
    if ct == "filing_current":
        return "Regularise the pending statutory filings and provide proof of current filing status."
    if ct == "min_local_content":
        return "Declare the actual local-content percentage with supporting evidence meeting the tender threshold."
    if ct in ("name_match", "names_consistent"):
        return "Reconcile the legal name across profile and source records; if the name changed legally, submit the name-change/correction certificate."
    if ct in ("pan_gst_match", "gst_state_match", "gstin_checksum_valid"):
        return "Verify the GSTIN/PAN combination against the GST portal and correct any transcription error in the profile."
    if ct == "not_blacklisted":
        return "Debarment findings cannot be cured by clarification; any exception requires the Override Accountability Engine with full accountability chain."
    if rule.get("mandatory"):
        return f"Provide corrected evidence satisfying {label} and request re-verification."
    return None


# ------------------------------- narrative layer ----------------------------


def _en_narrative(evaluation: dict, s: dict) -> str:
    """Officer-first explanation: verdict, the exact findings that need
    action (each with the concrete fix), what passed, document state, and
    the single recommended action. Precise and skimmable \u2014 no filler."""
    es = s["executive_summary"]
    rr = evaluation.get("rule_results") or []
    fails = [r for r in rr if r["result"] == "FAIL"]
    unables = [r for r in rr if r["result"] == "UNABLE_TO_VERIFY"]
    blacklist = evaluation.get("blacklist_findings") or []

    if blacklist:
        verdict = "NON-COMPLIANT \u2014 DEBARRED BIDDER"
    elif es["eligibility_result"] in ("NOT_ELIGIBLE", "BLACKLISTED") or (fails and not unables):
        verdict = "NON-COMPLIANT"
    elif unables or [r for r in fails if r.get("allows_manual_review")]:
        verdict = "MANUAL REVIEW REQUIRED"
    elif fails:
        verdict = "NON-COMPLIANT"
    else:
        verdict = "COMPLIANCE VERIFIED"

    lines = [
        f"VERDICT: {verdict} \u2014 Score {es['compliance_score']}/100, Risk {es['risk_level']}. "
        f"{es['counts']['passed']} of {sum(es['counts'].values())} checks passed; "
        f"{len(fails)} failed, {len(unables)} unverified."
    ]
    if blacklist:
        b = blacklist[0]
        lines.append(
            f"DEBARMENT: {b.get('entity_name')} is on the debarment register "
            f"({b.get('source_authority')}; order {b.get('official_order_reference')}). "
            f"Reason: {b.get('reason')}. The bid cannot proceed; any exception requires the "
            f"Override Accountability Engine."
        )
    action_items = []
    for r in fails:
        cite = f" [{r.get('clause_ref')}]" if r.get("clause_ref") else ""
        action_items.append(
            f"- {r['label']}{cite} \u2014 FAILED: {r.get('message') or 'n/a'}. "
            f"Fix: {remedy_for(r) or 'Provide corrected evidence and request re-verification.'}"
        )
    for r in unables:
        cite = f" [{r.get('clause_ref')}]" if r.get("clause_ref") else ""
        action_items.append(
            f"- {r['label']}{cite} \u2014 UNVERIFIED: {r.get('message') or 'source unavailable'}. "
            f"Treat as NOT passed. Fix: {remedy_for(r) or 'restore source availability or verify offline, then re-run verification.'}"
        )
    if action_items:
        lines.append("FINDINGS NEEDING ACTION:")
        lines.extend(action_items)
    passed = [r["label"] for r in rr if r["result"] == "PASS"]
    if passed:
        lines.append("CHECKS PASSED: " + "; ".join(passed) + ".")
    if s["cross_verification"]:
        mm = "; ".join(f"{m['field']}: declared {m['bidder_value']} vs source {m['source_value']} ({m['source']})"
                       for m in s["cross_verification"])
        lines.append(f"MISMATCHES: {mm}.")
    if evaluation.get("documents_summary"):
        lines.append(f"DOCUMENTS: {evaluation['documents_summary']}.")
    rec = evaluation.get("recommendation") or _recommended_action(blacklist, fails, unables)
    lines.append(f"RECOMMENDED ACTION: {rec}")
    lines.append(
        "Note: the deterministic rule engine decides; this explanation makes the decision "
        "readable and does not alter it. Final authority rests with the Competent Authority "
        "of the procuring organization."
    )
    return "\n".join(lines)


def _recommended_action(blacklist: list, fails: list, unables: list) -> str:
    if blacklist:
        return "REJECT at screening — the bidder is on the debarment register; any exception " \
               "requires the Override Accountability Engine with Admin visibility."
    if fails:
        return "Request the corrective evidence listed above from the bidder and re-run " \
               "verification before the bid proceeds to evaluation."
    if unables:
        return "Resolve the unverified source(s) (re-run verification once available, or verify " \
               "offline with documented evidence) before the bid proceeds."
    return "No findings requiring action — the application may proceed to the next pipeline stage."


def _hi_narrative(evaluation: dict, s: dict) -> str:
    es = s["executive_summary"]
    parts = [
        f"सत्यापन परिणाम। निर्धारित नियम इंजन ने इस आवेदन का टेंडर नियम-समूह के विरुद्ध मूल्यांकन किया: "
        f"अनुपालता स्कोर {es['compliance_score']}/100, जोखिम स्तर {es['risk_level']} ({es['risk_label']}), "
        f"पात्रता परिणाम {es['eligibility_result']}।"
    ]
    if s["basis_of_decision"]:
        lines = []
        for b in s["basis_of_decision"]:
            cite = f" [प्रवर्तन: {b['clause_ref']}]" if b.get("clause_ref") else ""
            lines.append(f"- {b['rule']}{cite}: {b['evidence']['message'] or 'n/a'} — {b['impact']}।")
        parts.append("निर्णय का आधार। " + " ".join(lines))
    if s["cross_verification"]:
        mm = "; ".join(f"{m['field']}: घोषित {m['bidder_value']} बनाम स्रोत {m['source_value']} ({m['source']})"
                       for m in s["cross_verification"])
        parts.append(f"क्रॉस-सत्यापन। {mm}।")
    if s["remediation"]:
        rm = " ".join(f"({i + 1}) {x['action']} " for i, x in enumerate(s["remediation"]))
        parts.append(f"अनुशंसित सुधार। {rm}")
    parts.append(
        "शासन। यह व्याख्या निर्धारित निर्णय की निरीक्षण-योग्यता के लिए तैयार की गई है; AI केवल व्याख्या करता है, "
        "परिणाम बदलता नहीं है। अंतिम पात्रता प्राधिकरण निर्दिष्ट क्रय अधिकारियों और टेंडर समिति के पास है।"
    )
    return " ".join(parts)


def _gemini_narrative(evaluation: dict, s: dict, language: str) -> Optional[str]:
    """Optional LLM narrative — grounded strictly on the RAG passages."""
    lang = "Hindi" if language == "hi" else "English"
    prompt = (
        f"You are a compliance explanation writer for a government procurement decision-support "
        f"system. The deterministic rule engine has ALREADY decided the outcome. Write a "
        f"professional {lang} narrative (max 250 words) explaining the decision to a reviewing "
        f"officer, in this structure: (1) Outcome; (2) Basis of decision — cite clause refs "
        f"exactly as given; (3) Evidence highlights; (4) Recommended remediation; (5) Governance "
        f"note. You may ONLY cite clauses from the provided passages or those present in the "
        f"findings. Do not change, soften, or override any finding.\n"
        f"STRUCTURED FINDINGS (JSON): {json.dumps({k: s[k] for k in ('executive_summary', 'basis_of_decision', 'cross_verification', 'document_findings', 'remediation')}, default=str, ensure_ascii=False)}\n"
        f"RETRIEVED PASSAGES (cite only these): {json.dumps([{ 'title': p['title'], 'refs': p.get('refs', []), 'text': p['text']} for p in evaluation.get('_retrieved', [])], ensure_ascii=False)}"
    )
    return gemini._call([], prompt)


# ------------------------------- public API ---------------------------------


def explain_verification(evaluation: dict, language: str = "en") -> tuple[str, str, dict]:
    """Returns (narrative_text, model_identifier, structured_explanation).

    Backward-compatible callers can ignore the third value. The narrative
    passes through the citation guard (anti-hallucination).
    """
    eval_copy = dict(evaluation)
    retrieved = rag.retrieve_for(evaluation)
    eval_copy["_retrieved"] = retrieved
    structured = structured_explanation(eval_copy, language)
    structured.pop("_retrieved", None)

    text = None
    model_id = MODEL_FALLBACK
    if gemini._available():
        text = _gemini_narrative(eval_copy, structured, language)
        if text:
            model_id = f"gemini:{gemini.settings.GEMINI_MODEL}"
    if not text:
        text = _hi_narrative(eval_copy, structured) if language == "hi" else _en_narrative(eval_copy, structured)
    kb_refs = [r for p in retrieved for r in (p.get("refs") or [])]
    text, _meta = sanitize_explanation(text, evaluation, model_id=model_id, allowed_refs=kb_refs)
    return text, model_id, structured
