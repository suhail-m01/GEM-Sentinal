"""Verification-result cache (additive; ported from BidVerify-AI's 3-tier cache).

Two tiers in this stack (Redis is an optional extension point — not required):
  L1: in-process LRU (no DB round-trip on hot hits)
  L2: Postgres `verification_cache` table (durable across restarts/reseeds)

Doctrine preserved:
- Only deterministic source states are cached: VALID, EXPIRED, MISMATCH,
  NOT_FOUND, BLACKLIST. Transient failures (UNAVAILABLE/ERROR) are NEVER
  cached — an unavailable source must keep yielding UNABLE_TO_VERIFY.
- TTL-bounded (VERIFY_CACHE_TTL_SECONDS); a fresh re-verification within the
  TTL returns the same portal data with `_cached: True` markers so the audit
  trail shows exactly when the source was actually retrieved.
- Invalidation: explicit admin clear (audited). Portal responses are a pure
  function of (source, identifier, extra) over immutable simulated source
  data, so no implicit state hooks are required; TTL + clear cover the rest.
"""
from __future__ import annotations

import hashlib
import json
import threading
from collections import OrderedDict
from typing import Optional

from sqlalchemy.orm import Session

from ..core.config import settings
from ..core.db import utcnow

# Only these source states are safe to serve from cache (deterministic data,
# not transient availability).
CACHEABLE_STATUSES = {"VALID", "EXPIRED", "MISMATCH", "NOT_FOUND", "BLACKLIST"}

# ---------------------------------------------------------------------------
# L1: in-process LRU
# ---------------------------------------------------------------------------
_lru: "OrderedDict[str, dict]" = OrderedDict()
_lru_lock = threading.Lock()


def _lru_put(entry: dict) -> None:
    with _lru_lock:
        _lru[entry["key"]] = entry
        _lru.move_to_end(entry["key"])
        while len(_lru) > max(1, settings.VERIFY_CACHE_MAX_ENTRIES):
            _lru.popitem(last=False)


def _lru_get(key: str) -> Optional[dict]:
    with _lru_lock:
        entry = _lru.get(key)
        if entry is None:
            return None
        if entry["expires_at"] <= utcnow():
            _lru.pop(key, None)
            return None
        _lru.move_to_end(key)
        return entry


def _lru_invalidate(source: Optional[str] = None) -> None:
    with _lru_lock:
        if source is None:
            _lru.clear()
            return
        for k in [k for k, v in _lru.items() if v["source"] == source]:
            _lru.pop(k, None)


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------

def make_key(source: str, identifier: Optional[str], extra: Optional[dict]) -> str:
    """Stable key for (source, identifier, extra) — extra order-insensitive."""
    norm = json.dumps(extra or {}, sort_keys=True, separators=(",", ":"))
    raw = f"{source}|{(identifier or '').strip()}|{norm}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get(db: Session, source: str, identifier: Optional[str], extra: Optional[dict]) -> Optional[dict]:
    """Return a fresh cached result dict or None.

    Result dict: {status, data, message, retrieved_at (datetime), fetched_at (datetime), age_seconds}
    """
    if not settings.VERIFY_CACHE_ENABLED:
        return None
    key = make_key(source, identifier, extra)

    entry = _lru_get(key)
    if entry is not None:
        return {
            "status": entry["status"],
            "data": entry["data"],
            "message": entry["message"],
            "retrieved_at": entry["retrieved_at"],
            "fetched_at": entry["fetched_at"],
            "age_seconds": int((utcnow() - entry["fetched_at"]).total_seconds()),
            "hits": entry.get("hits", 0),
        }

    from ..models import VerificationCache

    row = (
        db.query(VerificationCache)
        .filter(VerificationCache.source == source, VerificationCache.cache_key == key)
        .first()
    )
    now = utcnow()
    if row is None:
        return None
    if row.expires_at <= now:
        db.delete(row)  # lazy cleanup of expired rows
        return None
    row.hits = (row.hits or 0) + 1
    row.last_hit_at = now
    entry = {
        "key": key,
        "source": row.source,
        "status": row.status,
        "data": dict(row.data or {}),
        "message": row.message or "",
        "retrieved_at": row.retrieved_at,
        "fetched_at": row.created_at,
        "expires_at": row.expires_at,
        "hits": row.hits,
    }
    _lru_put(entry)
    return {
        "status": entry["status"],
        "data": entry["data"],
        "message": entry["message"],
        "retrieved_at": entry["retrieved_at"],
        "fetched_at": entry["fetched_at"],
        "age_seconds": int((now - entry["fetched_at"]).total_seconds()),
        "hits": entry["hits"],
    }


