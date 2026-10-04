"""Tender-scoped collusion / proxy-bidding indicator endpoints (advisory).

Read-only, deterministic analysis. The result is factual shared-attribute
evidence for human review — never an automatic disqualification.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..audit import append_audit
from ..core.db import get_db
from ..core.deps import (
    BUYER_SIDE_ROLES,
    ROLE_ADMIN,
    ROLE_AUDITOR,
    get_current_user,
)
from ..models import CollusionAnalysis, Tender, User
from ..services.collusion import analyze_tender_collusion

router = APIRouter(prefix="/collusion", tags=["collusion"])


def _can_view(user: User) -> bool:
    return user.role in BUYER_SIDE_ROLES or user.role in (ROLE_ADMIN, ROLE_AUDITOR)


def _get_tender(db: Session, tender_id: str) -> Tender:
    t = db.get(Tender, tender_id)
    if not t:
        raise HTTPException(status_code=404, detail="Tender not found")
    return t


@router.get("/tenders/{tender_id}")
def run_collusion_analysis(
    tender_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not _can_view(user):
        raise HTTPException(status_code=403, detail="You do not have access to collusion analysis.")
    tender = _get_tender(db, tender_id)
    # Bid confidentiality: inter-bidder comparison reveals who competed —
    # only available after the bid deadline (bid opening).
    from ..core.bids import sealed_detail, tender_bids_open

    if not tender_bids_open(tender):
        raise HTTPException(status_code=403, detail=sealed_detail(tender))

    result = analyze_tender_collusion(db, tender_id)

    # persist the run (advisory record, part of the audit trail)
    run = CollusionAnalysis(
        tender_id=tender_id,
        created_by=user.id,
        created_by_role=user.role,
        indicator_level=result["risk_level"],
        bidders_analyzed=result["bidders_analyzed"],
        findings=result,
    )
    db.add(run)
    append_audit(
        db, "collusion_analysis_run", "CollusionAnalysis", run.id,
        actor_id=user.id, actor_role=user.role,
        payload={
            "tender_id": tender_id,
            "risk_level": result["risk_level"],
            "bidders_analyzed": result["bidders_analyzed"],
            "indicator_count": len(result.get("indicators", [])),
            "advisory_only": True,
        },
        tender_id=tender_id,
        request=request,
        commit=False,
    )
    db.commit()

    return {"analysis_id": run.id, "created_at": run.created_at.isoformat(), **result}


@router.get("/tenders/{tender_id}/history")
def collusion_history(
    tender_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not _can_view(user):
        raise HTTPException(status_code=403, detail="You do not have access to collusion analysis.")
    tender = _get_tender(db, tender_id)
    from ..core.bids import sealed_detail, tender_bids_open

    if not tender_bids_open(tender):
        raise HTTPException(status_code=403, detail=sealed_detail(tender))
    rows = (
        db.query(CollusionAnalysis)
        .filter(CollusionAnalysis.tender_id == tender_id)
        .order_by(CollusionAnalysis.created_at.desc())
        .limit(50)
        .all()
    )
    return {
        "runs": [
            {
                "id": r.id,
                "created_at": r.created_at.isoformat(),
                "created_by_role": r.created_by_role,
                "indicator_level": r.indicator_level,
                "bidders_analyzed": r.bidders_analyzed,
                "indicator_count": len((r.findings or {}).get("indicators", [])),
            }
            for r in rows
        ]
    }
