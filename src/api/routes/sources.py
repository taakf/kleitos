"""Source management routes for Axion API."""

import os
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_session
from src.config import PROJECT_ROOT
from src.database.models import Source as SourceModel
from src.sources.registry import SourceConfig, SourceRegistry
from src.sources.source_status import (
    SourceHealth,
    build_health,
    summarise_by_status,
)

router = APIRouter(prefix="/api/v1/sources", tags=["sources"])

# Singleton registry — read once per process. The YAML is the static
# allowlist and metadata source; the DB row is the runtime state.
_REGISTRY: SourceRegistry | None = None


def _get_registry() -> SourceRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = SourceRegistry(PROJECT_ROOT / "config" / "sources.yaml")
    return _REGISTRY


def _resolve_source_status(
    cfg: SourceConfig,
    db_row: SourceModel | None,
) -> tuple[str, str | None, str | None]:
    """Compute (status, last_error_code, last_error_message) for a source.

    Decision order:
      1. ``unsupported`` in YAML → status="unsupported".
      2. enabled=false on either side → "disabled".
      3. requires_auth and env var not set → "missing_key".
      4. DB row carries a non-trivial ``last_status`` → propagate it
         (already a Phase 7 normalized status, e.g. ``rate_limited``).
      5. DB row says ``ok`` → "active".
      6. DB row says ``error`` (legacy) → "error".
      7. No DB row yet → "active" if enabled+configured, else "disabled".
    """
    if cfg.unsupported:
        return "unsupported", "parser_missing", None

    # DB row is the runtime state — when a user toggles a source on in
    # the UI, the YAML's ``enabled`` default is no longer relevant.
    if db_row is not None:
        enabled = bool(db_row.enabled)
    else:
        enabled = bool(cfg.enabled)
    if not enabled:
        return "disabled", None, None

    if cfg.requires_auth:
        env_var = cfg.auth_env_var or ""
        if env_var and not os.environ.get(env_var, ""):
            return "missing_key", "missing_key", None

    if db_row is not None and db_row.last_status:
        last = db_row.last_status
        # Map legacy "ok" → normalized "active"; everything else passes
        # through if it's already in the Phase 7 vocabulary, otherwise
        # "error" with the raw string as the error code.
        valid = {
            "active", "disabled", "missing_key", "degraded", "rate_limited",
            "unreachable", "parser_error", "unsupported", "misconfigured", "error",
        }
        if last == "ok":
            return "active", None, None
        if last in valid:
            return last, None, None
        return "error", last, None

    return "active", None, None


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------
class SourceResponse(BaseModel):
    """Data source used by the collection agents."""

    id: str
    name: str
    source_type: str
    domain: str
    url: str | None = None
    enabled: bool
    priority: int
    trust_level: str
    last_fetched_at: str | None
    last_status: str | None
    created_at: str

    model_config = {"from_attributes": True}


class SourceHealthDetail(BaseModel):
    id: str
    name: str
    domain: str
    source_type: str
    enabled: bool
    last_status: str | None
    last_fetched_at: str | None
    requires_auth: bool
    rate_limit_rpm: int


class SourceToggleResponse(BaseModel):
    id: str
    name: str
    enabled: bool


class SourceCreateRequest(BaseModel):
    name: str
    domain: str
    url: str | None = None
    source_type: str
    parser_id: str
    priority: int = 5
    trust_level: str = "standard"
    rate_limit_rpm: int = 10
    requires_auth: bool = False
    auth_type: str | None = None

    @classmethod
    def validate_source(cls, name: str, domain: str, url: str | None) -> list[str]:
        """Return list of validation errors, empty if valid."""
        errors = []
        if not name or not name.strip():
            errors.append("Source name is required.")
        if not domain or not domain.strip():
            errors.append("Domain is required.")
        elif "." not in domain:
            errors.append("Domain must be a valid hostname (e.g. feeds.reuters.com).")
        if url and not url.startswith(("http://", "https://")):
            errors.append("URL must start with http:// or https://.")
        return errors


class SourceUpdateRequest(BaseModel):
    name: str | None = None
    domain: str | None = None
    url: str | None = None
    source_type: str | None = None
    parser_id: str | None = None
    priority: int | None = None
    trust_level: str | None = None
    rate_limit_rpm: int | None = None
    requires_auth: bool | None = None
    auth_type: str | None = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.get("", response_model=list[SourceResponse])
