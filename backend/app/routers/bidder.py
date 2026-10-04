import hashlib
import hmac
import json
import os
import re
import secrets
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import or_
import httpx
from sqlalchemy.orm import Session

from ..audit import append_audit
from ..core.config import settings
from ..core.db import get_db, utcnow
from ..core.deps import get_current_bidder
from ..core.security import encrypt_field, mask_gstin, mask_pan, mask_udyam
from ..models import (
    Application,
    ApplicationDocument,
    ApplicationStatusHistory,
    BidderProfile,
    BlacklistEntry,
    Clarification,
    ConsentRecord,
    Document,
    FinancialBid,
    Grievance,
    OfficerDecision,
    ProcuringOrganization,
    Tender,
    TenderBookmark,
    TenderCorrigendum,
    TenderPayment,
    User,
    VerificationReport,
    PROCUREMENT_MODE_LABELS,
)
from ..services import financial as fin_svc
from ..notifications import notify
from ..rules_engine import build_evidence_context, evaluate_tender
from ..services.decisions import set_status
from ..services.files import resolve_path, save_upload
from ..services.verification import mark_report_stale
from ..scoring import eligibility_label, risk_label

router = APIRouter(prefix="/bidder", tags=["bidder"])

SOURCE_CATEGORIES = ["GST", "UDYAM", "PAN", "MCA21", "EPFO", "ESIC", "STARTUP_INDIA", "NSIC", "BIS", "DIGILOCKER", "ALL"]
DOC_TYPE_LABELS = {
    "GST_CERT": "GST Certificate", "UDYAM_CERT": "Udyam/MSME Certificate", "PAN": "PAN Certificate",
    "MCA_DOC": "MCA21 Incorporation", "EPFO_DOC": "EPFO Compliance", "ESIC_DOC": "ESIC Compliance",
    "STARTUP_CERT": "Startup India Recognition", "NSIC_CERT": "NSIC Registration", "BIS_CERT": "BIS Certification",
    "DIGILOCKER_DOC": "DigiLocker Document", "OEM_AUTHORIZATION": "OEM Authorization",
    "LOCAL_CONTENT_DECLARATION": "Local-Content Declaration", "BANK_DOC": "Bank/Financial Document",
    "OTHER": "Other Document",
}



