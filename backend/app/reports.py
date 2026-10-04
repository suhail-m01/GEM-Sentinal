"""Compliance report rendering: professional multi-section PDF (reportlab).

The PDF is the ONLY downloadable report format (per spec). It embeds the full
Explainable-AI explanation: executive summary, basis of decision with clause
citations, cross-verification, document findings, evidence trail, remediation,
the full narrative, citations (grounding passages), and governance.

Fonts: DejaVu Sans (Latin/symbols) + Noto Sans Devanagari (Hindi reports).
"""
import json
from datetime import datetime
from io import BytesIO

from .scoring import eligibility_label, risk_label

_FONT_READY = False
_HAS_DEVANAGARI = False


def _register_fonts() -> None:
    global _FONT_READY, _HAS_DEVANAGARI
    if _FONT_READY:
        return
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    def reg(name, path):
        try:
            pdfmetrics.registerFont(TTFont(name, path))
        except Exception:
            pass

    reg("DVS", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    reg("DVS-Bold", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
    _HAS_DEVANAGARI = False
    try:
        pdfmetrics.registerFont(TTFont("Dev", "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf"))
        pdfmetrics.registerFont(TTFont("Dev-Bold", "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf"))
        _HAS_DEVANAGARI = True
    except Exception:
        pass
    _FONT_READY = True


def _fonts(language: str) -> tuple[str, str]:
    """(regular, bold) font names for the report language."""
    if language == "hi" and _HAS_DEVANAGARI:
        return "Dev", "Dev-Bold"
    return "DVS", "DVS-Bold"


def _esc(s) -> str:
    return (
        str(s if s is not None else "—")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def report_to_pdf(report, application, tender, bidder_name) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        BaseDocTemplate, Frame, HRFlowable, PageBreak, PageTemplate, Paragraph, Spacer, Table, TableStyle,
    )

    _register_fonts()
    lang = report.ai_explanation_language or "en"
    fnt, fnt_b = _fonts(lang)
    det = report.explanation_detail or {}

    NAVY = colors.HexColor("#0b2545")
    SLATE = colors.HexColor("#475569")
    GREEN = colors.HexColor("#166534")
    RED = colors.HexColor("#b91c1c")
    AMBER = colors.HexColor("#b45309")
    LINE = colors.HexColor("#cbd5e1")

    S = {
        "title": ParagraphStyle("t", fontName=fnt_b, fontSize=15, leading=19, textColor=NAVY),
        "meta": ParagraphStyle("m", fontName=fnt, fontSize=8, leading=11, textColor=SLATE),
        "h2": ParagraphStyle("h2", fontName=fnt_b, fontSize=11, leading=14, textColor=NAVY, spaceBefore=10, spaceAfter=4),
        "body": ParagraphStyle("b", fontName=fnt, fontSize=9, leading=13),
        "small": ParagraphStyle("s", fontName=fnt, fontSize=8, leading=11, textColor=SLATE),
        "bullet": ParagraphStyle("bl", fontName=fnt, fontSize=9, leading=13, leftIndent=10, bulletIndent=2),
    }
    T = ParagraphStyle("th", fontName=fnt_b, fontSize=8, leading=10, textColor=colors.white)
    TD = ParagraphStyle("td", fontName=fnt, fontSize=8, leading=11)
    TDB = ParagraphStyle("tdb", fontName=fnt_b, fontSize=8, leading=11)

    def P(text, style="body"):
        return Paragraph(_esc(text), S[style])

    def TP(text, style=TD):
        return Paragraph(_esc(text), style)

    story = []

    # ---- header ----------------------------------------------------------
    story.append(P("GeM-Sentinel — Bid Compliance Verification Report", "title"))
    story.append(Spacer(1, 2 * mm))
    story.append(P(
        f"Report {report.id}  ·  Run #{report.run_no}  ·  Generated {report.generated_at:%Y-%m-%d %H:%M UTC}  ·  "
        f"Model: {report.model_identifier or 'n/a'}  ·  Report version {report.report_version}", "meta"))
    story.append(P(
        f"Application: {application.application_no}   ·   Tender: {tender.tender_no} — {tender.title}   ·   "
        f"Company: {bidder_name}   ·   Bidder: {application.bidder_user.full_name}", "meta"))
    story.append(Spacer(1, 3 * mm))

    # ---- score box ---------------------------------------------------------
    rcol = {"LOW": GREEN, "MEDIUM": AMBER, "HIGH": RED, "CRITICAL": colors.HexColor("#7f1d45")}.get(report.risk_level, SLATE)
    score_tbl = Table(
        [[
            Paragraph(f"Compliance Score", T), Paragraph(f"Risk Level", T), Paragraph(f"Eligibility", T),
        ], [
            Paragraph(_esc(f"{report.compliance_score} / 100"), ParagraphStyle("sc", fontName=fnt_b, fontSize=14, leading=17)),
            Paragraph(_esc(risk_label(report.risk_level)), ParagraphStyle("rk", fontName=fnt_b, fontSize=11, leading=14, textColor=rcol)),
            Paragraph(_esc(eligibility_label(report.eligibility_result)), ParagraphStyle("el", fontName=fnt_b, fontSize=11, leading=14)),
        ]],
        colWidths=[60 * mm, 60 * mm, 60 * mm],
    )
    score_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("BOX", (0, 0), (-1, -1), 0.8, NAVY),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(score_tbl)

    es = det.get("executive_summary") or {}
    counts = es.get("counts") or {}
    # ---- executive summary -------------------------------------------------
    story.append(P("1. Executive Summary", "h2"))
    for h in es.get("headline") or [f"Eligibility result: {report.eligibility_result}"]:
        story.append(Paragraph(_esc(h), S["bullet"], bulletText="•"))
    if counts:
        story.append(P(
            f"Checks evaluated: {sum(counts.values())}  ·  Passed: {counts.get('passed', 0)}  ·  "
            f"Failed: {counts.get('failed', 0)}  ·  Unverified (manual review): {counts.get('unverified', 0)}  ·  "
            f"Not applicable: {counts.get('not_applicable', 0)}", "small"))

    # ---- basis of decision --------------------------------------------------
    basis = det.get("basis_of_decision")
    if basis is None:  # legacy report without structured detail
        basis = [
            {"rule": r.get("label"), "result": r.get("result"), "clause_ref": r.get("clause_ref"),
             "evidence": {"message": r.get("message")}, "weight": r.get("weight"),
             "mandatory": r.get("mandatory")}
            for r in (report.rule_results or [])
            if r.get("result") in ("FAIL", "UNABLE_TO_VERIFY")
        ]
    if basis:
        story.append(P("2. Basis of Decision (confirmed failures & unverifiable checks)", "h2"))
        rows = [[TP("Rule", T), TP("Clause Ref", T), TP("Result", T), TP("Evidence (source finding)", T), TP("Wt", T)]]
        for b in basis:
            res = b.get("result")
            rcol2 = RED if res == "FAIL" else AMBER
            rows.append([
                TP(f"{b.get('rule') or '—'}{'  [mandatory]' if b.get('mandatory') else ''}", TDB),
                TP(b.get("clause_ref")),
                Paragraph(_esc(res), ParagraphStyle("r", fontName=fnt_b, fontSize=8, leading=11, textColor=rcol2)),
                TP((b.get("evidence") or {}).get("message")),
                TP(str(b.get("weight")) if b.get("weight") is not None else "—"),
            ])
        tb = Table(rows, colWidths=[46 * mm, 26 * mm, 24 * mm, 72 * mm, 12 * mm], repeatRows=1)
        tb.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("BOX", (0, 0), (-1, -1), 0.6, LINE),
            ("INNERGRID", (0, 0), (-1, -1), 0.3, LINE),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ]))
        story.append(tb)

    # ---- statutory basis (clause language actually cited) --------------------
    statutory = [b for b in (basis or []) if b.get("statutory_basis")]
    if statutory:
        story.append(P("3. Statutory Basis (grounding clause language)", "h2"))
        for b in statutory:
            story.append(Paragraph(
                f"<b>{_esc(b.get('clause_ref') or b.get('rule'))}:</b> {_esc(b.get('statutory_basis'))}", S["small"]))

    # ---- cross-verification --------------------------------------------------
    xval = det.get("cross_verification") if det.get("cross_verification") is not None else (
        [{"field": m.get("field"), "bidder_value": m.get("bidder_value"),
          "source_value": m.get("source_value"), "source": m.get("source")}
         for m in (report.mismatches or [])])
    if xval:
        story.append(P("4. Cross-Verification Mismatches (bidder declaration vs official source)", "h2"))
        rows = [[TP("Field", T), TP("Bidder Value", T), TP("Source Value", T), TP("Source", T)]]
        for m in xval:
            rows.append([TP(m.get("field")), TP(m.get("bidder_value")), TP(m.get("source_value")), TP(m.get("source"))])
        tb = Table(rows, colWidths=[38 * mm, 54 * mm, 54 * mm, 30 * mm], repeatRows=1)
        tb.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("BOX", (0, 0), (-1, -1), 0.6, LINE),
            ("INNERGRID", (0, 0), (-1, -1), 0.3, LINE),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ]))
        story.append(tb)

    # ---- document findings ----------------------------------------------------
    dfind = det.get("document_findings")
    if dfind is None:
        dfind = (
            [{"document": n, "finding": "MISSING", "detail": "Mandatory document not present."}
             for n in (report.missing_documents or [])]
            + [{"document": d.get("name"), "finding": "EXPIRED", "detail": "Document validity ended."}
               for d in (report.expired_documents or [])]
        )
    if dfind:
        story.append(P("5. Document Findings", "h2"))
        for d in dfind:
            story.append(Paragraph(f"<b>{_esc(d.get('document'))}</b> — {_esc(d.get('finding'))}: {_esc(d.get('detail'))}", S["small"]))

    # ---- evidence trail ---------------------------------------------------------
    trail = det.get("evidence_trail")
    if trail is None:
        trail = [{"source": s, "status": v.get("status"), "retrieved_at": v.get("retrieved_at"),
                  "cached": bool((v.get("data") or {}).get("_cached")), "message": v.get("message")}
                 for s, v in (report.source_results or {}).items()]
    if trail:
        story.append(P("6. Evidence Trail (government sources consulted)", "h2"))
        rows = [[TP("Source", T), TP("Status", T), TP("Retrieved (UTC)", T), TP("Cached", T), TP("Source Message", T)]]
        for e in trail:
            st = e.get("status")
            scol = RED if st in ("UNAVAILABLE", "ERROR", "BLACKLIST") else (GREEN if st == "VALID" else SLATE)
            rows.append([
                TP(e.get("source"), TDB),
                Paragraph(_esc(st), ParagraphStyle("st", fontName=fnt_b, fontSize=8, leading=11, textColor=scol)),
                TP((e.get("retrieved_at") or "—")[:19].replace("T", " ")),
                TP("yes" if e.get("cached") else "live"),
                TP(e.get("message")),
            ])
        tb = Table(rows, colWidths=[24 * mm, 26 * mm, 34 * mm, 16 * mm, 64 * mm], repeatRows=1)
        tb.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("BOX", (0, 0), (-1, -1), 0.6, LINE),
            ("INNERGRID", (0, 0), (-1, -1), 0.3, LINE),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ]))
        story.append(tb)

    # ---- blacklist findings (always visible if present) ---------------------------
    if report.blacklist_findings:
        story.append(P("Blacklist / Debarment Findings", "h2"))
        for b in report.blacklist_findings:
            story.append(Paragraph(
                f"<b>{_esc(b.get('entity_name'))}</b> — matched on {_esc(b.get('matched_on'))}; "
                f"authority: {_esc(b.get('source_authority'))}; order: {_esc(b.get('official_order_reference'))}",
                ParagraphStyle("bl2", parent=S["small"], textColor=colors.HexColor("#7f1d45"))))

    # ---- remediation ---------------------------------------------------------------
    rem = det.get("remediation")
    if rem:
        story.append(P("7. Recommended Remediation", "h2"))
        for i, x in enumerate(rem, 1):
            story.append(Paragraph(f"({i}) {_esc(x.get('action'))}", S["bullet"]))

    # ---- narrative -------------------------------------------------------------------
    if report.ai_explanation:
        story.append(P("8. AI Explanation (advisory narrative)", "h2"))
        story.append(Paragraph(_esc(report.ai_explanation), S["body"]))

    # ---- citations ----------------------------------------------------------------------
    cites = det.get("citations") or []
    if cites:
        story.append(P("9. Citations (RAG-grounded knowledge-base passages)", "h2"))
        for c in cites:
            refs = f"  [{_esc(', '.join(c.get('refs') or []))}]" if c.get("refs") else ""
            story.append(Paragraph(f"• {_esc(c.get('title'))}{refs}", S["small"]))

    # ---- governance ---------------------------------------------------------------------
    gov = det.get("governance") or {}
    if gov:
        story.append(P("10. Governance & Limitations", "h2"))
        for k in ("decided_by", "ai_role", "retrieval", "final_authority"):
            if gov.get(k):
                story.append(Paragraph(f"• <b>{_esc(k.replace('_', ' ').title())}:</b> {_esc(gov[k])}", S["small"]))

    # ---- recommendation + footer -----------------------------------------------------------
    story.append(Spacer(1, 4 * mm))
    story.append(HRFlowable(width="100%", thickness=0.8, color=NAVY))
    story.append(Spacer(1, 2 * mm))
    story.append(P(f"Recommendation: {_esc(report.recommendation or 'n/a')}", "body"))
    story.append(Spacer(1, 2 * mm))
    story.append(P(
        "Decision support system. The verification outcome was decided exclusively by the deterministic "
        "rule engine; the AI component explains only. Final qualification authority rests with the "
        "designated procurement officers and the Tender Committee. No bid is auto-awarded.", "small"))

    # ---- page template with footer page numbers -----------------------------------------------
    w, h = A4
    buf = BytesIO()
    doc = BaseDocTemplate(buf, pagesize=A4,
                          leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=18 * mm,
                          title=f"Verification Report {application.application_no}")

    def _footer(canvas, _doc):
        canvas.saveState()
        canvas.setFont(fnt, 7.5)
        canvas.setFillColor(SLATE)
        canvas.drawString(18 * mm, 10 * mm,
                          f"GeM-Sentinel · {application.application_no} · {tender.tender_no} · Report {report.id}")
        canvas.drawRightString(w - 18 * mm, 10 * mm, f"Page {_doc.page}")
        canvas.setStrokeColor(LINE)
        canvas.line(18 * mm, 13 * mm, w - 18 * mm, 13 * mm)
        canvas.restoreState()

    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="main")
    doc.addPageTemplates([PageTemplate(id="all", frames=[frame], onPage=_footer)])
    doc.build(story)
    return buf.getvalue()


