"""Anti-hallucination citation guard for AI explanations.

The deterministic rule engine decides; the AI only explains. To keep the
explainer honest, any *statutory citation* it emits must either
  (a) name a rule that actually exists in the active evaluation, or
  (b) name a well-known Indian statutory instrument from a static whitelist.
Anything else is removed at sentence granularity and the removal is noted.

The deterministic template explainer passes through unchanged (it only cites
real rule labels), so the demo works with all cloud AI disabled.
"""
import re
from typing import Optional

# Well-known Indian statutory instruments — always legitimate to cite.
STATUTORY_WHITELIST = [
    "gfr", "government fiscal rules", "gem guidelines", "gem procurement",
    "cvc", "central vigilance commission", "vigilance",
    "it act", "information technology act", "section 65b", "65b",
    "dpdp", "digital personal data protection",
    "msme", "msme development act", "udyam",
    "gst act", "cgst", "sgst", "igst", "goods and services tax",
    "income tax act", "income-tax",
    "companies act", "mca", "minister of corporate affairs",
    "epf act", "employees provident fund", "epfo",
    "esic", "employees state insurance",
    "bis", "bureau of indian standards",
    "startup india", "nsic", "foreign trade", "make in india",
    "local content", "rti", "right to information",
    "arbitration and conciliation act", "adr",
    "pan", "gst in", "gstin",
]

_RULE_CITE_RE = re.compile(r"\bRule\s*[:#]?\s*([A-Za-z][A-Za-z0-9_\- ]{2,70})", re.IGNORECASE)
# A citation requires either an explicit delimiter ("Clause: X") or a
# capitalized/numeric reference after whitespace ("Section 7.1", "Rule 12").
# This keeps prose like "the rule set was applied" from being treated as a
# citation (the capture must start uppercase or with a digit).
_SECTION_CITE_COLON = re.compile(
    r"\b(?:Section|Clause|Rule)\s*[:#]\s*([A-Za-z0-9§][A-Za-z0-9§\.\-/ ]{1,60})", re.IGNORECASE
)
_SECTION_CITE_WORD = re.compile(
    r"\b(?:Section|Clause|Rule)\s+([A-Z0-9][A-Za-z0-9§\.\-/ ]{1,60})"
)
_SENTENCE_RE = re.compile(r"(\n|(?<=[.!?])\s+)")


def _citation_whitelisted(citation: str) -> bool:
    c = citation.lower().strip(" .,;:")
    return any(w in c or c in w for w in STATUTORY_WHITELIST)


def _citation_matches_rules(citation: str, allowed_labels: set[str]) -> bool:
    c = citation.lower().strip(" .,;:")
    if not c:
        return True  # nothing to check
    for label in allowed_labels:
        if c == label or c in label or label in c:
            return True
    return False


def sanitize_explanation(text: Optional[str], evaluation: Optional[dict],
                         model_id: str = "", allowed_refs: Optional[list[str]] = None) -> tuple[str, dict]:
    """Returns (sanitized_text, guard_meta).

    A citation is kept when it (a) matches a real rule label of the active
    evaluation, (b) matches the static statutory whitelist, or (c) matches a
    clause ref from the RAG knowledge base (`allowed_refs` — grounded
    citations). Anything else is removed at sentence granularity.

    guard_meta: {removed_citations: [..], sentences_removed: n, mode: "applied"|"template-safe"}
    """
    meta = {"removed_citations": [], "sentences_removed": 0, "mode": "none"}
    if not text:
        return text, meta

    # Template (deterministic) explainer: structurally safe — still scan, but
    # it only cites real rule labels, so expect zero removals.
    allowed_labels = set()
    if evaluation:
        for r in evaluation.get("rule_results", []) or []:
            if r.get("label"):
                allowed_labels.add(str(r["label"]).lower())
    kb_refs = {str(r).lower() for r in (allowed_refs or []) if r}

    # Split into sentences (keeping the separators, so the line structure of
    # the skimmable officer narrative survives sanitisation); drop any
    # sentence containing an unverifiable citation.
    pieces = _SENTENCE_RE.split(text.strip())
    # pieces is [s0, sep0, s1, sep1, ...] — pair each sentence with its separator.
    pairs: list[tuple[str, str]] = []
    for i in range(0, len(pieces) - 1, 2):
        pairs.append((pieces[i], pieces[i + 1]))
    if len(pieces) % 2 == 1 and pieces[-1].strip():
        pairs.append((pieces[-1], ""))
    kept: list[str] = []
    for s, sep in pairs:
        bad = []
        for pat in (_SECTION_CITE_COLON, _SECTION_CITE_WORD):
            for m in pat.finditer(s):
                cite = m.group(1).strip()
                if _citation_whitelisted(cite):
                    continue
                if _citation_matches_rules(cite, allowed_labels):
                    continue
                if kb_refs:
                    c = cite.lower().strip(" .,;:")
                    if any(ref == c or ref in c or c in ref for ref in kb_refs):
                        continue
                bad.append(cite)
        if bad:
            meta["removed_citations"].extend(bad)
            meta["sentences_removed"] += 1
            continue
        kept.append(s + sep)

    cleaned = "".join(kept).strip()
    if not cleaned:
        cleaned = text  # never return an empty explanation
        meta["removed_citations"] = []
        meta["sentences_removed"] = 0

    if meta["removed_citations"]:
        cleaned = (
            cleaned + " (Citation guard: "
            f"{meta['sentences_removed']} sentence(s) with unverifiable citation(s) "
            f"removed: {', '.join(sorted(set(meta['removed_citations']))[:5])}.)"
        )
        meta["mode"] = "applied"
    else:
        meta["mode"] = "template-safe" if "deterministic" in model_id else "clean"
    return cleaned, meta