def _payment_requirements(db: Session, user: User, t: Tender) -> dict:
    """Resolve one-checkout tender payment requirements.

    Every bidder pays the universal non-refundable application/platform fee.
    When EMD/Bid Security is also due, it is added to that fee and collected
    in the same Razorpay order. EMD exemptions remain tender/bidder-specific.
    Legacy separately-verified fee/EMD rows are still recognised so an older
    database is not broken by this upgrade.
    """
    p = user.bidder_profile
    emd_exempt = bool(t.bid_security_exemption_allowed and p and p.udyam_masked)
    app_amt = Decimal(str(settings.APPLICATION_FEE_AMOUNT)).quantize(Decimal("0.01"))
    if t.bid_security_amount is not None:
        emd_amt = Decimal(str(t.bid_security_amount)).quantize(Decimal("0.01"))
    elif t.bid_security_percent is not None and (t.value_max is not None or t.value_min is not None):
        base = Decimal(str(t.value_max if t.value_max is not None else t.value_min))
        emd_amt = (base * Decimal(str(t.bid_security_percent)) / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    else:
        emd_amt = Decimal("0.00")

    verified_types = {
        r.payment_type for r in db.query(TenderPayment).filter(
            TenderPayment.tender_id == t.id, TenderPayment.bidder_user_id == user.id,
            TenderPayment.status == "VERIFIED"
        ).all()
    }
    app_required = bool(app_amt > 0)
    emd_required = bool(t.bid_security_required and emd_amt > 0 and not emd_exempt)

    # New model: one checkout. A COMBINED payment satisfies both obligations.
    combined_paid = "APPLICATION_FEE_EMD" in verified_types
    app_paid = combined_paid or "APPLICATION_FEE" in verified_types
    emd_paid = (not emd_required) or combined_paid or "EMD" in verified_types
    total_amt = app_amt + (emd_amt if emd_required else Decimal("0.00"))
    total_paid = ((not app_required) or app_paid) and emd_paid

    return {
        "application_fee": {"required": app_required, "amount": float(app_amt), "paid": (not app_required) or app_paid,
                            "refundable": False},
        "emd": {"required": emd_required, "amount": float(emd_amt), "paid": emd_paid,
                "exempt": emd_exempt, "exemption_allowed": bool(t.bid_security_exemption_allowed),
                "percent": float(t.bid_security_percent) if t.bid_security_percent is not None else None},
        "checkout": {
            "payment_type": "APPLICATION_FEE_EMD" if emd_required else "APPLICATION_FEE",
            "amount": float(total_amt),
            "paid": total_paid,
            "includes_application_fee": app_required,
            "includes_emd": emd_required,
        },
        "gateway_configured": bool(settings.ENABLE_RAZORPAY_PAYMENTS and settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET),
        "all_satisfied": total_paid,
    }


class PaymentOrderIn(BaseModel):
    payment_type: str = "AUTO"


class PaymentVerifyIn(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


@router.post("/tenders/{tender_id}/payments/order")
def create_payment_order(tender_id: str, body: PaymentOrderIn, request: Request,
                         user: User = Depends(get_current_bidder), db: Session = Depends(get_db)):
    t = db.get(Tender, tender_id)
    if not t or t.status not in {"PUBLISHED", "CLOSED"}:
        raise HTTPException(status_code=404, detail="Tender not found")
    req = _payment_requirements(db, user, t)
    checkout = req["checkout"]
    requested = (body.payment_type or "AUTO").strip().upper()
    # AUTO is the public flow. Keep APPLICATION_FEE/EMD accepted only for
    # backward compatibility with older clients, but always charge the remaining
    # obligations together so there is still a single new checkout.
    if requested not in {"AUTO", "APPLICATION_FEE", "EMD", "APPLICATION_FEE_EMD"}:
        raise HTTPException(status_code=400, detail="payment_type must be AUTO")
    if checkout["paid"]:
        return {"already_paid": True, "payment_type": checkout["payment_type"], "requirements": req}
    if not req["gateway_configured"]:
        raise HTTPException(status_code=503, detail="Razorpay is not configured. Set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET in backend/.env")

    # If a legacy application-fee row is already verified, charge only EMD.
    # Otherwise the normal path is one combined amount (₹2,500 + EMD).
    app_due = Decimal(str(req["application_fee"]["amount"])) if not req["application_fee"]["paid"] else Decimal("0")
    emd_due = Decimal(str(req["emd"]["amount"])) if req["emd"]["required"] and not req["emd"]["paid"] else Decimal("0")
    amount = (app_due + emd_due).quantize(Decimal("0.01"))
    if amount <= 0:
        return {"already_paid": True, "payment_type": checkout["payment_type"], "requirements": req}

    if app_due > 0 and emd_due > 0:
        typ = "APPLICATION_FEE_EMD"
    elif emd_due > 0:
        typ = "EMD"
    else:
        typ = "APPLICATION_FEE"

    amount_paise = int((amount * 100).to_integral_value())
    receipt = f"GS-{t.tender_no}-PAY-{secrets.token_hex(4)}"[:40]
    payload = {
        "amount": amount_paise, "currency": "INR", "receipt": receipt,
        "notes": {
            "tender_no": t.tender_no, "bidder_user_id": user.id, "payment_type": typ,
            "application_fee_inr": str(app_due), "emd_inr": str(emd_due),
        }
    }
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post("https://api.razorpay.com/v1/orders", json=payload,
                               auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
        resp.raise_for_status()
        order = resp.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Razorpay order creation failed: {exc}")

    rec = TenderPayment(tender_id=t.id, bidder_user_id=user.id, payment_type=typ, amount_paise=amount_paise,
                        razorpay_order_id=order["id"], receipt=receipt, status="CREATED")
    db.add(rec)
    db.flush()
    append_audit(db, "payment_order_created", "TenderPayment", rec.id, actor_id=user.id, actor_role="BIDDER",
                 payload={"tender_no": t.tender_no, "payment_type": typ, "amount_paise": amount_paise,
                          "application_fee_inr": float(app_due), "emd_inr": float(emd_due)},
                 request=request, commit=False)
    db.commit()
    desc = (f"{t.tender_no} - Application Fee + EMD/Bid Security" if typ == "APPLICATION_FEE_EMD"
            else f"{t.tender_no} - {'EMD / Bid Security' if typ == 'EMD' else 'Application Fee'}")
    return {"order_id": order["id"], "key_id": settings.RAZORPAY_KEY_ID, "amount": amount_paise,
            "currency": "INR", "payment_type": typ, "name": "GeM-Sentinel",
            "description": desc, "breakdown": {"application_fee": float(app_due), "emd": float(emd_due)}}


@router.post("/tenders/{tender_id}/payments/verify")
def verify_payment(tender_id: str, body: PaymentVerifyIn, request: Request,
                   user: User = Depends(get_current_bidder), db: Session = Depends(get_db)):
    rec = db.query(TenderPayment).filter(
        TenderPayment.tender_id == tender_id, TenderPayment.bidder_user_id == user.id,
        TenderPayment.razorpay_order_id == body.razorpay_order_id
    ).first()
    if not rec:
        raise HTTPException(status_code=404, detail="Payment order not found")
    expected = hmac.new(settings.RAZORPAY_KEY_SECRET.encode(),
                        f"{body.razorpay_order_id}|{body.razorpay_payment_id}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, body.razorpay_signature):
        rec.status = "FAILED"
        rec.failure_reason = "Signature verification failed"
        db.commit()
        raise HTTPException(status_code=400, detail="Payment signature verification failed")
    rec.razorpay_payment_id = body.razorpay_payment_id
    rec.razorpay_signature = body.razorpay_signature
    rec.status = "VERIFIED"
    rec.verified_at = utcnow()
    append_audit(db, "payment_verified", "TenderPayment", rec.id, actor_id=user.id, actor_role="BIDDER",
                 payload={"payment_type": rec.payment_type, "amount_paise": rec.amount_paise,
                          "razorpay_payment_id": body.razorpay_payment_id}, request=request, commit=False)
    db.commit()
    return {"verified": True, "payment_type": rec.payment_type, "requirements": _payment_requirements(db, user, db.get(Tender, tender_id))}

def _doc_out(d: Document) -> dict:
    return {
        "id": d.id,
        "name": d.name,
        "doc_type": d.doc_type,
        "doc_type_label": DOC_TYPE_LABELS.get(d.doc_type, d.doc_type),
        "source": d.source,
        "source_classification": d.source_classification,
        "is_government_source": d.source_classification == "GOVERNMENT_SOURCE",
        "verification_status": d.verification_status,
        "issue_date": d.issue_date.isoformat() if d.issue_date else None,
        "expiry_date": d.expiry_date.isoformat() if d.expiry_date else None,
        "expired": bool(d.expiry_date and d.expiry_date < date.today()),
        "last_synced_at": d.last_synced_at.isoformat() if d.last_synced_at else None,
        "reference_id": d.reference_id,
        "document_hash": d.document_hash,
        "masked_identifier": d.masked_identifier,
        "ocr_confidence": d.ocr_confidence,
        "authenticity_status": d.authenticity_status,
        "version": d.version,
        "file_available": bool(d.file_path),
    }


def _require_profile(user: User) -> BidderProfile:
    p = user.bidder_profile
    if not p:
        raise HTTPException(status_code=400, detail="Bidder profile not found")
    return p


def _compute_completion(p: BidderProfile) -> int:
    fields = [
        p.company_name, p.pan_encrypted, p.gstin_encrypted, p.udyam_encrypted,
        p.business_type, p.registered_address, p.district, p.state,
        p.authorized_rep_name, p.authorized_rep_phone, p.annual_turnover,
    ]
    return int(100 * sum(1 for f in fields if f is not None and str(f) != "") / len(fields))


@router.get("/dashboard")
def dashboard(user: User = Depends(get_current_bidder), db: Session = Depends(get_db)):
    p = _require_profile(user)
    docs = db.query(Document).filter(Document.user_id == user.id).all()
    apps = db.query(Application).filter(Application.bidder_user_id == user.id).all()
    open_tenders = db.query(Tender).filter(Tender.status == "PUBLISHED", Tender.close_date >= date.today()).count()
    eligible_count = 0
    for t in db.query(Tender).filter(Tender.status == "PUBLISHED", Tender.close_date >= date.today()).all():
        try:
            ev = evaluate_tender(db, user, t)
            if ev["eligibility_result"] == "ELIGIBLE":
                eligible_count += 1
        except Exception:
            continue
    clarifications = (
        db.query(Clarification)
        .join(Application, Application.id == Clarification.application_id)
        .filter(Application.bidder_user_id == user.id, Clarification.status == "OPEN")
        .count()
    )
    return {
        "profile": {
            "company_name": p.company_name,
            "profile_completion": _compute_completion(p),
            "gstin_masked": p.gstin_masked,
            "udyam_masked": p.udyam_masked,
            "district": p.district,
            "state": p.state,
        },
        "verified_document_count": sum(1 for d in docs if d.verification_status == "VERIFIED"),
        "total_document_count": len(docs),
        "eligible_tender_count": eligible_count,
        "open_tender_count": open_tenders,
        "pending_applications": sum(1 for a in apps if a.status not in ("Approved", "Rejected", "Withdrawn", "Closed")),
        "clarification_requests": clarifications,
        "applications": [
            {"id": a.id, "application_no": a.application_no, "tender": a.tender.tender_no,
             "status": a.status, "risk_level": a.risk_level, "compliance_score": a.compliance_score,
             "submitted_at": a.submitted_at.isoformat() if a.submitted_at else None}
            for a in apps[-10:][::-1]
        ],
        "reminders": [
            {"tender_no": t.tender_no, "title": t.title, "close_date": t.close_date.isoformat(),
             "days_left": (t.close_date - date.today()).days}
            for t in db.query(Tender).filter(
                Tender.status == "PUBLISHED", Tender.close_date >= date.today(),
                Tender.close_date <= date.today() + timedelta(days=5),
            ).all()
        ],
    }


@router.get("/compliance-health")
def compliance_health(user: User = Depends(get_current_bidder), db: Session = Depends(get_db)):
    """Bidder self-service compliance health (spec §8.7): expiring/lapsed
    items so they can fix them before it costs them a bid. Own data only."""
    from ..models import ComplianceWatch, DriftEvent, VerificationReport

    p = _require_profile(user)
    reports = (
        db.query(VerificationReport)
        .join(Application, VerificationReport.application_id == Application.id)
        .filter(Application.bidder_user_id == user.id)
        .order_by(VerificationReport.generated_at.desc())
        .all()
    )
    latest = reports[0] if reports else None
    watch = (
        db.query(ComplianceWatch)
        .filter(ComplianceWatch.subject_user_id == user.id, ComplianceWatch.active.is_(True))
        .order_by(ComplianceWatch.created_at.desc())
        .first()
    )
    baseline = (watch.baseline or {}) if watch else {}
    drifts = (
        db.query(DriftEvent)
        .filter(DriftEvent.watch_id == watch.id)
        .order_by(DriftEvent.detected_at.desc())
        .all()
    ) if watch else []

    def item(attr: str, label: str) -> dict:
        b = baseline.get(attr)
        val = (b or {}).get("value") or {}
        inner = str(val.get("inner_status") or val.get("source_status") or "").upper()
        src_st = str(val.get("source_status") or "").upper()
        degraded = inner in {"CANCELLED", "SUSPENDED", "REVOKED", "INACTIVE", "EXPIRED", "DEFAULTED", "NOT_FOUND", "STRIKE_OFF"}
        unverified = src_st in {"UNAVAILABLE", "NOT_FETCHED", "CONSENT_REQUIRED"} or (not inner and not src_st)
        status = "LAPSED" if degraded else ("UNVERIFIED" if unverified else "OK")
        return {"attribute": attr, "label": label, "status": status,
                "value": inner or src_st or None,
                "last_checked": (b or {}).get("observed_at")}

    items = [
        item("GST", "GST registration"),
        item("PAN", "PAN record"),
        item("UDYAM", "Udyam (MSME) registration"),
        item("EPFO", "EPFO establishment compliance"),
        item("MCA21", "MCA company status"),
        item("BLACKLIST", "Debarment / blacklist clearance"),
    ]

    # certificate expiry horizon from documents (expiry within 30 days => expiring)
    docs = db.query(Document).filter(Document.user_id == user.id).all()
    today = date.today()
    expiring = []
    for d in docs:
        if d.expiry_date and d.expiry_date >= today:
            days = (d.expiry_date - today).days
            if days <= 30:
                expiring.append({"name": d.name, "doc_type": d.doc_type,
                                 "expiry_date": d.expiry_date.isoformat(), "days_remaining": days})
    expiring.sort(key=lambda x: x["days_remaining"])

    return {
        "items": items,
        "expiring_documents": expiring,
        "score_at_verification": latest.compliance_score if latest else None,
        "score_now": latest.compliance_score if latest else None,
        "risk_now": latest.risk_level if latest else None,
        "last_verified_at": latest.generated_at.isoformat() if latest else None,
        "watch_active": watch is not None,
        "recent_drifts": [
            {"attribute": d.attribute, "to": (d.new_value or {}).get("inner_status") or (d.new_value or {}).get("source_status"),
             "severity": d.severity, "detected_at": d.detected_at.isoformat() if d.detected_at else None}
            for d in drifts[:5]
        ],
        "advice": None if not any(i["status"] != "OK" for i in items) and not expiring else
                  "Fix lapsed or expiring items before your next bid — continuous monitoring can block eligibility after a source change.",
    }


@router.get("/profile")
def get_profile(user: User = Depends(get_current_bidder)):
    p = _require_profile(user)
    return {
        "company_name": p.company_name, "trade_name": p.trade_name,
        "pan_masked": p.pan_masked, "gstin_masked": p.gstin_masked, "udyam_masked": p.udyam_masked,
        "business_type": p.business_type, "business_category": p.business_category,
        "registered_address": p.registered_address, "city": p.city, "district": p.district, "state": p.state,
        "pincode": p.pincode, "authorized_rep_name": p.authorized_rep_name,
        "authorized_rep_email": p.authorized_rep_email, "authorized_rep_phone": p.authorized_rep_phone,
        "annual_turnover": p.annual_turnover, "established_year": p.established_year,
        "registration_date": p.registration_date.isoformat() if p.registration_date else None,
        "filing_status": p.filing_status, "local_content_percent": p.local_content_percent,
        "profile_completion": _compute_completion(p),
    }


class ProfileIn(BaseModel):
    company_name: str | None = None
    trade_name: str | None = None
    pan: str | None = None
    gstin: str | None = None
    udyam_number: str | None = None
    business_type: str | None = None
    business_category: str | None = None
    registered_address: str | None = None
    city: str | None = None
    district: str | None = None
    state: str | None = None
    pincode: str | None = None
    authorized_rep_name: str | None = None
    authorized_rep_email: str | None = None
    authorized_rep_phone: str | None = None
    annual_turnover: int | None = None
    established_year: int | None = None
    registration_date: str | None = None
    filing_status: str | None = None
    local_content_percent: int | None = None


@router.put("/profile")
def update_profile(body: ProfileIn, request: Request, user: User = Depends(get_current_bidder), db: Session = Depends(get_db)):
    p = _require_profile(user)
    data = body.model_dump(exclude_none=True)
    sensitive = {"pan": "pan_encrypted", "gstin": "gstin_encrypted", "udyam_number": "udyam_encrypted"}
    for field, attr in sensitive.items():
        if field in data:
            val = data.pop(field).upper()
            setattr(p, attr, encrypt_field(val))
            if attr == "pan_encrypted":
                p.pan_masked = mask_pan(val)
            elif attr == "gstin_encrypted":
                p.gstin_masked = mask_gstin(val)
            else:
                p.udyam_masked = mask_udyam(val)
    if "registration_date" in data and data["registration_date"]:
        data["registration_date"] = date.fromisoformat(data["registration_date"])
    for k, v in data.items():
        if hasattr(p, k):
            setattr(p, k, v)
    p.profile_completion = _compute_completion(p)
    append_audit(db, "profile_updated", "BidderProfile", p.id, actor_id=user.id, actor_role="BIDDER",
                 payload={"fields": [k for k in data.keys()]},
                 request=request, commit=False)
    db.commit()
    return {"message": "Profile updated", "profile_completion": p.profile_completion}


# ---------------- consent ----------------

class ConsentIn(BaseModel):
    sources: list[str] = Field(min_length=1)
    purpose: str = "Compliance verification for tender eligibility"


@router.post("/consent")
def grant_consent(body: ConsentIn, request: Request, user: User = Depends(get_current_bidder), db: Session = Depends(get_db)):
    for s in body.sources:
        if s.upper() not in SOURCE_CATEGORIES:
            raise HTTPException(status_code=400, detail=f"Unknown source category: {s}")
    for s in body.sources:
        s = s.upper()
        rec = (
            db.query(ConsentRecord)
            .filter(ConsentRecord.user_id == user.id, ConsentRecord.source_category == s)
            .first()
        )
        if rec:
            rec.status = "GRANTED"
            rec.granted_at = utcnow()
            rec.revoked_at = None
            rec.purpose = body.purpose
        else:
            db.add(ConsentRecord(user_id=user.id, source_category=s, purpose=body.purpose))
    append_audit(db, "consent_granted", "ConsentRecord", None, actor_id=user.id, actor_role="BIDDER",
                 payload={"sources": [s.upper() for s in body.sources]}, commit=False)
    db.commit()
    return {"message": "Consent recorded", "sources": [s.upper() for s in body.sources]}


@router.post("/consent/revoke")
def revoke_consent(body: ConsentIn, user: User = Depends(get_current_bidder), db: Session = Depends(get_db)):
    for s in body.sources:
        rec = (
            db.query(ConsentRecord)
            .filter(ConsentRecord.user_id == user.id, ConsentRecord.source_category == s.upper())
            .first()
        )
        if rec:
            rec.status = "REVOKED"
            rec.revoked_at = utcnow()
    append_audit(db, "consent_revoked", "ConsentRecord", None, actor_id=user.id, actor_role="BIDDER",
                 payload={"sources": [s.upper() for s in body.sources]}, commit=False)
    db.commit()
    return {"message": "Consent revoked", "sources": [s.upper() for s in body.sources]}


@router.get("/consent")
def get_consent(user: User = Depends(get_current_bidder), db: Session = Depends(get_db)):
    rows = db.query(ConsentRecord).filter(ConsentRecord.user_id == user.id).all()
    return [
        {"source": r.source_category, "status": r.status, "purpose": r.purpose,
         "granted_at": r.granted_at.isoformat() if r.granted_at else None,
         "revoked_at": r.revoked_at.isoformat() if r.revoked_at else None}
        for r in rows
    ]


# ---------------- document vault ----------------

SOURCE_TO_DOC = {
    "UDYAM": ("UDYAM_CERT", "Udyam/MSME Certificate"),
    "GST": ("GST_CERT", "GST Certificate"),
    "PAN": ("PAN", "PAN Certificate"),
    "MCA21": ("MCA_DOC", "MCA21 Incorporation Certificate"),
    "EPFO": ("EPFO_DOC", "EPFO Compliance Certificate"),
    "ESIC": ("ESIC_DOC", "ESIC Compliance Certificate"),
    "STARTUP_INDIA": ("STARTUP_CERT", "Startup India Recognition"),
    "NSIC": ("NSIC_CERT", "NSIC Registration"),
    "BIS": ("BIS_CERT", "BIS Certification"),
}


def _sync_from_source(db: Session, user: User, source: str) -> dict:
    from ..services.portal import fetch_source

    p = _require_profile(user)
    identifiers = {"pan": p.pan_plain, "gstin": p.gstin_plain, "udyam": p.udyam_plain}
    adapter_key = {"UDYAM": "udyam", "GST": "gstin", "PAN": "pan"}.get(source, "pan")
    res = fetch_source(db, source, identifier=identifiers.get(adapter_key))
    doc_type, default_name = SOURCE_TO_DOC.get(source, (f"{source}_DOC", f"{source} Document"))
    doc = (
        db.query(Document)
        .filter(Document.user_id == user.id, Document.doc_type == doc_type, Document.source == source)
        .first()
    )
    if doc is None:
        doc = Document(user_id=user.id, doc_type=doc_type, source=source,
                       source_classification="GOVERNMENT_SOURCE", name=default_name)
        db.add(doc)
    doc.last_synced_at = utcnow()
    st = res.status
    data = res.data or {}
    if st == "VALID":
        doc.verification_status = "VERIFIED"
        doc.issue_date = data.get("issue_date") and date.fromisoformat(str(data["issue_date"])[:10])
        doc.expiry_date = data.get("expiry_date") and date.fromisoformat(str(data["expiry_date"])[:10])
        doc.reference_id = data.get("reference_id") or data.get("gstin") or data.get("pan") or data.get("udyam_number")
        doc.masked_identifier = (
            mask_gstin(str(doc.reference_id)) if source == "GST"
            else mask_pan(str(doc.reference_id)) if source == "PAN"
            else mask_udyam(str(doc.reference_id)) if source == "UDYAM"
            else (doc.reference_id or None)
        )
        doc.ocr_data = data.get("fields") or {}
        doc.ocr_confidence = 0.9 if data.get("fields") else 0.6
        outcome = "verified"
    elif st == "EXPIRED":
        doc.verification_status = "EXPIRED"
        doc.expiry_date = data.get("expiry_date") and date.fromisoformat(str(data["expiry_date"])[:10])
        doc.notes = res.message
        outcome = "expired"
    elif st == "MISMATCH":
        doc.verification_status = "MISMATCH"
        doc.notes = res.message
        outcome = "mismatch"
    elif st == "NOT_FOUND":
        doc.verification_status = "PENDING"
        doc.notes = "Record not found at source"
        outcome = "not_found"
    else:  # UNAVAILABLE / ERROR — never treated as verified
        doc.verification_status = "UNABLE_TO_VERIFY"
        doc.notes = res.message
        outcome = "unable_to_verify"
    db.flush()
    return {"source": source, "document_id": doc.id, "outcome": outcome, "status": doc.verification_status}


@router.post("/fetch-documents")
def fetch_documents(request: Request, user: User = Depends(get_current_bidder), db: Session = Depends(get_db)):
    """Consent-gated fetch from mock government sources (Verify Once, Apply Many)."""
    consents = {
        r.source_category
        for r in db.query(ConsentRecord)
        .filter(ConsentRecord.user_id == user.id, ConsentRecord.status == "GRANTED").all()
    }
    fetchable = []
    for source in SOURCE_TO_DOC:
        if "ALL" in consents or source in consents:
            fetchable.append(source)
    if not fetchable:
        raise HTTPException(status_code=400, detail="No consent granted. Grant consent before fetching documents.")
    results = [_sync_from_source(db, user, s) for s in fetchable]
    append_audit(db, "documents_fetched", "Document", None, actor_id=user.id, actor_role="BIDDER",
                 payload={"sources": fetchable, "results": results},
                 request=request, commit=False)
    db.commit()
    return {"fetched": results, "documents": [_doc_out(d) for d in db.query(Document).filter(Document.user_id == user.id).all()]}


@router.get("/documents")
def list_documents(user: User = Depends(get_current_bidder), db: Session = Depends(get_db)):
    docs = db.query(Document).filter(Document.user_id == user.id).order_by(Document.created_at.desc()).all()
    return {"documents": [_doc_out(d) for d in docs]}


@router.get("/documents/{doc_id}/view")
def view_document(doc_id: str, user: User = Depends(get_current_bidder), db: Session = Depends(get_db)):
    """Render an original-looking synthetic replica of the bidder's document
    (Document Studio). PNG with a persistent SYNTHETIC marker + content hash."""
    if not settings.DEMO_MODE:
        raise HTTPException(status_code=404, detail="Synthetic Document Studio is disabled")
    from ..services.doc_studio import get_replica

    d = db.get(Document, doc_id)
    if not d or d.user_id != user.id:
        raise HTTPException(status_code=404, detail="Document not found")
    profile = user.bidder_profile if user.bidder_profile else None
    png, digest = get_replica(d, profile, user, officer_view=False)
    append_audit(db, "document_replica_viewed", "Document", d.id, actor_id=user.id, actor_role=user.role, commit=False)
    db.commit()
    return Response(
        content=png,
        media_type="image/png",
        headers={
            "Content-Disposition": f'inline; filename="replica-{d.doc_type}.png"',
            "X-Document-Hash": digest,
            "X-Synthetic": "true",
            "Cache-Control": "private, max-age=300",
        },
    )


@router.post("/documents/upload")
def upload_document(
    request: Request,
    user: User = Depends(get_current_bidder),
    db: Session = Depends(get_db),
    file: UploadFile = File(...),
    doc_type: str = Form("OTHER"),
    name: str = Form(None),
):
    if doc_type not in DOC_TYPE_LABELS:
        raise HTTPException(status_code=400, detail=f"Unknown doc_type. Allowed: {', '.join(DOC_TYPE_LABELS)}")
    content = file.file.read()
    # upload-time security guard: magic-byte validation + PDF exploit scan
    from ..services.doc_guard import assess_upload
    guard = assess_upload(file.filename, content)
    if not guard.ok:
        raise HTTPException(status_code=400, detail="; ".join(guard.errors))
    try:
        safe_name, digest = save_upload(content, file.filename or "upload.bin", kind="docs")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # duplicate detection within the bidder's vault
    existing = db.query(Document).filter(Document.user_id == user.id, Document.document_hash == digest).first()
    doc = Document(
        user_id=user.id,
        name=name or (file.filename or "Uploaded document"),
        doc_type=doc_type,
        source="MANUAL",
        source_classification="BIDDER_UPLOADED",
        verification_status="MANUAL_UPLOADED",
        file_path=safe_name,
        file_type=os.path.splitext(safe_name)[1].lstrip("."),
        document_hash=digest,
    )
    if existing:
        doc.notes = "Duplicate of an existing vault document (same SHA-256)"
    if guard.suspicious:
        note = "Security scan flags: " + ", ".join(guard.suspicious_indicators)
        doc.notes = f"{doc.notes}; {note}" if doc.notes else note
    db.add(doc)
    db.flush()
    append_audit(db, "document_upload", "Document", doc.id, actor_id=user.id, actor_role="BIDDER",
                 payload={"doc_type": doc_type, "name": doc.name, "sha256": digest,
                          "duplicate": bool(existing), "detected_type": guard.detected_type,
                          "suspicious_indicators": guard.suspicious_indicators},
                 request=request, commit=False)
    db.commit()
    return {"document": _doc_out(doc)}


@router.post("/documents/{doc_id}/resync")
def resync_document(doc_id: str, request: Request, user: User = Depends(get_current_bidder), db: Session = Depends(get_db)):
    doc = db.get(Document, doc_id)
    if not doc or doc.user_id != user.id:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc.source not in SOURCE_TO_DOC:
        raise HTTPException(status_code=400, detail="Only government-source documents can be resynced")
    result = _sync_from_source(db, user, doc.source)
    append_audit(db, "document_resync", "Document", doc.id, actor_id=user.id, actor_role="BIDDER",
                 payload={"source": doc.source, "status": result["status"]}, commit=False)
    db.commit()
    return result


# ---------------- tender discovery ----------------

@router.get("/tenders")
def list_tenders(
    user: User = Depends(get_current_bidder),
    db: Session = Depends(get_db),
    q: str | None = None,
    district: str | None = None,
    state: str | None = None,
    department: str | None = None,
    category: str | None = None,
    min_value: float | None = None,
    max_value: float | None = None,
    close_before: str | None = None,
    msme: bool | None = None,
    make_in_india: bool | None = None,
    startup: bool | None = None,
):
    query = db.query(Tender).filter(Tender.status == "PUBLISHED", Tender.close_date >= date.today())
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Tender.title.ilike(like), Tender.tender_no.ilike(like), Tender.category.ilike(like)))
    if district:
        query = query.filter(Tender.district == district)
    if state:
        query = query.filter(Tender.state == state)
    if department:
        query = query.filter(Tender.department == department)
    if category:
        query = query.filter(Tender.category == category)
    if min_value is not None:
        query = query.filter(Tender.value_max >= min_value)
    if max_value is not None:
        query = query.filter(Tender.value_min <= max_value)
    if close_before:
        query = query.filter(Tender.close_date <= date.fromisoformat(close_before))
    if msme is not None:
        query = query.filter(Tender.msme_preference == msme)
    if make_in_india is not None:
        query = query.filter(Tender.make_in_india_required == make_in_india)
    if startup is not None:
        query = query.filter(Tender.startup_eligible == startup)
    tenders = query.order_by(Tender.close_date.asc()).all()

    bookmarks = {b.tender_id for b in db.query(TenderBookmark).filter(TenderBookmark.user_id == user.id).all()}
    out = []
    for t in tenders:
        badge = "NOT_CHECKED"
        apply_allowed = False
        blocked_reasons: list[str] = []
        try:
            ev = evaluate_tender(db, user, t)
            badge = ev["eligibility_result"]
            apply_allowed = ev["apply_allowed"]
            blocked_reasons = ev["apply_blocked_reasons"]
        except Exception:
            badge = "UNABLE_TO_VERIFY"
        out.append({
            "id": t.id, "tender_no": t.tender_no, "title": t.title, "department": t.department,
            "organization": t.organization, "district": t.district, "state": t.state, "category": t.category,
            "product_service_category": t.product_service_category,
            "value_min": float(t.value_min) if t.value_min is not None else None,
            "value_max": float(t.value_max) if t.value_max is not None else None,
            "close_date": t.close_date.isoformat(), "msme_preference": t.msme_preference,
            "make_in_india_required": t.make_in_india_required, "startup_eligible": t.startup_eligible,
            "eligibility_badge": badge, "bookmarked": t.id in bookmarks,
            "apply_allowed": apply_allowed, "apply_blocked_reasons": blocked_reasons,
            "days_left": (t.close_date - date.today()).days,
        })
    return {"tenders": out}


@router.get("/tenders/bookmarks")
def bookmarks(user: User = Depends(get_current_bidder), db: Session = Depends(get_db)):
    rows = (
        db.query(Tender).join(TenderBookmark, TenderBookmark.tender_id == Tender.id)
        .filter(TenderBookmark.user_id == user.id).all()
    )
    return {"tenders": [{"id": t.id, "tender_no": t.tender_no, "title": t.title, "close_date": t.close_date.isoformat()} for t in rows]}


@router.post("/tenders/{tender_id}/bookmark")
def bookmark(tender_id: str, body: dict | None = None, user: User = Depends(get_current_bidder), db: Session = Depends(get_db)):
    t = db.get(Tender, tender_id)
    if not t:
        raise HTTPException(status_code=404, detail="Tender not found")
    existing = db.query(TenderBookmark).filter(TenderBookmark.user_id == user.id, TenderBookmark.tender_id == t.id).first()
    action = (body or {}).get("action", "add")
    if existing and action == "add":
        return {"bookmarked": True}
    if not existing and action == "remove":
        return {"bookmarked": False}
    if existing:
        db.delete(existing)
        db.commit()
        return {"bookmarked": False}
    db.add(TenderBookmark(user_id=user.id, tender_id=t.id))
    db.commit()
    return {"bookmarked": True}


@router.get("/tenders/{tender_id}")
def tender_detail(tender_id: str, user: User = Depends(get_current_bidder), db: Session = Depends(get_db)):
    t = db.get(Tender, tender_id)
    if not t or t.status != "PUBLISHED":
        raise HTTPException(status_code=404, detail="Tender not found")
    ev = evaluate_tender(db, user, t)
    existing_app = (
        db.query(Application)
        .filter(Application.tender_id == t.id, Application.bidder_user_id == user.id)
        .order_by(Application.submitted_at.desc())
        .first()
    )
    # Procuring entity + its delegated authority chain for THIS tender's value
    org = t.procuring_organization
    value = float(t.value_max) if t.value_max is not None else (float(t.value_min) if t.value_min is not None else None)
    authority_chain = None
    if org and org.authority_bands:
        authority_chain = []
        v = float(value or 0)
        for b in sorted(org.authority_bands, key=lambda x: x.get("min_value") or 0):
            lo = float(b.get("min_value") or 0)
            hi = b.get("max_value")
            if v >= lo and (hi is None or v <= float(hi)):
                authority_chain.append({
                    "band": f"{lo:,.0f}–{hi:,.0f}" if hi else f"{lo:,.0f}+",
                    "review_role_title": b.get("review_role_title"),
                    "approval_role_title": b.get("approval_role_title"),
                    "committee_required": bool(b.get("committee_required", False)),
                })
    return {
        **{
            "id": t.id, "tender_no": t.tender_no, "title": t.title, "description": t.description,
            "department": t.department, "organization": t.organization, "district": t.district,
            "state": t.state, "category": t.category, "product_service_category": t.product_service_category,
            "procurement_method": t.procurement_method,
            "procurement_mode": t.effective_procurement_mode,
            "procurement_mode_label": PROCUREMENT_MODE_LABELS.get(t.effective_procurement_mode, t.effective_procurement_mode),
            "financial_bid_required": t.requires_financial_bid,
            "reverse_auction_note": (
                "Reverse Auction may be conducted after technical qualification according to tender conditions."
                if t.effective_procurement_mode == "E_BID_WITH_REVERSE_AUCTION" else None
            ),
            "procuring_organization": (
                {
                    "id": org.id, "name": org.name, "short_code": org.short_code,
                    "org_type": org.org_type, "parent_ministry": org.parent_ministry,
                    "procurement_division": org.procurement_division,
                    "committee_min_members": org.committee_min_members,
                    "committee_requires_finance": org.committee_requires_finance,
                    "committee_requires_user_rep": org.committee_requires_user_rep,
                }
                if org else None
            ),
            "authority_chain": authority_chain,
            "corrigenda": [
                {"seq_no": c.seq_no, "title": c.title, "summary": c.summary,
                 "changes": c.changes or [], "published_at": c.published_at.isoformat() if c.published_at else None,
                 "deadline_extended_to": c.deadline_extended_to.isoformat() if c.deadline_extended_to else None}
                for c in t.corrigenda
            ],
            "value_min": float(t.value_min) if t.value_min is not None else None,
            "value_max": float(t.value_max) if t.value_max is not None else None,
            "close_date": t.close_date.isoformat(), "open_date": t.open_date.isoformat() if t.open_date else None,
            "msme_preference": t.msme_preference, "make_in_india_required": t.make_in_india_required,
            "startup_eligible": t.startup_eligible, "required_turnover": t.required_turnover,
            "min_local_content": t.min_local_content,
            "required_document_types": t.required_document_types or [],
            "notes": t.notes,
            "payment_requirements": _payment_requirements(db, user, t),
        },
        "existing_application": (
            {
                "application_no": existing_app.application_no,
                "status": existing_app.status,
                "compliance_score": existing_app.compliance_score,
                "submitted_at": existing_app.submitted_at.isoformat() if existing_app.submitted_at else None,
            }
            if existing_app
            else None
        ),
        "eligibility": {
            "result": ev["eligibility_result"],
            "label": eligibility_label(ev["eligibility_result"]),
            "apply_allowed": ev["apply_allowed"],
            "apply_blocked_reasons": ev["apply_blocked_reasons"],
            "compliance_score": ev["compliance_score"],
            "risk_level": ev["risk_level"],
            "risk_label": risk_label(ev["risk_level"]),
            "checklist": [
                {
                    "rule_key": r["rule_key"], "label": r["label"], "source": r["source"],
                    "mandatory": r["mandatory"], "result": r["result"], "message": r["message"],
                    "hard_disqualification": r["hard_disqualification"],
                    "compliance_layer": r.get("compliance_layer", "STATUTORY"),
                    "clause_ref": r.get("clause_ref"),
                }
                for r in ev["rule_results"]
            ],
            "missing_documents": ev["missing_documents"],
            "mismatches": ev["mismatches"],
            "blacklist_findings": ev["blacklist_findings"],
            "source_results": {s: {"status": v.get("status"), "message": v.get("message"), "retrieved_at": v.get("retrieved_at")} for s, v in ev["source_results"].items()},
        },
    }


# ---------------- applications ----------------

class FinancialBidIn(BaseModel):
    """Commercial quotation (financial envelope). Amounts are decimal
    strings (e.g. "95000000.00") — the server quantizes to 2 dp with
    exact Decimal arithmetic; no float math anywhere in the path."""
    base_amount: str
    tax_amount: str = "0"
    other_charges: str = "0"
    currency: str = "INR"
    price_valid_days: int = 90
    commercial_declaration: str = Field(min_length=20)
    idempotency_key: str | None = None


class ApplyIn(BaseModel):
    declaration_text: str = Field(min_length=40)
    consent_confirmed: bool
    additional_files: list[str] = Field(default_factory=list)  # temp ids not supported; use upload first
    remarks: str | None = None
    # Financial envelope (required when tender.financial_bid_required).
    financial_bid: FinancialBidIn | None = None


@router.post("/applications")
def submit_application(
    tender_id: str,
    body: ApplyIn,
    request: Request,
    user: User = Depends(get_current_bidder),
    db: Session = Depends(get_db),
):
    t = db.get(Tender, tender_id)
    if not t or t.status != "PUBLISHED":
        raise HTTPException(status_code=404, detail="Tender not found or not open")
    if t.close_date < date.today():
        raise HTTPException(status_code=400, detail="Tender is closed")
    if not body.consent_confirmed:
        raise HTTPException(status_code=400, detail="Compliance consent confirmation is required")
    # Integrity gate BEFORE the timing gate: a debarred/hard-failed bidder is
    # blocked on every tender unconditionally — the real reason must be
    # reported even when the deadline has passed (or was force-opened).
    ev = evaluate_tender(db, user, t)
    if not ev["apply_allowed"]:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "APPLICATION_BLOCKED",
                "message": "Application cannot be submitted: confirmed hard requirement failure. See reasons.",
                "reasons": ev["apply_blocked_reasons"],
            },
        )
    if not fin_svc.tender_accepting_bids(t):
        raise HTTPException(status_code=400, detail={
            "code": "DEADLINE_PASSED",
            "message": "Tender submission deadline has passed."})
    pay_req = _payment_requirements(db, user, t)
    if not pay_req["all_satisfied"]:
        missing = []
        if not pay_req["application_fee"]["paid"]: missing.append("Application/Tender Fee")
        if not pay_req["emd"]["paid"]: missing.append("EMD/Bid Security")
        raise HTTPException(status_code=402, detail={
            "code": "PAYMENT_REQUIRED",
            "message": "Complete the required tender payment(s) before submitting the application.",
            "missing": missing,
            "requirements": pay_req,
        })

    if t.requires_financial_bid and body.financial_bid is None:
        raise HTTPException(status_code=400, detail={
            "code": "FINANCIAL_BID_REQUIRED",
            "message": "This tender requires a financial quotation. Provide the financial_bid section with your quoted amount."})
    # Financial envelope — validated up front so a bad quote 422s before any
    # row exists; sealed atomically with the application below.
    quote = None
    if body.financial_bid is not None:
        quote = fin_svc.validate_financial_quote(
            base=body.financial_bid.base_amount, tax=body.financial_bid.tax_amount,
            other=body.financial_bid.other_charges, currency=body.financial_bid.currency,
            price_valid_days=body.financial_bid.price_valid_days)

    count = db.query(Application).count() + 1
    app_no = f"APP-2026-{count:06d}"
    submitted_at = utcnow()
    application = Application(
        tender_id=t.id, bidder_user_id=user.id, application_no=app_no,
        status="Submitted",
        eligibility_result=ev["eligibility_result"],
        compliance_score=ev["compliance_score"],
        risk_level=ev["risk_level"],
        declaration_text=body.declaration_text,
        declaration_at=submitted_at,
        submitted_at=submitted_at,
        # Bid-time snapshot: documents attached at this moment are frozen;
        # later additions only via clarification responses.
        snapshot_frozen_at=submitted_at,
    )
    db.add(application)
    db.flush()

    # 11-stage pipeline: SUBMISSION completes now (certificate of submission)
    from .. import pipeline as pl

    pl.init_pipeline(db, application)
    pl.stage_rows(db, application.id)["SUBMISSION"].certificate_no = (
        f"CERT-SUB-{application.application_no.replace('-', '')}"
    )

    # Financial envelope: sealed atomically with the submission (§11).
    # Audit carries the hash + version — not the plaintext amount.
    if quote is not None:
        from ..ipcontext import resolve_client_ip as _ipc
        ip_addr, _ip_src = _ipc(request.client.host if request.client else None,
                                request.headers.get("x-forwarded-for"),
                                request.headers.get("x-real-ip"))
        fin_bid = fin_svc.create_financial_bid(
            db, app=application, tender=t, user=user, quote=quote,
            commercial_declaration=body.financial_bid.commercial_declaration,
            ip_address=ip_addr,
            user_agent=(request.headers.get("user-agent") or "")[:290] or None,
            request_id=getattr(request.state, "request_id", None),
            idempotency_key=body.financial_bid.idempotency_key,
            commit=False)
        append_audit(db, "financial_bid_sealed", "FinancialBid", fin_bid.id,
                     actor_id=user.id, actor_role="BIDDER",
                     tender_id=t.id, application_id=application.id,
                     payload={"version": fin_bid.version, "hash": fin_bid.financial_bid_hash},
                     request=request, commit=False)

    # auto-attach verified vault documents
    for doc in db.query(Document).filter(Document.user_id == user.id).all():
        if doc.verification_status in ("VERIFIED", "MANUAL_UPLOADED", "PENDING"):
            db.add(ApplicationDocument(
                application_id=application.id, document_id=doc.id, name=doc.name,
                doc_type=doc.doc_type, file_path=doc.file_path, purpose="ORIGINAL", uploaded_by=user.id,
            ))
    db.flush()

    # jurisdiction routing
    from ..services.routing import route_application

    routing = route_application(db, application, t)
    set_status(db, application, "AI Verification Not Run", None, f"Routed via {routing['method']}")

    if routing["fallback"]:
        from ..notifications import notify_admins

        notify_admins(db, "routing_fallback",
                      message=f"{app_no} routed via {routing['method']} (no exact jurisdiction match)",
                      entity_type="Application", entity_id=application.id)
    if application.assigned_l1_id:
        notify(db, application.assigned_l1_id, "application_submitted",
               message=f"New application {app_no} for {t.tender_no} — {user.bidder_profile.company_name if user.bidder_profile else user.full_name}",
               entity_type="Application", entity_id=application.id)
    notify(db, user.id, "application_routed",
           message=f"Application {app_no} submitted and routed for L1 review.",
           entity_type="Application", entity_id=application.id)
    append_audit(db, "application_submitted", "Application", application.id,
                 actor_id=user.id, actor_role="BIDDER",
                 payload={"tender": t.tender_no, "routing": routing, "eligibility": ev["eligibility_result"]},
                 request=request,
                 user_agent=request.headers.get("user-agent", "")[:290],
                 commit=False)
    from sqlalchemy.exc import IntegrityError

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail={
            "code": "DUPLICATE_BID_REQUEST",
            "message": "Duplicate bid request — the idempotency key was already used."})
    return {"application_id": application.id, "application_no": app_no, "status": application.status,
            "routing": routing,
            "financial_envelope": fin_svc.financial_envelope_status(db, application, owner=True)}


