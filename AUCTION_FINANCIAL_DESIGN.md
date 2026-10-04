# GeM-Sentinel — Financial Bidding, Reverse Auction & Forward Auction (Design)

Extension of the existing evidence-grounded compliance platform per the master
development prompt (2026-09). **Additive only** — every existing module, route,
role, stage, test and demo keeps working; where the prompt's concepts match
existing mechanisms, the existing mechanism is extended, never duplicated.

> Principle preserved: **THE RULE ENGINE DECIDES. AI EXPLAINS.**
> Auction principle: **PROCUREMENT RULES CONTROL AUCTION ELIGIBILITY AND
> OUTCOME. AI MAY EXPLAIN OR FLAG ANOMALIES — NEVER PLACE BIDS, CHANGE PRICES,
> CHOOSE WINNERS, OR MOVE CLOCKS.** No LLM anywhere in the bid-placement path.

---

## 1. Module map (what exists — Phase 1 inspection)

| Concern | Existing implementation |
|---|---|
| Tender | `app/models/tender.py::Tender` (`procurement_method` GeM method, `bid_deadline`, `sealing_enabled`, `force_opened`) |
| Application | `app/models/application.py::Application` (`quoted_amount` Float — legacy demo field; `status` machine; `snapshot_frozen_at`) |
| Bid sealing | `app/core/bids.py` — `effective_bid_deadline`, `tender_bids_open`, `sealed_detail` (BIDS_SEALED 403) |
| 11-stage pipeline | `app/pipeline.py` (STAGES, `complete_stage`, `_rank_financial` L-rank, certificates) + `app/routers/pipeline.py` (role-scoped actions, 403 gates, PDF certs) |
| Workflow roles | `app/models/workflow.py` — `WORKFLOW_ROLES` (incl. **BID_OPENING_OFFICER, FINANCE_EVALUATOR, CFA, COMMITTEE_***), **`FINANCIAL_VIEW_ROLES`** (defined, previously unused — now the financial-opening gate), `TenderAssignment` (role, tender) scoping |
| Compliance engine | `app/rules_engine.py::evaluate_tender` (PASS/FAIL/UNABLE_TO_VERIFY, scores, blacklist), `app/services/verification.py` (reports, XAI via `app/ai_engine/*`) |
| Audit | `app/audit.py` — SHA-256 hash chain, `pg_advisory_xact_lock`, IP/request correlation, `verify_chain`; `append_audit(db, action, …)` |
| Override accountability | `app/override.py` (challenge → reauth → justification → co-sign), reauth token via `POST /auth/reauth` (`REAUTH_TOKEN_MINUTES`) |
| Collusion | `app/services/collusion.py` + `CollusionAnalysis` (LOW/MODERATE/HIGH indicator, factual findings only) |
| Sentinel Watch | `app/services/watch.py` + `app/models/watch.py` (drift, alerts, gate), APScheduler tick in `app/main.py` |
| Notifications | `app/notifications.py::notify/notify_admins` (in-app, `EVENT_TITLES`) |
| Rate limiting | slowapi `limiter` (pattern in `app/routers/auth.py`) |
| PDF | `app/certificates.py::render_certificate`, `app/reports.py` |
| Seeding | `app/seed.py` (5 bidders, 9 tenders, per-tender role assignments, mock-portal source states embedded) |
| Mock govt sources | `mock-govt-portals/main.py` (state keyed by (source, identifier)) |
| Migrations | `app/core/db.py::ensure_schema()` — additive `ALTER TABLE ADD COLUMN` on startup (project's migration pattern; no alembic) |
| Frontend | Next.js app-router: `app/{bidder,officer,admin,l2}/*`, `lib/api.ts` (same-origin, Next rewrite → :8000), i18n `locales/{en,hi}.json`, tricolor design system, `components/app/*` |
| Tests | `backend/tests/` (91 pytest, hermetic spawned server, `WATCH_ENABLED=false`), `smoke_a.py`, `smoke_bcd.py`, `/home/user/{feature_check,e2e_pipeline_walk,final_state_check}.py` |

**Gaps this extension fills**
1. Bidders never entered a price — `ApplyIn` had only declaration + consent;
   `quoted_amount` was seed-data only, and the FINANCIAL stage amount was
   officer-typed. → **Feature A** (bidder financial bid, Decimal, sealed, hashed,
   versioned, withdrawable).
2. No auction of any kind. → **Feature B/C** (shared engine, REVERSE + FORWARD).
3. `_rank_financial` ranked **all** applications with a `quoted_amount`
   regardless of compliance outcome — violating "never rank disqualified
   bidders". → ranking extended to eligible-only, price source switchable
   (initial bid vs final RA price).
4. `ENVELOPE_ENCRYPTION_KEY` existed in config unused; `FINANCIAL_VIEW_ROLES`
   existed unused → both now wired into the two-envelope authorization.

## 2. Non-destructive adaptation decisions

| Prompt concept | Adaptation |
|---|---|
| `procurement_mode` | **New** `Tender.procurement_mode` (default `STANDARD_E_BID`); existing `procurement_method` untouched. Values: `STANDARD_E_BID`, `E_BID_WITH_REVERSE_AUCTION`, `FORWARD_E_AUCTION` |
| Financial bid required | **New** `Tender.financial_bid_required` (new tenders + seeded RA demo = true; seeded t1–t9 = false so existing flows/tests are byte-identical) |
| `FinancialBid`/`FinancialBidVersion` | `FinancialBid` rows carry `version` + previous-hash chain — a versioned row *is* the version (no separate table); `FinancialBidLineItem` table added for future line-item pricing (schema-ready, UI total-price only) |
| Money type | `Numeric(16, 2)` + Python `Decimal` end-to-end server-side; `Application.quoted_amount` (Float) kept for legacy display and kept in sync — authoritative value is `FinancialBid.total_quoted_amount` |
| L1/L2/L3 | Project doctrine: L-ranks = **bidder price ranking** (not officer levels) — extended `pipeline._rank_financial`, no parallel service |
| Financial opening gate | `FINANCIAL_VIEW_ROLES` (existing) + TECHNICAL stage completed-as-qualified + bids open ⇒ financial fields visible; audited `financial_envelope_opened` event; suppression is server-side in every serializer |
| Auction state machine | `Auction.status`: `DRAFT SCHEDULED READY LIVE EXTENDED PAUSED CLOSED RESULT_PENDING COMPLETED CANCELLED` (`REGISTRATION_LOCKED` folded into `SCHEDULED` — participants fixed at creation; controlled transitions only, in `services/auction.py`) |
| `AuctionExtension` entity | Recorded as `AUCTION_AUTO_EXTENDED` audit events + `Auction.extension_count` (audit chain already stores who/when/why — no duplicate table) |
| Migrations | `ensure_schema()` additive columns + `create_all` new tables (project pattern) |
| Real-time | **SSE** (`GET /auctions/{id}/events`) + 5s poll fallback (`GET /auctions/{id}/live`); no new infra (FastAPI `StreamingResponse`) |
| RBAC | Existing (role, tender) `TenderAssignment` hats: manage = `PROCUREMENT_OFFICER`/`CFA`/`COMMITTEE_CHAIR` on the tender or `ADMIN`; pause/cancel/approve additionally require reauth token; bidder = `auction.participate` via eligible participant row |
| Failure of sealed bid pre-deadline | Existing `core/bids.py` sealing unchanged; financial fields suppressed *even after* bid opening until technical qualification |

## 3. Data model (new tables, additive)

```
FinancialBid            financial_bids
  id, application_id (FK), tender_id, bidder_user_id, version
  currency, base_amount, tax_amount, other_charges, total_quoted_amount   Numeric(16,2)
  evaluated_amount, evaluation_adjustment, evaluation_basis               (set at financial eval)
  price_valid_days, price_valid_until, commercial_declaration
  submitted_at, sealed_at, opened_at, replaced_at, withdrawn_at
  financial_bid_hash (SHA-256 canonical), prev_version_hash
  status: SEALED | REPLACED | WITHDRAWN | OPENED
  idempotency_key (uniq per application), ip, user_agent, request_id
  + FinancialBidLineItem (item_id, financial_bid_id, tender_item_ref, description,
    quantity, unit, unit_price, tax_rate, tax_amount, line_total)

Auction                 auctions
  id, auction_no (RA-2026-NNNN / FA-2026-NNNN), tender_id, type REVERSE|FORWARD
  title, status, eligibility_rule (+ rule_params JSON)
  scheduled_start_at, scheduled_end_at, effective_end_at, started_at, closed_at
  min_increment Numeric, opening_reference_price Numeric
  auto_extend_enabled, extension_trigger_seconds, extension_seconds,
  max_extensions, extension_count
  created_by, cancel_reason, paused_reason, notes

AuctionParticipant      auction_participants
  id, auction_id, application_id, bidder_user_id, anonymous_id (RA-BIDDER-XXXX)
  ra_eligible, qualification_rule, qualification_reason, calculated_at
  initial_price, final_ra_price Numeric, suspended, suspended_reason
  uniq (auction_id, application_id)

AuctionBid              auction_bids
  id, auction_id, participant_id, bidder_user_id (officer-only field)
  amount Numeric(16,2), currency, sequence_number, previous_bid_amount,
  decrement_amount, server_received_at, idempotency_key (uniq per auction+user)
  ip_address, user_agent, request_id, status ACCEPTED|REJECTED, reject_reason
  bid_hash (SHA-256 over prev bid hash + canonical fields)

AuctionResult           auction_results (one per auction)
  closed_at, participant_count, accepted_bid_count,
  initial_lowest, final_lowest, savings_amount, savings_pct
  ranking JSON [{rank, anonymous_id, bidder_user_id, amount, price_source}],
  tie bool, result_hash, status, approved_by, approved_at
```

Application lifecycle: existing `Application.status` values stay authoritative
for the compliance/technical side. Auction states live on `Auction` +
`AuctionParticipant` (no duplicated/conflicting status system on Application).

## 4. Key flows

**Submission (atomic)** — `POST /bidder/applications` (extended body):
validate app → evaluate_tender gate → validate FinancialBid (Decimal > 0,
`total == base + tax + other` exact, validity 1–365d, declaration, INR, tender
open by **server time**, not blacklisted) → create Application + FinancialBid
(v1, sealed_at = submitted_at, hash) + ApplicationDocuments + pipeline
SUBMISSION + audit `application_submitted` (+ `financial_bid_sealed`) +
acknowledgement. Either all or nothing (`db.commit` once; rollback on any
violation). `quoted_amount` synced for legacy display.

**Modify before deadline** — `PUT /bidder/applications/{id}/financial-bid`:
only while tender open + app not withdrawn/terminal → new `version` row,
previous → `REPLACED`, new hash chains from previous, audit `financial_bid_modified`.
**Withdraw** — existing `POST .../withdraw` extended: marks FinancialBid
`WITHDRAWN` (never deleted), audit; withdrawn apps excluded from ranking/RA.

**Financial opening (two-envelope)** — when TECHNICAL stage completes with
`TECHNICALLY_QUALIFIED`: audit `financial_envelope_opened` (who/when/count),
notify financial evaluator. `financial_visible(db, app, viewer_role)` in
`services/financial.py` = bids open AND (technical qualified OR pipeline past
technical) AND viewer ∈ `FINANCIAL_VIEW_ROLES ∪ {ADMIN, AUDITOR}`. Serializers
emit financial fields only then; otherwise 403 `FINANCIAL_ENVELOPE_SEALED` on
explicit fetch. Technical evaluators never see amounts (need-to-know preserved).

**RA lifecycle** — prerequisites validated at creation (deadline passed, bids
open, compliance completed per app, technical completed, ≥1 qualified, ≥2 RA
eligible, config valid): officer creates (DRAFT) → schedule (SCHEDULED,
participants computed by `auction_eligibility` policy, each with reason, audit
`RA_ELIGIBILITY_CALCULATED`) → start (READY→LIVE at `scheduled_start_at` or
explicit start, audit) → bids (engine, §6) → auto-extend near close → close
( scheduler tick or explicit) → `RESULT_PENDING` (ranking computed,
`RANKING_CALCULATED`, tie flagged — **never random**) → CFA approves/rejects
(result approval; award stays with the pipeline APPROVAL stage — **L1 is not
auto-awarded**) → COMPLETED.

**AuctionEngine bid path (deterministic, no LLM)** — single transaction:
lock `Auction` row (`with_for_update`) → verify LIVE/EXTENDED,
`server_now < effective_end_at`, participant eligible & not suspended →
direction rule: REVERSE: `0 < new < participant.last_bid AND new <= leading - min_increment`;
FORWARD: `new > leading AND new >= leading + min_increment` → idempotency key
(uniq) → `sequence_number` = last+1 → insert bid + hash → if
`effective_end - now <= trigger_window` and extensions remain: `effective_end_at += extension_seconds`,
`extension_count += 1`, status EXTENDED, audit `AUCTION_AUTO_EXTENDED`,
notify all → commit. Concurrency: row lock + server timestamp + monotonic
sequence; browser time never trusted.

**Forward auction** — same engine, `AuctionType.FORWARD`, inverted price rule;
activated only on `FORWARD_E_AUCTION` tenders; not seeded into the procurement
demo.

## 5. Security & governance

- RBAC per §2; bidder endpoints strip all competitor identity (anonymous ids
  only); officer monitor maps identities only for CFA/ADMIN/`FINANCIAL_VIEW_ROLES`.
- Audit events: `AUCTION_CREATED/UPDATED/SCHEDULED/STARTED/BID_PLACED/BID_REJECTED/AUCTION_AUTO_EXTENDED/PAUSED/RESUMED/CLOSED/RANKING_CALCULATED/RESULT_REVIEWED/RESULT_APPROVED/CANCELLED` + `financial_bid_sealed/modified/withdrawn` + `financial_envelope_opened` — all via `append_audit` (hash-chained, IP/request correlated).
- Pause/resume/cancel/approve: reauth token (existing `/auth/reauth`) + reason + audit.
- Rate limit on bid placement: slowapi `50/minute` per user+auction.
- Sentinel Watch integration: a CRITICAL drift (e.g. GST cancellation) on a
  RA-eligible participant flips the participant to `suspended` +
  `AUCTION_ELIGIBILITY_REVIEW_REQUIRED` notification — officer re-eligibility
  decision required (auction module never auto-disqualifies).
- Collusion: post-close deterministic `analyze_auction` (shared IP/phone/
  address across participants, synchronized-timing pairs, alternating
  sequences) → `CollusionAnalysis` row, indicator LOW/MODERATE/HIGH, wording
  "RISK SIGNAL / REVIEW RECOMMENDED" only; optional Gemini narrative (key-gated,
  advisory) reuses `ai_engine.gemini` with the same citation doctrine.
- Failure safety: all state in Postgres (survives restart); SSE reconnect
  fetches `GET /auctions/{id}` snapshot; duplicate requests idempotent.

## 6. Demo (seeded, deterministic)

New tender **TDR-2026-010** — CPCL / MoPNG, "Supply of Industrial Process
Equipment", Belagavi, `procurement_mode=E_BID_WITH_REVERSE_AUCTION`,
`financial_bid_required=true`, bid deadline **already passed** at seed time
(so the demo walks the authorized opening live):

| Bidder | Company | Initial quote | Compliance |
|---|---|---|---|
| A | Arjun Industrial Systems | ₹10,20,00,000 | PASS |
| B | Bharat Process Solutions | ₹9,85,00,000 | PASS |
| C | Chetan Equipments | ₹10,05,00,000 | **FAIL** (GST lapsed — mock source EXPIRED) |
| D | Delta Plant & Machinery | ₹9,70,00,000 | PASS |

Applications seeded at "AI Verification Not Run" (post-deadline, sealed). Demo
walk (`/home/user/e2e_auction_walk.py`): officer opens bids → AI verification ×4
→ compliance stage (C TERMINATED) → technical (A,B,D qualified) → financial
envelope opened → **Create Reverse Auction** (min decrement ₹1,00,000,
auto-extend 2 min/2 min, cap 3) → schedule → start → live bids
(D 9.60 Cr, A 9.55 Cr, B 9.49 Cr, D 9.42 Cr, A 9.38 Cr, B 9.31 Cr,
D 9.26 Cr) → close → **L1 D ₹9.26 Cr, L2 B ₹9.31 Cr, L3 A ₹9.38 Cr**, savings
₹44 lakh / 4.54%, C stays disqualified → FINANCIAL stage prefilled from RA
result → CFA approval → award. All existing tenders (t1–t9) untouched
(`financial_bid_required=false`).

## 7. Files

New: `models/financial.py`, `models/auction.py`, `services/financial.py`,
`services/auction.py`, `services/auction_eligibility.py`, `routers/auction.py`,
`tests/test_financial_bid.py`, `tests/test_auctions.py` (RA+FA+security),
FE `app/bidder/auctions/{page,[id]/page}.tsx`,
`app/officer/auctions/{page,[id]/page}.tsx`, `e2e_auction_walk.py`.

Extended (minimal diffs): `models/tender.py` (+2 cols), `models/__init__.py`,
`core/config.py` (3 flags), `core/bids.py` (financial_visible helper),
`pipeline.py` (eligible-only ranking + RA price source + tie label),
`routers/bidder.py` (financial bid in submission + modify/withdraw),
`routers/officer.py` (financial gate + envelope-opened event),
`routers/admin.py` (TenderIn +2 fields), `services/collusion.py`
(analyze_auction), `services/watch.py` (RA suspension hook), `notifications.py`
(event titles), `seed.py` (t10 + 4 bidders), `main.py` (router + closer job),
FE: `tenders/[id]` apply dialog, `bidder/page.tsx`, `officer/page.tsx`,
`applications/[id]`, `admin/tenders`, `pipeline/[id]`, `lib/types.ts`,
locales en/hi, `README.md`/`RUN_LOCAL.md`.

## 8. Test matrix (additions)

`test_financial_bid.py`: valid quote; zero/negative/missing rejected
(NON_RESPONSIVE_FINANCIAL_BID); Decimal precision + exact total check; sealed
at submit; financial fields 403 before technical qualification; role gate
(technical evaluator sees no amount); modify before deadline (versioning,
old version kept, hash chain); modify after deadline 403; withdrawal (status,
hash kept, excluded from ranking); hash integrity (tamper detect).

`test_auctions.py`: creation prerequisite failures (open tender / incomplete
compliance / <2 eligible); ineligible (C) blocked from room (403); cannot
start early; valid lower accepted; **higher rejected (INVALID_REVERSE_AUCTION_BID)**;
decrement enforced; zero/negative rejected; late bid 409 (server time);
duplicate idempotency key → same bid, no duplicate; concurrent bids serialized
(monotonic sequence, both valid ones ordered); auto-extension fires + caps;
close freezes; final prices = lowest valid; ranking eligible-only ascending;
tie → flagged, not random; identity protected in bidder feed; audit events
present + chain valid; officer dashboard savings math; result approval →
COMPLETED; cancel (reason + reauth + audit, history retained); pause/resume
(reauth); forward auction: higher accepted, lower/equal rejected, increment
enforced, highest ranked first; rate limit 429.

Regression: full existing matrix (91 pytest + smoke A/B/C/D + feature_check +
e2e_pipeline_walk + final_state_check) must stay green.
