# GeM-Sentinel — Code Audit & Stabilization Notes

Audit date: 2026-09-27

## What the project implements

GeM-Sentinel is a FastAPI + PostgreSQL + Next.js procurement-compliance platform with:

- Bidder registration/login, OTP verification, trusted devices, JWT access/refresh tokens.
- Bidder profile, consent controls, document vault, mock government-source synchronization and manual uploads.
- Tender discovery, eligibility pre-checks, applications, bid snapshots, clarifications, withdrawals and grievances.
- Sealed financial bids with hash integrity, re-quoting/idempotency and post-technical financial opening.
- 11-stage procurement pipeline: submission, opening, compliance, technical, financial, approval, contract, execution, delivery, payment, audit/closure.
- Tender-scoped workflow roles and role-scoped document visibility.
- Deterministic compliance rule engine. AI is advisory/explanatory only.
- Local RAG/citation guard plus optional Gemini narrative generation.
- Mock government portals and adapter-based source integration.
- Document authenticity heuristics and synthetic demo document rendering.
- Override accountability, re-authentication, admin co-sign and vigilance-referral workflow.
- Reverse auction and forward e-auction flows with eligibility, ranking and result approval.
- Sentinel Watch continuous compliance monitoring and drift alerts.
- Hash-chained audit logging with source-IP context.
- PDF compliance/certificate/report generation.
- Admin functions for users, officers, tenders, rules, blacklist, audit, overrides and source health.
- English/Hindi Next.js UI.

## High-impact issues fixed

### 1. Sealed-bid opening behavior (demo-preserved, production-guarded)

The project intentionally needs the `BID_CLOSURE` stage to open a still-sealed tender during the demo so the video can show both a sealed district and an opened district, then continue into AI verification. That behavior has therefore been preserved when `DEMO_MODE=true`: the stage moves the deadline to the current time, marks the tender force-opened, and writes an audit event.

**Production guard:** when `DEMO_MODE=false` (and production forces demo mode off), the same pre-deadline stage action returns controlled `409 BIDS_SEALED`; early opening then remains available only through the dedicated Admin force-open endpoint. This keeps the requested demo flow without reintroducing the behavior into production.

### 2. Pipeline action and outcome fields could disagree

The API accepted a valid `action` while allowing a different or arbitrary `finding`, `outcome`, or `decision`. That could create inconsistent state, bypass terminal outcomes, or produce misleading certificates.

**Fix:** compliance, technical, financial, approval and delivery values are now bound to the selected action and strictly validated against the stage's allowed actions.

### 3. Unknown pipeline stage could cause a server error

The route looked up the stage owner before validating the stage key.

**Fix:** unknown stages are rejected with `404` before any dictionary lookup.

### 4. Explicit workflow roles were not consistently tender-scoped

The codebase documents `(role, tender)` authority, but some officer read paths relied only on broad jurisdiction and certain finance/CFA roles could see all applications.

**Fix:** V2 officer roles now require an active `TenderAssignment` for the tender. Admin and Auditor retain their documented global scope. Legacy `L1_OFFICER`/`L2_OFFICER` accounts keep the jurisdiction fallback for migration compatibility.

This also fixes the Auditor pipeline bug: Auditor was allowed by the pipeline dependency but was subsequently blocked by the jurisdiction check.

### 5. Access tokens were put into query strings

The frontend appended `?gs_token=...` and the backend accepted it. Tokens in URLs can leak through browser history, reverse-proxy logs, analytics and referrer headers.

**Fix:** URL token transport was removed from both frontend and backend. Supported carriers are now Authorization, `X-GS-Token`, or the server-issued HttpOnly cookie.

### 6. Frontend overwrote the server's HttpOnly access cookie

Client JavaScript also created a `gs_access` cookie, undermining the purpose of the server-issued HttpOnly cookie.

**Fix:** the JavaScript cookie writer was removed. The server remains responsible for the HttpOnly cookie.

### 7. Refresh-token rotation was not actually persisted/enforced

