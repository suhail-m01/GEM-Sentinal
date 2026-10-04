"""SHA-256 hash-chained audit trail (V2.1: IP-aware, request-correlated).

    current_hash = SHA256(canonical_event_payload_including_normalized_ip + previous_hash)

The canonical payload includes the normalized source IP, its derivation
source (DIRECT/TRUSTED_PROXY), the request correlation id and the session
id, so the security context is covered by the tamper-evident chain.

Records are append-only: no DELETE path exists, and historical
modification is detectable via verify_chain().
"""
import hashlib
import json
from typing import Any, Optional

from fastapi import Request
from sqlalchemy import text as _sa_text
from sqlalchemy.orm import Session

from .core.db import utcnow
from .ipcontext import resolve_client_ip
from .models import AuditLog

GENESIS_HASH = "0" * 64


def _canonical(data: dict) -> str:
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _hash_record(body: dict) -> str:
    return hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()


def _last_hash(db: Session) -> str:
    last = (
        db.query(AuditLog)
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .first()
    )
    return last.record_hash if last else GENESIS_HASH


_CHAIN_LOCK_KEY = 9021084415178324611  # fixed int8 key for the audit chain


def _chain_lock(db: Session) -> None:
    """Serialize concurrent appends (requests + background scheduler jobs).

    Without this, two transactions can read the same last hash and both
    chain off it — silently breaking the tamper-evident chain. The advisory
    lock is transaction-scoped: released on commit/rollback.
    """
    try:
        db.execute(_sa_text("SELECT pg_advisory_xact_lock(:k)"), {"k": _CHAIN_LOCK_KEY})
    except Exception:  # non-Postgres backends / edge cases: fail open
        pass


def _canonical_body(
    *,
    action: str,
    entity_type: Optional[str],
    entity_id: Optional[str],
    actor_id: Optional[str],
    actor_role: Optional[str],
    payload: Optional[dict],
    prev_hash: str,
    created_at_iso: str,
    tender_id: Optional[str] = None,
    application_id: Optional[str] = None,
    source_ip: Optional[str] = None,
    ip_source: Optional[str] = None,
    request_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> dict:
    return {
        "action": action,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "tender_id": tender_id,
        "application_id": application_id,
        "actor_id": actor_id,
        "actor_role": actor_role,
        "source_ip": source_ip,
        "ip_source": ip_source,
        "request_id": request_id,
        "session_id": session_id,
        "payload": payload or {},
        "prev_hash": prev_hash,
        "created_at": created_at_iso,
    }


def append_audit(
    db: Session,
    action: str,
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
    actor_id: Optional[str] = None,
    actor_role: Optional[str] = None,
    payload: Optional[dict] = None,
    ip_address: Optional[str] = None,
    ip_source: Optional[str] = None,
    user_agent: Optional[str] = None,
    tender_id: Optional[str] = None,
    application_id: Optional[str] = None,
    request_id: Optional[str] = None,
    session_id: Optional[str] = None,
    request: Optional[Request] = None,
    commit: bool = True,
) -> AuditLog:
    # Auto-capture the secure request context when a Request is supplied:
    # normalized source IP (+ derivation source), request id, session id, UA.
    if request is not None:
        peer = request.client.host if request.client else None
        auto_ip, auto_src = resolve_client_ip(
            peer,
            x_forwarded_for=request.headers.get("x-forwarded-for"),
            x_real_ip=request.headers.get("x-real-ip"),
        )
        if ip_address is None:
            ip_address = auto_ip
            ip_source = auto_src
        if user_agent is None:
            user_agent = (request.headers.get("user-agent") or "")[:300] or None
        if request_id is None:
            request_id = getattr(request.state, "request_id", None)
        if session_id is None:
            session_id = getattr(request.state, "session_id", None)
    ts = utcnow()
    _chain_lock(db)  # take the chain lock BEFORE reading the last hash
    # ponytail: sessions run autoflush=False, so an earlier append in the same
    # transaction (e.g. AUCTION_CREATED -> RA_ELIGIBILITY_CALCULATED) is still
    # pending and invisible to the tail read, forking the chain. Flushing under
    # the lock makes same-transaction chains see their own earlier records.
    # Ceiling: one flush per append; no issue until appends per request grow.
    db.flush()
    prev_hash = _last_hash(db)
    body = _canonical_body(
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        actor_id=actor_id,
        actor_role=actor_role,
        payload=payload,
        prev_hash=prev_hash,
        created_at_iso=ts.isoformat(),
        tender_id=tender_id,
        application_id=application_id,
        source_ip=ip_address,
        ip_source=ip_source,
        request_id=request_id,
        session_id=session_id,
    )
    record_hash = _hash_record(body)
    log = AuditLog(
        actor_id=actor_id,
        actor_role=actor_role,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        tender_id=tender_id,
        application_id=application_id,
        source_ip=ip_address,
        ip_source=ip_source,
        request_id=request_id,
        session_id=session_id,
        user_agent=user_agent,
        payload=payload or {},
        prev_hash=prev_hash,
        record_hash=record_hash,
        created_at=ts,
    )
    db.add(log)
    if commit:
        db.commit()
    return log


def verify_chain(db: Session) -> dict:
    """Re-compute every record hash in chain order. Returns validity report."""
    logs = (
        db.query(AuditLog).order_by(AuditLog.created_at.asc(), AuditLog.id.asc()).all()
    )
    prev = GENESIS_HASH
    for idx, log in enumerate(logs):
        body = _canonical_body(
            action=log.action,
            entity_type=log.entity_type,
            entity_id=log.entity_id,
            actor_id=log.actor_id,
            actor_role=log.actor_role,
            payload=log.payload,
            prev_hash=log.prev_hash,
            created_at_iso=log.created_at.isoformat(),
            tender_id=log.tender_id,
            application_id=log.application_id,
            source_ip=log.source_ip,
            ip_source=log.ip_source,
            request_id=log.request_id,
            session_id=log.session_id,
        )
        if log.prev_hash != prev or _hash_record(body) != log.record_hash:
            return {
                "valid": False,
                "total_records": len(logs),
                "broken_at_index": idx,
                "broken_record_id": log.id,
                "detail": "Hash-chain verification failed: historical modification detected.",
            }
        prev = log.record_hash
    return {"valid": True, "total_records": len(logs), "last_hash": prev}
