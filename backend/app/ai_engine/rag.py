"""Deterministic RAG retrieval over the local knowledge base.

No external embeddings or API calls — scoring is transparent and reproducible
(which is itself part of explainability: every retrieved passage carries the
reasons it was retrieved). The citation guard later verifies that any
citation in the generated explanation matches a retrieved passage's refs or a
whitelisted instrument, so hallucinated citations are stripped.
"""
import re
from collections import defaultdict

from . import knowledge_base as kb

_TOKEN = re.compile(r"[a-z0-9§]{3,}")

STOP = {
    "the", "and", "for", "with", "from", "that", "this", "must", "shall",
    "where", "when", "has", "are", "was", "were", "not", "any", "all",
    "bidder", "bidders", "bid", "tender", "status", "result", "record",
    "recorded", "value", "values", "check", "checks", "document",
    "documents", "source", "sources", "data", "based", "each", "per",
}


def _tokens(s: str) -> set[str]:
    return {t for t in _TOKEN.findall((s or "").lower()) if t not in STOP}


def _clause_matches(ref: str, passage_refs: list[str]) -> bool:
    """Exact or family-prefix match: 'GeM GTC §2.1' matches passage
    refs ['GeM GTC §2.1'] and 'GeM GTC §2.1(b)' matches both."""
    ref = (ref or "").strip()
    if not ref:
        return False
    for pr in passage_refs:
        if ref == pr or ref.startswith(pr) or pr.startswith(ref):
            return True
    return False


def retrieve_for(evaluation: dict, k: int = 8) -> list[dict]:
    """Retrieve the top-k KB passages grounding this evaluation.

    Returns passages augmented with `score` and `matched_on` (why retrieved) —
    surfaced in the report's governance/evidence sections for auditability.
    """
    scores: dict[str, float] = defaultdict(float)
    matched: dict[str, list[str]] = defaultdict(list)

    def bump(pid: str, pts: float, why: str) -> None:
        scores[pid] += pts
        matched[pid].append(why)

    rules = evaluation.get("rule_results") or []
    for r in rules:
        if r.get("result") not in ("FAIL", "UNABLE_TO_VERIFY"):
            continue
        clause = r.get("clause_ref") or ""
        ctype = r.get("condition_type") or ""
        label = f"{r.get('label', '')} {r.get('message', '')}"
        ltoks = _tokens(label)
        for p in kb.PASSAGES:
            if clause and _clause_matches(clause, p["refs"]):
                bump(p["id"], 4.0, f"clause_ref:{clause}")
            if ctype and ctype in p["applies_to"]:
                bump(p["id"], 3.0, f"condition:{ctype}")
            overlap = ltoks & _tokens(p["title"])
            if len(overlap) >= 2:
                bump(p["id"], 1.5, f"topic-terms:{','.join(sorted(overlap)[:3])}")

    # topic triggers from the evaluation as a whole
    if evaluation.get("blacklist_findings"):
        bump("integrity-7-1", 4.0, "blacklist-finding")
    if evaluation.get("mismatches"):
        bump("topic-mismatch", 4.0, "cross-verification-mismatches")
    if evaluation.get("missing_documents"):
        bump("atc-2", 3.0, "missing-mandatory-documents")
    if evaluation.get("expired_documents"):
        bump("spec-3-2", 3.0, "expired-documents")
    unables = [r for r in rules if r.get("result") == "UNABLE_TO_VERIFY"]
    if unables:
        bump("topic-source-unavailable", 4.0, f"{len(unables)}-unverified-checks")
    fails = [r for r in rules if r.get("result") == "FAIL"]
    if fails:
        bump("topic-remedy", 2.5, f"{len(fails)}-confirmed-failures")
    auth = evaluation.get("authenticity_summary") or []
    if any(a.get("status") == "SUSPICIOUS" for a in auth):
        bump("topic-doc-integrity", 3.5, "suspicious-document-finding")
    # governance grounding is always present (every explanation cites it)
    bump("topic-governance", 1.0, "governance-always")

    ranked = sorted(scores.items(), key=lambda kv: -kv[1])[:k]
    out = []
    for pid, score in ranked:
        p = kb.get_passage(pid)
        if p is None:
            continue
        out.append({
            **p,
            "score": round(score, 2),
            "matched_on": sorted(set(matched[pid])),
        })
    return out
