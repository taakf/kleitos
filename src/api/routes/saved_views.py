"""Phase 9U — Saved analytical views routes.

CRUD surface for named saved views.  Each saved view is a compact
``NavigationTarget``-compatible payload that can be restored via
the existing deep-link pipeline or copied as a shareable hash URL.

Routes:

  GET    /api/v1/views           → list saved views for a portfolio
  POST   /api/v1/views           → create or update a saved view
  DELETE /api/v1/views/{view_id} → delete a saved view
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_session
from src.database.models import SavedView
from src.intelligence.navigation import (
    _KNOWN_SURFACES,
    describe_view,
    validate_filters,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/views", tags=["views"])


# ---------------------------------------------------------------------------
# Response / request models
# ---------------------------------------------------------------------------


class SavedViewResponse(BaseModel):
    id: str
    portfolio_id: str
    name: str
    surface: str
    payload: dict[str, Any]
    #: Phase 9V — compact human-readable summary of what this view restores.
    description: str = ""
    created_at: str
    updated_at: str


class CreateViewRequest(BaseModel):
    portfolio_id: str = Field(..., min_length=1, max_length=128)
    name: str = Field(..., min_length=1, max_length=120)
    surface: str = Field(..., min_length=1, max_length=64)
    payload: dict[str, Any] = Field(default_factory=dict)


class DeleteViewResponse(BaseModel):
    id: str
    deleted: bool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_payload(surface: str, raw: dict[str, Any]) -> dict[str, Any]:
    """Validate + normalize a saved-view payload.

    Ensures:
      * ``surface`` is a known surface
      * ``portfolio_id`` is present
      * ``filters`` only contains approved keys
      * unknown top-level keys are preserved (forward compat) but
        ``filters`` is always cleaned
    """
    if surface not in _KNOWN_SURFACES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown surface: {surface}",
        )

    payload = dict(raw)
    payload["surface"] = surface

    # Validate + strip filters to approved keys only
    subtab = payload.get("subtab")
    raw_filters = payload.get("filters")
    if isinstance(raw_filters, dict):
        clean = validate_filters(surface, subtab, raw_filters)
        if clean:
            payload["filters"] = clean
        else:
            payload.pop("filters", None)

    # Also preserve the legacy single-filter field untouched for
    # backward compat with older deep-link hashes.
    return payload


def _row_to_response(row: SavedView) -> SavedViewResponse:
    try:
        payload = json.loads(row.payload_json)
    except (json.JSONDecodeError, TypeError):
        payload = {}
    return SavedViewResponse(
        id=row.id,
        portfolio_id=row.portfolio_id,
        name=row.name,
        surface=row.surface,
        payload=payload,
        # Phase 9V — compact human-readable description computed from
        # the shared ``describe_view`` labeler.
        description=describe_view(payload),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("", response_model=list[SavedViewResponse])
async def list_saved_views(
    portfolio_id: str = Query(..., min_length=1, max_length=128),
    session: AsyncSession = Depends(get_session),
) -> list[SavedViewResponse]:
    """List all saved views for a portfolio, newest first."""
    stmt = (
        select(SavedView)
        .where(SavedView.portfolio_id == portfolio_id)
        .order_by(SavedView.updated_at.desc())
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [_row_to_response(r) for r in rows]


@router.post("", response_model=SavedViewResponse, status_code=201)
async def create_or_update_view(
    payload: CreateViewRequest,
    session: AsyncSession = Depends(get_session),
) -> SavedViewResponse:
    """Create a new saved view or update an existing one with the
    same name.

    Upsert semantics: if ``(portfolio_id, name)`` already exists the
    payload and timestamps are updated in place.  No duplicate rows.
    """
    clean_payload = _validate_payload(payload.surface, payload.payload)
    # Ensure portfolio_id in the payload matches the request
    clean_payload["portfolio_id"] = payload.portfolio_id
    now = datetime.now(timezone.utc).isoformat()

    existing = (await session.execute(
        select(SavedView).where(
            SavedView.portfolio_id == payload.portfolio_id,
            SavedView.name == payload.name,
        )
    )).scalars().first()

    if existing is not None:
        existing.surface = payload.surface
        existing.payload_json = json.dumps(clean_payload)
        existing.updated_at = now
        row = existing
    else:
        row = SavedView(
            id=str(uuid.uuid4()),
            portfolio_id=payload.portfolio_id,
            name=payload.name,
            surface=payload.surface,
            payload_json=json.dumps(clean_payload),
            created_at=now,
            updated_at=now,
        )
        session.add(row)

    await session.commit()
    return _row_to_response(row)


@router.delete("/{view_id}", response_model=DeleteViewResponse)
async def delete_saved_view(
    view_id: str,
    portfolio_id: str = Query(..., min_length=1, max_length=128),
    session: AsyncSession = Depends(get_session),
) -> DeleteViewResponse:
    """Delete a saved view by id.

    The ``portfolio_id`` query parameter is required to enforce
    portfolio isolation — a caller cannot delete another portfolio's
    views.
    """
    row = await session.get(SavedView, view_id)
    if row is None or row.portfolio_id != portfolio_id:
        raise HTTPException(status_code=404, detail="Saved view not found")

    await session.delete(row)
    await session.commit()
    return DeleteViewResponse(id=view_id, deleted=True)
