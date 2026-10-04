# GeM-Sentinel — Agent Progress Log

Working strictly to the SIH26100 PDF (no feature removal / simplification;
Override Accountability Engine implemented for real; unverified data is never
marked compliant).

## Completed

### Dual-portal UX fix (user-reported: "not getting login, no dual portal")
- Root cause: the first live preview served a broken build (next-intl middleware
  404'd `/login`), and there was no portal-entry screen — a single flat login form.
- Fix: new landing page (`/`) with **two portal cards — Bidder Portal and
  Government Portal** — each deep-linking to a role-aware login
  (`/login?portal=bidder|gov`) with Bidder/Government tabs and **one-click demo
  accounts** (click a card to autofill credentials). Frontend rebuilt and restarted.
- Verified: landing cards render (en + hi), tabs + all 9 demo accounts render,
  admin/bidder login 200 via proxy.

### Backend (FastAPI + PostgreSQL) — DONE, all suites green
- **Models**: users/roles (BIDDER, L1_OFFICER, L2_OFFICER, ADMIN), jurisdictions &
  officer assignments, tenders + per-tender deterministic rules (versioned), bidder
  profiles (PAN/GSTIN/Udyam Fernet-encrypted + masked), consent records per source,
  document vault (government-fetched vs bidder-uploaded, hashes, versions),
  applications + status history, verification reports, clarifications, officer
  decisions, override records, vigilance referrals, grievances, blacklist reference
  entries, hash-chained audit log, source health.
- **Auth**: JWT access/refresh, OTP verify (dev mock 123456), **/auth/reauth**
  short-lived (10-min) re-auth token required before overrides, login rate limiting,
  role/jurisdiction RBAC with strict isolation (bidder 404, L1 cross-jurisdiction 403,
  L2 scope, admin-only 403s).
- **Deterministic Rule Engine**: `PASS / FAIL / UNABLE_TO_VERIFY` per rule with
  evidence; condition types: is_active, valid_unexpired, min_turnover, has_document,
  has_authorization, not_blacklisted (hard), name_match, filing_current,
  registration_required, min_local_content, startup_certified. Consent-gated source
  fetches; source failure is never PASS.
- **Scoring & eligibility**: weighted compliance score → LOW/MEDIUM/HIGH/CRITICAL;
  deterministic ELIGIBLE / CONDITIONAL / NOT_ELIGIBLE; apply blocked on confirmed
  hard failures (409 APPLICATION_BLOCKED with reasons).
- **AI layer (advisory only)**: Gemini explainer (deterministic fallback template),
  OCR-style field extraction, document authenticity heuristics (OpenCV seal/signature
  detection, similarity to trusted references, PDF metadata, tampering indicators) —
  similarity alone never conclusive; suspicious docs never VERIFIED.
- **Routing**: exact-jurisdiction routing with fallback + admin notification.
- **Decisions workflow**: L1 (qualify/reject/clarify/manual/forward/escalate/fraud),
  L2 (approve/approve-with-observation/reject/send-back/additional clarification/
  fresh verification/escalate), report staleness triggers.
- **Override Accountability Engine**: 409 challenge on blocking findings; enforced
  min-justification length; re-authentication; optional evidence upload (SHA-256);
  audit entries (re-auth time, IP, UA); Admin notification; CRITICAL/FRAUD →
  admin_cosign_required + Vigilance referral auto-created (notice with "must not be
  read as stating" disclaimer, manual CVC filing only); post-cosign gate bypass so
  L2 approve works exactly once.
- **Admin**: user/officer approval (role + jurisdiction), suspend/activate,
  jurisdictions, tender CRUD + publish/close, **Rule Builder** bulk save (blacklist
  rule locked hard), blacklist DB (no hard delete; status transitions only),
  override monitoring (review/reject/escalate/cosign), vigilance referrals
  (DRAFT→REVIEWED→READY_FOR_CVC_FILING→FILED_MANUALLY + notice export), grievances
  (respond with outcome), **hash-chained audit viewer + chain verification**,
  analytics (verification time, status mix, rejection reasons, clarifications,
  tender/district workload, portal failure rate, override trends, officer frequency,
  SLA, participation), source health.
- **Reports**: PDF (reportlab) + HTML verification reports with model identifier.
- **Seed**: 4 demo scenarios — A clean pass (ramesh), B compliance gap +
  clarification + resubmit (sita), C blacklist override chain (vikram), D
  unable-to-verify (meena, EPFO unavailable → CONDITIONAL, never auto-approved).
- **Tests**: `tests/test_critical.py` (23 tests) + `smoke_a.py` + `smoke_bcd.py` —
  ALL PASS.

### Frontend (Next.js 14 App Router, TypeScript) — DONE, production build green
- All UI strings in **en + hi** (next-intl, cookie-based locale, global persisted
  EN/हिंदी switch in the shell header).
- Auth: login (with demo-account panel), bidder registration + OTP activation.
- Shell: role-aware sidebar nav, topbar with language switch, notifications bell
  (30 s poll, unread badge, mark read/all), role badge, sign out, decision-support
  footer.
- **Bidder portal**: dashboard (stats, eligible tenders, pending apps, deadline
  reminders), profile (completion %, encrypted ID fields), consent page (grant/revoke
  per source + ALL), Document Vault (consent-gated fetch from sources, resync, manual
  upload with duplicate detection, source badges, expiry), tender discovery (search,
  filters, bookmarks, live eligibility badges), tender detail (live pre-check,
  checklist, source status, missing/mismatch alerts, apply modal with 40-char
  declaration + consent confirmation; 409 block shows reasons), applications list +
  detail (timeline, next step, report summary, clarifications with text+file response,
  withdraw, grievance/appeal).
- **Officer (L1)**: dashboard (queue stats, high-risk, SLA, override alerts),
  jurisdiction queue (filters), review page (tabs: AI Report / Bidder profile /
  Documents / Clarifications / Decisions+Overrides / Audit) with Run AI Verification,
  full evidence report (score ring, rule checklist, cross-verification table,
  mismatches, document gaps, blacklist findings, source results, authenticity, AI
  explanation, PDF/HTML download), decision panel; **Override Accountability modal**
  (challenge, finding breakdown, category, justification min-length, password
  re-auth via /auth/reauth, optional evidence upload).
- **L2 Approving Officer**: queue with L1 recommendation + override badges, review
  page with L2 decision set (approve, approve with observation, reject, send-back,
  additional clarification, fresh verification, escalate to admin), co-sign pending
  banner, override modal (same engine, /l2 prefix).
- **Admin**: dashboard (critical override watchlist, co-sign queue, referrals),
  user management (officer approval with role + jurisdictions, suspend/activate,
  create jurisdictions), tender management (create, publish, close) + **Rule
  Builder** (per-rule key/label/source/condition/value/weight/failure/mandatory/
  manual-review/hard-disqualification/enabled; blacklist rule locked, no delete),
  blacklist page (source authority, order ref, validity, status transitions, no
  delete), override monitoring (severity filter, cosign button, review/reject with
  notes, officer frequency), escalations (grievances with outcome + response),
  **audit viewer** (hash chain columns + verify-chain button), vigilance referrals
  (detail, report JSON, notice text, review/ready/filed, notice export), analytics
  (recharts: status pie, district/override/officer/tender bars, SLA, participation,
  rejection reasons, portal failure, source health).
- API layer: same-origin `/api/*` (Next rewrites → backend; no page-route clashes),
  single 401→refresh retry, X-Preferred-Language header from persisted locale.
- `npm run build` → 24 routes, compiles clean.

### Verified end-to-end through the frontend proxy (:3000)
- Login for all 4 roles; dashboard/queue JSON via proxy.
- Full Scenario C chain: 409 CRITICAL challenge → wrong-password re-auth 401 (logged)
  → re-auth 200 → override → PENDING_COSIGN + "Admin Co-sign Required" + vigilance
  referral → admin cosign → "Pending L2 Approval" → L2 APPROVE → "Approved" →
  audit chain valid (16 records) → referral VIG-2026-0001 with required disclaimer.
- Blacklisted fresh submission → 409 APPLICATION_BLOCKED (hard block at apply time).
- Hindi locale renders (NEXT_LOCALE cookie) with full translations.
- All 24 page routes render 200 with real content.

## Running processes (sandbox)
- backend :8000 — `uvicorn app.main:app --reload` (process backend-api-b67ab2e7)
- mock govt portals :8001 (process mock-govt-portals-35d12fa2)
- frontend :3000 — `npm run start` (production build, process website-07afdbad)

## Remaining (minor polish only)
- Optional: screenshot-based demo walkthrough; frontend unit tests.
- Re-run `python3 -m app.seed` before any fresh demo (DB holds test mutations).
- [x] 2026-09-13 Login "stuck on login page" FIXED: proxy strips Authorization header + user
  browser had stale cached JS → server-side gs_access cookie (set on login/refresh/OTP, read
  by get_current_user), no-store HTML caching, middleware req-logging. Verified end-to-end
  through :3000 proxy with cookie-only client. 23/23 tests green (test updated for cookie
  carryover).
- [x] 2026-09-13 Sandbox RESET recovered: fresh container wiped node_modules/.next/pip/postgres + all
  processes. Reinstalled: postgresql-17 (fresh cluster, role gemsentinel, db gem_sentinel),
  pip -r requirements.txt, npm install; restarted mock :8001 (mock-portals-ce609961),
  backend :8000 (backend-api-1d5fd7ee), frontend :3000 (website-e620d6dd). Reseeded. 23/23 tests.
- [x] 2026-09-13 Consent → apply round-trip fixed: consent page now has a back button
  (context-aware: "Back to tender — TDR-xxx" when arrived from the apply dialog, else back to
  tenders list). After granting consent from the apply flow, a success banner shows
  "Go back & submit your application". Tender page persists the in-progress application draft
  (declaration/remarks/consent checkbox) in sessionStorage keyed per tender, restores it on
  return, and auto-opens the apply dialog via ?resume=1. i18n: backToTender/returnToSubmit/
  consentDoneApply (en+hi). Build green, pages 200, full apply flow via :3000 verified.
- [x] 2026-09-13 "Unable to submit application" fixed: user's submit POST never fired — dialog's
  Submit was silently disabled (needs 40-char declaration + consent tick; no explanation shown).
  Fixes: (1) tender detail API now returns existing_application (no: APP-2026-000001 for ramesh
  on TDR-2026-001, seeded); (2) tender page shows "You have already applied" info alert w/ link;
  (3) consent checkbox auto-ticks when GET /bidder/consent shows GRANTED (+ "Consent verified"
  note); (4) dialog now has live "To unlock Submit you need" checklist (✓/○ + char counter).
  i18n en+hi. E2E verified via :3000: ramesh → TDR-2026-003 → 200 APP-2026-000006 (GLOBAL_FALLBACK
  routing) → test app cleaned from DB. State kept: user's consent GRANTED (10 rows), only seeded
  app for ramesh. TDR-2026-002 correctly blocked for ramesh (BIS/OEM missing — by design).


## V2.1 Master Build (in-place, per the 21-section MASTER BUILD PROMPT)

Multi-session vertical-slice build reusing the V1 foundations (mock govt portals,
Postgres, rule engine, override engine, hash-chained audit, consent, vault).
Slice order per prompt §21; checkpoint with user between slices.

### Slice 1 — Identity, roles & IP-aware audit (COMPLETE 2026-09-15)
Scope: 17-role model (explicit role names, never "L1/L2 officer" — L1/L2/L3 are
only the 1st/2nd/3rd-lowest evaluated financial bids), role enforcement in the
backend + DB queries, and the IP-aware hash-chained audit log.

Backend (all green — full suite 35 passed in ~43s):
- `User.role` expanded to 18 values (17 V2.1 roles + legacy L1/L2 bridge kept
  for old DB rows). `PROCUREMENT_OFFICER`, `BID_OPENING_OFFICER`,
  `TECHNICAL_EVALUATOR`, `FINANCE_EVALUATOR`, `COMMITTEE_CHAIR`,
  `COMMITTEE_MEMBER`, `CFA`, `CONTRACT_MANAGER`, `CONTRACT_OFFICER`,
  `INSPECTION_OFFICER`, `PAYMENT_OFFICER`, `AUDITOR`, etc.
- **Behavior-role groups** (`core/deps.py`): `L1_BEHAVIOR_ROLES` =
  (L1_OFFICER, PROCUREMENT_OFFICER, BID_OPENING_OFFICER, TECHNICAL_EVALUATOR);
  `L2_BEHAVIOR_ROLES` = (L2_OFFICER, FINANCE_EVALUATOR, CFA). Legacy
  `officer.py`/`l2.py` routers, `routing.py` (4 filters) and `get_current_l2`
  now use the groups so the V1 flow keeps working for V2.1 role names during
  the migration window (superseded in Slice 7).
- **IP-aware audit**: `append_audit`/`process_override` derive the secure
  client IP via `ipcontext.resolve_client_ip` (honors X-Forwarded-For) + user
  agent from the request; canonical payload stores `source_ip` (normalized),
  `ip_source`, `request_id`, `session_id`, `user_agent`, `tender_id`,
  `application_id`.
- **Masked-by-default audit API**: `GET /admin/audit-logs` returns masked IP
  for everyone; full IP only for ADMIN+VIEW_FULL_IP via
  `POST /admin/audit-logs/reveal-ip` {event_id, password} (password re-auth;
  401 on wrong password). Every reveal is itself recorded as a HIGH-severity
  `audit_ip_revealed` event (tamper-evident chain).
- Tests: `tests/test_v2_slice1.py` (12 tests: role set, behavior groups,
  audit IP fields, masked-vs-full, reveal re-auth 401/200, reveal audit event,
  tamper detection) — plus the 23 pre-existing critical tests. 35/35 green.

Frontend (built, restarted, smoke-verified):
- Login page: 11 one-click government demo accounts (all V2.1 roles + admin +
  a PENDING_APPROVAL officer) with the seeded passwords.
- `lib/types.ts` Role → 18 values; `components/app/shell.tsx` `RoleBadge` →
  `ROLE_META` map (18 entries; legacy labeled "(legacy)") + generic fallback.
- `lib/auth.tsx` `Guard` now accepts a single role OR a behavior-group array;
  `app/officer/layout.tsx` and `app/l2/layout.tsx` guard by the L1/L2
  behavior groups (frontend mirror of the backend).
- `app/admin/users/page.tsx`: role filter + assign-role dropdown now cover all
  18 roles with readable labels; jurisdiction mandatory only for
  queue-based officer roles (optional for committee/CFA/contract/etc.).
- `app/admin/audit/page.tsx`: added **IP (masked) + source** column,
  **Req ID** column, and a **Reveal (audited)** dialog (VIEW_FULL_IP +
  password re-auth; wrong password shows 401 detail). New filter actions
  `audit_ip_revealed`/`audit_ip_reveal_rejected`/`corrigendum_published`.
- i18n (en + hi): removed all "L1/L2 Officer" role wording from nav + officer
  console labels (dashboard → "Officer Console", forward → "Forward for
  approval", send-back → "Send back to officer", boundary note no longer says
  "declare L1"); landing page gov-portal copy now names the real roles.

### Role-count reconciliation (17 roles) — caught at Slice-1 checkpoint
- The spec defines **17 V2.1 roles**. On review the backend had only **16** —
  `CONTRACT_OFFICER` (Contract Execution Officer) was missing (it exists in the
  contract phase but was never added as a distinct role; only CONTRACT_MANAGER,
  INSPECTION_OFFICER, PAYMENT_OFFICER were present).
- Fix (all green, 35/35): added `ROLE_CONTRACT_OFFICER` to `core/deps.py`
  (+ `BUYER_SIDE_ROLES`), `permissions.CONTRACT_ROLES`,
  `models/workflow.WORKFLOW_ROLES`; frontend `types.ts` Role union,
  `shell.tsx` `ROLE_META`, login demo card, and admin users role list; seeded
  `contractexecution.karnataka@cpcl.gov.in` (Execution@1234) + a
  `CONTRACT_OFFICER` tender assignment. Rebuilt + restarted frontend; DB
  reseeded. Now all 17 V2.1 roles are defined (legacy L1/L2 bridge kept
  separately) and the 17th logs in + is tender-assigned end-to-end.

Smoke verification (live, through :3000 proxy + :8000 direct):
- procurement.belagavi@cpcl.gov.in (PROCUREMENT_OFFICER) login → /auth/me ok,
  /officer queue returns its application; cfa.karnataka@ (CFA) → /l2/dashboard
  returns role CFA.
- Admin /admin/audit-logs: rows carry source_ip (masked) + ip_source=DIRECT +
  request_id; reveal-ip wrong pw → 401, correct pw → full IP + ip_source; an
  `audit_ip_revealed` event is recorded.
- All 5 key pages (/admin/audit, /officer, /l2, /admin/users, /login) serve
  200; login page chunk contains the new demo accounts.

## BidVerify-AI key-feature port (additive, COMPLETE 2026-09-16)

User request: port the *key* features from the BidVerify-AI reference repo
additively — preserving core code/functionality. Full comparison in
`COMPARE_BIDVERIFY_AI.md`. All items below are additive (new modules/tables/
endpoints; no rewrite of the engine, workflow, or security).

1. **Collusion indicators (P0)** — `services/collusion.py`,
   `GET /collusion/tenders/{id}` + `/history`, `collusion_analyses` table,
   admin tender-detail panel, role-gated (buyer-side/admin/auditor),
   advisory-only (never auto-disqualifies), hash-chained audit on each run.
2. **Cross-document identity rules (P1)** — `names_consistent`,
   `gst_state_match`, `pan_gst_match`, `turnover_gst_match`,
   `gstin_checksum_valid` in `rules_engine.py` + rule-builder dropdown entries.
3. **Portal resiliency (P1)** — `services/resiliency.py`: circuit breaker
   (CLOSED/OPEN/HALF_OPEN) + full-jitter backoff + 24h stale-while-revalidate;
   `GET /admin/source-resiliency`.
4. **Upload security guard (P2)** — `services/doc_guard.py`: extension-vs-magic
   byte match, PDF exploit flags, PII masking (PAN/GSTIN/phone/email).
5. **LLM citation whitelist (P2)** — `services/citation_guard.py`: strips
   non-whitelisted citations from AI explanations (Pydantic v2, verbatim only).
6. **Verification-result cache (P2) — added 2026-09-16** —
   `services/verify_cache.py` + `verification_cache` table (2-tier: in-process
   LRU → Postgres; Redis is an optional extension point). TTL
   `VERIFY_CACHE_TTL_SECONDS` (900 s). Only deterministic states cached
   (VALID/EXPIRED/MISMATCH/NOT_FOUND/BLACKLIST) — failures are NEVER cached,
   so UNABLE_TO_VERIFY semantics are preserved. Cached results carry
   `_cached`/`_cached_at`/`_cache_age_seconds` markers. Admin:
   `GET /admin/verify-cache` (stats) + `POST /admin/verify-cache/clear`
   (audited `verify_cache_cleared`). Mock portal gained `GET/POST
   /api/admin/stats` call counters (proves repeat verifications skip the
   portal: run1=5 → run2=5, delta 0) — this also exposed + fixed a latent
   `UnboundLocalError` bug in the mock's fault endpoints (AGENT_ERRORS #15).

Deferred intentionally (would alter core or are P3 infra): batch worker,
`/metrics` + Grafana + k8s manifests.

**Status:** 57/57 pytest green (23 critical + 12 slice-1 + 18 enhancements +
4 cache); frontend rebuilt (collusion panel + 4 new rule conditions, en/hi);
live-verified through :3000 proxy + :8000 direct. Sandbox was reset between
sessions — environment re-provisioned: venv + Postgres 17 + reseed + frontend
build; all three services restarted.

---

## Session 2026-09-16 — Six-part upgradation (RAG + XAI, PDF-only reports, bidder rejection explanation, document replicas, deadline gating, scalability)

User request (verbatim intent): (1) add RAG *only where it is architecturally essential*;
(2) Explainable AI for Run Verification — professional explanations with real reasons;
(3) report download **PDF only**, containing all explanations; (4) rejected bidders get a
reasonable, **downloadable** explanation in the bidder portal; (5) bidder-portal documents get a
**view button** → original-looking replica generated with a free AI tool of my choice, also viewable
by officers for manual review; (6) officers may view/run AI verification **only after the bid
deadline** — before it, ALL details hidden; every deadline set to **today 6 PM IST** (demo);
(7) system must be **scalable for large concurrent traffic**.

### 1. RAG — local deterministic retrieval (architectural placement decision)
- Placement (senior-architect call): RAG sits in `app/ai_engine/` and **grounds the explainer
  only** — it never touches the deterministic rule engine, scoring, or any decision path.
  This is the single point where retrieval is *essential*: the AI narrative must cite real
  statutory clauses without a paid embedding API (provider-neutral, offline, deterministic,
  zero-latency).
- `ai_engine/knowledge_base.py`: 19 grounding passages keyed to the DB's real `clause_ref`s
  (GeM GTC §2.1/§2.1(b)/§2.4, ATC-1/2/4, Documents §4.1, Spec §3.1/3.2/4.2, Eligibility §3.2,
  Integrity §7.1, Labour §6.1, Preference §8.3, …).
- `ai_engine/rag.py`: `retrieve_for(evaluation, k=8)` — deterministic scoring
  (clause match +4, condition-type match +3, token overlap +1.5); retrieved passages (with
  `score`/`matched_on`) are published in the report's `governance.retrieved_passages`
  (RAG transparency) and passed to the citation guard as allowed refs.

### 2. Explainable AI (structured v2.0)
- `ai_engine/explainer.py` rewritten: `structured_explanation()` → `executive_summary`
  (headline + counts), `basis_of_decision` (per rule: rule_key, clause_ref, statutory_basis
  from KB, cited_passage_id, evidence{source,status,detail}, weight, impact),
  `cross_verification`, `document_findings`, `evidence_trail` (source/status/retrieved/cached),
  `remediation`, `citations`, `governance` (decided_by / ai_role / retrieval / final_authority /
  retrieved_passages).
- Professional EN + HI narratives with inline `[Clause: …]` citations; optional Gemini
  (`gemini:<model>`) narrative grounded on retrieved passages only; fallback
  `deterministic-xai-2.0`.
- `citation_guard.py`: `sanitize_explanation(…, allowed_refs=…)` — grounded KB clause citations
  now pass the guard; regex split into colon-form + uppercase/digit-start word-form so prose
  ("rule set") is no longer mangled. Cache markers stripped from basis/evidence messages.
- Persisted: new `explanation_detail` JSON column on `verification_reports` (reseeded);
  returned by the review API. Frontend: new XAI section in `report-tab.tsx` (basis cards,
  statutory basis, remediation, citations, governance + retrieved-passages inspector).

### 3. PDF-only reports (all explanations in the PDF)
- `reports.py` rewritten: Platypus multi-section professional PDF — score box, executive
  summary, basis-of-decision table, statutory basis, cross-verification, document findings,
  evidence trail, blacklist findings, remediation, full AI narrative, citations, governance,
  disclaimer footer + page numbers. Unicode-safe: DejaVu Sans (latin) + Noto Sans Devanagari
  (Hindi reports render natively).
- Download endpoint is **PDF only** (`GET /officer|l2 …/report/download`) — HTML removed from
  the user path. Frontend: new `downloadFile()` blob helper in `lib/api.ts` (auth-carrying
  fetch → object URL → anchor) replacing the broken `window.open` (which carried no
  Authorization header). Wired on officer review + L2 review.

### 4. Rejected-bidder explanation (portal + downloadable)
- `GET /bidder/applications/{id}/rejection-explanation` (+ `/download` PDF) — 404 unless
  status is Rejected. Uses the deciding officer's recorded remarks, **sanitized** for the
  bidder (emails → `[redacted contact]`, other app IDs → `[application reference]`,
  override/vigilance/co-sign/escalation terms → `internal review`, L1/L2 → `the evaluation
  committee`); when no remarks exist, a neutral explanation is derived from the bidder's own
  report (failed mandatory checks with clause refs, missing/expired documents).
- Frontend: red "Why was my application rejected?" card with the explanation + PDF download on
  the bidder application page.

### 5. Document viewing — Synthetic Document Studio (free tooling: Pillow, no external API)
- `services/doc_studio.py`: renders an original-looking A4 replica per doc_type (letterhead
  with emblem, document title, mono reference/identification, deterministic QR block,
  field table from verified source data, seal, script signature, expiry banner) with a
  persistent **SYNTHETIC** diagonal watermark + footer ("simulated data — not a genuine
  government issuance") and a SHA-256 `X-Document-Hash` header. Officer variant carries a red
  "OFFICIAL REVIEW COPY — MANUAL REVIEW" banner.
- Stable cache `storage/docs/ai_replicas/{user_email}_{doc_type}[_review].png` + content
  fingerprint sidecar (survives reseeds; `RENDER_VERSION` invalidates on layout changes).
- Endpoints: `GET /bidder/documents/{id}/view`; `GET /officer/documents/{id}/view?application_id=`
  (officer view gated on the bid deadline via `get_scoped_application` → 403 BIDS_SEALED).
- Frontend: `DocViewer` dialog (image + hash + synthetic badge + PNG download) wired into the
  bidder vault ("View document" on every card) and the officer review docs tab.

### 6. Deadline gating (pre-close confidentiality) + demo deadlines
- `core/bids.py`: `effective_bid_deadline` (bid_deadline or close_date 18:00 IST),
  `tender_bids_open`, `sealed_detail` → 403 `{code:"BIDS_SEALED", bid_deadline_utc/_ist,
  tender_no}`. `ensure_bids_open` enforced in `get_scoped_application` → covers officer review,
  run-verification, decisions, downloads, L2 detail/decision/download (all gated).
- Sealed list items mask everything (company null, status "Sealed", null metrics) + expose the
  deadline; list supports `status=Sealed` filter.
- Admin: `POST /admin/tenders/{id}/open-bids` (force-open, audited `tender_bids_force_opened`);
  `_tender_out` exposes `effective_bid_deadline` + `bids_open` (None-safe close_date fixed —
  was a latent 500). Frontend: officer list Sealed badges + sealed filter; officer + L2 review
  403 sealed panel (lock, deadline IST, note); admin tenders table: deadline column + sealed/
  open badge + **Force open bids** button (confirm + audited).
- Seed: all demo tenders' `bid_deadline` = **today 18:00 IST** (12:30 UTC) — live-verified.

### 7. Scalability (NFR)
- SQLAlchemy pool: `pool_size=20, max_overflow=40, pool_recycle=1800, pool_pre_ping=True`
  (configurable via `DB_POOL_SIZE/MAX_OVERFLOW/POOL_RECYCLE`); SQLite path unaffected.
- GZip middleware (min 1 KB) for large JSON/PDF/PNG responses; rate limiting (slowapi) already
  in place; document replicas cached on disk (no re-render per view); verification cache
  (session 2026-09-15) cuts portal load; deterministic RAG is in-process (no network hop).
- Production: run uvicorn with multiple workers (each worker gets its own pool), e.g.
  `uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4`.

### Verification (2026-09-16)
- **59/59 pytest green** (23 critical + 12 slice-1 + 18 enhancements + 4 cache + 2 deadline).
- Live through the :3000 public proxy: officer list sealed (company masked, deadline shown) →
  review 403 BIDS_SEALED → admin force-open (audited) → review 200 → run-verification 200 →
  PDF download 200 (3-page, 52 KB, all XAI sections) → officer doc view 200 (PNG replica,
  red review banner) → bidder rejection explanation 200 (sanitized) + PDF 200.
- XAI live-verified in DB: `basis_of_decision[0]` = gst_active / `GeM GTC §2.1` / KB statutory
  text / `cited_passage_id: ktc-2-1` / weight 20 / evidence{source:GST,status:EXPIRED};
  citation guard: 0 removals on clean narrative.
- Frontend: `npm run build` green (29 routes, strict TS + lint); website restarted per build.

## Session 2026-09-16 — Corporate CIN verification + Application IDs (COMPLETE)

User request: (1) an Application ID for every tender a bidder applies, trackable
end-to-end; (2) upgrade bidder registration/login from email-only to incorporate
CIN verification + mapping for corporate bidders (unique CIN in DB, mandatory at
registration with 21-char format validation, email OTP kept, account explicitly
linked to CIN, login retrieves CIN + organization profile in the session context,
bid submission pre-checks CIN status ACTIVE and session match).

### 1. Schema (Company/Vendor table)
- `BidderProfile.cin` String(21) **unique index** (one CIN ⇒ one account) +
  `cin_status` / `cin_registered_address` / `cin_status_checked_at`.

### 2. Backend
- New `app/core/cin.py` — hard format `^[LF][A-Z0-9]{20}$`, structural pattern
  (soft warning), MCA21 lookup (fail-safe), `check_cin_for_submission` gate.
- Registration (auth.py): CIN mandatory for corporate (400), format 400,
  unique 409, best-effort MCA21 metadata stored (unknown ⇒ UNVERIFIED);
  CIN without company name rejected; email-OTP flow untouched.
- Session context: login response + `/auth/me` + `/bidder/profile` carry
  `cin` + status + registered address + company.
- Gate: `POST /bidder/applications` verifies CIN ACTIVE + session match before
  the application is created — 403 CIN_MISSING / CIN_UNVERIFIABLE (fail-closed) /
  CIN_NOT_ACTIVE.
- Officer review + L2 review serializers: CIN in the bidder block.
- Mock portal: `GET /api/mca/cin/{cin}` registry (4 ACTIVE demo CINs, 2 INACTIVE,
  1 ACTIVE blacklisted-entity CIN; unknown ⇒ 404).
- Seed: all four demo bidders carry ACTIVE CINs.

### 3. Frontend
- /register: CIN field (auto-uppercase, client 21-char check, required for
  corporate), i18n en+hi.
- Dashboard CIN chip; profile CIN card + editable field; officer/L2 review CIN
  row with status badge.
- Apply success now shows the `application_no` (APP-2026-NNNNNN) inline.

### Verification (2026-09-16)
- **70/70 pytest green** (added `tests/test_cin.py`, 11 tests; updated
  `test_hard_fail_submission_prevented` for the CIN-mandatory registration).
- Live smoke: session-context CIN on login; register-without-CIN 400;
  INACTIVE-CIN bidder blocked 403 CIN_NOT_ACTIVE; duplicate CIN 409;
  ACTIVE-CIN submission 200 (APP-2026-000006); unknown CIN fail-closed 403
  CIN_UNVERIFIABLE; officer review shows CIN block.
- `npm run build` green (strict TS + lint).
- README + VALIDATION updated.

### Follow-up (same session): CIN at login (user: "login is taking email only → update")
- Sign-in is now Email + Password + CIN for corporate bidders.
- Backend: `LoginIn.cin`; login gate — corporate (CIN-linked) missing CIN → 400
  (shows linked CIN), mismatch → 401 + audited `login_cin_mismatch` + failed
  attempt `CIN_MISMATCH`; non-corporate + officers unaffected. ACTIVE check
  stays at submission.
- Frontend: CIN input on /login (en+hi), demo cards autofill CIN, `login()` in
  lib/auth.tsx accepts cin.
- Tests: conftest `login()` auto-attaches demo CINs; 3 new CIN-login tests;
  **73/73 green**; live smoke via :3000 proxy verified (400 w/o CIN, 200 with).

### Follow-up (same session): real OTP 2FA replaces CIN-at-login
User: remove CIN from login (public identifier ≠ credential); no mock OTP;
real OTP to the numbers they'll provide; OTP-to-owner rejected (owner not
always available).
- Built: `app/core/otp.py` (secrets 6-digit, SHA-256-hashed storage, 5-min TTL,
  5 attempts, 60s cooldown, ≤3 live/10min, console|msg91|twilio|textlocal
  providers, fail-closed on gateway error), `models/otp.py` (OtpRecord,
  TrustedDevice), 2FA login (bidder-side roles only; officers direct),
  trusted-device 30-day fast path (hashed X-Device-Id), resend-otp, mobile
  required at registration + E.164 validation + masked display.
- Frontend: /login two-step UI (masked mobile, 6-digit input, 60s resend
  countdown, trust-device, dev-gateway hint, sms_error alert), /register mobile
  required + SMS activation, device ID in lib/api.ts, i18n en+hi. CIN field
  removed from login; session still auto-attaches CIN from DB.
- Tests: 73/73 green (3 new 2FA tests; conftest does full 2FA; cooldown
  wait-out in helper). Live smoke via :3000 verified.
- PENDING from user: SMS provider choice + credentials + the phone numbers to
  bind to the demo bidders (then set env vars + update seed mobiles).

### Follow-up (same session, 2026-09-17): Govt-portal rework — Govt ID + OTP,
employee master, OTP never shown in UI
User: OTP must go to the phone, not be viewed on screen; remove normal email
login from the govt portal (unique Govt ID + OTP to the phone registered
against it); System-Admin database of employees (Govt ID, phone, role) with
first-login approval; wire the 5 provided numbers.
- Backend: `GovEmployee` model; `POST /auth/gov-login` + `/auth/gov-verify`
  (OTP-only, pending-approval returns no token, auto-provisions
  `{gov_id}@gov.in` user); admin employee master (list w/ masked phones,
  create→PENDING_APPROVAL, approve, suspend, update — all audited); seed
  GOV-0001…GOV-0015 (GOV-0015 pending) with the 5 real numbers wired
  (bidders + admin/GOV-0003 share +919164346113), demo print lists Govt IDs.
- Frontend: gov tab = Govt ID → send-OTP → verify (no email/password);
  `dev_otp` display removed from login AND register (honest no-gateway
  warning without the code); new `/admin/gov-employees` page (table, add
  dialog, approve/suspend) + nav; i18n en+hi.
- Tests: `tests/test_gov_auth.py` (6) + conftest `gov_login()`; **79/79 green**.
- Live smoke via :3000: full pending→approve→login cycle, unknown 400,
  masked-only phones, and login HTML contains zero 6-digit codes ✅.
- STILL PENDING from user: SMS provider + credentials (console mode logs
  codes server-side until then); confirm the phone→account mapping.

### Follow-up (same session, 2026-09-17): Twilio credentials wired — real OTP delivery
User supplied Twilio SID/token/From (+17372508034).
- `backend/.env`: `SMS_PROVIDER=twilio` + creds. Zero app-code changes needed
  (adapters pre-existed); `dev_otp` is now `null` in all API responses (code
  unreachable by any client in gateway mode).
- `/health` exposes `sms_provider`; conftest `base_url` fixture auto-spawns a
  console-mode test server when :8000 runs a real gateway (tests never fire
  paid SMS; `GS_TEST_BASE` override). 79/79 green under the new setup.
- Live call: Twilio 422 code 572002 — trial account requires the destination
  numbers as **Verified Destinations** (user-side step in Twilio console).
  Fail-closed verified: error surfaced as `sms_error`, OTP not marked sent.
- Docs: RUN_LOCAL.md (gateway section + 572002 troubleshooting + test-suite
  isolation), .env.example, README, VALIDATION.
- PENDING: user adds the 5 numbers as verified destinations (or upgrades
  Twilio to paid) → then one final live send test.

### Follow-up (same session, 2026-09-17): Demo-SMS design locked in (user decision)
User: keep OTP in backend logs (read it there for the demo); additionally fire
a real Twilio message on every OTP generation to prove SMS capability (paid
OTP routing deliberately not enabled; explainable in demo).
- Empirical Twilio trial findings: raw Messages API rejects ALL custom bodies
  (572006); Verify v2 API works on trial (service VAf5a6…e1d2 "GEM OTP";
  Verifications 201 queued; 35/day cap; detail-fetch trial-blocked).
- Built: `_send_verify_demo_sms()` in core/otp.py (best-effort, never blocks,
  never carries the code; channel sms|whatsapp), config flags, /health
  demo_sms flag, conftest spawn-guard (tests never fire real SMS), 1 guard
  test, UI copy en+hi.
- Live: gov-login GOV-0003 → console log code 972636 + Verify 201 queued to
  the phone. 80/80 tests green. README/RUN_LOCAL/VALIDATION updated.
- User-side note: confirm receipt on the phones (+919164346113, +919380962272
  received test sends); Twilio console Messaging page shows delivery status.

## 2026-09-17 (later) — Sandbox reset #10 + authentic Govt ID format `GOI-PNG-YYYY-NNNN`
- **Reset #10** (mid-task): venv/node_modules/Postgres/services all wiped; code
  intact → standard recipe (apt pg + venv/pip + npm parallel → pg start →
  role/db → mock :8001 + backend :8000 (--reload, console, demo SMS) → reseed →
  build → website :3000) → smoke green.
- **User feedback:** `GOV-0001`-style IDs "every common… not giving any
  credibility and not giving authentic vibe" → reformat to the Ministry of
  Petroleum & Natural Gas civil-servant ID: **`GOI-PNG-YYYY-NNNN`** (Govt of India
  · ministry · year of joining · employee no.; user's example `GOI-PNG-2021-0492`
  adapted → main demo officer is `GOI-PNG-2018-0492`).
- **Done & verified:** seed (15 officers, format documented in the printout),
  tests (all refs incl. auto-provisioned email), login demo chips + placeholder,
  admin form placeholder, README/RUN_LOCAL — zero `GOV-` references left.
  80/80 tests; live smoke: login page renders the 15 new IDs, full OTP sign-in
  with `GOI-PNG-2018-0492` → token, pending `GOI-PNG-2025-1102` gated, old
  `GOV-0003` → 400. Evidence in VALIDATION.md.

## 2026-09-17 (later) — CIN feature removed at user's request
- **Context:** user's laptop hit `UndefinedColumn: bidder_profiles.cin` —
  their DB predates the CIN columns. After explaining the drift + adding the
  startup auto-migration (`ensure_schema()`), user chose **"remove the CIN
  feature entirely"** (they considered it abandoned).
- **Scope:** full vertical removal across model, `core/cin.py`, auth/register
  (field, validation, MCA21 lookup, session context), profile update,
  pre-submission gate, officer payload, seed, tests (test_cin.py deleted),
  frontend (register, dashboard, profile, review pages, auth lib, en/hi
  locales), mock portal (CIN_REGISTRY + endpoint), README/RUN_LOCAL.
- **Kept:** MCA incorporation-certificate document demo (separate feature).
- **Result:** 66/66 tests green; smoke-verified (corporate register w/o CIN →
  200; no cin keys in login/profile/dashboard); works with old DBs in both
  directions (missing columns ignored, leftover columns ignored).

## 2026-09-17 (later) — Demo OTP shown in the login UI (user request)
- User runs the project from the sandbox preview (no access to the backend
  console) → the console-mode OTP must be visible on-screen.
- Added a top-of-form amber demo banner on the login page's OTP step for both
  bidder + government portals, strictly gated on `dev_otp` (console mode only
  — real-SMS mode never displays the code). Locale keys en+hi; old
  "never displayed" copy updated.
- Verified: bidder + gov-login both return `dev_otp` (console); build clean;
  strings + render gate present in compiled bundle.

### V3 spec gap-fill — Sentinel Watch + evidence strength + replay (2026-09-20) — DONE, 78/78 tests
User pasted the 26-section MASTER BUILD SPECIFICATION V3: "add strictly the extra
and good features" (additive only). Diffed against the running system:
- **Built**: Sentinel Watch (auto-registered ComplianceWatch, 20s+jitter tick,
  value-level drift detection w/ flap-confirmation + dedup + owner resolution +
  alert fan-out + ack/snooze/escalate, CRITICAL never snoozable), mandatory
  pre-commitment gate check (409 WATCH_GATE_BLOCKED), drift report revisions
  (history never mutated), bidder compliance-health self-service,
  reproducibility replay (bit-identical determinism_hash), evidence-strength
  ladder S0–S4 + per-tender minimum strength + coverage % + strength
  distribution, drift PDF, Watch Tower UI + Compliance Health UI (EN/HI),
  mock-portal value-level state override API (demo beat), scheduled
  deadline-close job.
- **Fixed live while validating**: cache masking drift (watch polls now
  use_cache=False), mock snapshot-restore ordering, first-observation false
  drifts, revision id flush, MCA21/EPFO watch attributes.
- **Skipped (documented in VALIDATION.md)**: bench harness, reverse auction,
  two-envelope (rewrite-scope, not additive).
- Frontend built clean; full suite 78 passed (66 pre-existing + 12 watch).