def put(
    db: Session,
    source: str,
    identifier: Optional[str],
    extra: Optional[dict],
    status: str,
    data: dict,
    message: str,
    retrieved_at,
) -> bool:
    """Store a deterministic source result. Returns True if stored."""
    if not settings.VERIFY_CACHE_ENABLED:
        return False
    if status not in CACHEABLE_STATUSES:
        return False

    from ..models import VerificationCache

    key = make_key(source, identifier, extra)
    now = utcnow()
    row = (
        db.query(VerificationCache)
        .filter(VerificationCache.source == source, VerificationCache.cache_key == key)
        .first()
    )
    if row is None:
        # autoflush=False: a row added earlier in THIS transaction is not
        # visible to the SELECT above. Reuse the pending object (the watch
        # flap-confirmation polls the same source twice per tick) instead of
        # inserting a duplicate that violates the unique cache_key.
        for obj in list(db.new):
            if isinstance(obj, VerificationCache) and obj.cache_key == key and obj.source == source:
                row = obj
                break
    if row is None:
        row = VerificationCache(source=source, cache_key=key)
        db.add(row)
    row.status = status
    row.data = dict(data or {})
    row.message = (message or "")[:290]
    row.retrieved_at = retrieved_at
    row.expires_at = now + _ttl()
    row.hits = 0
    row.last_hit_at = None
    _lru_put({
        "key": key,
        "source": source,
        "status": status,
        "data": dict(data or {}),
        "message": (message or "")[:290],
        "retrieved_at": retrieved_at,
        "fetched_at": now,
        "expires_at": row.expires_at,
        "hits": 0,
    })
    return True


def _ttl():
    from datetime import timedelta
    return timedelta(seconds=max(0, settings.VERIFY_CACHE_TTL_SECONDS))


def invalidate(db: Session, source: Optional[str] = None) -> int:
    """Delete cached rows (all, or one source) + purge L1. Returns rows deleted."""
    from ..models import VerificationCache

    q = db.query(VerificationCache)
    if source:
        q = q.filter(VerificationCache.source == source)
    n = q.count()
    q.delete(synchronize_session=False)
    _lru_invalidate(source)
    return n


def stats(db: Session) -> dict:
    from sqlalchemy import func
    from ..models import VerificationCache

    now = utcnow()
    rows = (
        db.query(
            VerificationCache.source,
            func.count(VerificationCache.id),
            func.coalesce(func.sum(VerificationCache.hits), 0),
            func.max(VerificationCache.expires_at),
        )
        .group_by(VerificationCache.source)
        .all()
    )
    total = sum(r[1] for r in rows)
    total_hits = sum(int(r[2]) for r in rows)
    return {
        "enabled": bool(settings.VERIFY_CACHE_ENABLED),
        "ttl_seconds": int(settings.VERIFY_CACHE_TTL_SECONDS),
        "max_entries": int(settings.VERIFY_CACHE_MAX_ENTRIES),
        "entries": total,
        "total_hits": total_hits,
        "cacheable_statuses": sorted(CACHEABLE_STATUSES),
        "by_source": [
            {
                "source": r[0],
                "entries": r[1],
                "hits": int(r[2]),
                "expires_at": r[3].isoformat() if r[3] else None,
            }
            for r in sorted(rows, key=lambda x: x[0])
        ],
        "as_of": now.isoformat(),
    }
