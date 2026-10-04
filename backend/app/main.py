"""GeM-Sentinel — AI-Powered Integrated Bid Compliance Verification Platform.

The Rule Engine Decides. AI Only Explains.
"""
import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from .core.config import settings
from .core.db import Base, engine, ensure_schema
from .routers import (admin, auction, auth, bidder, collusion, files, health, l2, notifications,
                          officer, orgs, pipeline, watch)
from .routers.auth import limiter as rate_limiter

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

# create all tables on startup (alembic migrations available for production)
Base.metadata.create_all(bind=engine)
# heal databases created by earlier builds (adds any missing columns in place)
ensure_schema()

app = FastAPI(
    title=settings.APP_NAME,
    tagline=settings.APP_TAGLINE,
    version="2.1.0",
    description="SIH26100 — AI-Powered Integrated Bid Compliance Verification Platform for GeM Procurement (Ministry of P&NG / petroleum-sector procuring entities). Decision support system: the deterministic rule engine decides; AI only extracts and explains.",
)


@app.middleware("http")
async def _request_context(request: Request, call_next):
    """Assign a correlation id to every request (audit evidence, logs)."""
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:20]
    request.state.request_id = request_id
    request.state.session_id = None  # set by auth dependency on demand
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https://.*e2b\.app$|http://localhost(:\d+)?|http://127\.0\.0\.1(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Compress large JSON/PDF/PNG responses (reports, replicas) to cut bandwidth
# and latency under load.
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.state.limiter = rate_limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.include_router(auth.router)
app.include_router(orgs.router)
app.include_router(bidder.router)
app.include_router(officer.router)
app.include_router(l2.router)
app.include_router(admin.router)
app.include_router(notifications.router)
app.include_router(files.router)
app.include_router(collusion.router)
app.include_router(health.router)
app.include_router(watch.router)
app.include_router(pipeline.router, prefix="/officer")
app.include_router(pipeline.bidder_router, prefix="/bidder")
app.include_router(auction.router, prefix="/officer")
app.include_router(auction.bidder_router, prefix="/bidder")


# ---------------------------------------------------------------------------
# SENTINEL WATCH scheduler — continuous compliance monitoring (spec §8).
# A short, jittered tick keeps the demo reactive; production uses
# per-attribute cadence with backoff. Each tick opens its own DB session.
# ---------------------------------------------------------------------------
def _watch_tick_job() -> None:
    if not settings.WATCH_ENABLED:
        return
    from .core.db import SessionLocal
    from .services import watch as watch_svc

    db = SessionLocal()
    try:
        watch_svc.run_tick(db)
    except Exception:
        db.rollback()
        logging.getLogger("gem.watch").exception("watch scheduler tick failed")
    finally:
        db.close()


def _deadline_close_job() -> None:
    """Auto-close tenders at their bid deadline (spec §6).

    Bids are sealed by deadline computation at all times; this job makes the
    lifecycle state explicit (PUBLISHED -> CLOSED) and writes a system audit
    entry so the close is evidenced, not implicit.
    """
    from .audit import append_audit
    from .core.bids import effective_bid_deadline
    from .core.db import SessionLocal, utcnow
    from .models import Tender

    db = SessionLocal()
    try:
        now = utcnow()
        candidates = db.query(Tender).filter(Tender.status.in_(["DRAFT", "PUBLISHED"])).all()
        closed = 0
        for t in candidates:
            if t.force_opened:
                continue  # admin force-open override — auto-close must not revert it
            dl = effective_bid_deadline(t)
            if dl is not None and now >= dl:
                t.status = "CLOSED"
                closed += 1
                append_audit(
                    db, "tender_auto_closed", "Tender", t.id,
                    actor_id="system", actor_role="SYSTEM",
                    payload={"tender_no": t.tender_no, "deadline": dl.isoformat()},
                    commit=False,
                )
        if closed:
            db.commit()
            logging.getLogger("gem.deadline").info("auto-closed %d tender(s) at deadline", closed)
    except Exception:
        db.rollback()
        logging.getLogger("gem.deadline").exception("deadline close job failed")
    finally:
        db.close()


def _auction_close_job() -> None:
    """Server-authoritative auction close: any live auction whose effective
    end time has passed is closed and its ranking computed (atomic)."""
    if not (settings.ENABLE_REVERSE_AUCTION or settings.ENABLE_FORWARD_AUCTION):
        return
    from .core.db import SessionLocal
    from .services.auction import auto_close_due

    db = SessionLocal()
    try:
        n = auto_close_due(db)
        if n:
            logging.getLogger("gem.auction").info("auto-closed %d auction(s)", n)
    except Exception:
        db.rollback()
        logging.getLogger("gem.auction").exception("auction auto-close failed")
    finally:
        db.close()


_watch_scheduler = None
if settings.WATCH_ENABLED:
    try:
        from apscheduler.schedulers.background import BackgroundScheduler

        _watch_scheduler = BackgroundScheduler(daemon=True)
        _watch_scheduler.add_job(
            _watch_tick_job,
            "interval",
            seconds=max(5, settings.WATCH_TICK_SECONDS),
            jitter=3,
            id="sentinel-watch-tick",
            max_instances=1,
            coalesce=True,
        )
        _watch_scheduler.add_job(
            _deadline_close_job,
            "interval",
            seconds=60,
            id="tender-deadline-close",
            max_instances=1,
            coalesce=True,
        )
        if settings.ENABLE_REVERSE_AUCTION or settings.ENABLE_FORWARD_AUCTION:
            _watch_scheduler.add_job(
                _auction_close_job,
                "interval",
                seconds=10,
                id="auction-auto-close",
                max_instances=1,
                coalesce=True,
            )
        _watch_scheduler.start()
        logging.getLogger("gem.watch").info(
            "Sentinel Watch scheduler started (tick %ss + jitter)", settings.WATCH_TICK_SECONDS
        )
    except Exception:
        logging.getLogger("gem.watch").exception(
            "Sentinel Watch scheduler failed to start — /watch/run-now still works"
        )
