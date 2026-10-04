# GeM-Sentinel vs BidVerify-AI — Architecture Comparison

Source analyzed: https://github.com/AlamNawazSha/BidVerify-AI (single commit,
2026-09-14, ~7,000 LOC Python + FastAPI gateway + k8s manifests).
Analyzed from cloned source (claims verified against code, not just README).
Date: 2026-09-15.

## 1. What BidVerify-AI actually is

A **single-tenant verification engine** (library + FastAPI gateway), not a
platform: `verify(document, bidder_record) → result`. No user accounts, no
auth, no roles, no frontend, no workflow. It processes one bid dossier against
a static set of hard-coded document rules and emits a JSON result + optional
"officer report". Enterprise infrastructure (Redis, Celery, Prometheus, k8s)
wraps the engine for scale-out.

## 2. Capability matrix (verified against source)

| Capability | BidVerify-AI | GeM-Sentinel (us) |
|---|---|---|
| Deterministic rule engine decides; AI only explains | ✅ hard-coded rules in `rule_engine.py` (GST/PAN/Udyam checks) | ✅ **per-tender versioned rule catalog** (Rule Builder UI, weights, failure actions, corrigendum version bumps) |
| Result semantics | ELIGIBLE / INELIGIBLE / REQUIRES_MANUAL_REVIEW | PASS / FAIL / **UNABLE_TO_VERIFY** / NOT_APPLICABLE (source failure never PASS) |
| Roles & RBAC | ❌ none (stateless API) | ✅ 17 roles, JWT + re-auth, per-tender `TenderAssignment`, DB-level enforcement |
| Portal / buyer UX | ❌ none | ✅ Next.js dual portal, en/hi, polished PDF reports |
| Government source integration | ✅ simulated India Post/GSTN adapter | ✅ mock-govt-portals service, 11+ sources, adapter pattern, consent-gated fetches |
| **Cross-document entity graph** (PAN embedded in GSTIN chars 2–12, GSTIN state-code vs declared state, name consistency across 15+ docs) | ✅ `entity_graph.py` (strong) | ❌ per-document portal comparison only — **GAP** |
| **Inter-bidder cartel / proxy-bid detection** (shared phone, email domain, director PAN, bank account, address, submission IP → weighted collusion score + networkx clusters) | ✅ `entity_graph.analyze_tender_cartels` (strong, headline feature) | ❌ blacklist only, per-bidder — **GAP (biggest)** |
| Document classification | ✅ keyword-based 7-class | ✅ type inferred per vault document + tender doc registry |
| OCR / layout extraction | ✅ pypdf + optional tesseract, layout tokens [x0,y0,x1,y1] | ✅ OCR-style field extraction (provider-neutral, local fallback) |
| Document authenticity / tamper detection | ❌ (security guard = magic bytes, PDF exploit scan, PII mask — upload hygiene) | ✅ OpenCV seal/signature, similarity to trusted references, PDF metadata, tampering indicators |
| **Upload-time security guard** (magic-byte validation, PDF exploit scan, path sanitization, PII sanitize helper) | ✅ `security/doc_guard.py` | ⚠️ SHA-256 immutability + authenticity heuristics, but no upload-time exploit scan / PII-masking helper — **partial GAP** |
| Risk model | ⚠️ "calibrated ML" = hand-coded logistic weights (deterministic, no training) | ✅ weighted compliance score → LOW/MEDIUM/HIGH/CRITICAL from rule outcomes |
| RAG legal citations | ⚠️ JSON KB + **keyword/token-overlap** search (no embeddings) | ⚠️ Gemini explainer w/ deterministic fallback; **no citation whitelist** — **partial GAP** |
| Anti-hallucination guardrails | ✅ Pydantic schema + rule-citation whitelist (strips unknown citations, forces verbatim clauses) | ⚠️ "AI explains only" enforced at workflow level; no LLM-output citation whitelist — **partial GAP** |
| Portal resiliency | ✅ **circuit breaker (CLOSED/OPEN/HALF_OPEN) + backoff w/ jitter + 24h SWR cache** | ⚠️ source-health tracking + UNABLE_TO_VERIFY, but **no circuit-breaker state machine** — **GAP** |
| Verification result caching | ✅ 3-tier (in-memory LRU → Redis → SQLite) "verify once, apply many" | ✅ **Ported (2-tier): in-process LRU → Postgres, TTL + audited admin clear** — only deterministic states cached; failures never cached |
| Async batch queue | ✅ Celery or ThreadPool, queue-depth metrics | ❌ synchronous request-scoped — acceptable at demo scale |
| Observability / deployment | ✅ Prometheus /metrics + Grafana dashboard + k8s + KEDA/HPA + PgBouncer + docker-compose | ⚠️ docker-compose only, no metrics — **GAP (P3)** |
| Audit trail | ✅ SHA-256 custody hashes, 3 audit tables — **no hash chain** | ✅ **hash-chained**, tamper-evident, IP-aware (masked, VIEW_FULL_IP + re-auth reveal, itself audited), chain verify endpoint |
| Override / accountability | ❌ none | ✅ full Override Accountability Engine (re-auth, justification, evidence, admin co-sign, vigilance referral) |
| Blacklist handling | ⚠️ static mock register in code | ✅ managed reference DB (no hard delete, status transitions, order refs, validity windows) |
| Corrigenda / rule versioning | ❌ | ✅ corrigendum publishing bumps rules version, stale-report re-verification |
| Lifecycle coverage | eligibility → report (stops) | full: application → verification → decisions → committee → CFA → award → contract → inspection → PRC/CRAC → payment → closure (slices in progress) |
| Testing | ⚠️ 4 self-running demo "suites" (scripts, not pytest TDD); README benchmarks are self-reported | ✅ 35 integration tests (pytest, deterministic seed) |
| Data integrity (prototype truthfulness) | ⚠️ **ships "real" TCS/Infosys/BEL certificates** as sample data | ✅ synthetic-only, SYNTHETIC-marked, deterministic provisioning (spec §17) |