@router.get("/applications")
def list_applications(user: User = Depends(get_current_bidder), db: Session = Depends(get_db)):
    apps = db.query(Application).filter(Application.bidder_user_id == user.id).order_by(Application.created_at.desc()).all()
    return {
        "applications": [
            {
                "id": a.id, "application_no": a.application_no,
                "tender_no": a.tender.tender_no, "tender_title": a.tender.title,
                "status": a.status, "eligibility_result": a.eligibility_result,
                "compliance_score": a.compliance_score, "risk_level": a.risk_level,
                "submitted_at": a.submitted_at.isoformat() if a.submitted_at else None,
            }
            for a in apps
        ]
    }


def _timeline(app: Application) -> list[dict]:
    return [
        {"status": h.status, "remarks": h.remarks, "at": h.created_at.isoformat(), "actor_role": h.actor_role}
        for h in app.history
    ]


@router.get("/applications/{application_id}")
def application_detail(application_id: str, user: User = Depends(get_current_bidder), db: Session = Depends(get_db)):
    a = db.get(Application, application_id)
    if not a or a.bidder_user_id != user.id:
        raise HTTPException(status_code=404, detail="Application not found")
    clarifications = db.query(Clarification).filter(Clarification.application_id == a.id).order_by(Clarification.created_at.desc()).all()
    from ..models import VerificationReport

    report = (
        db.query(VerificationReport).filter(VerificationReport.application_id == a.id)
        .order_by(VerificationReport.generated_at.desc()).first()
    )
    # legally shareable remarks only (status history remarks)
    return {
        "id": a.id, "application_no": a.application_no, "status": a.status,
        "tender": {"tender_no": a.tender.tender_no, "title": a.tender.title, "close_date": a.tender.close_date.isoformat()},
        "eligibility_result": a.eligibility_result, "compliance_score": a.compliance_score,
        "risk_level": a.risk_level, "risk_label": risk_label(a.risk_level),
        "report_stale": a.report_stale,
        "submitted_at": a.submitted_at.isoformat() if a.submitted_at else None,
        "declaration": a.declaration_text,
        "financial_envelope": fin_svc.financial_envelope_status(db, a, owner=True),
        "timeline": _timeline(a),
        "next_step": _next_step(a.status),
        "report_summary": (
            {
                "generated_at": report.generated_at.isoformat(),
                "risk_level": report.risk_level, "eligibility_result": report.eligibility_result,
                "compliance_score": report.compliance_score,
                "ai_explanation": report.ai_explanation,
                "mismatches": report.mismatches,
                "missing_documents": report.missing_documents,
                "blacklist_findings": report.blacklist_findings,
            }
            if report
            else None
        ),
        "clarifications": [
            {
                "id": c.id, "reason": c.reason, "documents_needed": c.documents_needed,
                "deadline": c.deadline.isoformat() if c.deadline else None, "status": c.status,
                "response_text": c.response_text, "responded_at": c.responded_at.isoformat() if c.responded_at else None,
                "created_at": c.created_at.isoformat(),
            }
            for c in clarifications
        ],
    }


