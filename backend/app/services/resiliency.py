"""Portal resiliency: circuit breaker, jittered backoff, stale-while-revalidate.

Design (additive — the existing source-failure policy is preserved):
- A per-source circuit breaker (CLOSED → OPEN → HALF_OPEN) stops hammering a
  failing government portal; while OPEN, calls fail fast with UNAVAILABLE.
- Exponential backoff with full jitter honors Retry-After on 429s.
- The SWR snapshot store keeps the last successful response per
  (source, identifier) for up to 24h. When a live call fails and a fresh
  snapshot exists, the snapshot is attached to the UNAVAILABLE result with a
  `stale=True` marker. Rule evaluation still treats stale data as
  UNABLE_TO_VERIFY (stale data never becomes PASS) but the officer sees the
  stale values for context.
"""
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

SWR_TTL_SECONDS = 24 * 60 * 60  # 24h stale-while-revalidate horizon


# ---------------- circuit breaker ----------------

class CircuitBreakerOpen(Exception):
    def __init__(self, service: str, retry_in_seconds: float):
        self.service = service
        self.retry_in_seconds = retry_in_seconds
        super().__init__(
            f"Circuit breaker for '{service}' is OPEN (fail-fast); "
            f"recovery probe in {retry_in_seconds:.0f}s"
        )


class CircuitBreaker:
    """Thread-safe circuit breaker state machine (per external source)."""

    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"

    def __init__(self, name: str, failure_threshold: int = 5, recovery_timeout: float = 30.0,
                 half_open_max_calls: int = 2):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls
        self.state = self.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.half_open_calls = 0
        self.last_failure_at: Optional[float] = None
        self._lock = threading.Lock()

    def _maybe_half_open(self) -> None:
        if self.state == self.OPEN and self.last_failure_at is not None:
            if time.monotonic() - self.last_failure_at >= self.recovery_timeout:
                self.state = self.HALF_OPEN
                self.half_open_calls = 0
                self.failure_count = 0

    def allow(self) -> bool:
        with self._lock:
            self._maybe_half_open()
            if self.state == self.OPEN:
                return False
            if self.state == self.HALF_OPEN:
                if self.half_open_calls >= self.half_open_max_calls:
                    return False
                self.half_open_calls += 1
            return True

    def report_success(self) -> None:
        with self._lock:
            self.failure_count = 0
            self.success_count += 1
            if self.state in (self.HALF_OPEN, self.CLOSED):
                self.state = self.CLOSED

    def report_failure(self) -> None:
        with self._lock:
            self.failure_count += 1
            self.last_failure_at = time.monotonic()
            if self.state == self.HALF_OPEN or self.failure_count >= self.failure_threshold:
                self.state = self.OPEN
                self.half_open_calls = 0

    def status(self) -> dict:
        with self._lock:
            self._maybe_half_open()
            retry_in = 0.0
            if self.state == self.OPEN and self.last_failure_at is not None:
                retry_in = max(0.0, self.recovery_timeout - (time.monotonic() - self.last_failure_at))
            return {
                "source": self.name,
                "state": self.state,
                "failure_count": self.failure_count,
                "success_count": self.success_count,
                "retry_in_seconds": round(retry_in, 1),
            }


_breakers: dict[str, CircuitBreaker] = {}
_breakers_lock = threading.Lock()


def get_breaker(source: str) -> CircuitBreaker:
    key = source.upper()
    with _breakers_lock:
        if key not in _breakers:
            _breakers[key] = CircuitBreaker(key)
        return _breakers[key]


def reset_breakers() -> None:
    """Test helper: clear all breaker state."""
    with _breakers_lock:
        _breakers.clear()


# ---------------- jittered backoff ----------------

def backoff_delay(attempt: int, base: float = 0.5, cap: float = 8.0,
                  retry_after: Optional[float] = None) -> float:
    """Exponential backoff with full jitter. Honors Retry-After when present.
    `attempt` is 0-based (first retry = attempt 0)."""
    exp = min(cap, base * (2 ** attempt))
    delay = random.uniform(0, exp)  # full jitter
    if retry_after is not None and retry_after > 0:
        delay = max(delay, min(retry_after, cap * 2))
    return delay


# ---------------- stale-while-revalidate snapshot store ----------------

@dataclass
class _Snapshot:
    data: dict
    message: str
    status: str
    captured_at: float  # time.time()
    payload: dict = field(default_factory=dict)


class SwrStore:
    """In-memory last-success store per (source, identifier).

    Single-process by design (matches the existing in-process source health
    handling); a Redis backend can replace this without API change.
    """

    def __init__(self, ttl_seconds: int = SWR_TTL_SECONDS):
        self.ttl_seconds = ttl_seconds
        self._store: dict[tuple[str, str], _Snapshot] = {}
        self._lock = threading.Lock()

    def put(self, source: str, identifier: str, *, data: dict, message: str = "",
            status: str = "VALID") -> None:
        with self._lock:
            self._store[(source.upper(), identifier.upper())] = _Snapshot(
                data=data or {}, message=message, status=status, captured_at=time.time()
            )

    def get(self, source: str, identifier: str) -> Optional[dict]:
        """Returns {data, message, status, captured_at, age_hours} if fresh."""
        with self._lock:
            snap = self._store.get((source.upper(), (identifier or "").upper()))
        if snap is None:
            return None
        age = time.time() - snap.captured_at
        if age > self.ttl_seconds:
            return None
        return {
            "data": snap.data,
            "message": snap.message,
            "status": snap.status,
            "captured_at": snap.captured_at,
            "age_hours": round(age / 3600, 2),
        }

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


swr_store = SwrStore()


def reset_resiliency() -> None:
    """Test helper."""
    reset_breakers()
    swr_store.clear()
