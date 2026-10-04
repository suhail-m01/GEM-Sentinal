# GeM-Sentinel — Agent Errors & Dead Ends Log

Problems hit while building this project, their root causes, and the fixes that
worked. Kept so the same dead ends are not retried.

## Backend
1. **`credentials.token` AttributeError on every protected route (500s)**
   - Cause: FastAPI 0.115's `HTTPAuthorizationCredentials` exposes `.credentials`,
     not `.token`; also a stale uvicorn (started **without** `--reload`) kept serving
     old code while I edited files, making the 500s chase endlessly.
   - Fix: `getattr` fallback on the dependency (`credentials` then `token`), and
     always run uvicorn with `--reload` during development.
2. **500 on `/officer/*` — `ModuleNotFoundError` in rules_engine**
   - Cause: import path `from .portal import ...`; the module lives under
     `services/portal` (adapter package `portal_client`).
   - Fix: `from .services.portal import fetch_source` (and use
     `portal_client.get_adapter` for the adapter lookup).
3. **500 on limit-exceeded (slowapi)**
   - Cause: slowapi needs `app.state.limiter` set; with `None` it raised 500 instead
     of a clean 429.
   - Fix: `app.state.limiter = rate_limiter` in `main.py`; `RATE_LIMIT_LOGIN`
     30/minute so test suites don't trip it.
4. **422 on override with JSON body + file upload**
   - Cause: a FastAPI endpoint declaring both a JSON body model and a `File()`
     parameter forces multipart for the whole route, breaking JSON clients.
   - Fix: split into `POST /override` (sync JSON, `evidence: list[dict]` of
     pre-uploaded file metadata) + `POST /override-evidence-upload` (multipart).
     L1 and L2 endpoints mirror this.
5. **500 on `/admin/overrides` and `/admin/vigilance-referrals`**
   - Cause: serializers accessed `record.application` without a relationship.
   - Fix: `relationship("Application", lazy="joined")` on `OverrideRecord` and
     `VigilanceReferral`.
6. **L2 approve stayed 409 forever after admin co-sign**
   - Cause: the override gate re-evaluated blocking findings on every decision, even
     after an override had been accepted and co-signed.
   - Fix: `_has_accepted_override()` in `services/decisions.py` — if the latest
     non-rejected override (cosigned where required) exists, the gate passes exactly
     for that override's outcome.
7. **500 on `/auth/register` — `IntegrityError: user_id NULL`**
   - Cause: SQLAlchemy Python-side `default=new_id` is only applied at INSERT; a
     dependent row (BidderProfile) flushed in the same transaction saw `None`.
   - Fix: assign `id = new_id()` explicitly before `add()` in the register endpoint.
8. **404 instead of 405 on DELETE without a handler**
   - Cause/impact: FastAPI returns 404 for a method it doesn't route on that path.
     A pytest assertion expected 405 (blacklist hard-delete protection).
   - Fix: assertion now accepts (404, 405); the API documents "no delete endpoint".
9. **`AmbiguousForeignKeysError` on Application relationships**
   - Cause: multiple FKs from Application to the same table.
   - Fix: explicit `foreign_keys=[...]` on the relationships.
10. **numpy/OpenCV build issues**
    - Fix: pin `numpy>=2.0,<2.3` (opencv-python-headless 4.12 requires it), and use
      the headless build (no GUI deps in the sandbox).
11. **Postgres availability in sandbox (no Docker)**
    - Fix: native install via `pg_ctlcluster 17`; dev connection string in
      `.env.example`.
12. **pytest reseed race with --reload server**
    - Cause: seeding deletes/recreates rows while uvicorn reloads; immediate requests
      hit a half-reloaded server.
    - Fix: conftest `seeded` fixture waits ~3 s after `python3 -m app.seed`.