# ---------------- rejection explanation (bidder-facing) ----------------

_REDACT_PATTERNS = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "[redacted contact]"),
    (re.compile(r"\bAPP-\d{4}-\d{6}\b"), "[application reference]"),
    (re.compile(r"(?i)\b(first-lowest|second-lowest|third-lowest)\s+evaluation committee\b"), "the evaluation committee"),
    (re.compile(r"(?i)\b(vigilance|admin co-?sign(?:ature)?|co-?sign(?:ature)?|override(?:d| record)?|escalat\w*|audit trail|hash chain\w*|internal referral\w*)\b"), "internal review"),
    (re.compile(r"(?i)\b(first-lowest|second-lowest|third-lowest)\b"), "the evaluation"),
    (re.compile(r"\bL[12]\b"), "the evaluation committee"),
]


def _sanitize_for_bidder(text: str | None) -> str:
    """Strip internal workflow references so only the substantive reason
    reaches the bidder."""
    if not text:
        return ""
    s = text
    for pat, repl in _REDACT_PATTERNS:
        s = pat.sub(repl, s)
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip()


def _rejection_reason(db: Session, a: Application):
    """(remarks, decided_by_label, at) — latest rejection decision remarks,
    falling back to the status-history remarks."""
    dec = (
        db.query(OfficerDecision)
        .filter(
            OfficerDecision.application_id == a.id,
            OfficerDecision.action.in_(["REJECT", "RECOMMEND_REJECT"]),
        )
        .order_by(OfficerDecision.created_at.desc())
        .first()
    )
    if dec and (dec.remarks or "").strip():
        return dec.remarks, "Evaluation committee (second-line review)", dec.created_at
    hist = (
        db.query(ApplicationStatusHistory)
        .filter(
            ApplicationStatusHistory.application_id == a.id,
            ApplicationStatusHistory.status == "Rejected",
        )
        .order_by(ApplicationStatusHistory.created_at.desc())
        .first()
    )
    if hist and (hist.remarks or "").strip():
        return hist.remarks, "Evaluation committee", hist.created_at
    return None, "Evaluation committee", (hist.created_at if hist else None)


