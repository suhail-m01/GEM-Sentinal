"""Secure client-IP derivation and normalization (V2.1 spec §12).

Rules:
- The direct socket peer is always trusted.
- Forwarded/X-Forwarded-For is honoured ONLY when the direct peer is a
  configured trusted reverse proxy (env TRUSTED_PROXY_CIDRS).
- From an XFF chain we walk right-to-left and take the first address that is
  NOT a trusted proxy — that is the client as seen by the innermost trusted hop.
- Addresses are normalized: IPv4-mapped IPv6 is reduced to IPv4, lowercase,
  zone identifiers stripped, validated via the `ipaddress` module.
- Malformed chains yield (None, "MALFORMED") — never an exception.
- IP is evidential, never identity proof.
"""
from __future__ import annotations

import ipaddress
from typing import Optional

from .core.config import settings


def _trusted_cidrs() -> list[ipaddress._BaseNetwork]:
    raw = (getattr(settings, "TRUSTED_PROXY_CIDRS", "") or "").strip()
    nets: list[ipaddress._BaseNetwork] = []
    if not raw:
        return nets
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            nets.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            continue
    return nets


def _is_trusted_proxy(ip_str: str, nets: list) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    for net in nets:
        # compare same version only
        if ip.version == net.version and ip in net:
            return True
    return False


def normalize_ip(raw: str | None) -> Optional[str]:
    """Normalize an IP string (IPv4-mapped → IPv4, lowercase, no zone)."""
    if not raw:
        return None
    raw = raw.strip().lower()
    if "%" in raw:
        raw = raw.split("%", 1)[0]
    try:
        ip = ipaddress.ip_address(raw)
    except ValueError:
        return None
    if ip.version == 6:
        mapped = ip.ipv4_mapped
        if mapped is not None:
            return str(mapped)
        # strip leading zero groups compactly
        return str(ip)
    return str(ip)


def mask_ip(normalized: str | None) -> str:
    """Privacy-safe rendering for ordinary (non-security) screens.

    IPv4: keep first two octets. IPv6: keep first 32 bits.
    """
    if not normalized:
        return "—"
    try:
        ip = ipaddress.ip_address(normalized)
    except ValueError:
        return "—"
    if ip.version == 4:
        parts = str(ip).split(".")
        return f"{parts[0]}.{parts[1]}.*.*"
    return normalized[:8] + "*" * 28


def resolve_client_ip(
    peer_ip: str | None,
    x_forwarded_for: str | None = None,
    x_real_ip: str | None = None,
) -> tuple[Optional[str], str]:
    """Return (normalized_client_ip, source).

    source ∈ DIRECT | TRUSTED_PROXY | MALFORMED
    """
    nets = _trusted_cidrs()
    peer = normalize_ip(peer_ip)
    if peer is None:
        return None, "MALFORMED"
    if not nets or not _is_trusted_proxy(peer, nets):
        # Untrusted peer: ignore ALL forwarding headers (anti-spoofing).
        return peer, "DIRECT"
    # Trusted peer: honour the forwarding chain.
    chain: list[str] = []
    if x_forwarded_for:
        chain.extend(h.strip() for h in x_forwarded_for.split(",") if h.strip())
    elif x_real_ip:
        chain.append(x_real_ip.strip())
    if not chain:
        return peer, "DIRECT"
    # Walk right-to-left: first non-trusted address is the client.
    for hop in reversed(chain):
        norm = normalize_ip(hop)
        if norm is None:
            return None, "MALFORMED"
        if not _is_trusted_proxy(norm, nets):
            return norm, "TRUSTED_PROXY"
    # Entire chain is trusted proxies — no client address available.
    return None, "MALFORMED"
