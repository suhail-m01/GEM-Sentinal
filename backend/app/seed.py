"""Seed script — realistic users, documents, tenders, blacklist and the four
demo scenarios (A clean pass, B compliance gap, C blacklist override,
D unable-to-verify).

Usage: python -m app.seed  (from backend/)
"""
import hashlib
import os
import random
import sys
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING

from .core.db import Base, SessionLocal, engine, utcnow
from .core.security import encrypt_field, hash_password, mask_gstin, mask_pan, mask_udyam

if TYPE_CHECKING:
    from PIL import Image
from .models import (
    Application,
    ApplicationStatusHistory,
    AuditLog,
    BidderProfile,
    BlacklistEntry,
    ConsentRecord,
    Document,
    GovEmployee,
    Jurisdiction,
    OfficerAssignment,
    Organization,
    OrganizationMember,
    ProcuringOrganization,
    Tender,
    TenderAssignment,
    TenderCorrigendum,
    TenderRule,
    TrustedReference,
    User,
)
from . import audit

STORAGE = os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "storage"))


# ---------------------------------------------------------------------------
# image generation (PIL) — certificate lookalikes with seals & signatures
# ---------------------------------------------------------------------------

def _font(size: int):
    from PIL import ImageFont

    try:
        return ImageFont.load_default(size)
    except TypeError:
        return ImageFont.load_default()