def _rejection_fallback(db: Session, a: Application) -> str:
    """Neutral explanation derived from the bidder's own verification report
    (mandatory-check failures on their own declarations/documents)."""
    report = (
        db.query(VerificationReport).filter(VerificationReport.application_id == a.id)
        .order_by(VerificationReport.generated_at.desc()).first()
    )
    if not report:
        return (
            "The evaluation committee determined that the application could not be qualified "
            "against the mandatory compliance requirements of the tender."
        )
    fails = [r for r in (report.rule_results or []) if r.get("result") == "FAIL" and r.get("mandatory")]
    unv = [r for r in (report.rule_results or []) if r.get("result") == "UNABLE_TO_VERIFY"]
    parts = [
        "Your application was not qualified. The deterministic compliance evaluation recorded the "
        "following against your declarations and documents for this tender:"
    ]
    for r in fails:
        clause = f" (clause {r['clause_ref']})" if r.get("clause_ref") else ""
        parts.append(f"• {r['label']}{clause} — {r['message']}")
    for r in unv:
        parts.append(f"• {r['label']} — could not be verified against the official source; treated as unverified.")
    if report.missing_documents:
        parts.append("• Missing mandatory documents: " + ", ".join(report.missing_documents) + ".")
    if report.expired_documents:
        parts.append("• Expired documents: " + ", ".join(d["name"] for d in report.expired_documents) + ".")
    parts.append(
        "The final decision remains with the evaluation committee. If you believe the source data is "
        "in error, you may update your documents and raise a grievance through the portal."
    )
    return "\n".join(parts)


