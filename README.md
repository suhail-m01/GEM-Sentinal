# GeM-Sentinel — Verify Once. Trust Always.

**SIH26100 — AI-Powered Integrated Bid Compliance Verification Platform**
for GeM procurement (Ministry of P&G / CPCL), built per the Smart India Hackathon
problem statement PDF.

> **The Rule Engine Decides. AI Only Explains.**
> A decision-support system. Final authority always remains with the Procurement Officer.
> Unverified data is never marked compliant: source failure → `UNABLE_TO_VERIFY`, never `PASS`.

---

## Architecture

```
┌────────────────────────┐     ┌──────────────────────────────┐
│  Next.js 14 frontend   │     │  FastAPI backend (:8000)      │
│  (App Router, TS)      │────▶│  routers: auth, bidder,       │
│  :3000  ──/api/*──────▶│     │  officer, l2, admin, ...      │
└────────────────────────┘     │  ┌──────────────────────────┐ │   ┌──────────────────────┐
        │                      │  │ Deterministic Rule Engine│ │──▶│ Mock govt portals    │
        │ same-origin /api     │  │ (PASS/FAIL/UNABLE)       │ │   │ (:8001) Udyam, GST,  │
        ▼                      │  │ + Scoring + AI explainer │ │   │ PAN, MCA21, EPFO,    │
   Next.js rewrites ─────────▶ │  │ (Gemini; deterministic   │ │   │ blacklist, ...       │
   → backend :8000             │  │  fallback, advisory only)│ │   └──────────────────────┘
                               │  └──────────────────────────┘ │
                               │  Override Accountability Engine│
                               │  Hash-chained audit (SHA-256)  │
                               └──────────────┬─────────────────┘
                                              ▼
                                   PostgreSQL (per spec)
```

- **Backend** — Python 3 / FastAPI / SQLAlchemy 2 / PostgreSQL. Deterministic rule
  evaluation per tender; compliance score; deterministic eligibility; consent-gated
  portal fetches; document authenticity heuristics (OpenCV seal/signature analysis,
  PDF metadata, tampering indicators); hash-chained immutable audit log; JWT auth
  (access + refresh + short-lived re-auth tokens); Fernet-encrypted PAN/GSTIN/Udyam
  fields with masked display; slowapi rate limiting.
- **Frontend** — Next.js 14 (App Router, TypeScript), Tailwind CSS, next-intl
  (English / हिंदी, cookie-based locale persisted globally), recharts analytics.
  All API calls use same-origin `/api/*` paths proxied by Next.js rewrites to the
  backend (browser code never talks to another origin).
- **Mock government portals** — a separate FastAPI service simulating Udyam, GST,
  PAN, MCA21, EPFO, ESIC, Startup India, NSIC, BIS, blacklist/debarment and
  local-content sources with configurable failure modes (valid / expired / mismatch /
  not_found / unavailable / timeout / blacklist). Real authorized APIs can replace
  them via the adapter pattern without touching business logic.

## Roles & key flows

| Role | Account | What they do |
|---|---|---|
| Bidder | `ramesh@bidder.in` / `Bidder@1234` (also sita, vikram, meena) | Profile, consent per source, Document Vault (fetch from sources / manual upload), tender discovery with live eligibility pre-check, apply, track, clarify, withdraw, grievance/appeal |
| Pipeline stage officers | see the **11-stage pipeline** table below (one account per role, e.g. `procurement.belagavi@cpcl.gov.in` / `Officer@1234` runs the AI Screening stage) | Each stage officer sees the **Pipeline** tab + role-scoped documents, acts only on their stage, and issues the stage certificate. Legacy decision endpoints (qualify / reject / clarify / escalate / fraud) remain available during rollout |
| System Admin | `admin@cpcl.gov.in` / `Admin@1234` | User & officer approval, jurisdiction & tender management with **Rule Builder** (blacklist rule is a locked hard rule), Reference Blacklist DB (source recorded, no hard delete), **Override Monitoring + Admin co-sign**, Vigilance referral management (manual CVC filing, no auto-submit), hash-chained audit viewer with chain verification, analytics, source health |
| Officer pending approval | `officer.new@cpcl.gov.in` / `Pending@1234` | Demonstrates the Admin-approval gate |

