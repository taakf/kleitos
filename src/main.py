"""Axion API - FastAPI application entry point."""

import logging
import time

from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from src.api.routes import (
    action_state,
    agents,
    alerts,
    analysis,
    audit,
    chat,
    digests,
    events,
    export,
    health,
    intelligence,
    notifications,
    operator,
    portfolio,
    portfolios,
    saved_views,
    settings,
    sources,
    ws,
)
from src.config import get_settings, PROJECT_ROOT

logger = logging.getLogger("axion")


# ---------------------------------------------------------------------------
# Lifespan (startup / shutdown)
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application startup and shutdown."""
    # --- Startup -----------------------------------------------------------
    logger.info("Axion API starting up ...")

    from src.database.connection import close_database
    from src.database.migrations import run_migrations
    from src.scheduler.jobs import AxionScheduler
    from src.llm.client import close_llm_client

    await run_migrations()
    logger.info("Database initialised.")

    # Sync YAML-configured sources into the sources DB table so the
    # Collection Agent has something to iterate over.
    from src.sources.registry import SourceRegistry
    from src.database.connection import get_db
    from src.database.models import Source
    from sqlalchemy import select
    from datetime import datetime, timezone

    settings = get_settings()

    try:
        sources_yaml = PROJECT_ROOT / "config" / "sources.yaml"
        registry = SourceRegistry(sources_yaml)
        async with get_db() as session:
            existing_ids = set()
            rows = (await session.execute(select(Source.id))).scalars().all()
            existing_ids = set(rows)

            synced = 0
            for src in registry.get_all_sources():
                if src.id not in existing_ids:
                    now = datetime.now(timezone.utc).isoformat()
                    session.add(Source(
                        id=src.id,
                        name=src.name,
                        domain=src.domain,
                        url=src.url,
                        source_type=src.type,
                        parser_id=src.parser,
                        priority=src.priority,
                        trust_level=src.trust_level,
                        enabled=1 if src.enabled else 0,
                        rate_limit_rpm=src.rate_limit_rpm,
                        requires_auth=1 if src.requires_auth else 0,
                        auth_type=src.auth_type,
                        created_at=now,
                    ))
                    synced += 1
            if synced:
                await session.commit()
                logger.info("Synced %d source(s) from config/sources.yaml to database.", synced)
            else:
                logger.info("All %d configured source(s) already present in database.", len(existing_ids))
    except Exception as e:
        logger.warning("Source sync from YAML failed (non-fatal): %s", e)

    # Phase 9D corrective pass: reconcile the repo-managed
    # relationship seed registry into ``holding_relationships`` so
    # the live runtime path can use deterministic relationships out
    # of the box.  Idempotent; safe to run on every boot; preserves
    # manual and ai_inferred rows untouched.
    try:
        from src.intelligence.relationships.reconciler import (
            reconcile_seed_relationships,
        )
        stats = await reconcile_seed_relationships()
        logger.info(
            "Relationship seed reconcile complete: %s", stats.as_dict(),
        )
    except Exception as e:
        logger.warning(
            "Relationship seed reconcile failed (non-fatal): %s", e,
        )

    # Check LLM availability
    api_key = settings.anthropic_api_key.get_secret_value()
    if api_key and api_key.startswith("sk-ant-"):
        app.state.llm_available = True
        logger.info("Anthropic API key configured — LLM features enabled.")
    else:
        app.state.llm_available = False
        if api_key:
            logger.warning("Anthropic API key looks invalid (expected sk-ant-* prefix).")
        else:
            logger.warning("No Anthropic API key — LLM features will use fallback mode.")

    # Start background scheduler
    scheduler = AxionScheduler()
    scheduler.setup(settings.model_dump())
    scheduler.start()
    app.state.scheduler = scheduler
    logger.info("Scheduler started with %d jobs.", len(scheduler.get_jobs_status()))

    # Start Telegram bot if configured
    app.state.telegram_bot = None
    if settings.telegram.enabled and settings.telegram.token:
        try:
            from src.integrations.telegram.bot import start_bot
            from src.integrations.telegram.notifications import start_dispatcher

            bot = await start_bot(
                token=settings.telegram.token,
                chat_ids=settings.telegram.chat_ids or None,
            )
            app.state.telegram_bot = bot
            start_dispatcher()
            logger.info(
                "Telegram bot started. Chat IDs: %s",
                settings.telegram.chat_ids or "ALL",
            )
        except ImportError as e:
            logger.warning(
                "Telegram integration unavailable (install python-telegram-bot): %s", e
            )
        except Exception as e:
            logger.error("Failed to start Telegram bot: %s", e, exc_info=True)
    else:
        logger.info("Telegram bot disabled (set KLEITOS_TELEGRAM_TOKEN env var to enable)")

    logger.info("Axion API startup complete.")
    yield

    # --- Shutdown ----------------------------------------------------------
    logger.info("Axion API shutting down ...")

    # Stop Telegram bot
    if app.state.telegram_bot:
        try:
            from src.integrations.telegram.bot import stop_bot
            from src.integrations.telegram.notifications import stop_dispatcher
            stop_dispatcher()
            await stop_bot()
        except Exception as e:
            logger.warning("Error stopping Telegram bot: %s", e)

    scheduler.stop()
    await close_llm_client()
    await close_database()
    logger.info("Axion API shutdown complete.")


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Axion API",
    version="1.0.0",
    description="Portfolio Intelligence & Hedge Fund Management System",
    lifespan=lifespan,
)

# -- Middleware (must be added BEFORE app starts, not inside lifespan) ------
_settings = get_settings()

from src.api.middleware import ErrorHandlingMiddleware, APIKeyAuthMiddleware, RateLimitMiddleware

# Starlette executes middleware in reverse add order (last added = outermost).
# Desired order: ErrorHandling → CORS → APIKeyAuth → RateLimit (innermost)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(APIKeyAuthMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.api.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(ErrorHandlingMiddleware)


# -- Request-level middleware (logging) ------------------------------------
@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    """Log every request with method, path, status and duration."""
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - start) * 1000
    logger.info(
        "%s %s -> %s (%.1fms)",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
    )
    # Prevent browser caching of dashboard files
    if request.url.path.startswith("/dashboard"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


# -- Static dashboard files ------------------------------------------------
_dashboard_dir = Path(__file__).resolve().parent.parent / "dashboard"
if _dashboard_dir.is_dir():
    app.mount("/dashboard", StaticFiles(directory=str(_dashboard_dir), html=True), name="dashboard")

# -- Routers ---------------------------------------------------------------
app.include_router(health.router)
app.include_router(portfolios.router)
app.include_router(portfolio.router)
app.include_router(events.router)
app.include_router(analysis.router)
app.include_router(alerts.router)
app.include_router(digests.router)
app.include_router(sources.router)
app.include_router(agents.router)
app.include_router(audit.router)
app.include_router(export.router)
app.include_router(settings.router)
app.include_router(ws.router)
app.include_router(chat.router)
app.include_router(intelligence.router)
app.include_router(operator.router)
app.include_router(notifications.router)
app.include_router(action_state.router)
app.include_router(saved_views.router)

# OpenClaw bridge
try:
    from src.integrations.openclaw.bridge import router as openclaw_router
    app.include_router(openclaw_router)
except ImportError:
    logger.info("OpenClaw bridge not available (optional integration)")


# -- Root redirect ---------------------------------------------------------
@app.get("/", include_in_schema=False)
async def root():
    """Redirect root to dashboard."""
    return RedirectResponse(url="/dashboard")


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(name)s | %(levelname)s | %(message)s")
    _run_settings = get_settings()
    uvicorn.run(
        "src.main:app",
        host=_run_settings.api.host,
        port=_run_settings.api.port,
        reload=_run_settings.system.environment == "development",
    )