@router.get("/applications/{application_id}/rejection-explanation")
def rejection_explanation(application_id: str, user: User = Depends(get_current_bidder), db: Session = Depends(get_db)):
    a = db.get(Application, application_id)
    if not a or a.bidder_user_id != user.id:
        raise HTTPException(status_code=404, detail="Application not found")
    if a.status != "Rejected":
        raise HTTPException(status_code=404, detail="No rejection recorded for this application")
    remarks, decided_by, at = _rejection_reason(db, a)
    explanation = _sanitize_for_bidder(remarks) or _rejection_fallback(db, a)
    return {
        "application_no": a.application_no,
        "tender_no": a.tender.tender_no,
        "tender_title": a.tender.title,
        "rejected_at": at.isoformat() if at else None,
        "decided_by": decided_by,
        "explanation": explanation,
        "appeal_note": (
            "If you believe this outcome is in error, you may raise a grievance/appeal through the "
            "portal's grievance facility."
        ),
    }


@router.get("/applications/{application_id}/rejection-explanation/download")
def rejection_explanation_download(
    application_id: str, user: User = Depends(get_current_bidder), db: Session = Depends(get_db)
):
    """Download the rejection explanation. PDF is the only downloadable format."""
    from ..reports import rejection_to_pdf

    a = db.get(Application, application_id)
    if not a or a.bidder_user_id != user.id:
        raise HTTPException(status_code=404, detail="Application not found")
    if a.status != "Rejected":
        raise HTTPException(status_code=404, detail="No rejection recorded for this application")
    remarks, decided_by, at = _rejection_reason(db, a)
    explanation = _sanitize_for_bidder(remarks) or _rejection_fallback(db, a)
    append_audit(db, "rejection_explanation_download", "Application", a.id,
                 actor_id=user.id, actor_role="BIDDER", commit=False)
    db.commit()
    pdf = rejection_to_pdf(a, a.tender, {
        "explanation": explanation,
        "decided_by": decided_by,
        "rejected_at_human": at.strftime("%Y-%m-%d %H:%M UTC") if at else None,
    })
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename=rejection-explanation-{a.application_no}.pdf",
            "Cache-Control": "no-store",
        },
    )