### 11-stage procurement pipeline (2026-09-20)
The legacy L1→L2 approval model is replaced by the full procurement process.
Every application walks an **11-stage pipeline** (`app/pipeline.py`); each stage
has exactly **one owning workflow role** (per-tender assignment), a fixed set of
allowed outcomes, and generates a **downloadable certificate of completion**
(numbered `CERT-…`, rendered on demand, hash-referenced):

```
Bid Submission → Bid Closing & Opening → Procurement Screening (AI compliance)
→ Technical Evaluation → Financial Evaluation → Competent Authority Approval/Award
→ Contract → Execution → Delivery & Inspection → Payment & Settlement
→ Audit Review & Closure
```

- **Role gate:** only the owning officer (or Admin) can act on a stage
  (403 otherwise); the current stage is computed per application; a terminal
  outcome (Non-Compliant / Disqualified / Not Awarded / Rejected delivery)
  **terminates** the pipeline; Delivery "Rectification Required" re-opens the
  same stage for re-inspection.
- **Stage officers (demo, all on TDR-2026-001/002):** `bidopening.karnataka` (Opening@1234),
  `procurement.belagavi` (Officer@1234, AI screening), `technical.karnataka`
  (TechEval@1234), `finance.karnataka` (L2Officer@1234), `cfa.karnataka`
  (CFA@1234), `contract.karnataka` (Contract@1234),
  `contractexecution.karnataka` (Execution@1234), `inspection.karnataka`
  (Inspect@1234), `payment.karnataka` (Payment@1234), `auditor@cpcl.gov.in`
  (Auditor@1234, hash-chain verified closure). UI: **Pipeline tab** on the
  review page + full **`/officer/pipeline/{id}`** view with per-stage action
  forms and certificate downloads.
- **Financial evaluation** records the evaluated amount, GST %, and computes
  the **L-rank** (L1 = lowest evaluated price among same-tender bids).
- **Award (APPROVAL)** reuses the Override Accountability Engine: approving
  over a blocking finding still requires re-auth + justified override; the
  award certificate becomes the Letter of Award.
- **Role-based document visibility** (`app/pipeline_docs.py`): an officer sees
  **only** the documents their role needs — the Technical Evaluation Officer
  never sees PAN/GST/Udyam/EPFO/MCA; the Financial Evaluation Officer sees
  GST + commercial documents only. Rule checklists are scoped the same way.
  The banner on the review page states what is hidden and why.
- **Readable officer output:** every report now starts with a deterministic
  **Officer Summary** card (verdict, one line, checks, documents, the single
  recommended action, every failed finding with its concrete **Fix**), and the
  AI explanation is **line-oriented** (VERDICT / FINDINGS NEEDING ACTION /
  CHECKS PASSED / MISMATCHES / DOCUMENTS / RECOMMENDED ACTION) instead of a
  wall of prose. The citation guard preserves this structure.
- **Bidder:** downloads a **Certificate of Bid Submission** from the
  application page; every stage completion is notified to the bidder.
- **Named process offices (2026-09-20):** every stage shows the **assigned
  officer by name** (e.g. "Financial Evaluation — Meera Kulkarni"); the review
  page's sidebar is now a **Pipeline progression** panel (current stage +
  its owner + one-tap action) — the legacy L1/L2 decision buttons
  ("Forward to L2", "Recommend qualify/reject", …) are no longer presented,
  so no role ever hits an "Invalid L1/L2 action" error. Request Clarification
  and Flag Suspected Fraud remain available to any officer as cross-cutting
  integrity actions.

### Override Accountability Engine (not faked)
When an officer tries to act against a **blocking** finding (blacklist/debarment hit,
confirmed mandatory FAIL), the decision is rejected with a **409 override challenge**.
Recording an override requires:

1. a reason category,
2. a **meaningful justification** (minimum length enforced; blank/trivial rejected),
3. **password re-authentication** (`/auth/reauth` → 10-minute JWT; wrong password → 401,
   logged as `reauth_failed`),