**Bottom line:** different shapes. They are an *optimised engine* (fast,
resilient, cache-heavy, scale-out infra, two genuinely novel detection
features). We are a *complete product* (identity, workflow, accountability,
audit, lifecycle, UX) around a comparable deterministic core. Of their
headlines, we are missing **cartel detection**, **cross-document entity
consistency**, and **portal resiliency patterns** — all three are additive and
fit our architecture cleanly.

## 3. Recommended additions (prioritized)

### P0 — Collusion / cartel indicator service (tender-scoped)
Their strongest differentiator, and we already hold most of the input data:
- New service `services/collusion.py`: build a per-tender graph over bidders'
  contact phone, email + domain (public domains excluded), director PAN
  (MCA-sourced), bank account (bank guarantee docs), registered address
  (pincode+tokens), and **normalized submission IP from our hash-chained
  audit log** (they use IP too — our IP-aware audit makes this first-class).
- Weighted indicators (bank 0.95 / director-PAN 0.90 / phone 0.75 / email 0.70
  / address 0.50 / IP 0.40), clusters via `networkx` (pure-Python fallback).
- **Deterministic, advisory only**: produces `collusion_indicators` findings
  (PASS / INDICATORS_FOUND with evidence per pair), surfaced in officer
  review + committee report. Never auto-disqualifies — humans decide (spec §5).
- UI: "Collusion indicators" panel on tender review + committee report section.
- Fits Slice 5 (opening/clarification) or its own sub-slice; ~1 service +
  ~1 router + ~1 UI panel + tests.

### P1 — Cross-document identity rules in the rule engine
Cheap deterministic condition types (their `entity_graph.validate_intra_bidder`):
- `gst_pan_embedded_match`: PAN = GSTIN chars 2–12 must equal declared PAN
  (CRITICAL on mismatch — catches forged GSTINs).
- `gst_state_match`: GSTIN state code vs declared/registered state.
- `cross_doc_name_consistency`: legal name similarity across all uploaded docs
  vs profile (threshold 0.85 token-sort).
- Add to the Rule Builder catalog (new `condition_type` values) so admins can
  weight/enforce per tender; UNABLE_TO_VERIFY if fields missing.
  ~1 day including tests.

### P1 — Portal resiliency: circuit breaker + jittered backoff + SWR
- `services/portal/circuit_breaker.py`: CLOSED/OPEN/HALF_OPEN per source,
  fail-fast + canary probe, thread-safe (their implementation is a good
  reference — 133 LOC).
- Exponential backoff with full jitter honoring Retry-After on 429.
- SWR: serve stale portal snapshot (≤24h) while refreshing in background —
  turns "portal down" from a hard failure into a degraded-but-verified path
  (still never PASS on stale-only data: mark UNABLE_TO_VERIFY with
  `stale_snapshot` evidence).
