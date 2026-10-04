import httpx
import os
import socket
import subprocess
import time
from pathlib import Path
import pytest

BASE = "http://127.0.0.1:8000"
BACKEND_DIR = str(Path(__file__).resolve().parents[1])


def _python() -> str:
    """Prefer the project venv interpreter (deps live there), fall back to python3."""
    venv = os.path.join(BACKEND_DIR, ".venv", "bin", "python")
    return venv if os.path.exists(venv) else "python3"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_health(base: str, timeout: float = 60.0) -> dict:
    deadline = time.time() + timeout
    last: Exception | None = None
    while time.time() < deadline:
        try:
            r = httpx.get(base + "/health", timeout=3)
            if r.status_code == 200:
                return r.json()
        except Exception as e:  # noqa: BLE001
            last = e
        time.sleep(0.5)
    raise RuntimeError(f"test server at {base} did not become healthy: {last}")


@pytest.fixture(scope="session")
def base_url():
    """Resolve the server the suite runs against.

    The suite MUST run in SMS console mode: it reads the freshly issued code
    (`dev_otp`) to complete 2FA/gov flows, and it must never fire real (paid)
    SMS at the user's phones. Resolution:
    1. `GS_TEST_BASE` env override (must be console mode);
    2. a freshly spawned console-mode uvicorn on a free port (torn down after
       the session) — always with TWILIO_DEMO_SMS forced off and the background
       scheduler disabled, so no test ever fires a real SMS at a real phone and
       no background tick mutates the shared DB mid-suite.
    """
    override = os.environ.get("GS_TEST_BASE")
    if override:
        h = _wait_health(override)
        assert h.get("sms_provider") == "console", (
            f"GS_TEST_BASE server runs sms_provider={h.get('sms_provider')} — tests need console mode"
        )
        yield override
        return
    port = _free_port()
    mock_port = _free_port()
    mock_dir = str(Path(BACKEND_DIR).parent / "mock-govt-portals")
    mock_proc = subprocess.Popen(
        [_python(), "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(mock_port)],
        cwd=mock_dir,
        env={**os.environ},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    mock_url = f"http://127.0.0.1:{mock_port}"
    # Make the same portal URL visible to app.seed subprocesses run by module fixtures.
    old_mock_url = os.environ.get("MOCK_PORTAL_URL")
    os.environ["MOCK_PORTAL_URL"] = mock_url
    _wait_health(mock_url)
    # WATCH_ENABLED off: the background watch tick + deadline-close jobs would
    # otherwise mutate tender state mid-suite. Watch behavior is exercised
    # deterministically through the API endpoints (tests/test_watch.py).
    # BID_SEALING_ENABLED forced true: tests assert the spec's sealing
    # behavior regardless of the developer's .env (the off-switch for
    # "test verification any time" is covered by a unit test).
    env = {**os.environ, "SMS_PROVIDER": "console", "TWILIO_DEMO_SMS": "false",
           "WATCH_ENABLED": "false", "BID_SEALING_ENABLED": "true",
           "MOCK_PORTAL_URL": mock_url}
    proc = subprocess.Popen(
        [_python(), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=BACKEND_DIR,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        h = _wait_health(f"http://127.0.0.1:{port}")
        assert h.get("sms_provider") == "console", (
            f"spawned test server runs sms_provider={h.get('sms_provider')} — tests need console mode"
        )
        yield f"http://127.0.0.1:{port}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            proc.kill()
        mock_proc.terminate()
        try:
            mock_proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            mock_proc.kill()
        if old_mock_url is None:
            os.environ.pop("MOCK_PORTAL_URL", None)
        else:
            os.environ["MOCK_PORTAL_URL"] = old_mock_url


@pytest.fixture(scope="session")
def client(base_url):
    with httpx.Client(timeout=120, base_url=base_url) as c:
        yield c


@pytest.fixture(scope="module")
def seeded(client):
    """Re-seed the database once PER TEST MODULE for a clean, known state.

    Module scope (not session) keeps mutations made by one suite from
    leaking into the next suite's assumptions.
    """
    subprocess.run(
        [_python(), "-m", "app.seed"],
        cwd=BACKEND_DIR,
        capture_output=True,
        check=True,
    )
    time.sleep(1.5)  # let the server's pools settle after the reset
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["database"] == "up"
    return True


def login(client, email, pwd, device=None, remember=False, _tries: int = 0):
    """Full two-factor login: credentials → real OTP (read from dev_otp in
    console mode) → access token. `device`/`remember` exercise the
    trusted-device fast path. Waits out the 60s resend cooldown when a
    previous test left a live OTP for this account."""
    import re as _re

    headers = {"X-Device-Id": device} if device else None
    r = client.post("/auth/login", json={"email": email, "password": pwd}, headers=headers)
    if r.status_code == 429 and _tries < 3:
        m = _re.search(r"wait (\d+)s", r.text)
        wait = (int(m.group(1)) + 2) if m else 62
        time.sleep(min(wait, 65))
        return login(client, email, pwd, device=device, remember=remember, _tries=_tries + 1)
    assert r.status_code == 200, f"login step1 failed {email}: {r.text[:200]}"
    data = r.json()
    if not data.get("otp_required"):
        return data["access_token"]  # trusted device
    dev = data.get("dev_otp")
    assert dev, "OTP is real but no dev_otp available for tests (console mode required)"
    r2 = client.post(
        "/auth/verify-otp",
        json={"email": email, "otp": dev, "purpose": "login", "remember_device": remember},
        headers=headers,
    )
    assert r2.status_code == 200, f"login step2 failed {email}: {r2.text[:200]}"
    return r2.json()["access_token"]


def gov_login(client, gov_id, _tries: int = 0):
    """Full two-step government sign-in: Govt ID → real OTP to the registered
    phone (read from dev_otp in console mode) → tokens. Waits out the resend
    cooldown when a previous test left a live OTP for this ID."""
    import re as _re

    r = client.post("/auth/gov-login", json={"gov_id": gov_id})
    if r.status_code == 429 and _tries < 3:
        m = _re.search(r"wait (\d+)s", r.text)
        wait = (int(m.group(1)) + 2) if m else 62
        time.sleep(min(wait, 65))
        return gov_login(client, gov_id, _tries=_tries + 1)
    assert r.status_code == 200, f"gov-login step1 failed {gov_id}: {r.text[:200]}"
    data = r.json()
    dev = data.get("dev_otp")
    assert dev, "OTP is real but no dev_otp available for tests (console mode required)"
    r2 = client.post("/auth/gov-verify", json={"gov_id": gov_id, "otp": dev})
    assert r2.status_code == 200, f"gov-login step2 failed {gov_id}: {r2.text[:200]}"
    return r2.json()


def H(tok):
    return {"Authorization": f"Bearer {tok}"}


ADMIN_CREDS = ("admin@cpcl.gov.in", "Admin@1234")


def open_bids(client, tender_ids=None):
    """Admin force-opens bid evaluation (deadline → now).

    Seeded tenders seal bids until 18:00 IST; test flows that need
    evaluation access call this after the module reseed.
    """
    tok = login(client, *ADMIN_CREDS)
    if tender_ids is None:
        r = client.get("/admin/tenders", headers=H(tok))
        tender_ids = [t["id"] for t in r.json()["tenders"]]
    for tid in tender_ids:
        r = client.post(f"/admin/tenders/{tid}/open-bids", headers=H(tok))
        assert r.status_code == 200, f"open-bids failed {tid}: {r.text[:200]}"
    return True
