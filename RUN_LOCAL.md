# GeM-Sentinel — Run Locally WITHOUT Docker (line by line)

Assumes: **PostgreSQL already installed on your laptop**, Python 3.11+ and Node.js 18+.
Ports used: **5432** (Postgres), **8001** (mock govt portals), **8000** (backend API), **3000** (website).

> Open **three terminals** (or tabs). We label them Terminal A, B, C as we go.

---

## Step 1 — Unzip the project

```bash
unzip gem-sentinel-latest.zip
cd gem-sentinel
```
(Windows: right-click the zip → *Extract All*. Then open a terminal in the `gem-sentinel` folder.)

---

## Step 2 — Make sure PostgreSQL is running

- **Windows:** `services.msc` → "PostgreSQL" → Start. Or: `pg_ctlcluster <version> main start` in an admin PowerShell.
- **macOS (Homebrew):** `brew services start postgresql@16` (or the major version you installed).
- **Linux:** `sudo systemctl start postgresql`

Verify it answers:
```bash
psql --version
```

---

## Step 3 — Create the database role and database

The backend expects exactly this (it's the built-in default, no env file needed):

```
user:     gemsentinel
password: gemsentinel123
database: gem_sentinel
host:     127.0.0.1
port:     5432
```

**Option 1 — psql (recommended).** Log in as the postgres system user and run ONE line:

- **Linux:**
```bash
sudo -u postgres psql -c "CREATE ROLE gemsentinel LOGIN PASSWORD 'gemsentinel123' SUPERUSER"
sudo -u postgres createdb -O gemsentinel gem_sentinel
```
- **macOS (Homebrew postgres, `psql` on PATH):**
```bash
sudo -u postgres psql -c "CREATE ROLE gemsentinel LOGIN PASSWORD 'gemsentinel123' SUPERUSER"
sudo -u postgres createdb -O gemsentinel gem_sentinel
```
- **Windows (run in a normal PowerShell — use your own install's psql, e.g. `C:\Program Files\PostgreSQL\16\bin\psql`):**
```bat
psql -U postgres -h localhost -c "CREATE ROLE gemsentinel LOGIN PASSWORD 'gemsentinel123' SUPERUSER"
psql -U postgres -h localhost -c "CREATE DATABASE gem_sentinel OWNER gemsentinel"
```
(It will prompt for the `postgres` account password you set at install time.)

> SUPERUSER is only needed for this prototype's simplicity. If your policy forbids it,
> a normal role **owned** by `gem_sentinel` also works — nothing here uses superuser SQL.

**Option 2 — pgAdmin / DBeaver (GUI).** Connect as `postgres`, then run this SQL in the query tool:
```sql
CREATE ROLE gemsentinel LOGIN PASSWORD 'gemsentinel123' SUPERUSER;
CREATE DATABASE gem_sentinel OWNER gemsentinel;
```

**Verify** (from a normal user terminal):
```bash
psql "postgresql://gemsentinel:gemsentinel123@127.0.0.1:5432/gem_sentinel" -c "SELECT 1"
```
Expected: one row with `1`. If this fails, check the Postgres `pg_hba.conf` allows password auth
for `127.0.0.1` (it does by default on most installs).

---

## Step 4 — Terminal A: backend virtualenv + dependencies

```bash
cd gem-sentinel/backend
python3 -m venv .venv
```
(Windows: `py -3 -m venv .venv` or `python -m venv .venv`)

Activate it:
```bash
# Linux / macOS
source .venv/bin/activate
# Windows PowerShell
.venv\Scripts\Activate.ps1
# Windows CMD
.venv\Scripts\activate.bat
```
(Your prompt now shows `(.venv)`.)

Install dependencies:
```bash
pip install -r requirements.txt
```
> If `psycopg2-binary` or `opencv-python-headless` fails to install (rare), you need a
> Python version the wheels support — 3.11/3.12/3.13 all work.

---

## Step 5 — Terminal A (still in backend, venv active): seed the demo data

```bash
python -m app.seed
```
Expected tail output: `Seed complete.` — it creates the demo users, documents,
tenders (deadlines = today 18:00 IST), blacklist, and scenarios.

> If you ever need a fresh database later:
> `DROP DATABASE gem_sentinel;` then recreate it (Step 3) and re-run the seed.

---

## Step 6 — Terminal B: start the mock government portals (port 8001)

Open a **new terminal**, go to the repo root, and use the backend's venv:

```bash
cd gem-sentinel
# Linux / macOS
backend/.venv/bin/python mock-govt-portals/main.py
# Windows
backend\.venv\Scripts\python.exe mock-govt-portals\main.py
```
Expected: `Uvicorn running on http://0.0.0.0:8001`

Quick check (any terminal):
```bash
curl http://127.0.0.1:8001/health
```
Expected: `{"service":"mock-govt-portals","status":"ok"}`

---

## Step 7 — Terminal C (still in `backend`, venv active): start the backend API (port 8000)

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```
Expected: `Application startup complete.` + `Uvicorn running on http://0.0.0.0:8000`

Quick check:
```bash
curl http://127.0.0.1:8000/health
```

---

## Step 8 — Terminal D: the website (port 3000)

Open a **fourth terminal**:

```bash
cd gem-sentinel/frontend
npm install          # first time only (creates node_modules)
npm run build        # production build (takes ~30-60s)
npx next start -p 3000
```
Expected: `✓ Ready` and the site serves on port 3000.

> For development with hot reload, use `npm run dev` instead of the build+start pair.

---

## Step 9 — Open the app

Browser → **http://localhost:3000**

You should see the landing page with two portal cards. Demo accounts (click a card to autofill):

| Role | Email | Password |
|---|---|---|
| Bidder A | ramesh@bidder.in | Bidder@1234 |
| Bidder B (doc gaps) | sita@bidder.in | Bidder@1234 |
| Bidder C (blacklisted) | vikram@bidder.in | Bidder@1234 |
| Bidder D (unverifiable) | meena@bidder.in | Bidder@1234 |
| Officer (L1 behavior) | procurement.belagavi@cpcl.gov.in | Officer@1234 |
| CFA (L2 behavior) | cfa.karnataka@cpcl.gov.in | CFA@1234 |
| Admin | admin@cpcl.gov.in | Admin@1234 |
| Auditor | auditor@cpcl.gov.in | Auditor@1234 |

**Government Portal** signs in with a **Govt Unique ID + OTP** (no email/password
on that tab): enter e.g. `GOI-PNG-2018-0492` (Procurement Officer, Belagavi —
format: GOI = Govt of India, PNG = Ministry of Petroleum & Natural Gas,
year of joining, employee no.) → an SMS OTP goes to the phone registered
against that ID → enter it → in. The System Admin (`GOI-PNG-2011-0114`) manages
the employee master under **Admin → Govt. Employees** (add/approve/suspend;
first login requires approval — `GOI-PNG-2025-1102` is the pending demo).

### Suggested 3-minute demo
1. **Bidder** → `sita@bidder.in` → *Documents* → **View document** (original-looking replica).
2. **Government** → officer login → open APP-2026-000002 → **Run AI Verification** → *Report* tab (Explainable-AI breakdown) → **Download PDF**. Sealing is disabled in the shipped `backend/.env` (`BID_SEALING_ENABLED=false`), so verification can be run immediately — even before the 6 PM IST deadline.
3. *(Optional — sealed-bid variant)* Set `BID_SEALING_ENABLED=true` in `backend/.env` and restart the backend: the officer queue now shows **Sealed** rows (details hidden until the deadline), and **Admin** → *Tenders* → **Force open bids** on TDR-2026-001 (audited) unseals it for evaluation.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `connection to server at "127.0.0.1", port 5432 failed` | Postgres not running, or not listening on TCP. Start it (Step 2); check `listen_addresses` in `postgresql.conf`. |
| `password authentication failed for user "gemsentinel"` | Re-run Step 3; ensure the password is exactly `gemsentinel123`. |
| `EADDRINUSE : 8000/8001/3000` | Another process holds the port. Kill it (`lsof -i :8000` / `netstat -ano | findstr :8000` on Windows) or change the port (backend: `-port 8002` + set `MOCK_PORTAL_URL`/frontend proxy accordingly). |
| Backend 502/500 with `could not translate host name "localhost"` in `DATABASE_URL` | Use the default `127.0.0.1` URL (already the default) — don't edit it. |
| `npm run build` fails on a TypeScript error | You have local code edits — `npm run dev` will still run and show errors in the browser. |
| Frontend loads but APIs fail | Backend (:8000) or mock portals (:8001) not running, or the page was opened before `npm run build` finished. |
| Login shows `Twilio HTTP 422 … 572002 … verified recipient` | Twilio **trial** account: add the destination numbers as Verified Destinations (see "Real OTP delivery" above) or upgrade to a paid account. |
| Login shows `Twilio HTTP 401/403` | Wrong SID/auth token in `backend/.env` — check `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN`. |
| Demo-SMS line in the backend log says `rejected`/`failed` | Check the Twilio console *Messaging → Messages* for the failure reason (unverified number, trial units exhausted, WhatsApp window closed). The sign-in flow is unaffected by design. |
| `column ... does not exist` (schema drift) | Your database was created by an **older build** of the app — `create_all` creates missing *tables* but never adds new *columns* to existing ones. **Just restart the backend**: an auto-migration now heals the schema in place (you'll see an `auto-migration: added missing columns ...` warning in the log, no data lost). Nuclear alternative: drop + recreate the database (Step 3) and re-run the seed. (Historic example: before 2026-09-17 the `bidder_profiles` table had CIN columns that were later removed from the product — an old DB may still carry them; they are simply ignored by the current code.) |

## Environment variables (all optional — defaults work out of the box)

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg2://gemsentinel:gemsentinel123@127.0.0.1:5432/gem_sentinel` | DB connection |
| `MOCK_PORTAL_URL` | `http://127.0.0.1:8001` | Simulated govt sources |
| `GEMINI_API_KEY` | *(empty)* | Optional — enables Gemini narrative; deterministic fallback used when empty |
| `BID_SEALING_ENABLED` | `true` | `false` disables bid-deadline sealing so officers can view details and **Run AI Verification before the deadline** (shipped `backend/.env` sets `false` for the demo). Set `true` to restore sealed-bid confidentiality. |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` / `DB_POOL_RECYCLE` | `20` / `40` / `1800` | Connection pool tuning |
| `SMS_PROVIDER` | `console` | `console` (log only) \| `twilio` \| `msg91` \| `textlocal` — where the OTP *code* goes |
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` / `TWILIO_FROM` | *(empty)* | Twilio credentials + sender number (E.164) |
| `TWILIO_VERIFY_SERVICE_SID` / `TWILIO_DEMO_SMS` / `TWILIO_DEMO_SMS_CHANNEL` | *(empty)* / `false` / `sms` | Demo delivery proof — real notification on every OTP issue, code never included |

Set them in your shell before starting the backend, e.g.:
```bash
export GEMINI_API_KEY="your-key-here"   # only if you want the LLM narrative
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

## Real OTP delivery (SMS gateway)

OTP sign-in (bidder + government portals) generates a 6-digit code that is
**never shown in the UI**. Two delivery modes:

**1. OTP channel** (`SMS_PROVIDER`) — where the *code itself* goes:

```bash
# console (default; this deployment): code → backend log only,
#    UI shows a "demo mode" note. Free, works everywhere.
SMS_PROVIDER=console

# …or a real gateway, then the code is SMS'd to the registered phone:
# SMS_PROVIDER=twilio   (+ TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_FROM)
# SMS_PROVIDER=msg91    (+ MSG91_AUTH_KEY / MSG91_SENDER_ID)
# SMS_PROVIDER=textlocal (+ TEXTLOCAL_API_KEY)
```

**2. Demo delivery proof** (this deployment's design) — the phone still gets a
real message even in console mode:

```bash
TWILIO_VERIFY_SERVICE_SID=VA…        # created once in the Twilio console (Verify)
TWILIO_DEMO_SMS=true                 # fire a real notification on every OTP issue
TWILIO_DEMO_SMS_CHANNEL=sms          # or "whatsapp" (trial: only numbers that
                                     # previously messaged the account)
```

Each OTP issue then fires a Twilio Verify **pre-approved template** message to
the registered phone (best-effort — a failure only logs; it never blocks or
fails sign-in, and it never carries the OTP code). The demo story: *"a real
SMS was delivered to the phone by the project's pipeline; the code to enter is
the one logged server-side, because paid OTP routing is not enabled."*

Gateway failures are fail-closed: the UI shows the error and the OTP is never
marked sent.

**Twilio trial-account limits (all verified against this account):**
- Only **verified destinations** receive messages — add each number (E.164)
  at Console → *Phone Numbers → Verified Destinations* (error `572002`
  otherwise).
- **Custom SMS bodies are rejected** (error `572006`) — only Twilio's
  pre-defined templates; the Verify product's template is what the demo proof
  uses.
- Trial includes **100 free SMS** and 30-day expiry; upgrade to paid removes
  the limits (verified-recipient + custom bodies + international).
- Check any send's outcome at Console → *Messaging → Messages*
  (delivered / failed + reason).

## Running the test suite

```bash
cd backend
.venv/bin/python -m pytest tests/ -q
```

The suite is **hermetic about SMS**: if your dev server (:8000) runs a real
gateway, conftest automatically spawns a short-lived **console-mode** instance
on a free port and runs the suite against it (torn down afterwards) — tests
never fire real/paid SMS at your phones. If :8000 is already in console mode it
is reused. Override with `GS_TEST_BASE=http://127.0.0.1:<port>` if you want to
target a specific console-mode server.

## Stopping everything
Press `Ctrl+C` in each terminal (Terminals A–D). No other cleanup needed — your
Postgres data stays in place between runs (re-seed only if you want fresh demo data).