4. optional supporting evidence upload (SHA-256 recorded),
5. hash-chained **audit** entries (`override_created`, re-auth timestamp, source IP, UA),
6. **Admin notification**; for **CRITICAL** overrides (blacklist/hard-disqualification)
   the application enters **`Admin Co-sign Required`** — L2 approval is blocked until the
   Admin co-signs (`admin_cosign_completed`), and an **external Vigilance referral**
   (VIG-2026-xxxx) is auto-generated with a notice that *"must not be read as stating"*
   a conclusion — prepared for **manual** CVC filing, never auto-submitted.

### Verification guarantees
- Every rule result carries evidence; `UNABLE_TO_VERIFY` is distinct from `PASS`.
- Consent-gated: no protected data is fetched before per-source consent.
- Suspicious documents (visual similarity of seal/signature **alone is never
  conclusive**) are flagged `SUSPICIOUS` / `MANUAL_REVIEW_REQUIRED` and never
  `VERIFIED`.
- Re-run triggers: document update, clarification received, source refresh, rule
  change, report expiry → `report_stale`.
- Verification-result cache (additive): deterministic source results are cached
  (in-process LRU → Postgres, 15-min TTL) so repeat verifications don't re-hit
  government portals; cached results are marked `_cached` in reports, transient
  failures are never cached, and admins can view/clear the cache (audited).
- Audit chain: SHA-256 hash chain, `GET /admin/audit-logs/verify` re-computes every
  record; historical modification breaks the chain.

### Explainable AI + local RAG (2026-09-16)
- **RAG** (`app/ai_engine/knowledge_base.py` + `rag.py`) is a local, deterministic,
  offline retrieval layer placed **only where it is architecturally essential** — it
  grounds the AI explainer. It retrieves the statutory-clause passages that match the
  deterministic findings (clause +4 / condition +3 / token +1.5) so the narrative cites
  **real** clause language. It never touches the rule engine, scoring, or any decision
  path. Retrieved passages (with scores) are published in the report's
  `governance.retrieved_passages` for transparency and passed to the citation guard as
  allowed refs.
- **Explainable AI** (`app/ai_engine/explainer.py`): a structured v2.0 explanation
  (`executive_summary`, `basis_of_decision` with clause refs + statutory basis +
  evidence + weight, `cross_verification`, `document_findings`, `evidence_trail`,
  `remediation`, `citations`, `governance`) plus a professional EN/HI narrative with
  inline `[Clause: …]` cites. Optional Gemini narrative (grounded on retrieved passages)
  with a deterministic fallback (`deterministic-xai-2.0`). The **citation guard**
  strips any clause the AI cites that is not in the findings/KB, and prose is no longer
  mangled. The structured detail is stored on `verification_reports.explanation_detail`
  and rendered in the UI (Report tab → XAI section).

### PDF-only reports & rejected-bidder explanation (2026-09-16)
- **Report download is PDF only** (`GET /officer|l2 …/report/download`). The PDF
  (`app/reports.py`, Platypus) contains every explanation: score box, executive summary,
  basis-of-decision table, statutory basis, cross-verification, document findings,
  evidence trail, blacklist findings, remediation, the full AI narrative, citations, and
  governance, with page numbers + disclaimer footer. Unicode-safe (DejaVu Sans + Noto
  Sans Devanagari for Hindi). Frontend downloads via an authenticated blob helper
  (`lib/api.ts#downloadFile`) — a direct navigation would 401.
- **Rejected bidders** get a reasonable, **downloadable** explanation in the portal:
  `GET /bidder/applications/{id}/rejection-explanation` (+ `/download` PDF). The deciding
  officer's recorded remarks are **sanitized** for the bidder (contacts, other
  application IDs, and internal override/vigilance/co-sign/L1-L2 terms are redacted); if
  no remarks exist, a neutral explanation is derived from the bidder's own failed
  mandatory checks, missing/expired documents. Shown as a card on the application page
  with a PDF download.

### Document Studio — viewable original-looking replicas (2026-09-16)
- Every document in the **bidder portal** (and the officer review docs tab) has a **View**
  button. Clicking it opens an original-looking replica rendered by the **Document
  Studio** (`app/services/doc_studio.py`) — a free, deterministic Pillow renderer (no
  external API, scalable): government letterhead + emblem, document title, mono
  reference/identification, a deterministic QR block, a field table populated from the
  verified source data, a seal, a script signature, and an expiry banner.
