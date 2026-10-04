"""Tender-scoped collusion / proxy-bidding indicator service.

Builds an inter-bidder graph over the bidders who applied to a tender and
flags shared attributes that are factual *indicators* of possible collusion,
proxy bidding, or common control:

  shared_gstin        0.95   same GSTIN across competing bidders
  shared_pan          0.90   same legal PAN across competing bidders
  shared_phone        0.75   same 10-digit contact phone
  shared_email        0.70   identical email address
  shared_email_domain 0.60   same custom (non-public) email domain
  shared_address      0.50   same pincode + address tokens
  shared_ip           0.40   same normalized submission source IP

The output is deliberately framed as *indicators* — factual data points for a
human reviewer — never as a finding of misconduct. This matches the platform
doctrine that a deterministic engine decides compliance, AI explains, and
humans make the call. No automatic disqualification is triggered here; the
result is advisory evidence surfaced to the officer / committee / CFA.

Deterministic, no LLM. PII in evidence is masked.
"""
import re
from collections import defaultdict
from typing import Optional

from sqlalchemy.orm import Session

from ..core.security import mask_gstin, mask_pan
from ..models import Application, AuditLog, BidderProfile, User

# Signal weights (higher = stronger indicator of common control / collusion)
SIGNAL_WEIGHTS = {
    "shared_gstin": 0.95,
    "shared_pan": 0.90,
    "shared_phone": 0.75,
    "shared_email": 0.70,
    "shared_email_domain": 0.60,
    "shared_address": 0.50,
    "shared_ip": 0.40,
}

PUBLIC_EMAIL_DOMAINS = {
    "gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "rediffmail.com",
    "icloud.com", "protonmail.com", "proton.me", "yahoo.in", "sbi.co.in",
    "bankofbaroda.in", "hdfcbank.com", "icicibank.com",
}

_ADDR_STOPWORDS = {
    "road", "street", "near", "opposite", "floor", "plot", "building",
    "block", "lane", "nagar", "colony", "sector", "phase", "post",
}
# Generic district-level terms that do NOT make an address specific
_ADDR_GENERIC = {
    * _ADDR_STOPWORDS,
    "industrial", "area", "estate", "village", "taluk", "district", "town",
    "city", "state", "india", "zone", "region",
}


# ---------------- normalization helpers ----------------