def rejection_to_pdf(application, tender, data: dict) -> bytes:
    """Bidder-facing rejection explanation (PDF only).

    Contains only what is legitimately shareable with the bidder: the decision
    date, the deciding authority's recorded remarks (sanitized), and — when no
    remarks were recorded — the mandatory compliance checks that failed, which
    relate to the bidder's own declarations and documents.
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        BaseDocTemplate, Frame, HRFlowable, PageTemplate, Paragraph, Spacer,
    )

    _register_fonts()
    lang = "hi" if any("\u0900" <= ch <= "\u097F" for ch in (data.get("explanation") or "")) else "en"
    fnt, fnt_b = _fonts(lang)
    NAVY = colors.HexColor("#0b2545")
    SLATE = colors.HexColor("#475569")
    LINE = colors.HexColor("#cbd5e1")

    S = {
        "title": ParagraphStyle("t", fontName=fnt_b, fontSize=15, leading=19, textColor=NAVY),
        "meta": ParagraphStyle("m", fontName=fnt, fontSize=9, leading=12, textColor=SLATE),
        "body": ParagraphStyle("b", fontName=fnt, fontSize=10, leading=15),
        "small": ParagraphStyle("s", fontName=fnt, fontSize=8.5, leading=12, textColor=SLATE),
        "h2": ParagraphStyle("h2", fontName=fnt_b, fontSize=11, leading=14, textColor=NAVY, spaceBefore=10, spaceAfter=4),
    }

    story = [
        Paragraph(_esc("GeM-Sentinel — Application Outcome Explanation"), S["title"]),
        Spacer(1, 3 * mm),
        Paragraph(
            _esc(f"Application: {application.application_no}   ·   Tender: {tender.tender_no} — {tender.title}   ·   "
                 f"Decision recorded: {data.get('rejected_at_human') or '—'}"),
            S["meta"]),
        Spacer(1, 2 * mm),
        Paragraph(
            _esc(f"Outcome: REJECTED   ·   Decided by: {data.get('decided_by') or 'Evaluation committee'}"),
            ParagraphStyle("oc", fontName=fnt_b, fontSize=10.5, leading=14, textColor=colors.HexColor("#b91c1c"))),
        Spacer(1, 4 * mm),
        Paragraph(_esc("Reason for rejection"), S["h2"]),
        Paragraph(_esc(data.get("explanation") or "—"), S["body"]),
        Spacer(1, 6 * mm),
        HRFlowable(width="100%", thickness=0.8, color=NAVY),
        Spacer(1, 2 * mm),
        Paragraph(
            _esc(data.get("appeal_note") or
                 "If you believe this outcome is in error, you may raise a grievance/appeal through the portal's "
                 "grievance facility. The evaluation committee's recorded reasons are the official explanation for "
                 "this outcome."),
            S["small"]),
        Spacer(1, 2 * mm),
        Paragraph(
            _esc("Decision support system. This document records the evaluation committee's recorded reasons; "
                 "final qualification authority rests with the designated procurement authorities."),
            S["small"]),
    ]

    from io import BytesIO

    w, h = A4
    buf = BytesIO()
    doc = BaseDocTemplate(buf, pagesize=A4,
                          leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=18 * mm,
                          title=f"Rejection explanation {application.application_no}")

    def _footer(canvas, _doc):
        canvas.saveState()
        canvas.setFont(fnt, 7.5)
        canvas.setFillColor(SLATE)
        canvas.drawString(18 * mm, 10 * mm,
                          f"GeM-Sentinel · {application.application_no} · {tender.tender_no}")
        canvas.drawRightString(w - 18 * mm, 10 * mm, f"Page {_doc.page}")
        canvas.setStrokeColor(LINE)
        canvas.line(18 * mm, 13 * mm, w - 18 * mm, 13 * mm)
        canvas.restoreState()

    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="main")
    doc.addPageTemplates([PageTemplate(id="all", frames=[frame], onPage=_footer)])
    doc.build(story)
    return buf.getvalue()


def report_to_html(report, application, tender, bidder_name) -> str:
    """Legacy HTML rendering (internal/debug only — the downloadable format
    is PDF exclusively)."""
    def esc(s):
        return (
            str(s if s is not None else "—")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    rows = ""
    for r in report.rule_results or []:
        color = {"PASS": "#16a34a", "FAIL": "#dc2626", "UNABLE_TO_VERIFY": "#d97706"}.get(r["result"], "#334155")
        rows += f"<tr><td style='color:{color};font-weight:600'>{esc(r['result'])}</td><td>{esc(r['label'])}</td><td>{esc(r['message'])}</td></tr>"
    cross = ""
    for c in report.cross_verification or []:
        color = {"MATCH": "#16a34a", "MISMATCH": "#dc2626"}.get(c["result"], "#d97706")
        cross += f"<tr><td>{esc(c['field'])}</td><td>{esc(c['bidder_value'])}</td><td>{esc(c['source_value'])}</td><td style='color:{color};font-weight:600'>{esc(c['result'])}</td></tr>"
    auth = ""
    for a in report.authenticity or []:
        auth += (
            f"<tr><td>{esc(a.get('document_name'))}</td><td>{esc(a.get('status'))}</td>"
            f"<td>{esc('; '.join(a.get('reasons', [])[:3]))}</td></tr>"
        )
    return f"""<!doctype html><html><head><meta charset='utf-8'><title>Verification Report {esc(report.id)}</title>
