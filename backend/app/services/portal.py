"""Source fetch service with Adapter Pattern + source-failure policy.

If a source fails, times out, returns malformed data or is unavailable,
the affected result is set to UNABLE_TO_VERIFY — never silently to PASS.

Resiliency (additive):
- Per-source circuit breaker (fail-fast while OPEN, canary probe HALF_OPEN).
- Stale-while-revalidate: when a live call fails, the last successful
  snapshot (≤24h) is attached with stale=True. Stale data is NEVER used for
  compliance decisions — the rule engine still yields UNABLE_TO_VERIFY.
"""
import logging
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from ..core.config import settings
from ..core.db import utcnow
from ..models import SourceHealth
from ..portal_client import SourceResult, get_adapter
from . import resiliency

logger = logging.getLogger("gem.portal")


def _record_health(db: Session, source: str, ok: bool, message: str) -> None:
    last = db.query(SourceHealth).filter(SourceHealth.source == source).order_by(SourceHealth.checked_at.desc()).first()
    if last and ((last.status == "UP") == ok) and (last.message or "") == message:
        return  # no state change
    db.add(SourceHealth(source=source, status="UP" if ok else "DOWN", message=message[:290], checked_at=utcnow()))


def _attach_stale_snapshot(result: SourceResult, source: str, identifier: Optional[str]) -> SourceResult:
    """On failure, attach the freshest ≤24h snapshot as context (never for
    compliance decisions). The status stays UNAVAILABLE/ERROR."""
    snap = resiliency.swr_store.get(source, identifier or "")
    if snap:
        result.message = (
            f"{result.message} [stale snapshot attached: last success "
            f"{snap['age_hours']}h ago — reference only, NOT used for compliance decisions]"
        )
        result.data = {**(snap.get("data") or {}), "_stale": True, "_stale_age_hours": snap["age_hours"]}
    return result


