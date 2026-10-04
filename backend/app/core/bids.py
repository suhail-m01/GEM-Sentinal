"""Bid-confidentiality / bid-opening helpers.

Doctrine: bidder details are sealed to evaluation roles until the bid
deadline passes (bid opening). Before that, only aggregates are visible —
never identities, amounts, or documents. Post-deadline confidentiality is
immutable: once open, an evaluation must not be re-sealed silently (the
admin force-open action is audited and one-directional in practice).
"""
from datetime import datetime, time, timedelta, timezone

from ..core.db import utcnow

IST = timezone(timedelta(hours=5, minutes=30))
# GeM standard bid cut-off time when only a close date is known.
DEFAULT_CUTOFF_IST = time(18, 0)


def effective_bid_deadline(tender) -> datetime | None:
    """Naive-UTC deadline at which bids open for this tender."""
    if tender.bid_deadline:
        return tender.bid_deadline
    if tender.close_date is not None:
        ist = datetime.combine(tender.close_date, DEFAULT_CUTOFF_IST, tzinfo=IST)
        return ist.astimezone(timezone.utc).replace(tzinfo=None)
    return None


def tender_bids_open(tender, now: datetime | None = None) -> bool:
    from ..core.config import settings
    # Global master switch (env) OR per-tender override (demo district split):
    # either one disables sealing for this tender.
    if not settings.BID_SEALING_ENABLED or tender.sealing_enabled is False:
        return True  # sealing disabled → evaluation always open
    dl = effective_bid_deadline(tender)
    if dl is None:
        return True  # no deadline defined → nothing to seal (legacy tenders)
    return (now or utcnow()) >= dl


def sealed_detail(tender) -> dict:
    """403 payload for evaluation attempts while bids are sealed."""
    dl = effective_bid_deadline(tender)
    ist = dl.astimezone(IST) if dl else None
    return {
        "code": "BIDS_SEALED",
        "message": (
            "Bid details are sealed until the bid deadline. "
            "No bid content, bidder identities, or verification is available before bid opening."
        ),
        "bid_deadline_utc": dl.isoformat() if dl else None,
        "bid_deadline_ist": ist.isoformat() if ist else None,
        "tender_no": tender.tender_no,
    }