async def list_sources(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> list[SourceResponse]:
    """List all configured data sources with health status."""
    stmt = select(SourceModel).limit(limit).offset(offset)
    rows = (await session.execute(stmt)).scalars().all()
    return [
        SourceResponse(
            id=s.id,
            name=s.name,
            source_type=s.source_type,
            domain=s.domain,
            url=s.url,
            enabled=bool(s.enabled),
            priority=s.priority,
            trust_level=s.trust_level,
            last_fetched_at=s.last_fetched_at,
            last_status=s.last_status,
            created_at=s.created_at,
        )
        for s in rows
    ]


@router.get("/{source_id}/health", response_model=SourceHealthDetail)
async def source_health(
    source_id: str,
    session: AsyncSession = Depends(get_session),
) -> SourceHealthDetail:
    """Check health of a specific source."""
    source = await session.get(SourceModel, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail=f"Source {source_id} not found")
    return SourceHealthDetail(
        id=source.id,
        name=source.name,
        domain=source.domain,
        source_type=source.source_type,
        enabled=bool(source.enabled),
        last_status=source.last_status,
        last_fetched_at=source.last_fetched_at,
        requires_auth=bool(source.requires_auth),
        rate_limit_rpm=source.rate_limit_rpm,
    )


class SourceHealthList(BaseModel):
    """Phase 7 — normalized per-source health for the Settings/Sources UI.

    Joins the YAML metadata (auth_type, required_env_var, parser, notes,
    unsupported flag) with the DB runtime state (enabled toggle,
    last_fetched_at, last_status). Status follows the vocabulary in
    :mod:`src.sources.source_status`.
    """

    sources: list[SourceHealth]
    summary: dict[str, int]


@router.get("/health", response_model=SourceHealthList)
async def all_sources_health(
    session: AsyncSession = Depends(get_session),
) -> SourceHealthList:
    """Return normalized health for every configured source.

    Lists every source declared in ``config/sources.yaml`` regardless of
    whether the DB has caught up yet, plus any DB-only entries. Never
    returns secrets or raw exception text — ``last_error_message`` is
    pre-scrubbed by :func:`src.sources.source_status.build_health`.
    """
    registry = _get_registry()
    db_rows = (await session.execute(select(SourceModel))).scalars().all()
    db_by_id = {r.id: r for r in db_rows}

    healths: list[SourceHealth] = []
    seen: set[str] = set()

    # YAML-declared sources first (preserves the registry order).
    for cfg in registry.get_all_sources():
        seen.add(cfg.id)
        db = db_by_id.get(cfg.id)
        status, err_code, err_msg = _resolve_source_status(cfg, db)
        configured = (
            not cfg.requires_auth
            or bool(os.environ.get(cfg.auth_env_var or "", ""))
        )
        healths.append(build_health(
            id=cfg.id,
            name=cfg.name,
            source_type=cfg.type,
            enabled=bool(db.enabled) if db is not None else bool(cfg.enabled),
            configured=configured,
            status=status,
            parser=cfg.parser,
            auth_type=cfg.auth_type,
            required_env_var=cfg.auth_env_var,
            last_fetch_at=(db.last_fetched_at if db else None),
            last_success_at=(db.last_fetched_at if db and (db.last_status in ("ok", "active")) else None),
            last_error_at=None,
            last_error_code=err_code,
            last_error_message=err_msg,
            events_fetched_last_run=None,
            notes=(cfg.notes or None),
        ))

    # DB-only sources (added at runtime via POST /api/v1/sources).
    for r in db_rows:
        if r.id in seen:
            continue
        configured = (
            not r.requires_auth
            or bool(os.environ.get(getattr(r, "auth_env_var", "") or "", ""))
        )
        # Map the legacy "ok"/"error" tokens to normalized statuses.
        if not bool(r.enabled):
            status = "disabled"
        elif r.last_status == "ok":
            status = "active"
        elif r.last_status in (
            "active", "missing_key", "degraded", "rate_limited",
            "unreachable", "parser_error", "unsupported", "misconfigured", "error",
        ):
            status = r.last_status
        else:
            status = "active"
        healths.append(build_health(
            id=r.id,
            name=r.name,
            source_type=r.source_type,
            enabled=bool(r.enabled),
            configured=configured,
            status=status,
            parser=r.parser_id,
            auth_type=r.auth_type,
            required_env_var=getattr(r, "auth_env_var", None),
            last_fetch_at=r.last_fetched_at,
        ))

    return SourceHealthList(
        sources=healths,
        summary=summarise_by_status(healths),
    )


@router.post("/{source_id}/enable", response_model=SourceToggleResponse)
async def enable_source(
    source_id: str,
    session: AsyncSession = Depends(get_session),
) -> SourceToggleResponse:
    """Enable a data source for collection."""
    source = await session.get(SourceModel, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail=f"Source {source_id} not found")

    source.enabled = 1
    await session.commit()

    return SourceToggleResponse(id=source.id, name=source.name, enabled=True)


@router.post("/{source_id}/disable", response_model=SourceToggleResponse)
async def disable_source(
    source_id: str,
    session: AsyncSession = Depends(get_session),
) -> SourceToggleResponse:
    """Disable a data source to stop collection."""
    source = await session.get(SourceModel, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail=f"Source {source_id} not found")

    source.enabled = 0
    await session.commit()

    return SourceToggleResponse(id=source.id, name=source.name, enabled=False)


@router.post("", response_model=SourceResponse, status_code=201)
async def create_source(
    body: SourceCreateRequest,
    session: AsyncSession = Depends(get_session),
) -> SourceResponse:
    """Create a new data source."""
    errors = SourceCreateRequest.validate_source(body.name, body.domain, body.url)
    if errors:
        raise HTTPException(status_code=422, detail="; ".join(errors))

    source_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    source = SourceModel(
        id=source_id,
        name=body.name,
        domain=body.domain,
        url=body.url,
        source_type=body.source_type,
        parser_id=body.parser_id,
        priority=body.priority,
        trust_level=body.trust_level,
        enabled=1,
        rate_limit_rpm=body.rate_limit_rpm,
        requires_auth=1 if body.requires_auth else 0,
        auth_type=body.auth_type,
        created_at=now,
    )

    session.add(source)
    await session.commit()

    return SourceResponse(
        id=source.id,
        name=source.name,
        source_type=source.source_type,
        domain=source.domain,
        url=source.url,
        enabled=True,
        priority=source.priority,
        trust_level=source.trust_level,
        last_fetched_at=None,
        last_status=None,
        created_at=source.created_at,
    )


@router.put("/{source_id}", response_model=SourceResponse)
async def update_source(
    source_id: str,
    body: SourceUpdateRequest,
    session: AsyncSession = Depends(get_session),
) -> SourceResponse:
    """Update an existing data source configuration."""
    source = await session.get(SourceModel, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail=f"Source {source_id} not found")

    if body.name is not None:
        source.name = body.name
    if body.domain is not None:
        source.domain = body.domain
    if body.url is not None:
        source.url = body.url
    if body.source_type is not None:
        source.source_type = body.source_type
    if body.parser_id is not None:
        source.parser_id = body.parser_id
    if body.priority is not None:
        source.priority = body.priority
    if body.trust_level is not None:
        source.trust_level = body.trust_level
    if body.rate_limit_rpm is not None:
        source.rate_limit_rpm = body.rate_limit_rpm
    if body.requires_auth is not None:
        source.requires_auth = 1 if body.requires_auth else 0
    if body.auth_type is not None:
        source.auth_type = body.auth_type

    await session.commit()

    return SourceResponse(
        id=source.id,
        name=source.name,
        source_type=source.source_type,
        domain=source.domain,
        url=source.url,
        enabled=bool(source.enabled),
        priority=source.priority,
        trust_level=source.trust_level,
        last_fetched_at=source.last_fetched_at,
        last_status=source.last_status,
        created_at=source.created_at,
    )


@router.delete("/{source_id}")
async def delete_source(
    source_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Delete a data source."""
    source = await session.get(SourceModel, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail=f"Source {source_id} not found")

    await session.delete(source)
    await session.commit()

    return {"id": source_id, "message": "Source deleted successfully"}