- Every replica carries a persistent **SYNTHETIC** diagonal watermark + footer
  ("simulated data — not a genuine government issuance") and returns an
  `X-Document-Hash` (SHA-256) header. Officer views add a red
  "OFFICIAL REVIEW COPY — MANUAL REVIEW" banner and are gated on the bid deadline.
  Replicas are disk-cached at a stable key (`storage/docs/ai_replicas/`) and re-rendered
  only when the underlying data changes.

### Bid-deadline confidentiality & scalability (2026-09-16; per-district split 2026-09-26)
- **Officer access to bid details is gated by the bid deadline** (bid opening),
  controlled by the global master switch `BID_SEALING_ENABLED` (code **and** `.env`
  default now `true` — sealed-bid confidentiality is ON) **plus a per-tender override**
  `Tender.sealing_enabled`. A tender is open when the global switch is off **or** its
  own `sealing_enabled` is `false`; otherwise it seals until the deadline. When sealed,
  all bid details are hidden: the queue shows `Sealed` rows (bidder identity/score
  masked, deadline shown) and any review/run/decision/download returns `403 BIDS_SEALED`.
  `effective_bid_deadline` = `bid_deadline` or `close_date` 18:00 IST. **Admin may
  force-open** a tender (audited `tender_bids_force_opened`) — the Admin role itself is not
  gated; a force-opened tender is also exempt from the scheduled auto-close.
- **Per-district demo split (2026-09-26):** the two Karnataka districts now demonstrate
  different phases of the same flow, out of the box:
  - **Belagavi — `TDR-2026-001` (sealed-bid demo):** sealing ON, opening seeded to
    **+3 days 18:00 IST** so the sealed state is demonstrable at any time before then.
    The demo's opening step is the **audited admin force-open**
    (`POST /admin/tenders/{id}/open-bids`), after which the full 11-stage pipeline runs.
  - **Hubli — `TDR-2026-002` (AI-verification demo):** `sealing_enabled=false`, so bids
    are open from submission and AI compliance verification can be run **immediately**,
    before any deadline or force-open.
  - All other seeded tenders still close/open at **today 18:00 IST**.
- **Scalability (NFR):** SQLAlchemy pool `pool_size=20 / max_overflow=40 /
  pool_recycle=1800 / pool_pre_ping` (env-tunable), GZip for large JSON/PDF/PNG,
  slowapi rate limiting, disk-cached document replicas, in-process verification cache,
  and in-process RAG (no network hop). Production: run uvicorn with multiple workers
  (`--workers 4`), each with its own pool.
- **Precise eligibility labels (2026-09-20):** the bidder's pre-check reports the
  *specific* state instead of one generic "Not Eligible":
  - `BLACKLISTED` — an active debarment record (one domain is enough): **every**
    tender is labelled Blacklisted and application is disabled for all of them,
    with the register reason shown;
  - `MISSING_DOCUMENTS` — mandatory tender documents not yet in the Document Vault
    (actionable: upload and re-check) — distinct from a genuine `NOT_ELIGIBLE`
    hard failure;
  - `ELIGIBLE` / `CONDITIONAL` — as before (conditional = manual-review findings,
    never an auto-pass).
- **Credible verification output (2026-09-20):** document authenticity now
  corroborates fields against the issuing source (GSTIN/PAN/Udyam/**CIN**/legal
  name), so a compliant bidder's certificates read **VERIFIED** with the matched
  evidence — "unable to verify" is reserved for documents that genuinely cannot be
  corroborated (e.g. no registry record). The cross-verification table shows full
  values to the reviewing officer (no asterisk-masking that hides the comparison),
  address comparison tolerates house-number/pin-code differences, and the
  edge-density tampering heuristic only flags a localized content-band spike
  (no false positives on clean documents).

### Application IDs (2026-09-16)
- **Application IDs** — every bid gets a trackable `application_no`
  (`APP-2026-NNNNNN`, unique) at submission; it is shown in the apply
  **confirmation**, bidder dashboard/list/detail, officer queue & review,
  verification-report PDF and notifications.