13. **Combined `pytest tests/` hang #1 — orphaned test+seed processes (killing a
    bash-timeout left children alive)**
    - Cause: a `timeout`-killed pytest left its `app.seed` subprocess (and other
      children) running; the seed's reseed `DROP TABLE` sat in `active` waiting on
      a lock, while a `idle in transaction` session from the killed run held it.
      Every later reseed then blocked forever. Module-by-module runs masked it.
    - Recovery: `ps aux | grep -E "pytest|app.seed"` → `kill -9` all PIDs (kill the
      whole process tree, never just the parent) →
      `SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE
      datname='gem_sentinel' AND pid<>pg_backend_pid() AND state<>'active'` →
      re-seed.
    - Lesson: when timing out a test run, kill the entire process tree.
14. **Combined `pytest tests/` hang #2 — unclosed `SessionLocal()` in a test leaks
    a lock into the next module's reseed**
    - Cause: a test in `test_v2_enhancements.py` created an inline
      `SessionLocal()` (e.g. to look up a tender id) and never closed it. CPython
      GC is lazy, so the session — and its open transaction (autobegin) — held a
      `SELECT` lock on `tenders`/`applications` for minutes. When the next test
      module's reseed ran `drop_all()` in a new process, that `DROP TABLE` blocked
      on the leaked lock. Each module passed alone (no subsequent reseed), which
      hid the leak until the combined run.
    - Fix: every test that opens `SessionLocal()` must use
      `db = SessionLocal(); try: ...; finally: db.close()`. Grep for bare
      `SessionLocal()` in `tests/` when adding tests.
    - Lesson: tests that touch the DB directly must close their sessions; an
      unclosed session's lock outlives the test and blocks cross-module reseeds.
15. **Mock portal `set_fault`/`clear_fault` 500 — `UnboundLocalError` on
    `FAULTS` (latent, first hit by `test_v2_cache.py`)**
    - Cause: `set_fault`'s else-branch reassigns `FAULTS = {...}`, which makes
      `FAULTS` a function-local for the *entire* function; the if-branch's
      `FAULTS[key] = mode` then read the unbound local. `clear_fault` had the
      same defect (its dict-comprehension RHS reads `FAULTS.items()` before
      the assignment). Both endpoints had never been exercised (scenario D's
      fault is seeded at module load), so the bug survived every prior run.
    - Fix: `global FAULTS` in both handlers (mock-govt-portals/main.py).
    - Lesson: a reassignment anywhere in a function poisons all earlier reads
      of that name; when fixing "assign to dict then maybe rebuild dict",
      either mutate in place or declare `global`.

## Frontend
1. **Page-route / API-route clashes** (e.g. `/bidder/tenders` is both a Next.js page
   and an API endpoint; same for `/admin/users`, `/admin/analytics`,
   `/bidder/consent`, `/bidder/profile`, `/officer/applications`, `/admin/tenders`,
   `/admin/overrides`, `/admin/blacklist`)
   - Cause: Next.js resolves page routes **before** `afterFiles` rewrites, so
     `fetch('/bidder/tenders')` would have returned HTML (or 405 on POST).
   - Fix: all API calls are prefixed `/api/*` by `lib/api.ts` (`apiPath`), and a
     single rewrite `/api/:path* → $API_URL/:path*` proxies to FastAPI. Download
     links (`/api/officer/.../report/download`, `/api/admin/vigilance-referrals/.../export`)
     use the same prefix.
2. **404 on `/` and `/login` with next-intl middleware**
   - Cause: `createMiddleware` with `localePrefix: "as-needed"` rewrites URLs to
     `/en/...` expecting an `app/[locale]` segment; this app uses a flat structure
     with a cookie-driven locale.
   - Fix: **removed the middleware entirely**; locale is read from the `NEXT_LOCALE`
     cookie (fallback `accept-language`, default `en`) in `i18n.ts` and the root
     layout (`setRequestLocale`). The header language switch sets cookie +
     localStorage and reloads.
3. **"Couldn't find next-intl config file" at build**
   - Cause: next-intl v3 requires the plugin to be registered in `next.config.mjs`.
   - Fix: `createNextIntlPlugin("./i18n.ts")` wrapping the config.