def _next_step(status: str) -> str:
    return {
        "Submitted": "L1 review in progress",
        "AI Verification Not Run": "L1 will run AI verification",
        "AI Verification In Progress": "Verification running",
        "Pending L1 Review": "Awaiting L1 recommendation",
        "Clarification Requested": "Respond to the clarification request",
        "Resubmitted": "Awaiting fresh verification",
        "L1 Recommended": "Awaiting L2 approval",
        "Pending L2 Approval": "Awaiting L2 decision",
        "Admin Co-sign Required": "Critical override — Admin co-sign in progress",
        "Approved": "Application approved",
        "Rejected": "Rejected — grievance/appeal possible if you believe it is in error",
        "Withdrawn": "Withdrawn",
        "Closed": "Closed",
    }.get(status, status)


class ClarificationResponseIn(BaseModel):
    clarification_id: str
    response_text: str = Field(min_length=5)


@router.post("/applications/{application_id}/clarification-response")
def respond_clarification(
    application_id: str,
    body: ClarificationResponseIn,
    request: Request,
    user: User = Depends(get_current_bidder),
    db: Session = Depends(get_db),
):
    a = db.get(Application, application_id)
    if not a or a.bidder_user_id != user.id:
        raise HTTPException(status_code=404, detail="Application not found")
    c = db.get(Clarification, body.clarification_id)
    if not c or c.application_id != a.id:
        raise HTTPException(status_code=404, detail="Clarification not found")
    if c.status != "OPEN":
        raise HTTPException(status_code=400, detail="Clarification already responded")
    c.status = "RESPONDED"
    c.response_text = body.response_text
    c.responded_at = utcnow()
    # resubmit same application (no new application created)
    set_status(db, a, "Resubmitted", user, f"Clarification {c.id} responded")
    mark_report_stale(db, a, "clarification response received")
    if a.assigned_l1_id:
        notify(db, a.assigned_l1_id, "clarification_responded",
               message=f"Bidder responded to clarification on {a.application_no}. Report is now stale — fresh verification recommended.",
               entity_type="Application", entity_id=a.id)
    append_audit(db, "clarification_responded", "Clarification", c.id, actor_id=user.id, actor_role="BIDDER",
                 request=request, commit=False)
    db.commit()
    return {"status": a.status, "message": "Response recorded. Your corrected evidence may make the verification report stale."}


