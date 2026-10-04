"""In-app notification service (email/SMS may be simulated later)."""
from typing import Optional

from sqlalchemy.orm import Session

from .core.db import utcnow
from .models import Notification, User

EVENT_TITLES = {
    "tender_published": "New tender published",
    "eligibility_updated": "Eligibility result updated",
    "application_submitted": "Application submitted",
    "application_routed": "Application routed for review",
    "clarification_requested": "Clarification requested",
    "clarification_responded": "Clarification response received",
    "ai_report_ready": "AI verification report ready",
    "l1_recommendation_submitted": "L1 recommendation submitted",
    "l2_action_required": "L2 approval action required",
    "override_alert": "Override alert",
    "admin_cosign_required": "Admin co-sign required",
    "vigilance_referral_generated": "External Vigilance referral generated",
    "application_approved": "Application approved",
    "application_rejected": "Application rejected",
    "sla_breach": "SLA breach",
    "routing_fallback": "Application routed via fallback",
    "account_approved": "Officer account approved",
    # auctions (Features B/C)
    "ra_scheduled": "Auction scheduled",
    "ra_live": "Auction is live",
    "ra_bid_accepted": "Bid accepted",
    "ra_bid_rejected": "Bid rejected",
    "ra_extended": "Auction extended",
    "ra_paused": "Auction paused",
    "ra_resumed": "Auction resumed",
    "ra_cancelled": "Auction cancelled",
    "ra_result_ready": "Auction result ready for review",
    "ra_not_shortlisted": "Auction — not shortlisted",
    "auction_tie_detected": "Auction tie detected",
}


def notify(
    db: Session,
    user_id: str,
    event_type: str,
    title: Optional[str] = None,
    message: str = "",
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
    commit: bool = False,
) -> Notification:
    n = Notification(
        recipient_user_id=user_id,
        event_type=event_type,
        title=title or EVENT_TITLES.get(event_type, event_type.replace("_", " ").title()),
        message=message,
        entity_type=entity_type,
        entity_id=entity_id,
        created_at=utcnow(),
    )
    db.add(n)
    if commit:
        db.commit()
    return n


def notify_admins(
    db: Session,
    event_type: str,
    title: Optional[str] = None,
    message: str = "",
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
    commit: bool = False,
) -> list[Notification]:
    admins = db.query(User).filter(User.role == "ADMIN", User.status == "ACTIVE").all()
    out = []
    for a in admins:
        out.append(
            notify(db, a.id, event_type, title, message, entity_type, entity_id, commit=False)
        )
    if commit and out:
        db.commit()
    return out
