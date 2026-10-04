from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.deps import get_current_user
from ..models import Notification, User

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("")
def list_notifications(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = (
        db.query(Notification)
        .filter(Notification.recipient_user_id == user.id)
        .order_by(Notification.created_at.desc())
        .limit(100)
        .all()
    )
    unread = sum(1 for r in rows if not r.is_read)
    return {
        "unread": unread,
        "notifications": [
            {"id": n.id, "event_type": n.event_type, "title": n.title, "message": n.message,
             "entity_type": n.entity_type, "entity_id": n.entity_id, "is_read": n.is_read,
             "created_at": n.created_at.isoformat()}
            for n in rows
        ],
    }


@router.post("/{notification_id}/read")
def mark_read(notification_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    n = db.get(Notification, notification_id)
    if not n or n.recipient_user_id != user.id:
        raise HTTPException(status_code=404, detail="Notification not found")
    n.is_read = True
    db.commit()
    return {"message": "marked read"}


@router.post("/read-all")
def mark_all_read(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    db.query(Notification).filter(Notification.recipient_user_id == user.id, Notification.is_read.is_(False)).update({"is_read": True})
    db.commit()
    return {"message": "all marked read"}