def fetch_source(
    db: Session,
    source: str,
    identifier: Optional[str] = None,
    extra: Optional[dict] = None,
    commit: bool = False,
    use_cache: bool = True,
) -> SourceResult:
    adapter = get_adapter(source)
    url = f"{settings.MOCK_PORTAL_URL}/api/{adapter.path}/verify"
    body = {"identifier": identifier or "", **(extra or {})}

    # --- verification cache: serve fresh deterministic results without a
    #     portal round-trip (additive; failures are never cached).
    #     Watch polling passes use_cache=False: drift detection must always
    #     read the LIVE source state, never a cached snapshot. ---
    from . import verify_cache

    cached = verify_cache.get(db, source, identifier, extra) if use_cache else None
    if cached is not None:
        res = SourceResult(
            status=cached["status"],
            data={**(cached.get("data") or {}), "_cached": True,
                  "_cached_at": cached["retrieved_at"].isoformat(),
                  "_cache_age_seconds": cached["age_seconds"]},
            message=f"[cache hit: source verified {cached['age_seconds']}s ago] {cached['message']}",
            source=source,
            retrieved_at=cached["retrieved_at"],
            latency_ms=0,
        )
        return res

    # --- circuit breaker: fail fast while OPEN (do not hammer the portal) ---
    breaker = resiliency.get_breaker(source)
    if not breaker.allow():
        st = breaker.status()
        _record_health(db, source, False, f"Circuit breaker OPEN ({st['retry_in_seconds']}s to probe)")
        msg = f"Source temporarily paused (circuit breaker OPEN, retry in ~{st['retry_in_seconds']}s)"
        res = SourceResult(status="UNAVAILABLE", message=msg, source=source, retrieved_at=utcnow())
        return _attach_stale_snapshot(res, source, identifier)

    started = utcnow()
    try:
        resp = httpx.post(url, json=body, timeout=settings.PORTAL_TIMEOUT_SECONDS)
        latency = int((utcnow() - started).total_seconds() * 1000)
        if resp.status_code == 429:
            res = SourceResult(
                status="UNAVAILABLE",
                message="Source rate limit (429); exponential backoff with jitter applied",
                source=source, retrieved_at=utcnow(), latency_ms=latency,
            )
            breaker.report_failure()
            _record_health(db, source, False, "Rate limited (429)")
            return _attach_stale_snapshot(res, source, identifier)
        if resp.status_code == 503:
            breaker.report_failure()
            _record_health(db, source, False, "Source unavailable (503)")
            res = SourceResult(status="UNAVAILABLE", message="Source temporarily unavailable", source=source, retrieved_at=utcnow(), latency_ms=latency)
            return _attach_stale_snapshot(res, source, identifier)
        resp.raise_for_status()
        payload = resp.json()
        status = str(payload.get("status", "ERROR")).upper()
        if status not in {"VALID", "EXPIRED", "MISMATCH", "NOT_FOUND", "BLACKLIST", "UNAVAILABLE", "ERROR"}:
            status = "ERROR"
        breaker.report_success()
        _record_health(db, source, status in {"VALID", "EXPIRED", "MISMATCH", "NOT_FOUND", "BLACKLIST"}, payload.get("message", ""))
        result = SourceResult(
            status=status,
            data=payload.get("data", {}) or {},
            message=payload.get("message", ""),
            source=source,
            retrieved_at=utcnow(),
            latency_ms=latency,
        )
        if status in {"VALID", "EXPIRED", "MISMATCH", "NOT_FOUND"}:
            # snapshot only the deterministic source states useful as context
            resiliency.swr_store.put(source, identifier or "", data=result.data, message=result.message, status=status)
        # cache deterministic source states (incl. BLACKLIST) for repeat runs
        verify_cache.put(db, source, identifier, extra, status, result.data, result.message, result.retrieved_at)
        return result
    except httpx.TimeoutException:
        breaker.report_failure()
        _record_health(db, source, False, "Request timed out")
        logger.warning("Portal timeout for %s", source)
        return _attach_stale_snapshot(
            SourceResult(status="UNAVAILABLE", message="Source request timed out", source=source, retrieved_at=utcnow()),
            source, identifier,
        )
    except httpx.HTTPStatusError as exc:
        breaker.report_failure()
        _record_health(db, source, False, f"HTTP {exc.response.status_code}")
        return _attach_stale_snapshot(
            SourceResult(status="UNAVAILABLE", message=f"Source error (HTTP {exc.response.status_code})", source=source, retrieved_at=utcnow()),
            source, identifier,
        )
    except Exception as exc:  # malformed data / connection error
        breaker.report_failure()
        _record_health(db, source, False, f"Malformed/unreachable: {type(exc).__name__}")
        logger.warning("Portal fetch failed for %s: %s", source, exc)
        return _attach_stale_snapshot(
            SourceResult(status="UNAVAILABLE", message=f"Source unavailable: {type(exc).__name__}", source=source, retrieved_at=utcnow()),
            source, identifier,
        )
    finally:
        if commit:
            db.commit()


def source_resiliency_status() -> list[dict]:
    """Per-source breaker + snapshot status (admin/ops view)."""
    from ..core.db import SessionLocal
    from ..models import SourceHealth
    out = []
    db = SessionLocal()
    try:
        for src in ("UDYAM", "GST", "PAN", "DIGILOCKER", "MCA21", "EPFO", "ESIC",
                    "STARTUP_INDIA", "NSIC", "BIS", "BLACKLIST", "LOCAL_CONTENT"):
            b = resiliency.get_breaker(src)
            last = db.query(SourceHealth).filter(SourceHealth.source == src).order_by(SourceHealth.checked_at.desc()).first()
            last_health = {"status": last.status,
                           "checked_at": last.checked_at.isoformat() if last else None,
                           "message": last.message} if last else None
            out.append({**b.status(), "last_health": last_health})
    finally:
        db.close()
    return out