<style>body{{font-family:Inter,system-ui,sans-serif;margin:32px;color:#0f172a;max-width:900px}}
h1{{color:#0b2545;font-size:22px}}table{{border-collapse:collapse;width:100%;margin:12px 0}}
td,th{{border:1px solid #cbd5e1;padding:6px 10px;font-size:13px;text-align:left}}
th{{background:#f1f5f9}}.muted{{color:#64748b;font-size:12px}}</style></head><body>
<h1>GeM-Sentinel — Verification Report</h1>
<p class="muted">Report {esc(report.id)} · Run #{esc(report.run_no)} · {esc(report.generated_at)} · Model: {esc(report.model_identifier)}</p>
<p><b>Application:</b> {esc(application.application_no)} · <b>Tender:</b> {esc(tender.tender_no)} — {esc(tender.title)}<br>
<b>Company:</b> {esc(bidder_name)} · <b>Bidder:</b> {esc(application.bidder_user.full_name)}</p>
<p><b>Compliance Score:</b> {esc(report.compliance_score)}/100 · <b>Risk:</b> {esc(risk_label(report.risk_level))} · <b>Eligibility:</b> {esc(eligibility_label(report.eligibility_result))}</p>
<h3>Rule Checklist</h3><table><tr><th>Result</th><th>Rule</th><th>Message</th></tr>{rows}</table>
<h3>Cross-Verification</h3><table><tr><th>Field</th><th>Bidder Value</th><th>Government Source Value</th><th>Result</th></tr>{cross}</table>
<h3>Document Authenticity</h3><table><tr><th>Document</th><th>Status</th><th>Findings</th></tr>{auth or '<tr><td colspan=3>—</td></tr>'}</table>
<p class="muted">Blacklist findings: {esc(json.dumps(report.blacklist_findings or [], default=str))}<br>
Missing documents: {esc(', '.join(report.missing_documents or []))} · Expired: {esc(', '.join(d['name'] for d in report.expired_documents or []))}</p>
<h3>AI Explanation (advisory)</h3><p>{esc(report.ai_explanation)}</p>
<p><b>Recommendation:</b> {esc(report.recommendation)}</p>
<p class="muted">Decision support system. Final authority remains with the Procurement Officer.</p>
</body></html>"""


def auction_report_to_pdf(a, data: dict) -> bytes:
    """Auction summary report (PDF): configuration, participants, bid history
    (when disclosed), result and integrity checks.

    `data` keys (assembled by the router with identity rules applied):
      participants: [{rank, anonymous_id, company (None when not disclosable),
                      ra_eligible, qualification_reason, initial_price,
                      final_price, price_source}]
      bids:         [{seq, anonymous_id, amount, ts, hash}]  (or [])
      result:       {...} | None
      integrity:    {"bid_chain": {...}, "audit_events": int, "audit_chain_valid": bool}
      generated_by: str
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer, Table, TableStyle,
    )

    _register_fonts()
    fnt, fnt_b = _fonts("en")

    NAVY = colors.HexColor("#0b2545")
    SLATE = colors.HexColor("#475569")
    GREEN = colors.HexColor("#166534")
    RED = colors.HexColor("#b91c1c")
    LINE = colors.HexColor("#cbd5e1")
    HEADER_BG = colors.HexColor("#f1f5f9")

    S = {
        "title": ParagraphStyle("t", fontName=fnt_b, fontSize=15, leading=19, textColor=NAVY),
        "meta": ParagraphStyle("m", fontName=fnt, fontSize=8, leading=11, textColor=SLATE),
        "h2": ParagraphStyle("h2", fontName=fnt_b, fontSize=11, leading=14, textColor=NAVY, spaceBefore=10, spaceAfter=4),
        "small": ParagraphStyle("s", fontName=fnt, fontSize=8, leading=11, textColor=SLATE),
    }
    T = ParagraphStyle("th", fontName=fnt_b, fontSize=8, leading=10, textColor=colors.white)
    TD = ParagraphStyle("td", fontName=fnt, fontSize=8, leading=11)

    def P(text, style="small"):
        return Paragraph(_esc(text), S[style])

    def TP(text, style=TD):
        return Paragraph(_esc(text), style)

    def money(v):
        try:
            return f"{float(v):,.2f}"
        except (TypeError, ValueError):
            return "—"

    reverse = a.auction_type == "REVERSE"
    direction = ("Bids DECREASE — lowest valid price wins" if reverse
                 else "Bids INCREASE — highest valid bid wins")
    saved_lbl = "Savings vs initial lowest" if reverse else "Premium vs initial highest"

    story = []
    story.append(P("GeM-Sentinel — Auction Summary Report", "title"))
    story.append(Spacer(1, 2 * mm))
    story.append(P(f"Auction {a.auction_no}  ·  {a.auction_type} e-Auction  ·  Status: {a.status}  ·  "
                   f"Tender: {a.tender.tender_no} — {a.tender.title}", "meta"))
    sched = ""
    if a.scheduled_start_at:
        sched = (f"  ·  Scheduled {a.scheduled_start_at:%Y-%m-%d %H:%M UTC} – "
                 f"{(a.effective_end_at or a.scheduled_end_at):%Y-%m-%d %H:%M UTC}"
                 if (a.effective_end_at or a.scheduled_end_at) else "")
    story.append(P(f"Created by {_esc(data.get('created_by') or '—')}  ·  Generated "
                   f"{data['generated_at']:%Y-%m-%d %H:%M UTC} by {_esc(data.get('generated_by') or '—')}{sched}", "meta"))
    story.append(Spacer(1, 3 * mm))

    # ---- 1. configuration --------------------------------------------------
    story.append(P("1. Auction Configuration", "h2"))
    cfg_rows = [
        ["Auction type", direction],
        ["Eligibility rule", f"{_esc(a.eligibility_rule)} (qualified bidders only; disqualified bidders never participate)"],
        ["Minimum increment (INR)", money(a.min_increment)],
        ["Anti-sniping extension",
         (f"enabled — trigger {a.extension_trigger_seconds}s before close, +{a.extension_seconds}s, "
          f"max {a.max_extensions} (used: {a.extension_count})") if a.auto_extend_enabled else "disabled"],
    ]
    if a.opening_reference_price is not None:
        cfg_rows.append(["Opening reference price (INR)", money(a.opening_reference_price)])
    cfg = Table([[TP(k, T), TP(v)] for k, v in cfg_rows], colWidths=[55 * mm, 112 * mm])
    cfg.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), NAVY),
        ("BOX", (0, 0), (-1, -1), 0.8, NAVY),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, LINE),
        ("BACKGROUND", (1, 0), (1, -1), HEADER_BG),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
    ]))
    story.append(cfg)

    # ---- 2. participants ----------------------------------------------------
    story.append(P("2. Participants & Qualification", "h2"))
    p_rows = [[TP(x, T) for x in
               ("Rank", "Bidder (anonymous)", "Company", "Qualification", "Initial price (INR)", "Final price (INR)", "Price source")]]
    for p in data["participants"]:
        qual = "Qualified" if p["ra_eligible"] else "Not qualified"
        if not p["ra_eligible"]:
            qual += f" — {_esc(p.get('qualification_reason') or 'n/a')}"
        p_rows.append([
            TP("L" + str(p["rank"]) if p.get("rank") else "—"),
            TP(p["anonymous_id"]),
            TP(p.get("company") or "—"),
            TP(qual),
            TP(money(p.get("initial_price"))),
            TP(money(p.get("final_price"))),
            TP(p.get("price_source") or "—"),
        ])
    pt = Table(p_rows, colWidths=[13 * mm, 27 * mm, 42 * mm, 30 * mm, 22 * mm, 22 * mm, 11 * mm], repeatRows=1)
    pt.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("BOX", (0, 0), (-1, -1), 0.8, NAVY),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(pt)

    # ---- 3. bid history (when disclosed) -------------------------------------
    if data.get("bids"):
        story.append(P("3. Bid History (server-authoritative sequence)", "h2"))
        b_rows = [[TP(x, T) for x in ("Seq", "Bidder (anonymous)", "Amount (INR)", "Server received (UTC)", "Bid hash")]]
        for b in data["bids"]:
            b_rows.append([TP(b["seq"]), TP(b["anonymous_id"]), TP(money(b["amount"])),
                           TP(b["ts"]), TP((b.get("hash") or "")[:16] + "…")])
        bt = Table(b_rows, colWidths=[14 * mm, 40 * mm, 32 * mm, 52 * mm, 29 * mm], repeatRows=1)
        bt.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("BOX", (0, 0), (-1, -1), 0.8, NAVY),
            ("INNERGRID", (0, 0), (-1, -1), 0.4, LINE),
            ("TOPPADDING", (0, 0), (-1, -1), 3.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
            ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(bt)

    # ---- 4. result -----------------------------------------------------------
    res = data.get("result")
    story.append(P("4. Commercial Result", "h2"))
    if res:
        r_rows = [
            ["Initial reference (INR)", money(res.get("initial_lowest"))],
            ["Final (INR)", money(res.get("final_lowest"))],
            [saved_lbl, f"{money(res.get('savings_amount'))} ({res.get('savings_pct', '—')}%)"],
            ["Accepted bids", str(res.get("accepted_bid_count", "—"))],
            ["Joint (tie) flag", "JOINT — tender-specific resolution required" if res.get("tie") else "None"],
            ["Result hash (SHA-256)", (res.get("result_hash") or "—")[:24] + "…"],
            ["Result status", res.get("status", "—")],
        ]
        rt = Table([[TP(k, T), TP(v)] for k, v in r_rows], colWidths=[55 * mm, 112 * mm])
        rt.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (0, -1), NAVY),
            ("BOX", (0, 0), (-1, -1), 0.8, NAVY),
            ("INNERGRID", (0, 0), (-1, -1), 0.4, LINE),
            ("BACKGROUND", (1, 0), (1, -1), HEADER_BG),
            ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ]))
        story.append(rt)
        if res.get("ranking"):
            rk_rows = [[TP(x, T) for x in ("Rank", "Bidder (anonymous)", "Company", "Amount (INR)", "Price source")]]
            for e in res["ranking"]:
                comp = next((p.get("company") for p in data["participants"]
                             if p["anonymous_id"] == e.get("anonymous_id")), None)
                rk_rows.append([TP(str(e["rank"])), TP(e["anonymous_id"]), TP(comp or "—"),
                                TP(money(e.get("amount"))), TP(e.get("price_source", "—"))])
            rkt = Table(rk_rows, colWidths=[14 * mm, 38 * mm, 48 * mm, 34 * mm, 33 * mm], repeatRows=1)
            rkt.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), NAVY),
                ("BOX", (0, 0), (-1, -1), 0.8, NAVY),
                ("INNERGRID", (0, 0), (-1, -1), 0.4, LINE),
                ("TOPPADDING", (0, 0), (-1, -1), 3.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
                ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ]))
            story.append(rkt)
    else:
        story.append(P("The auction has not been closed yet — no commercial result on record.", "small"))

    # ---- 5. integrity ---------------------------------------------------------
    itg = data.get("integrity") or {}
    bc = itg.get("bid_chain") or {}
    story.append(P("5. Integrity Checks", "h2"))
    i_rows = [
        ["Bid hash chain (SHA-256, read-only recomputation)",
         (f"VALID — {bc.get('bids_checked', 0)} accepted bids re-verified" if bc.get("valid")
          else f"BROKEN at sequence {bc.get('broken_at_sequence', '—')}")],
        ["Auction audit events on the tamper-evident ledger", str(itg.get("audit_events", 0))],
        ["Global audit chain verification", "VALID" if itg.get("audit_chain_valid") else "INVALID"],
    ]
    it = Table([[TP(k, T), TP(v)] for k, v in i_rows], colWidths=[95 * mm, 72 * mm])
    it.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), NAVY),
        ("BOX", (0, 0), (-1, -1), 0.8, NAVY),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, LINE),
        ("BACKGROUND", (1, 0), (1, -1), HEADER_BG),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
    ]))
    story.append(it)

    story.append(Spacer(1, 6 * mm))
    story.append(P("Decision support system. Auction prices, eligibility and ranking are determined exclusively by the "
                   "deterministic auction engine and the tender's rules; the AI component explains only. No bid is "
                   "auto-awarded — the Competent Authority decides.", "small"))

    w, h = A4
    buf = BytesIO()
    doc = BaseDocTemplate(buf, pagesize=A4,
                          leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=18 * mm,
                          title=f"Auction Report {a.auction_no}")

    def _footer(canvas, _doc):
        canvas.saveState()
        canvas.setFont(fnt, 7.5)
        canvas.setFillColor(SLATE)
        canvas.drawString(18 * mm, 10 * mm,
                          f"GeM-Sentinel · {a.auction_no} · {a.tender.tender_no} · {a.status}")
        canvas.drawRightString(w - 18 * mm, 10 * mm, f"Page {_doc.page}")
        canvas.setStrokeColor(LINE)
        canvas.line(18 * mm, 13 * mm, w - 18 * mm, 13 * mm)
        canvas.restoreState()

    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="main")
    doc.addPageTemplates([PageTemplate(id="all", frames=[frame], onPage=_footer)])
    doc.build(story)
    return buf.getvalue()