4. **"Can't resolve '../locales'" in i18n.ts**
   - Cause: wrong relative path (`i18n.ts` is at repo-frontend root).
   - Fix: `import("./locales/${locale}.json")`.
5. **TS build error: `ref` prop on `<Input>`**
   - Cause: passed a DOM ref to a non-forwarding component.
   - Fix: removed the unused ref (file input uses `onChange` only).
6. **Override modal sent the raw password as `reauth_token` → 401**
   - Cause: the backend expects a short-lived JWT with `purpose="reauth"` issued by
     `POST /auth/reauth`, not the plaintext password.
   - Fix: modal now calls `/auth/reauth {password}` first, then submits the override
     with the returned `reauth_token` (10-min TTL).
7. **422 on `POST /bidder/applications`**
   - Cause: `tender_id` is a **query parameter** on the submit endpoint, not body.
   - Fix: frontend submits to `/bidder/applications?tender_id={id}`.
8. **Garbled character in `locales/hi.json`** (`participation` key)
   - Fix: rewrote the string; both locale files validated as JSON.

## Process / environment notes
- Keep uvicorn running with `--reload`; a stale server silently serves old code.
- Reseed (`python3 -m app.seed`) before any demo — pytest/smoke runs mutate state
  (extra bidder `newblack@bidder.in`, vikram's blacklist entry → APPEAL_PENDING,
  APP-2026-000003 Approved, etc.).
- The mock portal must be up (:8001) or every source rule degrades to
  `UNABLE_TO_VERIFY` (by design — never PASS).

---

## 2026-09-13 — Login "stuck on login page" (proxy strips Authorization + stale cached JS)
**Symptom:** user repeatedly logged in via the preview; each attempt: login 200 → `/auth/me` 401
→ refresh → `/auth/me` 401 → bounced to /login.
**Diagnosis (from `[req]` access log added to frontend middleware):**
1. The preview proxy strips the `Authorization` header on browser→frontend requests
   (verified: same login works with header via direct curl to :8000, fails through :3000).
2. The browser was running an OLD cached JS bundle (built before the `gs_access` cookie
   fix) — so the client-side cookie fallback never existed in the code the user actually had.
   `Cache-Control: no-store` was not previously set on HTML routes.
**Fix (server-side so it works with ANY client version):**
1. `backend/app/core/deps.py` — `get_current_user` now accepts the `gs_access` cookie as a
   fallback when the Authorization header is absent.
2. `backend/app/routers/auth.py` — login / refresh / verify-otp now also
   `Set-Cookie: gs_access=<access JWT>` (httpOnly, SameSite=Lax, path=/, max 1800s);
   logout deletes it. Browser gets the session cookie automatically on login — no JS needed.
