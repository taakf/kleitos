"""Phase 9T — Recommended action dismiss/read state routes.

Thin API surface for operators to mark recommended actions as
read or dismissed, and for the frontend to fetch effective
(visibility-filtered) actions.  All state is portfolio-scoped
and fingerprint-aware so materially changed actions reappear.

Routes:

  GET  /api/v1/actions/effective   → filtered visible + hidden count
  POST /api/v1/actions/set-state   → mark one action read/dismissed
  POST /api/v1/actions/read-all    → mark all currently visible read
  POST /api/v1/actions/clear-state → re-surface a previously handled action
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
from src.database.models import ActionState, AuditLog
from src.intelligence.actions import (
    compute_action_fingerprint,
    filter_actions_by_state,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/actions", tags=["actions"])


# ---------------------------------------------------------------------------
# Response / request models
# ---------------------------------------------------------------------------


class EffectiveActionsResponse(BaseModel):
    portfolio_id: str
    visible: list[dict[str, Any]]
    hidden_count: int
    total: int


class SetStateRequest(BaseModel):
    portfolio_id: str = Field(..., min_length=1, max_length=128)
    action_key: str = Field(..., min_length=1, max_length=256)
    state: str = Field(..., pattern=r"^(read|dismissed)$")
    fingerprint: str = Field(..., min_length=1, max_length=64)


class SetStateResponse(BaseModel):
    action_key: str
    state: str
    fingerprint: str
    portfolio_id: str


class ReadAllRequest(BaseModel):
    portfolio_id: str = Field(..., min_length=1, max_length=128)


class ReadAllResponse(BaseModel):
    portfolio_id: str
    marked: int


class ClearStateRequest(BaseModel):
    portfolio_id: str = Field(..., min_length=1, max_length=128)
    action_key: str = Field(..., min_length=1, max_length=256)


class ClearStateResponse(BaseModel):
    action_key: str
    portfolio_id: str
    cleared: bool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _fetch_handled_states(
    session: AsyncSession, portfolio_id: str,
) -> dict[str, tuple[str, str]]:
    """Return the current handled states for a portfolio as a dict
    keyed by ``action_key`` → ``(state, fingerprint)``."""
    stmt = select(ActionState).where(
        ActionState.portfolio_id == portfolio_id,
    )
    rows = (await session.execute(stmt)).scalars().all()
    return {r.action_key: (r.state, r.fingerprint) for r in rows}


async def _build_raw_actions(
    session: AsyncSession, portfolio_id: str,
) -> list[dict[str, Any]]:
    """Build the raw (unfiltered) recommended actions for a portfolio
    by driving the intelligence summary builder.  Falls back to an
    empty list on any error so the route always returns a valid
    response."""
    try:
        from src.intelligence.summary import build_intelligence_summary
        summary = await build_intelligence_summary(
            session, portfolio_id=portfolio_id,
        )
        return list(summary.recommended_actions or [])
    except Exception as exc:
        logger.warning(
            "action_state: summary build failed for %s: %s",
            portfolio_id, exc,
        )
        return []


async def _audit_action_state(
    session: AsyncSession,
    *,
    portfolio_id: str,
    action_key: str,
    action: str,
    old_state: str | None,
    new_state: str | None,
    fingerprint: str,
) -> None:
    """Write a compact audit row for an action-state change."""
    now = datetime.now(timezone.utc).isoformat()
    session.add(AuditLog(
        id=str(uuid.uuid4()),
        entity_type="recommended_action_state",
        entity_id=action_key,
        action=action,
        old_value=json.dumps({"state": old_state}) if old_state else None,
        new_value=json.dumps({
            "state": new_state,
            "fingerprint": fingerprint,
            "portfolio_id": portfolio_id,
        }),
        agent_id="operator",
        user_id="operator",
        reason=None,
        created_at=now,
    ))


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/effective", response_model=EffectiveActionsResponse)
async def get_effective_actions(
    portfolio_id: str = Query(..., min_length=1, max_length=128),
    session: AsyncSession = Depends(get_session),
) -> EffectiveActionsResponse:
    """Return the recommended actions for a portfolio split into
    visible (unhandled / materially changed) and a hidden count.

    The frontend uses this to render the overview card with only
    the visible actions and a compact "N handled actions hidden"
    footer.
    """
    raw = await _build_raw_actions(session, portfolio_id)
    handled = await _fetch_handled_states(session, portfolio_id)
    visible, hidden = filter_actions_by_state(raw, handled)
    return EffectiveActionsResponse(
        portfolio_id=portfolio_id,
        visible=visible,
        hidden_count=len(hidden),
        total=len(raw),
    )


@router.post("/set-state", response_model=SetStateResponse)
async def set_action_state(
    payload: SetStateRequest,
    session: AsyncSession = Depends(get_session),
) -> SetStateResponse:
    """Mark a single recommended action as read or dismissed.

    Idempotent — calling it twice with the same key + state is a
    no-op update (the timestamp refreshes but no duplicate row is
    created thanks to the unique constraint).
    """
    now = datetime.now(timezone.utc).isoformat()

    existing = (await session.execute(
        select(ActionState).where(
            ActionState.portfolio_id == payload.portfolio_id,
            ActionState.action_key == payload.action_key,
        )
    )).scalars().first()

    old_state = existing.state if existing else None

    if existing is not None:
        existing.state = payload.state
        existing.fingerprint = payload.fingerprint
        existing.updated_at = now
    else:
        session.add(ActionState(
            id=str(uuid.uuid4()),
            portfolio_id=payload.portfolio_id,
            action_key=payload.action_key,
            state=payload.state,
            fingerprint=payload.fingerprint,
            updated_at=now,
            created_at=now,
        ))

    await _audit_action_state(
        session,
        portfolio_id=payload.portfolio_id,
        action_key=payload.action_key,
        action=f"set_{payload.state}",
        old_state=old_state,
        new_state=payload.state,
        fingerprint=payload.fingerprint,
    )
    await session.commit()

    return SetStateResponse(
        action_key=payload.action_key,
        state=payload.state,
        fingerprint=payload.fingerprint,
        portfolio_id=payload.portfolio_id,
    )


@router.post("/read-all", response_model=ReadAllResponse)
async def read_all_actions(
    payload: ReadAllRequest,
    session: AsyncSession = Depends(get_session),
) -> ReadAllResponse:
    """Mark every currently visible action as read.

    Rebuilds the action list server-side and only marks actions that
    are currently visible (unhandled or fingerprint-changed).  This
    is bounded by the ``MAX_ACTIONS_PER_CALL`` cap from Phase 9N.
    """
    raw = await _build_raw_actions(session, payload.portfolio_id)
    handled = await _fetch_handled_states(session, payload.portfolio_id)
    visible, _ = filter_actions_by_state(raw, handled)

    now = datetime.now(timezone.utc).isoformat()
    marked = 0
    for action in visible:
        key = action.get("key") or ""
        if not key:
            continue
        fp = action.get("fingerprint") or compute_action_fingerprint(action)

        existing = (await session.execute(
            select(ActionState).where(
                ActionState.portfolio_id == payload.portfolio_id,
                ActionState.action_key == key,
            )
        )).scalars().first()

        if existing is not None:
            existing.state = "read"
            existing.fingerprint = fp
            existing.updated_at = now
        else:
            session.add(ActionState(
                id=str(uuid.uuid4()),
                portfolio_id=payload.portfolio_id,
                action_key=key,
                state="read",
                fingerprint=fp,
                updated_at=now,
                created_at=now,
            ))
        marked += 1

    if marked > 0:
        await _audit_action_state(
            session,
            portfolio_id=payload.portfolio_id,
            action_key="*",
            action="read_all",
            old_state=None,
            new_state="read",
            fingerprint=f"batch:{marked}",
        )
        await session.commit()

    return ReadAllResponse(
        portfolio_id=payload.portfolio_id,
        marked=marked,
    )


@router.post("/clear-state", response_model=ClearStateResponse)
async def clear_action_state(
    payload: ClearStateRequest,
    session: AsyncSession = Depends(get_session),
) -> ClearStateResponse:
    """Remove the handled state for a single action so it reappears
    as new/visible.  Useful when an operator wants to re-surface an
    action they previously dismissed."""
    existing = (await session.execute(
        select(ActionState).where(
            ActionState.portfolio_id == payload.portfolio_id,
            ActionState.action_key == payload.action_key,
        )
    )).scalars().first()

    if existing is None:
        return ClearStateResponse(
            action_key=payload.action_key,
            portfolio_id=payload.portfolio_id,
            cleared=False,
        )

    old_state = existing.state
    old_fp = existing.fingerprint
    await session.delete(existing)
    await _audit_action_state(
        session,
        portfolio_id=payload.portfolio_id,
        action_key=payload.action_key,
        action="clear_state",
        old_state=old_state,
        new_state=None,
        fingerprint=old_fp,
    )
    await session.commit()

    return ClearStateResponse(
        action_key=payload.action_key,
        portfolio_id=payload.portfolio_id,
        cleared=True,
    )