- Extends our existing source-health tracking; ~1 day + tests.

### P2 — Upload-time document security guard
- Magic-byte validation (PDF/ PNG/ JPG), size/extension enforcement.
- PDF exploit-indicator scan (their heuristic list: JS, launch actions,
  excessive objects) → flag `SUSPICIOUS_FILE` without rejecting.
- PII sanitize helper for audit payloads (mask PAN/GSTIN/AADHAAR in logs).
- Plugs into our document upload path + vault; complements authenticity
  heuristics; ~1 day.

### P2 — LLM citation whitelist (anti-hallucination guardrail)
- Constraint on the explainer: citations may only reference rule keys that
  exist in the *active rules version* of the tender; unknown citations are
  stripped and the strip is recorded.
- Optional small local legal-KB (GFR 144, GeM guidelines, CVC, DPDP) as
  deterministic fallback text when cloud AI is disabled (we already fall back
  to templates; this makes the fallback cite-able).
  ~1 day.

### P2 — Verification result cache (TTL + invalidation) — ✅ IMPLEMENTED
- Ported as a 2-tier cache (`backend/app/services/verify_cache.py` +
  `verification_cache` table): L1 in-process LRU → L2 Postgres (Redis is an
  optional extension point; the demo stack has no Redis dependency).
- Cache key = SHA-256(source, identifier, extra) — the exact portal query;
  TTL `VERIFY_CACHE_TTL_SECONDS` (default 900 s).
- Doctrine preserved: only deterministic states are cached
  (VALID/EXPIRED/MISMATCH/NOT_FOUND/BLACKLIST). UNAVAILABLE/ERROR are **never**
  cached, so an unavailable source keeps yielding UNABLE_TO_VERIFY.
- Cached results carry `_cached`/`_cached_at`/`_cache_age_seconds` markers in
  the source data, so reports/audit show when the source was actually
  retrieved. Mock portal gained `GET/POST /api/admin/stats` call counters
  proving repeat verifications skip the portal (delta = 0).
- Admin: `GET /admin/verify-cache` (stats), `POST /admin/verify-cache/clear`
  (audited `verify_cache_cleared`). No implicit invalidation hooks needed —
  portal responses are a pure function of the query over immutable simulated
  source data; TTL + audited clear cover operator flows.
- Complements the vault (vault = reusable *documents*, cache = reusable
  *results*).

### P3 — Observability & deployment parity
- `/metrics` Prometheus endpoint (verification latency, source failure rate,
  cache hit ratio, queue/override counts) + `monitoring/grafana_dashboard.json`.
- Optional `k8s/` manifests (deployments, HPA) alongside existing
  docker-compose. PgBouncer service in compose for prod profile.
  ~half day each; low urgency for the demo.

### P3 — Batch verification worker (defer)
- Celery/queue with depth metrics only matters at tender-closing peak volume;
  our synchronous flow is adequate for the demo. If added, expose queue depth
  in /metrics. Track as backlog, not a slice.

### Not recommended (differences to keep as-is)
- **Do NOT adopt real-company sample data** (they ship TCS/Infosys/BEL
  certificates). Our spec §17 mandates synthetic, SYNTHETIC-marked data only.
- **Do NOT chase their benchmark numbers** (self-reported, single commit, no
  third-party repro). If we want perf evidence, add a small `hey`/locust
  load script to VALIDATION.md instead.
- Keep our result semantics (UNABLE_TO_VERIFY is mandatory per spec; they
  don't have it) and our hash-chained audit (theirs is not chained).

## 4. Effort summary

| Item | Priority | Est. effort |
|---|---|---|
| Collusion indicator service + UI | P0 | 2–3 days |
| Cross-document identity rules | P1 | ~1 day |
| Circuit breaker + backoff + SWR | P1 | ~1 day |
| Upload security guard | P2 | ~1 day |
| LLM citation whitelist | P2 | ~1 day | ✅ done |
| Result cache | P2 | ~1 day | ✅ done (2-tier LRU→Postgres) |
| Metrics + Grafana + k8s | P3 | ~1–2 days | deferred (prototype) |
| Batch worker | P3 (defer) | 2+ days | deferred (would change core sync workflow) |

Suggested placement: P1 items fold into **Slice 5/6** (verification/technical
evaluation) work; P0 collusion fits a new **Slice 5.5** (post-opening,
pre-committee) since it needs the full bidder set for a tender; P2 items are
**Slice 10 (hardening)** candidates.