def make_seal(size=220, color=(178, 34, 52), label="CPCL") -> "Image.Image":
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (size, size), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.ellipse([8, 8, size - 8, size - 8], outline=color, width=6)
    d.ellipse([26, 26, size - 26, size - 26], outline=color, width=2)
    d.ellipse([44, 44, size - 44, size - 44], outline=color, width=1)
    d.text((size // 2, size // 2 - 14), label, fill=color, anchor="mm", font=_font(26))
    d.text((size // 2, size // 2 + 18), "PROCUREMENT", fill=color, anchor="mm", font=_font(12))
    d.text((size // 2, size // 2 + 38), "BELAGAVI", fill=color, anchor="mm", font=_font(12))
    for a in range(0, 360, 30):
        import math

        x = size // 2 + int(0.36 * size * math.cos(math.radians(a)))
        y = size // 2 + int(0.36 * size * math.sin(math.radians(a)))
        d.ellipse([x - 2, y - 2, x + 2, y + 2], fill=color)
    return img


def make_alt_seal(size=220):
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (size, size), (255, 255, 255))
    d = ImageDraw.Draw(img)
    # deliberately different stamp: blue rounded rectangle
    d.rounded_rectangle([20, 20, size - 20, size - 20], radius=24, outline=(28, 60, 160), width=6)
    d.text((size // 2, size // 2 - 10), "GEN. SUPPLIES", fill=(28, 60, 160), anchor="mm", font=_font(20))
    d.text((size // 2, size // 2 + 22), "KARNATAKA", fill=(28, 60, 160), anchor="mm", font=_font(14))
    return img


def make_signature(w=260, h=90, seed=7) -> "Image.Image":
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (w, h), (255, 255, 255))
    d = ImageDraw.Draw(img)
    rnd = random.Random(seed)
    x, y = 10, h // 2
    pts = [(x, y)]
    for i in range(60):
        x += rnd.randint(2, 5)
        y += rnd.randint(-14, 14)
        y = max(8, min(h - 8, y))
        pts.append((x, y))
    for i in range(len(pts) - 1):
        d.line([pts[i], pts[i + 1]], fill=(20, 30, 90), width=2)
    d.line([(10, h - 12), (w - 40, h - 12)], fill=(20, 30, 90), width=1)
    return img


def make_certificate(
    company: str,
    doc_label: str,
    rows: list[tuple[str, str]],
    seal,
    signature,
    issue: str,
    valid_until: str | None,
    issuer: str,
    suffix: str,
) -> tuple[str, str]:
    """Renders a certificate-like image. Returns (filename, sha256)."""
    from PIL import Image, ImageDraw

    W, H = 900, 1100
    img = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 90], fill=(11, 37, 69))
    d.text((W // 2, 45), "CERTIFICATE OF REGISTRATION", fill="white", anchor="mm", font=_font(30))
    d.rectangle([0, 90, W, 96], fill=(13, 148, 136))
    d.text((W // 2, 150), doc_label, fill=(30, 41, 59), anchor="mm", font=_font(24))
    d.text((W // 2, 200), company, fill=(11, 37, 69), anchor="mm", font=_font(34))
    y = 300
    for label, value in rows:
        d.text((80, y), f"{label}:", fill=(71, 85, 105), font=_font(20))
        d.text((320, y), value, fill=(15, 23, 42), font=_font(20))
        y += 46
    d.text((80, y + 10), f"Issue Date: {issue}", fill=(71, 85, 105), font=_font(20))
    d.text((400, y + 10), f"Valid Until: {valid_until or 'N/A'}", fill=(71, 85, 105), font=_font(20))
    d.text((80, y + 56), f"Issuer: {issuer}", fill=(71, 85, 105), font=_font(20))
    d.line([(60, y + 110), (W - 60, y + 110)], fill=(203, 213, 225), width=2)
    img.paste(seal.resize((200, 200)), (W - 290, 640))
    d.text((W - 190, 860), "Authorized Seal", fill=(100, 116, 139), anchor="mm", font=_font(14))
    img.paste(signature.resize((240, 84)), (70, 850))
    d.text((190, 950), "Authorized Signatory", fill=(100, 116, 139), anchor="mm", font=_font(14))
    os.makedirs(os.path.join(STORAGE, "docs"), exist_ok=True)
    fname = f"cert_{suffix}.png"
    img.save(os.path.join(STORAGE, "docs", fname))
    with open(os.path.join(STORAGE, "docs", fname), "rb") as f:
        return fname, hashlib.sha256(f.read()).hexdigest()


def make_mca_pdf(company: str, cin: str, fname: str) -> str:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas as pdfcanvas

    path = os.path.join(STORAGE, "docs", fname)
    os.makedirs(os.path.join(STORAGE, "docs"), exist_ok=True)
    c = pdfcanvas.Canvas(path, pagesize=A4)
    w, h = A4
    c.setFont("Helvetica-Bold", 18)
    c.drawCentredString(w / 2, h - 80, "MINISTRY OF CORPORATE AFFAIRS — INCORPORATION CERTIFICATE")
    c.drawString(80, h - 200, f"Company: {company}")
    c.drawString(80, h - 230, f"CIN: {cin}")
    c.drawString(80, h - 260, "Incorporation Date: 12/04/2015")
    c.drawString(80, h - 290, "Status: Active")
    c.save()
    return fname


def save_trusted(img, kind: str, label: str) -> str:
    from PIL import Image

    os.makedirs(os.path.join(STORAGE, "trusted"), exist_ok=True)
    fname = f"trusted_{kind}.png"
    img.save(os.path.join(STORAGE, "trusted", fname))
    return fname


# ---------------------------------------------------------------------------
# seed
# ---------------------------------------------------------------------------

def reset_db():
    print("Resetting database schema...")
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def seed():
    reset_db()
    db = SessionLocal()
    try:
        _seed_all(db)
        db.commit()
        print("Seed complete.")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _seed_all(db):
    random.seed(42)
    today = date.today()

    # ---------------- jurisdictions ----------------
    j_belagavi_oil = Jurisdiction(code="KARNATAKA-BELAGAVI-OILGAS", district="Belagavi", state="Karnataka", department="Oil & Natural Gas", organization="CPCL", description="CPCL refinery operations, Belagavi")
    j_hubli_oil = Jurisdiction(code="KARNATAKA-HUBLI-OILGAS", district="Hubli", state="Karnataka", department="Oil & Natural Gas", organization="CPCL", description="CPCL operations, Hubli")
    j_belagavi_it = Jurisdiction(code="KARNATAKA-BELAGAVI-IT", district="Belagavi", state="Karnataka", department="IT & Digital Services", organization="CPCL", description="IT & digital services, Belagavi")
    j_state_oil = Jurisdiction(code="KARNATAKA-ANY-OILGAS", state="Karnataka", department="Oil & Natural Gas", organization="CPCL", description="State-level fallback for Oil & Gas")
    j_dept_oil = Jurisdiction(code="ANY-ANY-OILGAS", department="Oil & Natural Gas", organization="CPCL", description="Department-level fallback for Oil & Gas")
    for j in (j_belagavi_oil, j_hubli_oil, j_belagavi_it, j_state_oil, j_dept_oil):
        db.add(j)
    db.flush()

    # ---------------- users ----------------
    def mk_user(email, pwd, name, role, status="ACTIVE", mobile=None):
        u = User(email=email, mobile=mobile, password_hash=hash_password(pwd), full_name=name, role=role, status=status, is_verified=(status == "ACTIVE"))
        db.add(u)
        return u

    # --- Real OTP phone numbers (provided by the user) -----------------------
    # Order as provided: ramesh, sita, vikram, meena, admin (5th number is
    # shared with the primary demo officer for live OTP delivery).
    PHONE_RAMESH = "+919380962272"
    PHONE_SITA = "+919590337483"
    PHONE_VIKRAM = "+918861911639"
    PHONE_MEENA = "+919476003005"
    PHONE_ADMIN = "+919164346113"

    # V2.1 role model — explicit titles (L1/L2 are financial RANKS, not roles)
    admin = mk_user("admin@cpcl.gov.in", "Admin@1234", "S. Iyer", "ADMIN", mobile=PHONE_ADMIN)
    auditor = mk_user("auditor@cpcl.gov.in", "Auditor@1234", "N. Prasad", "AUDITOR")
    l1_bel = mk_user("procurement.belagavi@cpcl.gov.in", "Officer@1234", "Anita Deshmukh", "PROCUREMENT_OFFICER", mobile=PHONE_ADMIN)
    l1_hub = mk_user("procurement.hubli@cpcl.gov.in", "Officer1@1234", "Rahul Verma", "PROCUREMENT_OFFICER")
    l2_off = mk_user("finance.karnataka@cpcl.gov.in", "L2Officer@1234", "Meera Kulkarni", "FINANCE_EVALUATOR")
    tech_eval = mk_user("technical.karnataka@cpcl.gov.in", "TechEval@1234", "Farhan Ali", "TECHNICAL_EVALUATOR")
    opening_off = mk_user("bidopening.karnataka@cpcl.gov.in", "Opening@1234", "Divya Menon", "BID_OPENING_OFFICER")
    comm_chair = mk_user("committee.chair@cpcl.gov.in", "Chair@1234", "R. Krishnan", "COMMITTEE_CHAIR")
    comm_member = mk_user("committee.member@cpcl.gov.in", "Member@1234", "P. Sharma", "COMMITTEE_MEMBER")
    cfa_off = mk_user("cfa.karnataka@cpcl.gov.in", "CFA@1234", "A. Bhosale", "CFA")
    contract_mgr = mk_user("contract.karnataka@cpcl.gov.in", "Contract@1234", "S. Naik", "CONTRACT_MANAGER")
    contract_exec = mk_user("contractexecution.karnataka@cpcl.gov.in", "Execution@1234", "T. Iyer", "CONTRACT_OFFICER")
    inspection_off = mk_user("inspection.karnataka@cpcl.gov.in", "Inspect@1234", "V. Joshi", "INSPECTION_OFFICER")
    payment_off = mk_user("payment.karnataka@cpcl.gov.in", "Payment@1234", "K. Pillai", "PAYMENT_OFFICER")
    pending_off = mk_user("officer.new@cpcl.gov.in", "Pending@1234", "Karthik Reddy", "TECHNICAL_EVALUATOR", status="PENDING_APPROVAL")

    ramesh = mk_user("ramesh@bidder.in", "Bidder@1234", "Ramesh Kumar", "BIDDER", mobile=PHONE_RAMESH)
    sita = mk_user("sita@bidder.in", "Bidder@1234", "Sita Nair", "BIDDER", mobile=PHONE_SITA)
    vikram = mk_user("vikram@bidder.in", "Bidder@1234", "Vikram Rao", "BIDDER", mobile=PHONE_VIKRAM)
    meena = mk_user("meena@bidder.in", "Bidder@1234", "Meena Patil", "BIDDER", mobile=PHONE_MEENA)
    # RA-demo bidders (TDR-2026-010) — synthetic; console-OTP demo numbers
    arjun = mk_user("arjun@bidder.in", "Bidder@1234", "Arjun Mehta", "BIDDER", mobile="+919845000016")
    bharat = mk_user("bharat@bidder.in", "Bidder@1234", "Bharat Gokhale", "BIDDER", mobile="+919845000017")
    chetan = mk_user("chetan@bidder.in", "Bidder@1234", "Chetan Kulkarni", "BIDDER", mobile="+919845000018")
    delta = mk_user("delta@bidder.in", "Bidder@1234", "Deepak Iyer", "BIDDER", mobile="+919845000019")
    # Multi-role bidder organization (owner + preparer + signatory)
    ramesh_preparer = mk_user("preparer@rameshtraders.in", "Preparer@1234", "A. Ramesh Kumar", "BID_PREPARER", mobile=PHONE_RAMESH)
    ramesh_signatory = mk_user("signatory@rameshtraders.in", "Signatory@1234", "K. Ramesh (Director)", "SIGNATORY", mobile=PHONE_RAMESH)
    db.flush()

    # ---------------- government employee master (Govt ID + OTP login) ------
    # The System Admin's registry: govt unique id, registered phone (OTP
    # destination), role, approval status. Govt-portal sign-in uses ONLY
    # Govt ID + OTP — no email/password.
    def mk_gov(user, gid, phone, dept, status="ACTIVE"):
        e = GovEmployee(
            gov_id=gid, full_name=user.full_name, phone=phone, role=user.role,
            department=dept, status=status, user_id=user.id,
            created_by=admin.id,
            approved_by=(admin.id if status == "ACTIVE" else None),
            approved_at=(utcnow() if status == "ACTIVE" else None),
        )
        db.add(e)
        return e

    DUMMY = "+9198450000{0:02d}"  # demo numbers for officers without a real line
    # Govt Unique IDs follow the Ministry of Petroleum & Natural Gas civil-servant
    # format: GOI (Govt of India) - PNG (ministry) - year of joining - employee no.
    mk_gov(admin, "GOI-PNG-2011-0114", PHONE_ADMIN, "System Administration")
    mk_gov(auditor, "GOI-PNG-2009-0387", DUMMY.format(2), "CAG (read-only)")
    mk_gov(l1_bel, "GOI-PNG-2018-0492", PHONE_ADMIN, "CPCL — Belagavi")
    mk_gov(l1_hub, "GOI-PNG-2016-0733", DUMMY.format(4), "CPCL — Hubli")
    mk_gov(l2_off, "GOI-PNG-2014-0265", DUMMY.format(5), "Karnataka Finance")
    mk_gov(tech_eval, "GOI-PNG-2017-0548", DUMMY.format(6), "Karnataka Technical")
    mk_gov(opening_off, "GOI-PNG-2015-0621", DUMMY.format(7), "Karnataka Bid Opening")
    mk_gov(comm_chair, "GOI-PNG-2010-0233", DUMMY.format(8), "CPCL Committee")
    mk_gov(comm_member, "GOI-PNG-2013-0876", DUMMY.format(9), "CPCL Committee")
    mk_gov(cfa_off, "GOI-PNG-2012-0349", DUMMY.format(10), "Karnataka CFA")
    mk_gov(contract_mgr, "GOI-PNG-2016-0912", DUMMY.format(11), "CPCL Contracts")
    mk_gov(contract_exec, "GOI-PNG-2014-0505", DUMMY.format(12), "CPCL Contracts")
    mk_gov(inspection_off, "GOI-PNG-2015-0688", DUMMY.format(13), "CPCL Inspection")
    mk_gov(payment_off, "GOI-PNG-2017-0771", DUMMY.format(14), "CPCL Payments")
    # first-login approval demo: created by admin, NOT yet approved
    mk_gov(pending_off, "GOI-PNG-2025-1102", DUMMY.format(15), "CPCL — onboarding", status="PENDING_APPROVAL")
    db.flush()

    # ---------------- bidder organizations (V2.1 org isolation) ----------------
    def mk_org(user, legal, trade, district="Belagavi", extra_members=()):
        o = Organization(
            legal_name=legal, trade_name=trade, state="Karnataka", district=district,
            constitution="Private Limited" if "PVT" in legal else ("Proprietorship" if "Works" not in legal else "Partnership"),
            registered_address=f"Industrial Area, {district}, Karnataka",
        )
        db.add(o)
        db.flush()
        db.add(OrganizationMember(organization_id=o.id, user_id=user.id, org_role="OWNER"))
        for m in extra_members:
            db.add(OrganizationMember(organization_id=o.id, user_id=m[0].id, org_role=m[1]))
        return o

    org_ramesh = mk_org(ramesh, "RAMESH TRADERS PVT LTD", "Ramesh Traders",
                        extra_members=[(ramesh_preparer, "PREPARER"), (ramesh_signatory, "SIGNATORY")])
    org_sita = mk_org(sita, "SITA ENGINEERING WORKS", "Sita Engineering", district="Hubli-Dharwad")
    org_vikram = mk_org(vikram, "VIKRAM INDUSTRIAL SUPPLIES", "Vikram Supplies")
    org_meena = mk_org(meena, "MEENA POLYFAB", "Meena Polyfab", district="Mysuru")
    org_arjun = mk_org(arjun, "ARJUN INDUSTRIAL SYSTEMS PVT LTD", "Arjun Industrial")
    org_bharat = mk_org(bharat, "BHARAT PROCESS SOLUTIONS PVT LTD", "Bharat Process")
    org_chetan = mk_org(chetan, "CHETAN EQUIPMENTS PVT LTD", "Chetan Equipments")
    org_delta = mk_org(delta, "DELTA PLANT & MACHINERY PVT LTD", "Delta Plant")
    db.flush()

    # ---------------- profiles ----------------
    def mk_profile(user, company, trade, pan, gstin, udyam, turnover, district="Belagavi", rep=None, cat=None, filing="CURRENT", local_content=None, address=None):
        p = BidderProfile(
            user_id=user.id,
            company_name=company,
            trade_name=trade,
            pan_encrypted=encrypt_field(pan),
            gstin_encrypted=encrypt_field(gstin),
            udyam_encrypted=encrypt_field(udyam),
            pan_masked=mask_pan(pan),
            gstin_masked=mask_gstin(gstin),
            udyam_masked=mask_udyam(udyam),
            business_type="Private Limited" if "PVT" in company else "Proprietorship",
            business_category=cat,
            # Must agree with the mock GST source record — the cross-
            # verification table compares profile vs source field by field.
            registered_address=address or f"Industrial Area, {district}, Karnataka",
            city=district,
            district=district,
            state="Karnataka",
            authorized_rep_name=rep or user.full_name,
            authorized_rep_email=user.email,
            authorized_rep_phone=user.mobile,
            annual_turnover=turnover,
            established_year=2015,
            registration_date=date(2015, 4, 12),
            filing_status=filing,
            local_content_percent=local_content,
            profile_completion=100,
        )
        db.add(p)
        return p

    # registered_address values mirror the mock GST source records exactly,
    # so the cross-verification table matches on address for every bidder
    # (the only seeded field mismatch is Sita's Udyam legal name — Scenario B).
    mk_profile(ramesh, "RAMESH TRADERS PVT LTD", "Ramesh Traders", "ABRCT1234F", "29ABRCT1234F1Z5", "U1234KA2601A0012345", 45000000, cat="Industrial Supplies", local_content=45,
               address="12, Industrial Layout, Belagavi, Karnataka 590016")
    mk_profile(sita, "Sita Engineering Works", "Sita Engineering", "AAAPS5678K", "29AAAPS5678K1Z2", "U5678KA2502B0056789", 25000000, cat="Engineering", filing="DEFAULTED", local_content=55,
               address="7, Ring Road, Belagavi, Karnataka 590009")
    mk_profile(vikram, "VIKRAM INDUSTRIAL SUPPLIES", "Vikram Supplies", "AAVCV9012L", "29AAVCV9012L1Z7", "U9012KA2403C0090123", 61000000, cat="Industrial Supplies", local_content=35,
               address="22, MIDC Phase II, Belagavi, Karnataka 590016")
    mk_profile(meena, "MEENA POLYFAB", "Meena Polyfab", "AABPM3456M", "29AABPM3456M1Z3", "U3456KA2504D0034567", 22000000, cat="Polymer Products", local_content=40,
               address="3, Kankanady, Belagavi, Karnataka 591142")
        # RA-demo profiles — mirror the mock source records exactly
    mk_profile(arjun, "ARJUN INDUSTRIAL SYSTEMS PVT LTD", "Arjun Industrial", "AAISA1001H", "29AAISA1001H1Z1", "U1001KA2605A0010001", 120000000, cat="Industrial Equipment", local_content=48,
               address="15, GIDC Estate, Belagavi, Karnataka 590016")
    mk_profile(bharat, "BHARAT PROCESS SOLUTIONS PVT LTD", "Bharat Process", "AABPS2002J", "29AABPS2002J1Z2", "U2002KA2606B0020002", 90000000, cat="Process Solutions", local_content=42,
               address="42, PE-4 Industrial Area, Belagavi, Karnataka 590018")
    mk_profile(chetan, "CHETAN EQUIPMENTS PVT LTD", "Chetan Equipments", "AACEQ3003K", "29AACEQ3003K1Z3", "U3003KA2607C0030003", 110000000, cat="Industrial Equipment", filing="DEFAULTED", local_content=38,
               address="8, Madhyamlok, Belagavi, Karnataka 590009")
    mk_profile(delta, "DELTA PLANT & MACHINERY PVT LTD", "Delta Plant", "AADPM4004L", "29AADPM4004L1Z4", "U4004KA2608D0040004", 85000000, cat="Plant & Machinery", local_content=45,
               address="27, Industrial Layout, Belagavi, Karnataka 590016")
    db.flush()

    # ---------------- consent (explicit, purpose, timestamp) ----------------
    for u in (ramesh, sita, vikram, meena, arjun, bharat, chetan, delta):
        db.add(ConsentRecord(user_id=u.id, source_category="ALL", purpose="Compliance verification for tender eligibility", status="GRANTED", granted_at=utcnow()))

    # ---------------- officer assignments ----------------
    db.add(OfficerAssignment(user_id=l1_bel.id, jurisdiction_id=j_belagavi_oil.id, is_primary=True))
    db.add(OfficerAssignment(user_id=l1_bel.id, jurisdiction_id=j_state_oil.id))
    db.add(OfficerAssignment(user_id=l1_hub.id, jurisdiction_id=j_hubli_oil.id, is_primary=True))
    db.add(OfficerAssignment(user_id=l2_off.id, jurisdiction_id=j_state_oil.id, is_primary=True))
    # Pipeline stage officers need jurisdiction scope over the demo tenders
    # (t1 Belagavi / t2 Hubli) so their queues and pipeline views work.
    for _so in (opening_off, tech_eval, comm_chair, comm_member, cfa_off,
                contract_mgr, contract_exec, inspection_off, payment_off, auditor):
        db.add(OfficerAssignment(user_id=_so.id, jurisdiction_id=j_belagavi_oil.id, is_primary=True))
        db.add(OfficerAssignment(user_id=_so.id, jurisdiction_id=j_hubli_oil.id))
    db.flush()

    # ---------------- trusted references for authenticity comparison ----------------
    seal = make_seal()
    sig = make_signature(seed=11)
    trusted_seal_file = save_trusted(seal, "seal", "CPCL trusted seal")
    trusted_sig_file = save_trusted(sig, "signature", "Authorized signatory reference")
    db.add(TrustedReference(issuer_name="CPCL Procurement — trusted seal sample", kind="SEAL", file_path=trusted_seal_file, notes="Reference seal of the issuing authority"))
    db.add(TrustedReference(issuer_name="CPCL Procurement — signatory reference", kind="SIGNATURE", file_path=trusted_sig_file, notes="Reference signature of the authorized signatory"))
    db.flush()

    # ---------------- documents ----------------
    alt_seal = make_alt_seal()
    alt_sig = make_signature(seed=99)

    def add_doc(user, name, doc_type, source, status, ocr=None, conf=None, file=None, sha=None, ref=None, masked=None, issue=None, expiry=None, notes=None):
        d = Document(
            user_id=user.id, name=name, doc_type=doc_type, source=source,
            source_classification="GOVERNMENT_SOURCE" if source != "MANUAL" else "BIDDER_UPLOADED",
            verification_status=status, ocr_data=ocr, ocr_confidence=conf,
            file_path=file, file_type=os.path.splitext(file)[1].lstrip(".") if file else None,
            document_hash=sha, reference_id=ref, masked_identifier=masked,
            issue_date=issue, expiry_date=expiry, last_synced_at=utcnow(), notes=notes,
        )
        db.add(d)
        return d

    # Ramesh (Scenario A) — all verified
    f, h = make_certificate("RAMESH TRADERS PVT LTD", "GST REGISTRATION CERTIFICATE",
                            [("GSTIN", "29ABRCT1234F1Z5"), ("Legal Name", "RAMESH TRADERS PVT LTD"), ("PAN", "ABRCT1234F"), ("Address", "12, Industrial Layout, Belagavi")],
                            seal, sig, "12/04/2015", None, "GSTN", "gst_ramesh")
    add_doc(ramesh, "GST Registration Certificate", "GST_CERT", "GST", "VERIFIED",
            ocr={"legal_name": "RAMESH TRADERS PVT LTD", "gstin": "29ABRCT1234F1Z5", "pan": "ABRCT1234F", "issue_date": "12/04/2015", "issuer": "GSTN"},
            conf=0.93, file=f, sha=h, ref="GST29ABRCT1234", masked=mask_gstin("29ABRCT1234F1Z5"), issue=date(2015, 4, 12))
    f, h = make_certificate("RAMESH TRADERS PVT LTD", "UDYAM (MSME) CERTIFICATE",
                            [("Udyam No.", "U1234KA2601A0012345"), ("Legal Name", "RAMESH TRADERS PVT LTD"), ("Category", "MSME")],
                            seal, sig, "01/06/2023", None, "Udyam/MSME", "udyam_ramesh")
    add_doc(ramesh, "Udyam (MSME) Certificate", "UDYAM_CERT", "UDYAM", "VERIFIED",
            ocr={"legal_name": "RAMESH TRADERS PVT LTD", "udyam_number": "U1234KA2601A0012345", "issue_date": "01/06/2023", "issuer": "Udyam/MSME"},
            conf=0.91, file=f, sha=h, ref="UDYAM-12345", masked=mask_udyam("U1234KA2601A0012345"), issue=date(2023, 6, 1))
    add_doc(ramesh, "PAN Certificate (Company)", "PAN", "PAN", "VERIFIED",
            ocr={"pan": "ABRCT1234F", "legal_name": "RAMESH TRADERS PVT LTD"}, conf=0.95,
            ref="PAN-ABRCT1234F", masked=mask_pan("ABRCT1234F"))
    mca_file = make_mca_pdf("RAMESH TRADERS PVT LTD", "CIN-U24230KA2015PTC0012345", "mca_ramesh.pdf")
    with open(os.path.join(STORAGE, "docs", mca_file), "rb") as fh:
        mca_sha = hashlib.sha256(fh.read()).hexdigest()
    add_doc(ramesh, "MCA21 Incorporation Certificate", "MCA_DOC", "MCA21", "VERIFIED",
            ocr={"legal_name": "RAMESH TRADERS PVT LTD", "certificate_number": "CIN-U24230KA2015PTC0012345"},
            conf=0.88, file=mca_file, sha=mca_sha, ref="MCA-0012345", issue=date(2015, 4, 12))
    add_doc(ramesh, "EPFO Compliance Certificate", "EPFO_DOC", "EPFO", "VERIFIED",
            ocr={"issuer": "EPFO", "issue_date": "01/04/2016"}, conf=0.8, ref="EPFO-00123")
    add_doc(ramesh, "Local-Content Declaration (45%)", "LOCAL_CONTENT_DECLARATION", "MANUAL", "MANUAL_UPLOADED",
            ocr={"percent": "45"}, conf=0.7, ref="LC-29-00123", notes="Self-declaration uploaded by bidder")

    # Sita (Scenario B) — GST filings lapsed + Udyam name mismatch + suspicious udyam doc
    f, h = make_certificate("SITA ENGINEERING WORKS", "GST REGISTRATION CERTIFICATE",
                            [("GSTIN", "29AAAPS5678K1Z2"), ("Legal Name", "SITA ENGINEERING WORKS"), ("PAN", "AAAPS5678K")],
                            seal, sig, "01/09/2017", "30/06/2025", "GSTN", "gst_sita")
    add_doc(sita, "GST Registration Certificate", "GST_CERT", "GST", "EXPIRED",
            ocr={"legal_name": "SITA ENGINEERING WORKS", "gstin": "29AAAPS5678K1Z2", "pan": "AAAPS5678K", "issue_date": "01/09/2017"},
            conf=0.9, file=f, sha=h, ref="GST29AAAPS5678", masked=mask_gstin("29AAAPS5678K1Z2"),
            issue=date(2017, 9, 1), expiry=date(2025, 6, 30), notes="Compliance lapsed — last return 2024-03-15")
    f, h = make_certificate("SITA ENGINEERING", "UDYAM (MSME) CERTIFICATE",
                            [("Udyam No.", "U5678KA2502B005678X"), ("Legal Name", "SITA ENGINEERING"), ("Category", "MSME")],
                            alt_seal, alt_sig, "12/10/2022", None, "Udyam/MSME", "udyam_sita")
    add_doc(sita, "Udyam (MSME) Certificate", "UDYAM_CERT", "UDYAM", "MISMATCH",
            ocr={"legal_name": "SITA ENGINEERING", "udyam_number": "U5678KA2502B005678X", "issue_date": "12/10/2022", "issuer": "Udyam/MSME"},
            conf=0.66, file=f, sha=h, ref="UDYAM-56789", masked=mask_udyam("U5678KA2502B0056789"),
            issue=date(2022, 10, 12), notes="Legal name differs across profile and source")
    add_doc(sita, "PAN Certificate (Company)", "PAN", "PAN", "VERIFIED",
            ocr={"pan": "AAAPS5678K", "legal_name": "SITA ENGINEERING WORKS"}, conf=0.94,
            ref="PAN-AAAPS5678K", masked=mask_pan("AAAPS5678K"))

    # Vikram (Scenario C) — clean documents but debarred
    f, h = make_certificate("VIKRAM INDUSTRIAL SUPPLIES", "GST REGISTRATION CERTIFICATE",
                            [("GSTIN", "29AAVCV9012L1Z7"), ("Legal Name", "VIKRAM INDUSTRIAL SUPPLIES"), ("PAN", "AAVCV9012L")],
                            seal, sig, "18/02/2013", None, "GSTN", "gst_vikram")
    add_doc(vikram, "GST Registration Certificate", "GST_CERT", "GST", "VERIFIED",
            ocr={"legal_name": "VIKRAM INDUSTRIAL SUPPLIES", "gstin": "29AAVCV9012L1Z7", "pan": "AAVCV9012L", "issue_date": "18/02/2013"},
            conf=0.92, file=f, sha=h, ref="GST29AAVCV9012", masked=mask_gstin("29AAVCV9012L1Z7"), issue=date(2013, 2, 18))
    f, h = make_certificate("VIKRAM INDUSTRIAL SUPPLIES", "UDYAM (MSME) CERTIFICATE",
                            [("Udyam No.", "U9012KA2403C0090123"), ("Legal Name", "VIKRAM INDUSTRIAL SUPPLIES"), ("Category", "MSME")],
                            seal, sig, "20/01/2023", None, "Udyam/MSME", "udyam_vikram")
    add_doc(vikram, "Udyam (MSME) Certificate", "UDYAM_CERT", "UDYAM", "VERIFIED",
            ocr={"legal_name": "VIKRAM INDUSTRIAL SUPPLIES", "udyam_number": "U9012KA2403C0090123", "issue_date": "20/01/2023"},
            conf=0.9, file=f, sha=h, ref="UDYAM-90123", masked=mask_udyam("U9012KA2403C0090123"), issue=date(2023, 1, 20))
    add_doc(vikram, "PAN Certificate (Company)", "PAN", "PAN", "VERIFIED",
            ocr={"pan": "AAVCV9012L", "legal_name": "VIKRAM INDUSTRIAL SUPPLIES"}, conf=0.95,
            ref="PAN-AAVCV9012L", masked=mask_pan("AAVCV9012L"))

    # Meena (Scenario D) — EPFO source timed out
    f, h = make_certificate("MEENA POLYFAB", "GST REGISTRATION CERTIFICATE",
                            [("GSTIN", "29AABPM3456M1Z3"), ("Legal Name", "MEENA POLYFAB"), ("PAN", "AABPM3456M")],
                            seal, sig, "05/11/2019", None, "GSTN", "gst_meena")
    add_doc(meena, "GST Registration Certificate", "GST_CERT", "GST", "VERIFIED",
            ocr={"legal_name": "MEENA POLYFAB", "gstin": "29AABPM3456M1Z3", "pan": "AABPM3456M", "issue_date": "05/11/2019"},
            conf=0.9, file=f, sha=h, ref="GST29AABPM3456", masked=mask_gstin("29AABPM3456M1Z3"), issue=date(2019, 11, 5))
    f, h = make_certificate("MEENA POLYFAB", "UDYAM (MSME) CERTIFICATE",
                            [("Udyam No.", "U3456KA2504D0034567"), ("Legal Name", "MEENA POLYFAB"), ("Category", "MSME")],
                            seal, sig, "11/02/2024", None, "Udyam/MSME", "udyam_meena")
    add_doc(meena, "Udyam (MSME) Certificate", "UDYAM_CERT", "UDYAM", "VERIFIED",
            ocr={"legal_name": "MEENA POLYFAB", "udyam_number": "U3456KA2504D0034567", "issue_date": "11/02/2024"},
            conf=0.9, file=f, sha=h, ref="UDYAM-34567", masked=mask_udyam("U3456KA2504D0034567"), issue=date(2024, 2, 11))
    add_doc(meena, "EPFO Compliance Certificate", "EPFO_DOC", "EPFO", "UNABLE_TO_VERIFY",
            conf=None, ref=None, notes="Source request timed out — EPFO portal unavailable (simulated). Never treated as verified.")

    # ---------------- sector / technical documents (technical-evaluation stage) ----------------
    # Technical Evaluation Officers see these (plus local content); statutory
    # identity docs (PAN/GST/UDYAM/EPFO) stay out of their review.
    # NOTE: Ramesh intentionally has NO OEM_AUTHORIZATION / TECH_SPEC — t2
    # (TDR-2026-002) makes OEM authorization mandatory, and Ramesh is the
    # demo case for the "Missing Documents" label (not "Not Eligible").
    add_doc(sita, "BIS ISO 9001 Quality License", "BIS_CERT", "BIS", "VERIFIED",
            ocr={"legal_name": "SITA ENGINEERING WORKS", "certificate_number": "BIS/ISO9001/KLN/4431", "issuer": "BIS"},
            conf=0.86, ref="BIS-4431", masked="BIS/ISO9001/KLN/4431", issue=date(2022, 5, 2))
    add_doc(sita, "OEM Authorization Letter", "OEM_AUTHORIZATION", "MANUAL", "MANUAL_UPLOADED",
            ocr={"issuer": "ABB India Ltd.", "scope": "Electromechanical components", "valid_until": "2026-12-31"},
            conf=0.68, ref="OEM/ABB/2023/104", notes="Letter uploaded by bidder; OEM registry lookup at technical evaluation")
    add_doc(sita, "Technical Specifications — Compliance Declaration", "TECH_SPEC", "MANUAL", "MANUAL_UPLOADED",
            ocr={"tender_ref": "TDR-2026-001", "conformance": "compliant except item 7 (awaiting clarification)"},
            conf=0.66, ref="TS/2026/104", notes="One non-conformance flagged by bidder for clarification")
    add_doc(vikram, "OEM Authorization Letter", "OEM_AUTHORIZATION", "MANUAL", "MANUAL_UPLOADED",
            ocr={"issuer": "Schaeffler India Pvt Ltd.", "scope": "Bearings and transmission components", "valid_until": "2027-01-31"},
            conf=0.71, ref="OEM/SCH/2024/077", notes="Letter uploaded by bidder")
    add_doc(vikram, "Technical Specifications — Compliance Declaration", "TECH_SPEC", "MANUAL", "MANUAL_UPLOADED",
            ocr={"tender_ref": "TDR-2026-001", "conformance": "item-wise compliance matrix attached"},
            conf=0.7, ref="TS/2026/077", notes="Bidder declaration conforming to the tender's technical specification")
    add_doc(meena, "BIS ISO 14001 Environmental License", "BIS_CERT", "BIS", "VERIFIED",
            ocr={"legal_name": "MEENA POLYFAB", "certificate_number": "BIS/ISO14001/KLN/5522", "issuer": "BIS"},
            conf=0.84, ref="BIS-5522", masked="BIS/ISO14001/KLN/5522", issue=date(2023, 8, 14))
    add_doc(meena, "OEM Authorization Letter", "OEM_AUTHORIZATION", "MANUAL", "MANUAL_UPLOADED",
            ocr={"issuer": "Toray Industries (India) Ltd.", "scope": "Polymer sheeting and fabricated products", "valid_until": "2027-06-30"},
            conf=0.75, ref="OEM/TOR/2024/221", notes="Letter uploaded by bidder; verified against OEM registry at technical evaluation")
    add_doc(meena, "Technical Specifications — Compliance Declaration", "TECH_SPEC", "MANUAL", "MANUAL_UPLOADED",
            ocr={"tender_ref": "TDR-2026-001", "conformance": "item-wise compliance matrix attached"},
            conf=0.74, ref="TS/2026/221", notes="Bidder declaration conforming to the tender's technical specification")
    db.flush()

    # ---------------- blacklist reference database ----------------
    db.add(BlacklistEntry(
        entity_name="VIKRAM INDUSTRIAL SUPPLIES",
        pan="AAVCV9012L",
        gstin="29AAVCV9012L1Z7",
        reference_id="CPSE/DEB/2025/117",
        reason="Supply of sub-standard industrial lubricants; debarment under CPCL purchase code clause 2.4.7",
        source_authority="CPCL Procurement / CPSE Vigilance",
        official_order_reference="CPCL/DEB/2025/117",
        valid_from=date(2025, 3, 1),
        valid_until=date(2027, 3, 1),
        status="ACTIVE",
        review_status="Order under appeal — review scheduled 2026-12-01",
        created_by=admin.id,
    ))
    db.flush()

    # ---------------- procuring organizations ----------------
    # Petroleum-sector buyers. Each operates through its own procurement
    # division, tender committee and delegated financial authority — approval
    # role titles & value bands are configured per organization (not fixed
    # L1/L2/L3 labels).
    def bands_psu():
        return [
            {"min_value": 0, "max_value": 2500000,
             "review_role_title": "Tender Evaluation Officer",
             "approval_role_title": "Delegated Procurement Officer",
             "committee_required": False},
            {"min_value": 2500000, "max_value": 200000000,
             "review_role_title": "Tender Evaluation Committee Member",
             "approval_role_title": "Chief Procurement Officer",
             "committee_required": True},
            {"min_value": 200000000, "max_value": None,
             "review_role_title": "Tender Committee (3 members)",
             "approval_role_title": "Competent Financial Authority",
             "committee_required": True},
        ]

    def bands_ministry():
        return [
            {"min_value": 0, "max_value": 5000000,
             "review_role_title": "Section Officer (Procurement)",
             "approval_role_title": "Joint Secretary — Competent Financial Authority",
             "committee_required": False},
            {"min_value": 5000000, "max_value": 500000000,
             "review_role_title": "Tender Committee Member",
             "approval_role_title": "Secretary (MoPNG)",
             "committee_required": True},
            {"min_value": 500000000, "max_value": None,
             "review_role_title": "Expenditure Committee",
             "approval_role_title": "Minister / Finance Secretary",
             "committee_required": True},
        ]

    ORG_DEFS = [
        ("Ministry of Petroleum & Natural Gas (Secretariat)", "MOPNG", "MINISTRY", "Procurement Wing, MoPNG Secretariat", bands_ministry()),
        ("Directorate General of Hydrocarbons", "DGH", "DIRECTORATE", "DGH e-Procurement Division", bands_ministry()),
        ("Petroleum & Natural Gas Regulatory Board", "PNGRB", "REGULATORY", "PNGRB Procurement Section", bands_ministry()),
        ("Oil & Natural Gas Corporation Ltd", "ONGC", "CPSE", "ONGC Procurement Division, New Delhi", bands_psu()),
        ("Indian Oil Corporation Ltd", "IOC", "CPSE", "IOC Corporate Procurement, Vadodara", bands_psu()),
        ("Bharat Petroleum Corporation Ltd", "BPCL", "CPSE", "BPCL Procurement Division, Mumbai", bands_psu()),
        ("Hindustan Petroleum Corporation Ltd", "HPCL", "CPSE", "HPCL Procurement Division, Mumbai", bands_psu()),
        ("GAIL (India) Ltd", "GAIL", "CPSE", "GAIL Corporate Procurement, New Delhi", bands_psu()),
        ("Oil India Ltd", "OIL", "CPSE", "Oil India Procurement Division, Kolkata", bands_psu()),
        ("Engineers India Ltd", "EIL", "CPSE", "EIL Procurement Department, New Delhi", bands_psu()),
        ("Chennai Petroleum Corporation Limited (CPCL)", "CPCL", "CPSE", "CPCL Procurement Division, Belagavi", bands_psu()),
    ]
    orgs = {}
    for name, code, otype, division, bands in ORG_DEFS:
        o = ProcuringOrganization(
            name=name, short_code=code, org_type=otype,
            parent_ministry="Ministry of Petroleum & Natural Gas",
            procurement_division=division, procurement_method_default="GEM_BID",
            committee_min_members=3, committee_requires_finance=True,
            committee_requires_user_rep=True, authority_bands=bands,
        )
        db.add(o)
        orgs[code] = o
    db.flush()

    # ---------------- tenders ----------------
    t1 = Tender(
        tender_no="TDR-2026-001",
        title="Supply of Industrial Lubricants for CPCL Refinery, Belagavi",
        description="Supply of bulk industrial lubricants and greases for refinery operations at Belagavi unit. Delivery over 12 months.",
        department="Oil & Natural Gas", organization="CPCL", category="Petroleum & Lubricants",
        product_service_category="Lubricants & Greases", district="Belagavi", state="Karnataka",
        jurisdiction_code="KARNATAKA-BELAGAVI-OILGAS", value_min=5000000, value_max=85000000,
        open_date=today - timedelta(days=10), close_date=today + timedelta(days=20),
        status="PUBLISHED", msme_preference=True, created_by=admin.id,
        organization_id=orgs["CPCL"].id, procurement_method="GEM_BID",
        application_fee_required=True, application_fee_amount=2500, application_fee_refundable=False, bid_security_required=True, bid_security_percent=1.0,
        bid_security_exemption_allowed=True,
    )
    t2 = Tender(
        tender_no="TDR-2026-002",
        title="Supply of PPE and Safety Equipment — IOC Hubli",
        description="Supply of certified personal protective equipment for field operations.",
        department="Oil & Natural Gas", organization="IOC", category="Safety Equipment",
        product_service_category="PPE", district="Hubli", state="Karnataka",
        jurisdiction_code="KARNATAKA-HUBLI-OILGAS", value_min=2000000, value_max=15000000,
        open_date=today - timedelta(days=5), close_date=today + timedelta(days=30),
        status="PUBLISHED", created_by=admin.id,
        organization_id=orgs["IOC"].id, procurement_method="GEM_BID",
        # Demo district split: Hubli = sealing OFF (bids open from submission —
        # the live AI-verification demo). Belagavi's t1 stays sealed (the
        # sealed-bid demo, opened by the audited admin force-open).
        sealing_enabled=False,
    )
    t3 = Tender(
        tender_no="TDR-2026-003",
        title="IT Modernization Services for Procurement Portal",
        description="Upgrade and integration services for the internal procurement portal.",
        department="IT & Digital Services", organization="MoPNG", category="IT Services",
        product_service_category="Software Services", district="Belagavi", state="Karnataka",
        jurisdiction_code="KARNATAKA-BELAGAVI-IT", value_min=10000000, value_max=40000000,
        open_date=today - timedelta(days=3), close_date=today + timedelta(days=25),
        status="PUBLISHED", make_in_india_required=True, startup_eligible=True, min_local_content=30,
        created_by=admin.id,
        organization_id=orgs["MOPNG"].id, procurement_method="CUSTOM_BID",
        application_fee_required=True, application_fee_amount=2500, application_fee_refundable=False,
        bid_security_required=True, bid_security_percent=2.0, bid_security_exemption_allowed=True,
    )
    t4 = Tender(
        tender_no="TDR-2026-004",
        title="Supply of Office Stationery — Oil India Head Office",
        description="Closed tender (seed reference).",
        department="Administration", organization="Oil India", category="General",
        product_service_category="Stationery", district="Mumbai", state="Maharashtra",
        jurisdiction_code="MAHARASHA-MUMBAI-ADMIN", value_min=500000, value_max=2000000,
        open_date=today - timedelta(days=60), close_date=today - timedelta(days=10),
        status="CLOSED", created_by=admin.id,
        organization_id=orgs["OIL"].id, procurement_method="GEM_BID",
    )
    t5 = Tender(
        tender_no="TDR-2026-005",
        title="Supply of Industrial Gases (Oxygen, Nitrogen, CO2) — CPCL Refinery",
        description="Annual supply of packaged and bulk industrial gases for refinery maintenance and operations.",
        department="Oil & Natural Gas", organization="CPCL", category="Industrial Gases",
        product_service_category="Gases & Cylinders", district="Belagavi", state="Karnataka",
        jurisdiction_code="KARNATAKA-BELAGAVI-OILGAS", value_min=8000000, value_max=60000000,
        open_date=today - timedelta(days=8), close_date=today + timedelta(days=15),
        status="PUBLISHED", msme_preference=True, created_by=admin.id,
        organization_id=orgs["CPCL"].id, procurement_method="GEM_BID",
    )
    t6 = Tender(
        tender_no="TDR-2026-006",
        title="Fabrication & Supply of Heat Exchangers — HPCL Vizag",
        description="Design, fabrication and supply of shell-and-tube heat exchangers for utility services.",
        department="Oil & Natural Gas", organization="HPCL", category="Equipment",
        product_service_category="Heat Exchangers", district="Vijayawada", state="Andhra Pradesh",
        jurisdiction_code="KARNATAKA-ANY-OILGAS", value_min=25000000, value_max=120000000,
        open_date=today - timedelta(days=6), close_date=today + timedelta(days=22),
        status="PUBLISHED", msme_preference=False, created_by=admin.id,
        organization_id=orgs["HPCL"].id, procurement_method="GEM_BID",
    )
    t7 = Tender(
        tender_no="TDR-2026-007",
        title="CCTV Surveillance & Security System Upgrade — Oil India",
        description="Supply, installation and commissioning of IP-CCTV with NVR and analytics for site security.",
        department="Security & Administration", organization="Oil India", category="Security Systems",
        product_service_category="CCTV & Access Control", district="Mumbai", state="Maharashtra",
        jurisdiction_code="MAHARASHA-MUMBAI-ADMIN", value_min=3000000, value_max=18000000,
        open_date=today - timedelta(days=4), close_date=today + timedelta(days=12),
        status="PUBLISHED", make_in_india_required=True, min_local_content=30,
        created_by=admin.id,
        organization_id=orgs["OIL"].id, procurement_method="GEM_BID",
    )
    t8 = Tender(
        tender_no="TDR-2026-008",
        title="Annual Maintenance Contract — Pumping & Compressor Systems",
        description="AMC covering preventive maintenance, spares and 24x7 breakdown support for pumping/compressor assets.",
        department="Maintenance & Operations", organization="CPCL", category="Maintenance Services",
        product_service_category="AMC & Field Services", district="Belagavi", state="Karnataka",
        jurisdiction_code="KARNATAKA-BELAGAVI-OILGAS", value_min=5000000, value_max=30000000,
        open_date=today - timedelta(days=2), close_date=today + timedelta(days=18),
        status="PUBLISHED", msme_preference=True, startup_eligible=True,
        created_by=admin.id,
        organization_id=orgs["CPCL"].id, procurement_method="GEM_BID",
    )
    t9 = Tender(
        tender_no="TDR-2026-009",
        title="Supply of Fire Extinguishers & Firefighting Consumables",
        description="Supply of ISI-marked fire extinguishers, refills, hoses and related consumables across depots.",
        department="Safety & Environment", organization="MoPNG", category="Safety Equipment",
        product_service_category="Fire Safety", district="Belagavi", state="Karnataka",
        jurisdiction_code="KARNATAKA-BELAGAVI-IT", value_min=1500000, value_max=9000000,
        open_date=today - timedelta(days=1), close_date=today + timedelta(days=10),
        status="PUBLISHED", msme_preference=True, created_by=admin.id,
        organization_id=orgs["MOPNG"].id, procurement_method="GEM_BID",
    )
    # TDR-2026-010 — the Reverse-Auction demo tender (CPCL / MoPNG).
    # Bid deadline already passed: bids are open, so the demo walks the
    # authorized opening → AI compliance → technical → RA live.
    import zoneinfo as _zi
    from datetime import time as _t10_clock
    _t10_deadline = (datetime.combine(today, _t10_clock(18, 0), tzinfo=_zi.ZoneInfo("Asia/Kolkata"))
                     - timedelta(days=1)).astimezone(timezone.utc).replace(tzinfo=None)
    t10 = Tender(
        tender_no="TDR-2026-010",
        title="Supply of Industrial Process Equipment — CPCL Belagavi",
        description=("Supply of industrial process equipment (heat exchangers, valves, "
                     "instrumentation) for the Belagavi refinery revamp. e-Bid with Reverse "
                     "Auction: technically qualified bidders may competitively reduce their "
                     "sealed quotation in the authorized reverse auction, per tender conditions."),
        department="Oil & Natural Gas", organization="CPCL", category="Process Equipment",
        product_service_category="Process Equipment", district="Belagavi", state="Karnataka",
        jurisdiction_code="KARNATAKA-BELAGAVI-OILGAS", value_min=90000000, value_max=110000000,
        open_date=today - timedelta(days=30), close_date=today + timedelta(days=7),
        status="CLOSED", msme_preference=True, created_by=admin.id,
        organization_id=orgs["CPCL"].id, procurement_method="GEM_BID",
        procurement_mode="E_BID_WITH_REVERSE_AUCTION", financial_bid_required=True,
        bid_deadline=_t10_deadline,
    )
    for t in (t1, t2, t3, t4, t5, t6, t7, t8, t9):
        # Legacy tenders predate financial bidding — keep their existing
        # flows (officer-evaluated amount) unchanged.
        t.financial_bid_required = False
        db.add(t)
    db.add(t10)
    # TDR-2026-011 — the Forward e-Auction demo tender (CPCL Belagavi).
    # Disposal of surplus assets: bidders compete by INCREASING their sealed
    # quotations (highest valid bid is recommended) — the mirror image of the
    # RA demo on TDR-2026-010. Same statutory rule set and same controlled
    # compliance failure (Chetan's lapsed GST), so A/B/D qualify and C does not.
    t11 = Tender(
        tender_no="TDR-2026-011",
        title="Disposal of Surplus Industrial Assets — CPCL Belagavi (Forward e-Auction)",
        description=("Public forward e-auction for the disposal of surplus industrial "
                     "assets (pumps, compressors, instrumentation) retired from the "
                     "Belagavi refinery. Qualified bidders may competitively increase "
                     "their sealed quotations; the highest valid bid is recommended for "
                     "Competent Authority approval, per tender conditions."),
        department="Oil & Natural Gas", organization="CPCL", category="Asset Disposal",
        product_service_category="Asset Disposal", district="Belagavi", state="Karnataka",
        jurisdiction_code="KARNATAKA-BELAGAVI-OILGAS", value_min=45000000, value_max=70000000,
        open_date=today - timedelta(days=20), close_date=today + timedelta(days=7),
        status="CLOSED", msme_preference=False, created_by=admin.id,
        organization_id=orgs["CPCL"].id, procurement_method="GEM_BID",
        procurement_mode="FORWARD_E_AUCTION", financial_bid_required=True,
        bid_deadline=_t10_deadline,
    )
    db.add(t11)

    # ---------------- final jury demo tenders ----------------
    # Six additional live tenders intentionally demonstrate different payment
    # policies while preserving the district story used in the presentation:
    #   Belagavi = SEALED until the future bid deadline
    #   Hubli    = UNSEALED for live verification
    # Application fee is universal; EMD/Bid Security remains tender-specific.
    _demo_future = today + timedelta(days=14)
    t12 = Tender(
        tender_no="TDR-2026-012",
        title="Supply of Laboratory Chemicals & Reagents — CPCL Belagavi",
        description="Custom e-tender for refinery laboratory consumables. Demonstrates one-checkout ₹2,500 application fee plus fixed Bid Security.",
        department="Oil & Natural Gas", organization="CPCL", category="Chemicals & Reagents",
        product_service_category="Lab Chemicals", district="Belagavi", state="Karnataka",
        jurisdiction_code="KARNATAKA-BELAGAVI-OILGAS", value_min=5000000, value_max=30000000,
        open_date=today - timedelta(days=2), close_date=_demo_future, status="PUBLISHED",
        msme_preference=True, created_by=admin.id, organization_id=orgs["CPCL"].id,
        procurement_method="CUSTOM_BID", financial_bid_required=True, sealing_enabled=True,
        application_fee_required=True, application_fee_amount=2500, application_fee_refundable=False,
        bid_security_required=True, bid_security_amount=150000, bid_security_percent=None,
        bid_security_exemption_allowed=False,
    )
    t13 = Tender(
        tender_no="TDR-2026-013",
        title="Industrial Safety Footwear & Hand Tools — CPCL Belagavi",
        description="GeM-style supply tender. Mandatory platform application fee plus percentage-based Bid Security with eligible exemption.",
        department="Oil & Natural Gas", organization="CPCL", category="Safety Equipment",
        product_service_category="PPE & Hand Tools", district="Belagavi", state="Karnataka",
        jurisdiction_code="KARNATAKA-BELAGAVI-OILGAS", value_min=3000000, value_max=18000000,
        open_date=today - timedelta(days=1), close_date=_demo_future + timedelta(days=2), status="PUBLISHED",
        msme_preference=True, created_by=admin.id, organization_id=orgs["CPCL"].id,
        procurement_method="GEM_BID", financial_bid_required=True, sealing_enabled=True,
        application_fee_required=True, application_fee_amount=2500, application_fee_refundable=False, bid_security_required=True, bid_security_percent=2.0,
        bid_security_exemption_allowed=True,
    )
    t14 = Tender(
        tender_no="TDR-2026-014",
        title="Field Communication Equipment — IOC Hubli",
        description="Open demo tender for field communications. Mandatory platform application fee plus 1% Bid Security.",
        department="Oil & Natural Gas", organization="IOC", category="Communication Systems",
        product_service_category="Radio & Communications", district="Hubli", state="Karnataka",
        jurisdiction_code="KARNATAKA-HUBLI-OILGAS", value_min=8000000, value_max=42000000,
        open_date=today - timedelta(days=2), close_date=_demo_future, status="PUBLISHED",
        msme_preference=True, created_by=admin.id, organization_id=orgs["IOC"].id,
        procurement_method="GEM_BID", financial_bid_required=True, sealing_enabled=False,
        application_fee_required=True, application_fee_amount=2500, application_fee_refundable=False, bid_security_required=True, bid_security_percent=1.0,
        bid_security_exemption_allowed=True,
    )
    t15 = Tender(
        tender_no="TDR-2026-015",
        title="Workshop Power Tools & Spares — IOC Hubli",
        description="Custom e-tender demonstrating one-checkout ₹2,500 application fee plus fixed Bid Security on an unsealed demo tender.",
        department="Oil & Natural Gas", organization="IOC", category="Workshop Equipment",
        product_service_category="Power Tools", district="Hubli", state="Karnataka",
        jurisdiction_code="KARNATAKA-HUBLI-OILGAS", value_min=4000000, value_max=22000000,
        open_date=today - timedelta(days=1), close_date=_demo_future + timedelta(days=3), status="PUBLISHED",
        msme_preference=True, created_by=admin.id, organization_id=orgs["IOC"].id,
        procurement_method="CUSTOM_BID", financial_bid_required=True, sealing_enabled=False,
        application_fee_required=True, application_fee_amount=2500, application_fee_refundable=False,
        bid_security_required=True, bid_security_amount=50000, bid_security_percent=None,
        bid_security_exemption_allowed=False,
    )
    t16 = Tender(
        tender_no="TDR-2026-016",
        title="Boiler Water Treatment Advisory Services — CPCL Belagavi",
        description="Low-value consultancy demo tender: tender document/application fee only, with Bid Security waived by tender terms.",
        department="Oil & Natural Gas", organization="CPCL", category="Consultancy Services",
        product_service_category="Water Treatment Advisory", district="Belagavi", state="Karnataka",
        jurisdiction_code="KARNATAKA-BELAGAVI-OILGAS", value_min=800000, value_max=2500000,
        open_date=today, close_date=_demo_future + timedelta(days=4), status="PUBLISHED",
        startup_eligible=True, created_by=admin.id, organization_id=orgs["CPCL"].id,
        procurement_method="CUSTOM_BID", financial_bid_required=True, sealing_enabled=True,
        application_fee_required=True, application_fee_amount=2500, application_fee_refundable=False,
        bid_security_required=False, bid_security_exemption_allowed=False,
    )
    t17 = Tender(
        tender_no="TDR-2026-017",
        title="Routine Calibration Services — IOC Hubli",
        description="Low-value service demo tender with mandatory platform application fee and no EMD/Bid Security requirement.",
        department="Oil & Natural Gas", organization="IOC", category="Calibration Services",
        product_service_category="Instrument Calibration", district="Hubli", state="Karnataka",
        jurisdiction_code="KARNATAKA-HUBLI-OILGAS", value_min=500000, value_max=1500000,
        open_date=today, close_date=_demo_future + timedelta(days=5), status="PUBLISHED",
        startup_eligible=True, created_by=admin.id, organization_id=orgs["IOC"].id,
        procurement_method="GEM_BID", financial_bid_required=True, sealing_enabled=False,
        application_fee_required=True, application_fee_amount=2500, application_fee_refundable=False, bid_security_required=False, bid_security_exemption_allowed=False,
    )
    for _demo_t in (t12, t13, t14, t15, t16, t17):
        db.add(_demo_t)
    db.flush()

    # ---------------- tender-scoped workflow assignments (V2.1) ----------------
    # Authority to act in the pipeline is (workflow_role, tender) scoped.
    def assign(tender, user, role):
        db.add(TenderAssignment(tender_id=tender.id, user_id=user.id,
                                workflow_role=role, assigned_by=admin.id))

    _belagavi_tenders = (t1, t10, t11, t12, t13, t16)
    _hubli_tenders = (t2, t14, t15, t17)
    for t in _belagavi_tenders:
        assign(t, l1_bel, "PROCUREMENT_OFFICER")
    for t in _hubli_tenders:
        assign(t, l1_hub, "PROCUREMENT_OFFICER")
    for t in (*_belagavi_tenders, *_hubli_tenders):
        assign(t, opening_off, "BID_OPENING_OFFICER")
        assign(t, tech_eval, "TECHNICAL_EVALUATOR")
        assign(t, l2_off, "FINANCE_EVALUATOR")
        assign(t, comm_chair, "COMMITTEE_CHAIR")
        assign(t, comm_member, "COMMITTEE_MEMBER")
        assign(t, cfa_off, "CFA")
        assign(t, contract_mgr, "CONTRACT_MANAGER")
        assign(t, contract_exec, "CONTRACT_OFFICER")
        assign(t, inspection_off, "INSPECTION_OFFICER")
        assign(t, payment_off, "PAYMENT_OFFICER")
    db.flush()

    def rules_for(tender, rows):
        for i, r in enumerate(rows):
            db.add(TenderRule(
                tender_id=tender.id, rule_key=r[0], label=r[1], source=r[2], mandatory=r[3],
                condition_type=r[4], condition_value=r[5] if len(r) > 5 else None,
                weight=r[6] if len(r) > 6 else 10,
                failure_action=r[7] if len(r) > 7 else "FAIL",
                allows_manual_review=r[8] if len(r) > 8 else False,
                hard_disqualification=r[9] if len(r) > 9 else False,
                sort_order=i, version=1,
                # compliance_layer (pos 10): STATUTORY (general statutory & GeM
                # compliance) | SECTOR_TECHNICAL (tender-specific petroleum
                # technical & safety compliance). clause_ref (pos 11).
                compliance_layer=r[10] if len(r) > 10 else "STATUTORY",
                clause_ref=r[11] if len(r) > 11 else None,
            ))

    t1.required_document_types = ["GST_CERT", "UDYAM_CERT"]
    rules_for(t1, [
        ("gst_active", "GST registration active", "GST", True, "is_active", None, 20, "FAIL", False, False, "STATUTORY", "GeM GTC §2.1"),
        ("udyam_active", "Udyam/MSME registration active", "UDYAM", True, "is_active", None, 15, "FAIL", False, False, "STATUTORY", "GeM GTC §2.4"),
        ("min_turnover", "Minimum annual turnover ₹2.0 Cr", "PROFILE", True, "min_turnover", "20000000", 15, "FAIL", False, False, "STATUTORY", "Eligibility §3.2"),
        ("epfo_compliant", "EPFO compliance current", "EPFO", True, "is_active", None, 10, "FAIL", False, False, "STATUTORY", "Labour compliance §6.1"),
        ("gst_filing_current", "GST filings current", "GST", True, "filing_current", None, 10, "FAIL", False, False, "STATUTORY", "GeM GTC §2.1(b)"),
        ("legal_name_match", "Legal name consistent (profile vs Udyam)", "UDYAM", True, "name_match", "profile.company_name:UDYAM.legal_name", 10, "FAIL", False, False, "STATUTORY", "ATC-1"),
        ("gst_document", "GST certificate on file", "DOCUMENT", True, "has_document", "GST_CERT", 10, "FAIL", False, False, "STATUTORY", "Documents §4.1"),
        ("udyam_document", "Udyam certificate on file", "DOCUMENT", True, "has_document", "UDYAM_CERT", 10, "FAIL", False, False, "STATUTORY", "Documents §4.1"),
        ("not_blacklisted", "Not blacklisted/debarred", "BLACKLIST", True, "not_blacklisted", None, 100, "FAIL", False, True, "STATUTORY", "Integrity §7.1"),
        # Layer 2 — tender-specific petroleum technical & safety compliance
        # (BIS: a missing source record = UNABLE_TO_VERIFY → Manual Review,
        # never auto-disqualification; confirmed expiry would be a hard FAIL)
        ("bis_lubricants_std", "BIS certification active to IS 1141:2023 (industrial lubricants)", "BIS", True, "is_active", None, 15, "MANUAL_REVIEW", True, False, "SECTOR_TECHNICAL", "Spec §4.2"),
        ("oem_authorization", "OEM or authorized-dealer authorization for the supplied lubricants", "DOCUMENT", True, "has_document", "OEM_AUTHORIZATION", 20, "MANUAL_REVIEW", True, False, "SECTOR_TECHNICAL", "ATC-2"),
    ])

    t2.required_document_types = ["OEM_AUTHORIZATION", "GST_CERT"]
    rules_for(t2, [
        ("not_blacklisted", "Not blacklisted/debarred", "BLACKLIST", True, "not_blacklisted", None, 100, "FAIL", False, True, "STATUTORY", "Integrity §7.1"),
        ("gst_active", "GST registration active", "GST", True, "is_active", None, 15, "FAIL", False, False, "STATUTORY", "GeM GTC §2.1"),
        ("min_turnover", "Minimum annual turnover ₹1.0 Cr", "PROFILE", True, "min_turnover", "10000000", 10, "FAIL", False, False, "STATUTORY", "Eligibility §3.2"),
        # Layer 2 — tender-specific petroleum technical & safety compliance
        ("oem_authorization", "OEM authorization for the PPE product line", "DOCUMENT", True, "has_document", "OEM_AUTHORIZATION", 25, "FAIL", False, True, "SECTOR_TECHNICAL", "Spec §3.1"),
        ("bis_ppe_std", "BIS/ISI certification active (PPE safety standard)", "BIS", True, "is_active", None, 15, "FAIL", False, False, "SECTOR_TECHNICAL", "Spec §3.2"),
    ])

    t3.required_document_types = ["GST_CERT"]
    rules_for(t3, [
        ("not_blacklisted", "Not blacklisted/debarred", "BLACKLIST", True, "not_blacklisted", None, 100, "FAIL", False, True, "STATUTORY", "Integrity §7.1"),
        ("gst_active", "GST registration active", "GST", True, "is_active", None, 20, "FAIL", False, False, "STATUTORY", "GeM GTC §2.1"),
        ("min_local_content", "Minimum 30% local content (Make in India)", "LOCAL_CONTENT", True, "min_local_content", "30", 20, "FAIL", False, False, "STATUTORY", "ATC-4"),
        ("startup_recognition", "Startup India recognition (preferred)", "STARTUP_INDIA", False, "startup_certified", None, 10, "MANUAL_REVIEW", True, False, "STATUTORY", "Preference §8.3"),
    ])

    # RA-demo tender: statutory rules only (no sector-technical layer) so the
    # PASS/FAIL split is driven purely by the compliance evidence (Chetan's
    # lapsed GST is the single controlled failure).
    t10.required_document_types = ["GST_CERT", "UDYAM_CERT", "PAN"]
    _stat_rule_rows = [
        ("not_blacklisted", "Not blacklisted/debarred", "BLACKLIST", True, "not_blacklisted", None, 100, "FAIL", False, True, "STATUTORY", "Integrity §7.1"),
        ("gst_active", "GST registration active (lapsed GST disqualifies)", "GST", True, "is_active", None, 20, "FAIL", False, True, "STATUTORY", "GeM GTC §2.1"),
        ("gst_filing_current", "GST filings current", "GST", True, "filing_current", None, 10, "FAIL", False, True, "STATUTORY", "GeM GTC §2.1(b)"),
        ("udyam_active", "Udyam/MSME registration active", "UDYAM", True, "is_active", None, 15, "FAIL", False, False, "STATUTORY", "GeM GTC §2.4"),
        ("legal_name_match", "Legal name consistent (profile vs Udyam)", "UDYAM", True, "name_match", "profile.company_name:UDYAM.legal_name", 10, "FAIL", False, False, "STATUTORY", "ATC-1"),
        ("epfo_compliant", "EPFO compliance current", "EPFO", True, "is_active", None, 10, "FAIL", False, False, "STATUTORY", "Labour compliance §6.1"),
        ("min_turnover", "Minimum annual turnover \u20b91.0 Cr", "PROFILE", True, "min_turnover", "10000000", 10, "FAIL", False, False, "STATUTORY", "Eligibility §3.2"),
        ("gst_document", "GST certificate on file", "DOCUMENT", True, "has_document", "GST_CERT", 10, "FAIL", False, False, "STATUTORY", "Documents §4.1"),
        ("udyam_document", "Udyam certificate on file", "DOCUMENT", True, "has_document", "UDYAM_CERT", 10, "FAIL", False, False, "STATUTORY", "Documents §4.1"),
        ("pan_document", "PAN certificate on file", "DOCUMENT", True, "has_document", "PAN", 5, "FAIL", False, False, "STATUTORY", "Documents §4.1"),
    ]
    rules_for(t10, _stat_rule_rows)
    # t11 (forward e-auction) reuses the same statutory gate — the controlled
    # failure (lapsed GST) must exclude the same bidder in both directions.
    t11.required_document_types = ["GST_CERT", "UDYAM_CERT", "PAN"]
    rules_for(t11, _stat_rule_rows)

    # The six jury-demo tenders share a concise statutory gate. They are live
    # bidder-entry scenarios, so their differences focus on payment/sealing.
    _demo_rule_rows = [
        ("not_blacklisted", "Not blacklisted/debarred", "BLACKLIST", True, "not_blacklisted", None, 100, "FAIL", False, True, "STATUTORY", "Integrity §7.1"),
        ("gst_active", "GST registration active", "GST", True, "is_active", None, 20, "FAIL", False, False, "STATUTORY", "GeM GTC §2.1"),
        ("gst_document", "GST certificate on file", "DOCUMENT", True, "has_document", "GST_CERT", 10, "FAIL", False, False, "STATUTORY", "Documents §4.1"),
    ]
    for _demo_t in (t12, t13, t14, t15, t16, t17):
        _demo_t.required_document_types = ["GST_CERT"]
        rules_for(_demo_t, _demo_rule_rows)
    db.flush()

    # ---------------- corrigendum (corrigendum-aware verification demo) ----------------
    # The applicable BIS standard for t1 was revised after publication: any
    # compliance result computed against rules v1 is stale until re-verified.
    t1_old_close = t1.close_date
    t1_new_close = t1_old_close + timedelta(days=7)
    corr1 = TenderCorrigendum(
        tender_id=t1.id, seq_no=1,
        title="Corrigendum No. 1 — BIS standard revision & deadline extension",
        summary=(
            "The applicable BIS standard for industrial lubricants has been revised from "
            "IS 1141:1985 to IS 1141:2023 per BIS Notification G.S.R. 2026/118. Bidders "
            "must hold certification to the 2023 revision. The submission deadline is "
            "extended by 7 days."
        ),
        changes=[
            {"clause": "Spec §4.2", "old": "BIS IS 1141:1985", "new": "BIS IS 1141:2023",
             "description": "BIS standard revision per notification G.S.R. 2026/118"},
            {"clause": "Schedule I — Deadline", "old": t1_old_close.isoformat(),
             "new": t1_new_close.isoformat(), "description": "Submission deadline extended by 7 days"},
        ],
        deadline_extended_to=t1_new_close,
        affects_rules=True,
        published_by=admin.id,
        published_at=utcnow() - timedelta(days=2),
    )
    t1.close_date = t1_new_close
    t1.rules_version = 2
    for r in t1.rules:
        r.version = 2
    db.add(corr1)
    db.flush()

    # ---------------- demo bid deadlines (demo directive) ----------------
    # Every published tender's bids close (and open for evaluation) TODAY at
    # 18:00 IST (6 PM), EXCEPT the two demo districts:
    #   - Belagavi t1: sealed until +3 days 18:00 IST (sealed-bid demo; the
    #     audited admin force-open is the demo's opening step).
    #   - Hubli t2: sealing disabled per-tender (live AI-verification demo).
    # (Overrides t1's corrigendum extension for demo consistency.)
    import zoneinfo
    from datetime import time as _clock

    _deadline_ist = datetime.combine(today, _clock(18, 0), tzinfo=zoneinfo.ZoneInfo("Asia/Kolkata"))
    _bid_deadline_utc = _deadline_ist.astimezone(timezone.utc).replace(tzinfo=None)
    for t in (t2, t3, t5, t6, t7, t8, t9):
        t.close_date = today
        t.bid_deadline = _bid_deadline_utc
    # t1 (Belagavi) is the sealed-bid demo tender: its opening is 3 days out
    # so the sealed state is demonstrable at any time before then, whatever
    # the wall clock. The demo's opening step is the audited admin force-open
    # (POST /admin/tenders/{id}/open-bids).
    _t1_deadline_ist = datetime.combine(today + timedelta(days=3), _clock(18, 0),
                                        tzinfo=zoneinfo.ZoneInfo("Asia/Kolkata"))
    t1.bid_deadline = _t1_deadline_ist.astimezone(timezone.utc).replace(tzinfo=None)

    # Final jury-demo tenders always remain available after a fresh seed.
    # Belagavi stays sealed; Hubli stays unsealed exactly as in the original
    # district demonstration.
    _jury_deadline_ist = datetime.combine(today + timedelta(days=7), _clock(18, 0),
                                           tzinfo=zoneinfo.ZoneInfo("Asia/Kolkata"))
    _jury_deadline_utc = _jury_deadline_ist.astimezone(timezone.utc).replace(tzinfo=None)
    for _demo_t in (t12, t13, t16):
        _demo_t.sealing_enabled = True
        _demo_t.bid_deadline = _jury_deadline_utc
    for _demo_t in (t14, t15, t17):
        _demo_t.sealing_enabled = False
        _demo_t.bid_deadline = _jury_deadline_utc
    db.flush()

    # ---------------- applications (demo scenarios) ----------------
    def mk_app(user, tender, n):
        submitted = utcnow()
        a = Application(
            tender_id=tender.id, bidder_user_id=user.id, application_no=f"APP-2026-{n:06d}",
            status="AI Verification Not Run", eligibility_result=None,
            declaration_text=("I hereby declare that the information furnished in this application and the "
                               "documents provided are true, complete and accurate to the best of my knowledge. "
                               "I understand that false declaration may lead to disqualification and legal action."),
            declaration_at=submitted, submitted_at=submitted, snapshot_frozen_at=submitted,
            assigned_l1_id=l1_bel.id if tender.jurisdiction_code == "KARNATAKA-BELAGAVI-OILGAS" else l1_hub.id,
            assigned_l2_id=l2_off.id,
            routing_method="EXACT_JURISDICTION", routing_fallback=False,
            sla_due_at=utcnow() + timedelta(days=5),
        )
        db.add(a)
        db.flush()
        db.add(ApplicationStatusHistory(application_id=a.id, status="Submitted", actor_id=user.id, actor_role="BIDDER", remarks="Application submitted"))
        db.add(ApplicationStatusHistory(application_id=a.id, status="AI Verification Not Run", actor_id=None, actor_role="SYSTEM", remarks="Routed to L1 (EXACT_JURISDICTION)"))
        return a

    a_ramesh = mk_app(ramesh, t1, 1)
    a_sita = mk_app(sita, t1, 2)
    a_vikram = mk_app(vikram, t1, 3)
    a_meena = mk_app(meena, t1, 4)
    # Sita also applies to PPE tender (Hubli) — routed to l1.hubli
    sita_submitted = utcnow()
    a_sita2 = Application(
        tender_id=t2.id, bidder_user_id=sita.id, application_no="APP-2026-000005",
        status="AI Verification Not Run",
        declaration_text="I hereby declare that the information furnished in this application is true and complete.",
        declaration_at=sita_submitted, submitted_at=sita_submitted, snapshot_frozen_at=sita_submitted,
        assigned_l1_id=l1_hub.id, assigned_l2_id=l2_off.id,
        routing_method="EXACT_JURISDICTION", routing_fallback=False,
        sla_due_at=utcnow() + timedelta(days=5),
    )
    db.add(a_sita2)
    db.flush()
    db.add(ApplicationStatusHistory(application_id=a_sita2.id, status="Submitted", actor_id=sita.id, actor_role="BIDDER", remarks="Application submitted"))
    db.add(ApplicationStatusHistory(application_id=a_sita2.id, status="AI Verification Not Run", actor_id=None, actor_role="SYSTEM", remarks="Routed to L1 (EXACT_JURISDICTION)"))

    # RA-demo bidders' documents — field values consistent with the mock
    # source records (cross-verification compares field by field).
    for _u, _pan, _gstin, _udyam, _company in (
        (arjun, "AAISA1001H", "29AAISA1001H1Z1", "U1001KA2605A0010001", "ARJUN INDUSTRIAL SYSTEMS PVT LTD"),
        (bharat, "AABPS2002J", "29AABPS2002J1Z2", "U2002KA2606B0020002", "BHARAT PROCESS SOLUTIONS PVT LTD"),
        (chetan, "AACEQ3003K", "29AACEQ3003K1Z3", "U3003KA2607C0030003", "CHETAN EQUIPMENTS PVT LTD"),
        (delta, "AADPM4004L", "29AADPM4004L1Z4", "U4004KA2608D0040004", "DELTA PLANT & MACHINERY PVT LTD"),
    ):
        add_doc(_u, f"GST Registration \u2014 {_company}", "GST_CERT", "GST", "VERIFIED",
                ocr={"gstin": _gstin, "legal_name": _company}, conf=0.97, ref=f"GST{_gstin[-12:]}")
        add_doc(_u, f"Udyam Certificate \u2014 {_company}", "UDYAM_CERT", "UDYAM", "VERIFIED",
                ocr={"udyam_number": _udyam, "legal_name": _company}, conf=0.96, ref=f"UDYAM-{_udyam[-5:]}")
        add_doc(_u, f"PAN \u2014 {_company}", "PAN", "PAN", "VERIFIED",
                ocr={"pan": _pan, "legal_name": _company}, conf=0.98, ref=f"PAN-{_pan}")
        add_doc(_u, f"EPFO Compliance \u2014 {_company}", "EPFO_DOC", "EPFO", "VERIFIED", conf=0.95)
        add_doc(_u, f"MCA Incorporation \u2014 {_company}", "MCA_DOC", "MCA21", "VERIFIED", conf=0.95)
        add_doc(_u, f"Local-Content Declaration \u2014 {_company}", "LOCAL_CONTENT_DECLARATION", "MANUAL", "MANUAL_UPLOADED")
    db.flush()

    # application document attachments (auto-attach style)
    from .models import ApplicationDocument

    for a in (a_ramesh, a_sita, a_vikram, a_meena, a_sita2):
        for d in db.query(Document).filter(Document.user_id == a.bidder_user_id).all():
            db.add(ApplicationDocument(
                application_id=a.id, document_id=d.id, name=d.name, doc_type=d.doc_type,
                file_path=d.file_path, purpose="ORIGINAL", uploaded_by=a.bidder_user_id,
            ))
    # ---------------- TDR-2026-010 (RA demo) applications ----------------
    # Four sealed bids submitted before the (already passed) deadline. The
    # demo walks: opening -> AI verification -> technical -> financial
    # opening -> reverse auction -> L-ranking -> CFA approval.
    from .services import financial as _fin
    _ra_submitted = _t10_deadline - timedelta(hours=3)

    def mk_ra_app(user, n, total, tender=t10):
        _a = Application(
            tender_id=tender.id, bidder_user_id=user.id, application_no=f"APP-2026-{n:06d}",
            status="AI Verification Not Run",
            declaration_text=("I hereby declare that the information furnished in this application and the "
                               "documents provided are true, complete and accurate to the best of my knowledge. "
                               "I understand that false declaration may lead to disqualification and legal action."),
            declaration_at=_ra_submitted, submitted_at=_ra_submitted, snapshot_frozen_at=_ra_submitted,
            assigned_l1_id=l1_bel.id, assigned_l2_id=l2_off.id,
            routing_method="EXACT_JURISDICTION", routing_fallback=False,
            sla_due_at=utcnow() + timedelta(days=5),
        )
        db.add(_a)
        db.flush()
        # Sealed financial envelope (GST-inclusive total; tax component 0).
        _quote = _fin.validate_financial_quote(base=total, tax="0", other="0",
                                               currency="INR", price_valid_days=90)
        _fb = _fin.create_financial_bid(
            db, app=_a, tender=tender, user=user, quote=_quote,
            commercial_declaration=("I declare that the quoted price is valid for 90 days and that all "
                                    "commercial terms and conditions of the tender are accepted."),
            submitted_at=_ra_submitted, commit=False)
        # Sealing is recorded on the tamper-evident chain (hash + version,
        # never the plaintext amount).
        audit.append_audit(db, "financial_bid_sealed", "FinancialBid", _fb.id,
                           actor_id=user.id, actor_role="BIDDER",
                           tender_id=tender.id, application_id=_a.id,
                           payload={"version": _fb.version, "hash": _fb.financial_bid_hash},
                           commit=False)
        db.add(ApplicationStatusHistory(application_id=_a.id, status="Submitted",
                                        actor_id=user.id, actor_role="BIDDER", remarks="Application submitted"))
        db.add(ApplicationStatusHistory(application_id=_a.id, status="AI Verification Not Run",
                                        actor_id=None, actor_role="SYSTEM", remarks="Routed to L1 (EXACT_JURISDICTION)"))
        for d in db.query(Document).filter(Document.user_id == user.id).all():
            db.add(ApplicationDocument(
                application_id=_a.id, document_id=d.id, name=d.name, doc_type=d.doc_type,
                file_path=d.file_path, purpose="ORIGINAL", uploaded_by=user.id))
        return _a

    a_arjun = mk_ra_app(arjun, 6, "102000000")     # A ₹10.20 Cr
    a_bharat = mk_ra_app(bharat, 7, "98500000")    # B ₹9.85 Cr
    a_chetan = mk_ra_app(chetan, 8, "100500000")   # C ₹10.05 Cr (compliance FAIL)
    a_delta = mk_ra_app(delta, 9, "97000000")      # D ₹9.70 Cr
    # TDR-2026-011 (forward e-auction) sealed quotations — the auction opens
    # against these and bidders must INCREASE by at least the min increment.
    b_arjun = mk_ra_app(arjun, 10, "50000000", t11)    # A ₹5.00 Cr
    b_bharat = mk_ra_app(bharat, 11, "52500000", t11)  # B ₹5.25 Cr
    b_chetan = mk_ra_app(chetan, 12, "51000000", t11)  # C ₹5.10 Cr (compliance FAIL)
    b_delta = mk_ra_app(delta, 13, "55000000", t11)    # D ₹5.50 Cr
    db.flush()

    # quoted amounts (INR) for financial evaluation & L-ranking
    for a, amt in ((a_ramesh, 72000000.0), (a_sita, 75400000.0), (a_vikram, 69000000.0),
                   (a_meena, 74100000.0), (a_sita2, 11200000.0)):
        a.quoted_amount = amt
    # 11-stage pipeline: all seeded bids are at "Submitted" (SUBMISSION completed)
    from . import pipeline as _pl

    for a in (a_ramesh, a_sita, a_vikram, a_meena, a_sita2, a_arjun, a_bharat, a_chetan, a_delta,
              b_arjun, b_bharat, b_chetan, b_delta):
        _pl.init_pipeline(db, a)
        _pl.stage_rows(db, a.id)["SUBMISSION"].certificate_no = (
            f"CERT-SUB-{a.application_no.replace('-', '')}"
        )
    db.flush()

    # ---------------- audit bootstrap ----------------
    audit.append_audit(db, "seed_completed", "System", None, actor_id=admin.id, actor_role="ADMIN",
                       payload={"scenarios": ["A_clean_pass", "B_compliance_gap", "C_blacklist_override", "D_unable_to_verify"]})

    print("""
================================================================
 GeM-Sentinel seeded successfully
 GOV PORTAL SIGN-IN: Government ID + OTP (sent to registered phone)
 BIDDER PORTAL SIGN-IN: email + password + OTP (to account mobile)
----------------------------------------------------------------
 GOVT IDs (Govt portal — OTP to the registered phone)
 Format: GOI (Govt of India) - PNG (Ministry of Petroleum & NG) -
         year of joining - employee no.
   GOI-PNG-2011-0114  System Admin (S. Iyer)      → admin's phone
   GOI-PNG-2009-0387  Auditor (N. Prasad)
   GOI-PNG-2018-0492  Procurement Officer, Belagavi (Anita Deshmukh) → demo phone
   GOI-PNG-2016-0733  Procurement Officer, Hubli
   GOI-PNG-2014-0265  Finance Evaluator
   GOI-PNG-2017-0548  Technical Evaluator
   GOI-PNG-2015-0621  Bid Opening Officer
   GOI-PNG-2010-0233  Committee Chair
   GOI-PNG-2013-0876  Committee Member
   GOI-PNG-2012-0349  CFA
   GOI-PNG-2016-0912  Contract Manager
   GOI-PNG-2014-0505  Contract Execution Officer
   GOI-PNG-2015-0688  Inspection Officer
   GOI-PNG-2017-0771  Payment Officer
   GOI-PNG-2025-1102  New officer — PENDING admin approval
 (email/password below remain valid for API/ops use; the Govt portal
  UI no longer offers them)

 Bidder A (clean)     : ramesh@bidder.in    / Bidder@1234
   Bidder A preparer  : preparer@rameshtraders.in  / Preparer@1234
   Bidder A signatory : signatory@rameshtraders.in / Signatory@1234
 Bidder B (gap)       : sita@bidder.in      / Bidder@1234
 Bidder C (blacklist) : vikram@bidder.in    / Bidder@1234
 Bidder D (unverif.)  : meena@bidder.in     / Bidder@1234
 RA-demo bidders (TDR-2026-010, e-Bid + Reverse Auction)
   A (pass)  : arjun@bidder.in   / Bidder@1234   ₹10.20 Cr
   B (pass)  : bharat@bidder.in  / Bidder@1234   ₹9.85 Cr
   C (FAIL)  : chetan@bidder.in  / Bidder@1234   ₹10.05 Cr (GST lapsed)
   D (pass)  : delta@bidder.in   / Bidder@1234   ₹9.70 Cr
 Forward-e-auction bidders (TDR-2026-011, disposal — bids INCREASE)
   A (pass)  : arjun@bidder.in   / Bidder@1234   ₹5.00 Cr
   B (pass)  : bharat@bidder.in  / Bidder@1234   ₹5.25 Cr
   C (FAIL)  : chetan@bidder.in  / Bidder@1234   ₹5.10 Cr (GST lapsed)
   D (pass)  : delta@bidder.in   / Bidder@1234   ₹5.50 Cr
================================================================
""")


if __name__ == "__main__":
    seed()