def _norm_phone(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    digits = re.sub(r"\D", "", str(value))
    if len(digits) >= 10:
        return digits[-10:]
    return None


def _norm_pan(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    v = re.sub(r"\s+", "", str(value)).upper()
    return v if len(v) == 10 else None


def _norm_gstin(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    v = re.sub(r"\s+", "", str(value)).upper()
    return v if len(v) >= 15 else None


def _norm_email(value: Optional[str]) -> Optional[str]:
    if not value or "@" not in value:
        return None
    return str(value).lower().strip()


def _email_domain(email: str) -> Optional[str]:
    dom = email.split("@")[-1].lower()
    if "." in dom and dom not in PUBLIC_EMAIL_DOMAINS:
        return dom
    return None


def _addr_key(address: Optional[str], pincode: Optional[str]) -> Optional[str]:
    """Address key for collusion matching — deliberately strict.

    Requires a pincode AND at least two *specific* tokens (i.e. not generic
    district-level words like 'industrial area, belagavi, karnataka'). This
    avoids false positives from shared generic addresses while still catching
    two bidders at the same specific office (pincode + building tokens).
    """
    if not address:
        return None
    pc = pincode or ""
    m = re.search(r"\b([1-9][0-9]{5})\b", str(address))
    if m and not pc:
        pc = m.group(1)
    if not pc:
        return None  # no pincode → cannot be specific enough to signal
    clean = re.sub(r"[^a-z0-9\s]", " ", str(address).lower())
    tokens = [w for w in clean.split() if len(w) > 2 and w not in _ADDR_GENERIC]
    if len(tokens) < 2:
        return None
    return f"{pc}:{'_'.join(sorted(tokens[:3]))}"


def _mask_ip(ip: Optional[str]) -> Optional[str]:
    if not ip:
        return None
    ip = ip.strip()
    if ":" in ip:  # IPv6
        return ip[:4] + ":…(masked)"
    parts = ip.split(".")
    if len(parts) == 4:
        return f"{parts[0]}.{parts[1]}.x.x"
    return ip[:6] + "…(masked)"


def _mask_phone(p: str) -> str:
    return f"…{p[-3:]}" if p else p


def _mask_email(e: str) -> str:
    if "@" not in e:
        return e
    local, dom = e.split("@", 1)
    keep = local[:1] if len(local) > 3 else local
    return f"{keep}•••@{dom}"


# ---------------- union-find for clustering ----------------

class _DSU:
    def __init__(self, n: int):
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


# ---------------- main analysis ----------------

def analyze_tender_collusion(db: Session, tender_id: str) -> dict:
    """Compute collusion indicators for all bidders who applied to a tender."""
    apps = (
        db.query(Application)
        .filter(Application.tender_id == tender_id)
        .all()
    )
    # de-dupe by bidder user (a bidder may have one application per tender)
    seen: set[str] = set()
    bidder_apps: list[tuple[str, Application]] = []
    for a in apps:
        if a.bidder_user_id in seen:
            continue
        seen.add(a.bidder_user_id)
        bidder_apps.append((a.bidder_user_id, a))

    n = len(bidder_apps)
    if n < 2:
        return {
            "tender_id": tender_id,
            "bidders_analyzed": n,
            "cartel_detected": False,
            "risk_level": "NONE",
            "collusion_clusters": [],
            "indicators": [],
            "graph_data": {"nodes": [], "edges": []},
            "excluded_common": [],
            "note": "Fewer than two competing bidders; collusion analysis not applicable.",
        }

    profiles = {
        u: db.query(BidderProfile).filter(BidderProfile.user_id == u).first()
        for u, _ in bidder_apps
    }
    names: dict[str, str] = {}
    for u, _ in bidder_apps:
        prof = profiles.get(u)
        names[u] = (prof.company_name if prof and prof.company_name else "Bidder")

    # submission IPs from audit log (normalized source IP, per application)
    app_ids = [a.id for _, a in bidder_apps]
    ips_by_app: dict[str, list[str]] = defaultdict(list)
    if app_ids:
        rows = (
            db.query(AuditLog)
            .filter(AuditLog.application_id.in_(app_ids), AuditLog.source_ip.isnot(None))
            .all()
        )
        for r in rows:
            ip = (r.source_ip or "").strip()
            if ip and ip not in {"127.0.0.1", "0:0:0:0:0:0:0:1", "::1"}:
                ips_by_app[r.application_id].append(ip)

    # index: signal -> normalized_value -> [bidder_user_ids]
    index: dict[tuple[str, str], list[str]] = defaultdict(list)

    def add(signal: str, value, uid: str):
        if value:
            index[(signal, value)].append(uid)

    app_of_user = {u: a for u, a in bidder_apps}
    for u, _ in bidder_apps:
        p = profiles.get(u)
        if p is None:
            continue
        add("shared_phone", _norm_phone(p.authorized_rep_phone), u)
        em = _norm_email(p.authorized_rep_email)
        if em:
            add("shared_email", em, u)
            dom = _email_domain(em)
            if dom:
                add("shared_email_domain", dom, u)
        add("shared_pan", _norm_pan(p.pan_plain), u)
        add("shared_gstin", _norm_gstin(p.gstin_plain), u)
        add("shared_address", _addr_key(p.registered_address, p.pincode), u)
        for ip in ips_by_app.get(app_of_user[u].id, []):
            add("shared_ip", ip, u)

    # find shared-attribute edges
    edges: list[dict] = []
    indicators: list[dict] = []
    excluded_common: list[dict] = []
    dsu = _DSU(n)
    uid_to_idx = {u: i for i, (u, _) in enumerate(bidder_apps)}

    for (signal, value), uids in index.items():
        # normalize to a set, keep unique bidders
        uniq = sorted(set(uids))
        if len(uniq) < 2:
            continue
        # A custom email domain shared by 3+ bidders is treated as common
        # infrastructure (e.g. a shared demo/registration domain), not as a
        # collusion indicator. Two bidders on the same custom domain IS a
        # meaningful indicator (possible proxy ring).
        if signal == "shared_email_domain" and len(uniq) >= 3:
            excluded_common.append({"signal": signal, "value": str(value),
                                    "bidders": len(uniq), "reason": "common_domain(3+ bidders)"})
            continue
        weight = SIGNAL_WEIGHTS[signal]
        label = _evidence_label(signal, value)
        for i in range(len(uniq)):
            for j in range(i + 1, len(uniq)):
                a, b = uniq[i], uniq[j]
                edges.append({
                    "source": a,
                    "target": b,
                    "signal": signal,
                    "weight": weight,
                    "value": label,
                })
                indicators.append({
                    "bidder_a": names[a],
                    "bidder_b": names[b],
                    "bidder_a_user_id": a,
                    "bidder_b_user_id": b,
                    "signal": signal,
                    "weight": weight,
                    "evidence": label,
                })
                dsu.union(uid_to_idx[a], uid_to_idx[b])

    # clusters
    clusters: dict[int, list[str]] = defaultdict(list)
    for i in range(n):
        clusters[dsu.find(i)].append(names[bidder_apps[i][0]])
    collusion_clusters = [
        sorted(set(members)) for _, members in clusters.items() if len(set(members)) >= 2
    ]

    # score: max accumulated edge weight per cluster
    cluster_risk: dict[int, float] = defaultdict(float)
    for e in edges:
        idx = dsu.find(uid_to_idx[e["source"]])
        cluster_risk[idx] = max(cluster_risk[idx], e["weight"])

    def _level(score: float) -> str:
        if score >= 0.9:
            return "HIGH"
        if score >= 0.7:
            return "MEDIUM"
        if score >= 0.4:
            return "LOW"
        return "NONE"

    top = max(cluster_risk.values()) if cluster_risk else 0.0
    risk_level = _level(top)
    cartel_detected = risk_level in ("HIGH", "MEDIUM")

    return {
        "tender_id": tender_id,
        "bidders_analyzed": n,
        "cartel_detected": cartel_detected,
        "risk_level": risk_level,
        "collusion_clusters": collusion_clusters,
        "indicators": sorted(indicators, key=lambda x: -x["weight"]),
        "graph_data": {
            "nodes": [{"id": u, "label": names[u], "type": "BIDDER"} for u, _ in bidder_apps],
            "edges": edges,
        },
        "excluded_common": excluded_common,
        "note": (
            "Advisory indicators only — factual shared-attribute data points for human review. "
            "This is NOT a finding of misconduct and triggers no automatic disqualification."
        ),
    }


def _evidence_label(signal: str, value) -> str:
    v = str(value)
    if signal == "shared_phone":
        return f"shared contact phone {_mask_phone(v)}"
    if signal == "shared_email":
        return f"shared email {_mask_email(v)}"
    if signal == "shared_email_domain":
        return f"shared custom email domain @{v}"
    if signal == "shared_pan":
        return f"shared PAN {mask_pan(v)}"
    if signal == "shared_gstin":
        return f"shared GSTIN {mask_gstin(v)}"
    if signal == "shared_address":
        pc = v.split(":", 1)[0]
        return f"shared registered address (pincode {pc})"
    if signal == "shared_ip":
        return f"shared submission source IP {_mask_ip(v)}"
    return f"shared {signal}"