@router.post("/applications/{application_id}/clarification-response/upload")
def clarification_upload(
    request: Request,
    user: User = Depends(get_current_bidder),
    db: Session = Depends(get_db),
    clarification_id: str = Form(...),
    response_text: str = Form(""),
    file: UploadFile = File(...),
):
    """Upload a corrected/new document against a clarification without creating a new application."""
    a = db.get(Application, application_id)
    if not a or a.bidder_user_id != user.id:
        raise HTTPException(status_code=404, detail="Application not found")
    c = db.get(Clarification, clarification_id)
    if not c or c.application_id != a.id or c.status != "OPEN":
        raise HTTPException(status_code=400, detail="Clarification not open")
    content = file.file.read()
    try:
        safe_name, digest = save_upload(content, file.filename or "correction.bin", kind="clarifications")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    doc_type = (file.filename or "").lower()
    guessed = "OTHER"
    if "oem" in doc_type:
        guessed = "OEM_AUTHORIZATION"
    elif "gst" in doc_type:
        guessed = "GST_CERT"
    elif "udyam" in doc_type:
        guessed = "UDYAM_CERT"
    doc = Document(
        user_id=user.id, name=file.filename or "Correction", doc_type=guessed, source="MANUAL",
        source_classification="BIDDER_UPLOADED", verification_status="PENDING",
        file_path=safe_name, file_type=os.path.splitext(safe_name)[1].lstrip("."), document_hash=digest,
        version=(
            db.query(Document).filter(Document.user_id == user.id, Document.doc_type == guessed).count() + 1
        ),
    )
    db.add(doc)
    db.flush()
    db.add(ApplicationDocument(
        application_id=a.id, document_id=doc.id, name=doc.name, doc_type=doc.doc_type,
        file_path=safe_name, purpose="CLARIFICATION_RESPONSE", uploaded_by=user.id,
    ))
    c.status = "RESPONDED"
    c.response_text = response_text or f"Uploaded correction: {file.filename}"
    c.responded_at = utcnow()
    set_status(db, a, "Resubmitted", user, f"Clarification {c.id} responded with upload")
    mark_report_stale(db, a, "clarification document uploaded")
    if a.assigned_l1_id:
        notify(db, a.assigned_l1_id, "clarification_responded",
               message=f"Corrected document uploaded for {a.application_no}. Fresh verification recommended.",
               entity_type="Application", entity_id=a.id)
    append_audit(db, "clarification_document_upload", "Clarification", c.id, actor_id=user.id, actor_role="BIDDER",
                 payload={"sha256": digest}, commit=False)
    db.commit()
    return {"status": a.status, "document": _doc_out(doc)}


@router.put("/applications/{application_id}/financial-bid")
def modify_financial_bid(application_id: str, body: FinancialBidIn,
                         request: Request, user: User = Depends(get_current_bidder),
                         db: Session = Depends(get_db)):
    """Pre-deadline modification of the financial envelope.

    Preserves the previous version (REPLACED, never overwritten), creates a
    new version with a hash chained from the previous one, reseals, and
    audits. Only allowed while the tender is still accepting bids and the
    application is not in a terminal state.
    """
    a = db.get(Application, application_id)
    if not a or a.bidder_user_id != user.id:
        raise HTTPException(status_code=404, detail="Application not found")
    if a.status in ("Withdrawn", "Approved", "Rejected", "Closed"):
        raise HTTPException(status_code=400, detail={
            "code": "MODIFICATION_NOT_ALLOWED",
            "message": f"The financial bid cannot be modified in status: {a.status}"})
    if not fin_svc.tender_accepting_bids(a.tender):
        raise HTTPException(status_code=400, detail={
            "code": "DEADLINE_PASSED",
            "message": "Tender submission deadline has passed — the bid is final."})
    # Re-check the eligibility gate: a blacklist action after submission
    # must block further financial modifications.
    ev = evaluate_tender(db, user, a.tender)
    if not ev["apply_allowed"]:
        raise HTTPException(status_code=409, detail={
            "code": "MODIFICATION_BLOCKED",
            "message": "Financial bid cannot be modified: confirmed hard requirement failure.",
            "reasons": ev["apply_blocked_reasons"]})

    quote = fin_svc.validate_financial_quote(
        base=body.base_amount, tax=body.tax_amount, other=body.other_charges,
        currency=body.currency, price_valid_days=body.price_valid_days)

    from sqlalchemy.exc import IntegrityError
    from ..ipcontext import resolve_client_ip as _ipc

    prev = fin_svc.active_financial_bid(db, a.id)
    if body.idempotency_key:
        existing = (db.query(FinancialBid)
                    .filter(FinancialBid.application_id == a.id,
                            FinancialBid.idempotency_key == body.idempotency_key).first())
        if existing is not None:
            return {"application_id": a.id, "version": existing.version,
                    "status": existing.status, "duplicate": True,
                    "financial_envelope": fin_svc.financial_envelope_status(db, a, owner=True)}
    version = (prev.version if prev else 0) + 1
    ip_addr, _ip_src = _ipc(request.client.host if request.client else None,
                            request.headers.get("x-forwarded-for"),
                            request.headers.get("x-real-ip"))
    try:
        fb = fin_svc.create_financial_bid(
            db, app=a, tender=a.tender, user=user, quote=quote,
            commercial_declaration=body.commercial_declaration,
            version=version, prev=prev,
            ip_address=ip_addr,
            user_agent=(request.headers.get("user-agent") or "")[:290] or None,
            request_id=getattr(request.state, "request_id", None),
            idempotency_key=body.idempotency_key, commit=False)
        append_audit(db, "financial_bid_modified", "FinancialBid", fb.id,
                     actor_id=user.id, actor_role="BIDDER",
                     tender_id=a.tender_id, application_id=a.id,
                     payload={"version": version, "replaces": prev.version if prev else None,
                              "hash": fb.financial_bid_hash},
                     request=request, commit=False)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail={
            "code": "DUPLICATE_BID_REQUEST",
            "message": "Duplicate bid request — the idempotency key was already used."})
    return {"application_id": a.id, "version": version, "status": fb.status, "duplicate": False,
            "financial_envelope": fin_svc.financial_envelope_status(db, a, owner=True)}


@router.post("/applications/{application_id}/withdraw")
def withdraw(application_id: str, user: User = Depends(get_current_bidder), db: Session = Depends(get_db)):
    a = db.get(Application, application_id)
    if not a or a.bidder_user_id != user.id:
        raise HTTPException(status_code=404, detail="Application not found")
    if a.status in ("Approved", "Rejected", "Withdrawn", "Closed"):
        raise HTTPException(status_code=400, detail=f"Cannot withdraw from status: {a.status}")
    set_status(db, a, "Withdrawn", user, "Withdrawn by bidder")
    append_audit(db, "application_withdrawn", "Application", a.id, actor_id=user.id, actor_role="BIDDER", commit=False)
    # Financial envelope: mark WITHDRAWN (record retained, never deleted);
    # withdrawn bids do not participate in evaluation or auction qualification.
    fb = fin_svc.active_financial_bid(db, a.id)
    if fb is not None:
        fb.status = "WITHDRAWN"
        fb.withdrawn_at = utcnow()
        append_audit(db, "financial_bid_withdrawn", "FinancialBid", fb.id,
                     actor_id=user.id, actor_role="BIDDER",
                     tender_id=a.tender_id, application_id=a.id,
                     payload={"version": fb.version, "hash": fb.financial_bid_hash}, commit=False)
    db.commit()
    return {"status": a.status}


# ---------------- grievance / appeal ----------------

class GrievanceIn(BaseModel):
    application_id: str
    reason: str = Field(min_length=10)
    details: str | None = None


@router.post("/grievances")
def file_grievance(body: GrievanceIn, request: Request, user: User = Depends(get_current_bidder), db: Session = Depends(get_db)):
    a = db.get(Application, body.application_id)
    if not a or a.bidder_user_id != user.id:
        raise HTTPException(status_code=404, detail="Application not found")
    if a.status not in ("Rejected", "Closed"):
        raise HTTPException(status_code=400, detail="Grievance is available only for rejected/closed decisions")
    g = Grievance(application_id=a.id, bidder_user_id=user.id, reason=body.reason, details=body.details)
    db.add(g)
    db.flush()
    from ..notifications import notify_admins

    notify_admins(db, "l1_recommendation_submitted",
                  title="Grievance/appeal submitted",
                  message=f"Grievance on {a.application_no}: {body.reason[:160]}",
                  entity_type="Grievance", entity_id=g.id)
    append_audit(db, "grievance_submitted", "Grievance", g.id, actor_id=user.id, actor_role="BIDDER", commit=False)
    db.commit()
    return {"grievance_id": g.id, "status": g.status}


@router.get("/grievances")
def list_grievances(user: User = Depends(get_current_bidder), db: Session = Depends(get_db)):
    rows = db.query(Grievance).filter(Grievance.bidder_user_id == user.id).order_by(Grievance.created_at.desc()).all()
    return {
        "grievances": [
            {"id": g.id, "application_no": g.application.application_no, "reason": g.reason, "status": g.status,
             "admin_response": g.admin_response, "created_at": g.created_at.isoformat()}
            for g in rows
        ]
    }
