"""Source management routes for Axion API."""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_session
from src.database.models import Source as SourceModel

router = APIRouter(prefix="/api/v1/sources", tags=["sources"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------
class SourceResponse(BaseModel):
    """Data source used by the collection agents."""

    id: str
    name: str
    source_type: str
    domain: str
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
    source_type: str
    parser_id: str
    priority: int = 5
    trust_level: str = "standard"
    rate_limit_rpm: int = 10
    requires_auth: bool = False
    auth_type: str | None = None


class SourceUpdateRequest(BaseModel):
    name: str | None = None
    domain: str | None = None
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
    source_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    source = SourceModel(
        id=source_id,
        name=body.name,
        domain=body.domain,
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