3. `frontend/middleware.ts` — restored (belt-and-braces) Authorization from the cookie for
   /api/*, PLUS per-request `[req]` logging so user journeys are traceable in the process log.
4. `frontend/next.config.mjs` — `Cache-Control: no-store` on all HTML routes so refresh always
   serves the current build.
5. `frontend/lib/api.ts` — also mirrors the token into the cookie (for fresh browsers).
**Verification:** through the :3000 proxy, simulating a header-stripping browser with NO
custom JS: login → cookie set → /auth/me 200, admin dashboard 200, bidder dashboard 200.
Full test suite: 23/23 (one test needed a cookie-free client since the shared session
client legitimately carries the gs_access cookie now).

---

## 2026-09-13 — Sandbox environment reset (not an app bug)
Fresh container: node_modules, .next, pip packages, postgres + data, all processes wiped.
Recovery recipe: (1) sudo apt-get install postgresql (v17), pg_ctlcluster 17 main start,
create role gemsentinel/gemsentinel123 + db gem_sentinel; (2) pip3 install -r requirements.txt;
(3) npm install; (4) start mock-govt-portals/main.py :8001, uvicorn app.main:app :8000 --reload,
next start :3000; (5) python3 -m app.seed. All 23 critical tests re-verified after recovery.
Note: first `app.main` import right after cluster start can transiently fail with
"password authentication failed" — re-run after the cluster settles.

---

## 2026-09-15 — V2.1 Slice 1 (identity, roles & IP-aware audit)

### 403s on the legacy L1/L2 console after the role rename (migration break)
- Symptom: after seeding users with the new V2.1 role names, the legacy officer
  queue came back empty and the L2 endpoints returned 403 for
  PROCUREMENT_OFFICER / FINANCE_EVALUATOR / CFA / BID_OPENING_OFFICER /
  TECHNICAL_EVALUATOR.
- Root cause: `officer.py`, `l2.py`, `routing.py` and `get_current_l2` compared
  `role == "L1_OFFICER"` / `role == "L2_OFFICER"` directly. V1 only ever had
  those two officer roles, so the equality checks "worked" — but the V2.1
  17-role model renames them.
- Fix: introduced **behavior-role groups** in `core/deps.py`
  (`L1_BEHAVIOR_ROLES`, `L2_BEHAVIOR_ROLES`) and switched every legacy check +
  the 4 routing `role.in_(...)` filters to the groups. Legacy role names are
  kept *inside* the tuples so old DB rows still match. The legacy flow is a
  migration bridge and is superseded by tender-scoped workspaces in Slice 7.
- Frontend mirror: `Guard` now takes `Role | Role[]`; `officer/layout.tsx` and
  `l2/layout.tsx` guard by the same behavior groups.
- Do NOT retry: reintroducing a single `role == "L1_OFFICER"` check anywhere —
  it silently breaks every V2.1 buyer role.

### Override "challenge does not match current findings" (test-ordering, not a product bug)
- Symptom: `pytest tests/test_critical.py::test_override_interception_and_full_chain`
  fails standalone with 400 "challenge does not match current findings", but
  passes in the full module.
- Root cause: `extract_blocking_findings` runs a live evaluation when no stored
  report exists, stamping `generated_at=utcnow()`. The intercept (first call)
  and the override (second call) therefore compute a bound hash over slightly
  different `generated_at` values and the challenge no longer matches.
- Resolution: real flow always verifies before deciding, so a report exists and
  the hash is stable. The test only relies on `test_blacklist_submitted_app_is_...`
  persisting a report first (ordering dependency). Kept as-is; do not "fix" by
  weakening the challenge binding.

### Bulk `sed` left call sites passing `request=` to old signatures (500 only in process log)
- Symptom: after mass-editing decision/override call sites to pass
  `request=request`, some routes 500'd with `TypeError` — visible only in the
  stopped process log, not the test run.
- Fix: grep every callee signature AND every call site after any bulk edit.
  `process_decision(..., request=None)` / `process_override(..., request=None)`
  are the canonical signatures; `process_override` derives the secure IP/UA
  from `request` (via `ipcontext.resolve_client_ip`) when given.

### slowapi 30/min per-IP broke the integration suite
- Fix: `backend/.env` `RATE_LIMIT_LOGIN=300/minute` for dev; `30/minute` stays
  in `.env.example` for prod. `.env` changes need a server restart
  (watchfiles ignores it).

### Spec said 17 roles but the backend only had 16 (missing CONTRACT_OFFICER)
- Symptom: at the Slice-1 checkpoint, the role model looked complete, but the
  contract phase only had CONTRACT_MANAGER / INSPECTION_OFFICER /
  PAYMENT_OFFICER — the distinct **Contract Execution Officer** role the spec
  lists was never modeled.
- Fix: added `CONTRACT_OFFICER` as the 17th role everywhere it is enumerated
  (`core/deps.py` ROLE_* + `BUYER_SIDE_ROLES`, `permissions.CONTRACT_ROLES`,
  `models/workflow.WORKFLOW_ROLES`, frontend `types.ts` Role union,
  `shell.tsx` ROLE_META, login demo card, admin users role list) and seeded a
  user + tender assignment.
- Lesson: after any role/enum expansion, grep EVERY enumeration site
  (backend constants, permission groups, workflow roles, frontend Role union,
  ROLE_META, role dropdowns, seed) and reconcile the count against the spec —
  a single missed list silently desyncs UI from backend.