### Real OTP two-factor sign-in (bidder portal, 2026-09-16)
- Bidder-portal sign-in (BIDDER / BID_PREPARER / SIGNATORY) is now two-factor:
  **email + password → 6-digit OTP delivered by SMS to the account's own
  registered mobile** (the filer's number — never the owner's). Government/
  officer accounts keep direct sign-in (they're gated by Admin approval).
- OTP security: codes are cryptographically random, stored **only as SHA-256
  hashes** (`otp_records`), 5-min expiry, max 5 attempts (then invalidated),
  60-sec resend cooldown, max 3 live unused codes per 10 min (SMS-bomb
  protection), full audit trail (`login_2fa_issued`, `login_2fa`,
  `otp_failed`).
- **Trusted device**: "Trust this device for 30 days" binds a hashed
  per-browser device ID (`trusted_devices`, client `X-Device-Id`) — next logins
  from that browser skip the OTP. Re-trust refreshes the window.
- **SMS providers** are env-configured (`app/core/otp.py`): `console` (default —
  the code is written to the **server log only**; the UI shows only a
  clearly-labelled "no SMS gateway configured" warning and **never the code**),
  `msg91`, `twilio`, `textlocal`. Set e.g. `SMS_PROVIDER=msg91
  MSG91_AUTH_KEY=… MSG91_SENDER_ID=…` (or the Twilio / TextLocal equivalents)
  to send **real SMS** to the registered phone. A gateway failure is surfaced
  to the user (`sms_error`) — an OTP is never silently marked "sent".
  **This environment ships with Twilio configured** (`backend/.env`, sender
  `+17372508034`); trial accounts must list each destination number under
  *Phone Numbers → Verified Destinations* in the Twilio console (error 572002
  otherwise — see RUN_LOCAL.md troubleshooting).

- **Demo delivery proof (this deployment's chosen design):** OTP delivery via
  a paid gateway was deliberately *not* enabled, so the OTP code stays in the
  **server log** (console channel; never shown in the UI). But to prove the
  pipeline *can* deliver, **every OTP generation also fires a real,
  best-effort notification SMS to the registered phone** via Twilio Verify's
  pre-approved template (`TWILIO_DEMO_SMS=true`,
  `TWILIO_DEMO_SMS_CHANNEL=sms|whatsapp`). Trial-account facts that shaped
  this: raw custom SMS bodies are rejected (error 572006), so the Verify
  product's pre-approved template is used; the demo message does **not**
  carry the system's OTP code; it is best-effort and can never fail the
  sign-in flow. The demo story: *"the phone received a real SMS from the
  project's pipeline; the code to enter is the one logged server-side."*
- **The OTP is never displayed in the interface** — not at login, not at
  registration. With a gateway configured it goes straight to the phone; in
  console mode it exists only in the backend log.
- Registration also sends the activation OTP by SMS (mobile is now a required
  registration field); owner-added org members must be given a mobile.

### Government portal: Govt Unique ID + OTP & employee master (2026-09-17)
- Government-portal sign-in no longer uses email/password. Officers sign in
  with their **Government Unique ID** (Ministry of Petroleum & Natural Gas
  civil-servant format `GOI-PNG-YYYY-NNNN`: Govt of India · ministry · year of
  joining · employee no., e.g. `GOI-PNG-2018-0492`) → a 6-digit **OTP is
  sent by SMS to the phone registered against that ID** → verify → session.
  The login screen's Government tab offers only the Govt ID field (demo cards
  prefill the ID), and the OTP is never shown in the UI.
- **System-Admin employee master** (`/admin/gov-employees`,
  `GET|POST /admin/gov-employees`, `POST …/{id}/approve|suspend`,
  `PUT …/{id}`) is the single source of truth for govt sign-in: Government ID
  (unique), full name, **registered phone (E.164, masked in the API)**, role,
  department, status. An officer **can log in only if their ID exists there**.
- **First-login approval gate**: a newly created employee starts as
  `PENDING_APPROVAL` — the OTP still reaches the phone and verifies, but **no
  token is issued** until the System Admin approves (`gov_employee_created /
  gov_employee_approved / gov_employee_suspended` audited). A linked
  `{gov_id}@gov.in` user is auto-provisioned (random password, unused — govt
  sign-in is OTP-only) and its status mirrors the employee record.
- Suspension blocks sign-in with `403`. Unknown IDs get `400`.
- The email+password `/auth/login` API endpoint still works for officer
  accounts (ops/backdoor used by the test suite); the **UI** gov portal does
  not expose it.
- Seeded demo Govt IDs (format `GOI-PNG-<year>-<emp no>`): `GOI-PNG-2011-0114`
  System Admin (S. Iyer), `GOI-PNG-2009-0387` Auditor, `GOI-PNG-2018-0492`
  Procurement Officer Belagavi (Anita Deshmukh), plus the full 17-role cast
  (`GOI-PNG-20xx-0NNN`, years 2009–2025), and `GOI-PNG-2025-1102` a new officer
  **pending approval** (approve it from the employee master to watch the gate
  lift). Real OTP numbers are wired to the four bidders + the admin/officer
  demo line.

## Running locally (no Docker required)

```bash
# 1. PostgreSQL (already running in this sandbox: user gemsentinel, db gem_sentinel)
# 2. Mock government portals
cd mock-govt-portals && uvicorn main:app --port 8001 &

# 3. Seed (creates demo users, documents, tenders, blacklist, scenarios A–D)
cd backend && python3 -m app.seed

# 4. Backend
cd backend && uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# 5. Frontend
cd frontend && npm install && npm run build && npx next start -p 3000
#   (development: npm run dev)
```

Open `http://localhost:3000`, sign in with any demo account (table above, also shown
on the login screen).

### Docker Compose (optional)
`docker-compose.yml` at the repo root brings up postgres, mock portals, backend and
frontend together.

## Tests

```bash
cd backend
python3 -m app.seed          # reseed before running
python3 -m pytest tests/ -q  # 66 tests: 22 critical + 12 slice-1 + 18 enhancements + 4 cache + 2FA + 8 govt-ID/OTP
```

All 66 pytest tests are green. The suite covers the scenario A–D E2E flows
(clean, clarifiable, critical + full override chain, source-unavailable →
UNABLE_TO_VERIFY), RBAC, audit chain, blacklist, collusion, resiliency, the
verification cache, the real-OTP two-factor
sign-in (2FA flow, wrong-code handling, trusted-device fast path, resend
cooldown), and the **government-portal flow** (`tests/test_gov_auth.py`:
seeded Govt-ID + OTP sign-in, unknown ID → 400, pending-approval OTP verifies
but yields no token, admin create→approve→sign-in, suspend → 403, masked
phone in the employee list, duplicate Govt ID → 409, wrong OTP rejected).
(Legacy `tests/smoke_a.py` /
`tests/smoke_bcd.py` scripts pre-date the V2.1 17-role redesign and are
superseded by the pytest suite.)

## Repo layout

```
gem-sentinel/
├── backend/            # FastAPI app (app/), tests (tests/), requirements.txt
├── frontend/           # Next.js 14 app (app/, components/, lib/, locales/)
├── mock-govt-portals/  # simulated government source endpoints
├── data/seed_data/     # trusted reference images for authenticity checks
├── storage/            # uploaded files (docs, evidence)
├── docker-compose.yml
├── .env.example
└── README.md
```

## Principle & honesty statements
- Government integrations in this prototype are **simulated** (mock portals); the
  reference blacklist DB records source authority, official order reference and
  last-verification date, and is explicitly **not** an official universal blacklist.
- AI is advisory: it may detect, extract, compare and explain; the deterministic rule
  engine produces the compliance outcome.
- This is a decision-support system: the Procurement Officer holds final authority.

##
cd "C:\Users\DELL\Downloads\gem-sentinel-audited-fixed-demo-flow\gem-sentinel\mock-govt-portals"

..\backend\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8001 --reload

cd "C:\Users\DELL\Downloads\gem-sentinel-audited-fixed-demo-flow\gem-sentinel\backend"

python -m venv .venv

.\.venv\Scripts\Activate.ps1

python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

cd "C:\Users\DELL\Downloads\gem-sentinel-audited-fixed-demo-flow\gem-sentinel\frontend"

npm install

npm run dev