`session_id` was rotated in memory after a commit in several login flows, so the new session id was not reliably stored. The refresh endpoint also did not enforce the token's `sid`, allowing refresh-token replay.

**Fix:** session rotation is persisted before responses are returned; refresh now requires the token `sid` to match the stored active session and persists a new sid after each refresh. Logout clears the active session id, revoking refresh-token reuse.

### 8. Production cookie was missing the `Secure` flag

**Fix:** `gs_access` is now `Secure` whenever `APP_ENV=production`.

### 9. `APP_ENV=production` did not enforce production safety

The comments said production disables demo behavior, but there was no effective enforcement and default development cryptographic secrets could still be used.

**Fix:** production startup now:

- forces `DEMO_MODE=false`;
- refuses to start if JWT, field-encryption or financial-envelope encryption secrets are empty/default development values.

### 10. Demo-only endpoints remained available in production

Synthetic document studio views and mock drift manipulation were still callable.

**Fix:** they now return `404` when `DEMO_MODE=false`.

### 11. Real provider secrets were present in `backend/.env`

The uploaded project contained Twilio and Gemini credentials.

**Fix:** the packaged `.env` has been sanitized and a `.gitignore` was added to prevent local secret files, virtual environments, Node modules, runtime storage and generated builds from being committed.

**Important:** the previously exposed Twilio and Gemini credentials must be revoked/rotated in their provider consoles. Removing them from this ZIP does not revoke the old credentials.

## Validation performed

- Python syntax/bytecode compilation completed successfully for `backend/app`, `backend/tests` and `mock-govt-portals` after the changes.
- Verified that the removed `gs_token` transport no longer exists in backend/frontend source.
- Verified that the exposed Twilio/Gemini credential strings no longer exist in the audited project copy.

Full integration tests could not be executed in the audit sandbox because it does not have the project's pinned Python packages, Node modules or a running PostgreSQL instance, and outbound package installation is unavailable there.

## Important production hardening still recommended

These are not changed automatically because each affects deployment architecture or requires product decisions:

1. **Use Alembic for production migrations.** `ensure_schema()` is useful for a prototype but adding columns automatically is not a substitute for versioned migrations, constraints, indexes, data transforms and rollback planning.
2. **Move access/refresh tokens fully out of `localStorage`** for a high-assurance deployment. The current frontend still stores tokens there for compatibility, making them reachable by any successful XSS. Prefer HttpOnly cookies plus CSRF protection.
3. **Harden the generic `/files/*` authorization model.** File names are randomized and path traversal is guarded, but non-document kinds still use coarse role checks. A production design should store owner/application/tender metadata for every file and authorize by that metadata on every download.
4. **Use a single scheduler/worker leader.** APScheduler starts inside the API process; multiple Uvicorn/Gunicorn workers can run duplicate Watch/deadline/auction jobs. Move scheduled work to a dedicated worker or add a distributed leader/advisory lock.
5. **Treat the audit chain as tamper-evident, not physically immutable.** A DB administrator can still modify rows. For stronger guarantees use restrictive DB roles, append-only permissions, external anchoring/WORM storage, or signed checkpoints.
6. **Tune DB pools per worker.** `pool_size=20`, `max_overflow=40` is per process. Several API workers can exceed PostgreSQL's connection budget quickly.
7. **Do not treat visual document-authenticity heuristics as authoritative.** Keep the existing manual-review doctrine and source verification as the controlling evidence.
8. **Separate demo seed credentials from deployable environments.** Demo usernames/passwords in seed/README are intentional for demos, but production deployment should never run the seed or expose those accounts.

## Local PostgreSQL expected by the project

The sanitized local `.env` expects:

- Host: `127.0.0.1`
- Port: `5432`
- Database: `gem_sentinel`
- Role: `gemsentinel`
- Password: `gemsentinel123`

If your pgAdmin role uses a different password, change only the password portion of `backend/.env` locally. Do not commit the updated `.env`.
