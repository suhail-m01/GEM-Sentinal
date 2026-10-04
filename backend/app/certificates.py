"""Downloadable certificates of process completion (one per pipeline stage).

Every milestone in the procurement pipeline — bid submission, AI compliance
verification, technical evaluation, financial evaluation, award, contract,
execution progress, delivery/inspection, payment, audit closure — generates
an official-looking certificate the officer (or bidder, for submission) can
download as evidence that the step was successfully completed.

Certificates are rendered on demand from the stage record (deterministic —
no stored blobs) and carry a certificate number + evidence hash so the
record can be matched against the audit trail.
"""
import hashlib
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.lib.enums import TA_CENTER

NAVY = colors.HexColor("#0b2545")
TEAL = colors.HexColor("#0d9488")
SLATE = colors.HexColor("#475569")

STYLE_TITLE = ParagraphStyle("t", fontName="Helvetica-Bold", fontSize=17, leading=21,
                             alignment=TA_CENTER, textColor=NAVY)
STYLE_SUB = ParagraphStyle("s", fontName="Helvetica", fontSize=10.5, leading=14,
                           alignment=TA_CENTER, textColor=SLATE)
STYLE_H = ParagraphStyle("h", fontName="Helvetica-Bold", fontSize=10.5, leading=14,
                         textColor=NAVY)
STYLE_B = ParagraphStyle("b", fontName="Helvetica", fontSize=10, leading=14, textColor=colors.black)
STYLE_ATTEST = ParagraphStyle("a", fontName="Helvetica-Oblique", fontSize=10, leading=14,
                              alignment=TA_CENTER, textColor=SLATE)
STYLE_FOOT = ParagraphStyle("f", fontName="Helvetica", fontSize=8, leading=10,
                            alignment=TA_CENTER, textColor=colors.HexColor("#94a3b8"))

STAGE_TITLES = {
    "SUBMISSION": "Certificate of Bid Submission",
    "BID_CLOSURE": "Certificate of Bid Opening",
    "COMPLIANCE": "Certificate of AI Compliance Verification",
    "TECHNICAL": "Certificate of Technical Evaluation",
    "FINANCIAL": "Certificate of Financial Evaluation",
    "APPROVAL": "Letter of Award",
    "CONTRACT": "Contract Issue Certificate",
    "EXECUTION": "Contract Execution Progress Record",
    "DELIVERY": "Inspection & Acceptance Certificate",
    "PAYMENT": "Payment Settlement Certificate",
    "AUDIT": "Audit & Closure Certificate",
}


def _esc(v):
    return str(v or "—").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _fmt_ts(ts):
    if not ts:
        return "—"
    try:
        return datetime.fromisoformat(ts).strftime("%d %b %Y, %H:%M UTC") if isinstance(ts, str) \
            else ts.strftime("%d %b %Y, %H:%M UTC")
    except Exception:
        return str(ts)


def _evidence_hash(*parts) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts if p).encode()).hexdigest()[:16]


def _header(doc, title: str, sub: str, cert_no: str):
    t = Table([[Paragraph(title, STYLE_TITLE)], [Spacer(1, 2 * mm)],
               [Paragraph(sub, STYLE_SUB)], [Spacer(1, 2 * mm)],
               [Paragraph(f"Certificate No: {cert_no}", STYLE_SUB)]],
              colWidths=[170 * mm])
    t.setStyle(TableStyle([
        ("LINEABOVE", (0, 0), (-1, 0), 2.5, NAVY),
        ("LINEBELOW", (0, -1), (-1, -1), 1, TEAL),
        ("TOPPADDING", (0, 0), (-1, -1), 1.5 * mm),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5 * mm),
    ]))
    doc.build([]) if False else None
    return t


def _kv_table(rows: list[tuple[str, str]]) -> Table:
    data = [[Paragraph(_esc(k), STYLE_H), Paragraph(_esc(v), STYLE_B)] for k, v in rows]
    t = Table(data, colWidths=[52 * mm, 118 * mm])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -2), 0.25, colors.HexColor("#e2e8f0")),
        ("TOPPADDING", (0, 0), (-1, -1), 2 * mm),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2 * mm),
    ]))
    return t


def _attestation(who: str, when: str, what: str, ref: str) -> list:
    return [
        Spacer(1, 6 * mm),
        Paragraph(f"This certifies that {what} was successfully completed on "
                  f"<b>{_fmt_ts(when)}</b> by <b>{_esc(who)}</b>.", STYLE_ATTEST),
        Spacer(1, 3 * mm),
        Paragraph(f"Record reference: {_esc(ref)} · GeM-Sentinel deterministic pipeline · "
                  f"entries are hash-chained and tamper-evident in the system audit log.",
                  STYLE_FOOT),
    ]


def render_certificate(out, *, stage_key: str, application_no: str, tender_no: str,
                       tender_title: str, bidder_company: str, result_label: str,
                       acted_by: str, acted_at, certificate_no: str,
                       extra_rows: list[tuple[str, str]] | None = None,
                       extra_lines: list[str] | None = None,
                       org_name: str = "CPCL — GeM-Sentinel"):
    """Render a stage certificate to `out` (file-like). Returns cert bytes."""
    import io
    if not hasattr(out, "write"):
        out = io.BytesIO(out)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm,
                            topMargin=18 * mm, bottomMargin=16 * mm,
                            title=f"{STAGE_TITLES.get(stage_key, 'Certificate')} — {application_no}")
    title = STAGE_TITLES.get(stage_key, "Certificate")
    elements = [_header(doc, title, f"{org_name}", certificate_no), Spacer(1, 6 * mm)]
    rows = [
        ("Application No", application_no),
        ("Tender No", tender_no),
        ("Tender Title", tender_title),
        ("Bidder (Company)", bidder_company),
        ("Stage Result", result_label),
        ("Completed By", acted_by),
        ("Completed At", _fmt_ts(acted_at)),
    ]
    for k, v in (extra_rows or []):
        rows.append((k, v))
    elements.append(_kv_table(rows))
    for ln in (extra_lines or []):
        elements.append(Spacer(1, 3 * mm))
        elements.append(Paragraph(_esc(ln), STYLE_B))
    ref = _evidence_hash(certificate_no, application_no, stage_key, acted_by, str(acted_at))
    elements += _attestation(acted_by, acted_at,
                             f"the stage <b>{_esc(title)}</b> for application {_esc(application_no)}",
                             ref)
    doc.build(elements)
    data = buf.getvalue()
    out.write(data)
    return data
