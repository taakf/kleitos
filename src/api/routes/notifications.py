"""Phase 9P — Notification Center routes.

Thin wrapper over existing trusted rows: reads alerts, the latest
digest, recent operator audit rows, and the current intelligence
summary's high-priority actions, then pipes everything through
:mod:`src.intelligence.notifications` to produce a sorted inbox.

Persists a small per-portfolio read state via the ``notification_reads``
table (Phase 9P migration v6).  No new scoring math, no new data
sources, no new schema on any existing table.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, case
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_session
from src.database.models import (
    Alert as AlertModel,
    AuditLog,
    Digest as DigestModel,
    NotificationRead,
)
from src.intelligence.notifications import (
    MAX_INBOX_ITEMS,
    OPERATOR_WINDOW_HOURS,
    InboxInputs,
    NotificationItem,
    build_inbox,
    summarise_inbox,
    within_window,
)
from src.intelligence.traceability import (
    _OPERATOR_ENTITY_TYPES,
    select_recent_operator_entries,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class NotificationItemResponse(BaseModel):
    """JSON shape of a single inbox item (mirrors
    :class:`src.intelligence.notifications.NotificationItem`).

    Phase 9Q — ``action_target`` is now a structured navigation
    dict produced by :mod:`src.intelligence.navigation` instead of
    a short string.  The frontend's ``jumpToTarget`` dispatcher is
    the only consumer.
    """

    key: str
    source_type: str
    source_id: str
    portfolio_id: str
    priority: str
    title: str
    body: str
    timestamp: str
    unread: bool
    evidence_refs: list[str] = Field(default_factory=list)
    action_label: str | None = None
    action_target: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class InboxResponse(BaseModel):
    """Top-level inbox response — a list of items plus a summary."""

    portfolio_id: str
    items: list[NotificationItemResponse]
    summary: dict[str, Any]


class MarkReadRequest(BaseModel):
    key: str = Field(..., min_length=1, max_length=256)
    portfolio_id: str = Field(..., min_length=1, max_length=128)


class MarkReadResponse(BaseModel):
    key: str
    portfolio_id: str
    read: bool
    read_at: str


class MarkAllReadRequest(BaseModel):
    portfolio_id: str = Field(..., min_length=1, max_length=128)


class MarkAllReadResponse(BaseModel):
    portfolio_id: str
    marked: int
    read_at: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _fetch_read_keys(
    session: AsyncSession, portfolio_id: str,
) -> frozenset[str]:
    """Return the set of notification keys already marked read for the
    given portfolio.  Scoped strictly to ``portfolio_id`` — there is
    no global read state in Phase 9P."""
    stmt = select(NotificationRead.notification_key).where(
        NotificationRead.portfolio_id == portfolio_id
    )
    rows = (await session.execute(stmt)).scalars().all()
    return frozenset(rows)


async def _fetch_recent_operator_entries(
    session: AsyncSession, portfolio_id: str, *, window_hours: int,
) -> list[dict[str, Any]]:
    """Pull recent operator audit rows and return them already shaped
    by the Phase 9O traceability helper.

    We read a wider window than we surface so the dedupe rule still
    has room to collapse consecutive no-op reconciles.  The inbox
    itself caps the final list at ``MAX_INBOX_ITEMS``.
    """
    # Read the last 50 operator-owned rows — bounded by entity type
    # so we never scan the entire audit log.
    stmt = (
        select(AuditLog)
        .where(AuditLog.entity_type.in_(_OPERATOR_ENTITY_TYPES))
        .order_by(AuditLog.created_at.desc())
        .limit(50)
    )
    audit_rows = (await session.execute(stmt)).scalars().all()

    # Filter by time window first (cheap, pure)
    recent: list[AuditLog] = []
    for r in audit_rows:
        if within_window(r.created_at, hours=window_hours):
            recent.append(r)

    entries = select_recent_operator_entries(recent, limit=50)

    # TraceabilityEntry → dict so the notifications module can read
    # attributes uniformly with the other sources.
    return [e.to_dict() for e in entries]


async def _fetch_alerts(
    session: AsyncSession, portfolio_id: str,
) -> list[dict[str, Any]]:
    """Return recent portfolio alerts as plain dicts.  Severity-first
    ordering so high-severity rows always land in the inbox even when
    a later info alert exists."""
    severity_rank = case(
        (AlertModel.severity == "critical", 0),
        (AlertModel.severity == "high", 1),
        (AlertModel.severity == "warning", 2),
        (AlertModel.severity == "medium", 2),
        (AlertModel.severity == "info", 3),
        (AlertModel.severity == "low", 3),
        else_=9,
    )
    stmt = (
        select(AlertModel)
        .where(AlertModel.portfolio_id == portfolio_id)
        .order_by(severity_rank.asc(), AlertModel.created_at.desc())
        .limit(50)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [
        {
            "id": a.id,
            "severity": a.severity,
            "title": a.title,
            "body": a.body,
            "acknowledged": bool(a.acknowledged),
            "related_events": a.related_events,
            "related_holdings": a.related_holdings,
            "created_at": a.created_at,
            "alert_type": a.alert_type,
        }
        for a in rows
    ]


async def _fetch_latest_digest(
    session: AsyncSession, portfolio_id: str,
) -> list[dict[str, Any]]:
    """Return the single most recent digest for the portfolio as a
    list (empty if no digest exists).  The inbox treats each digest
    as one item, not many."""
    stmt = (
        select(DigestModel)
        .where(DigestModel.portfolio_id == portfolio_id)
        .order_by(DigestModel.created_at.desc())
        .limit(1)
    )
    d = (await session.execute(stmt)).scalars().first()
    if d is None:
        return []
    return [{
        "id": d.id,
        "digest_type": d.digest_type,
        "content": d.content,
        "event_count": d.event_count,
        "alert_count": d.alert_count,
        "holding_count": d.holding_count,
        "created_at": d.created_at,
    }]


async def _fetch_high_priority_actions(
    session: AsyncSession, portfolio_id: str,
) -> list[dict[str, Any]]:
    """Build the intelligence summary and extract high-priority
    recommended actions that are VISIBLE (not handled/dismissed with
    an unchanged fingerprint).

    Phase 9T — the action_states table is the authority for action
    lifecycle.  Only actions whose fingerprint has materially changed
    since the operator last handled them appear in the inbox.
    """
    try:
        from src.intelligence.summary import build_intelligence_summary
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("notifications: summary import failed: %s", exc)
        return []

    try:
        summary = await build_intelligence_summary(
            session, portfolio_id=portfolio_id,
        )
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning(
            "notifications: intelligence summary failed for %s: %s",
            portfolio_id, exc,
        )
        return []

    all_actions = getattr(summary, "recommended_actions", None) or []
    high_actions = [a for a in all_actions if isinstance(a, dict) and a.get("priority") == "high"]

    # Phase 9T — filter by action state (fingerprint-aware)
    try:
        from src.intelligence.actions import filter_actions_by_state
        from src.api.routes.action_state import _fetch_handled_states
        handled = await _fetch_handled_states(session, portfolio_id)
        visible, _ = filter_actions_by_state(high_actions, handled)
        return visible
    except Exception as exc:
        logger.warning("notifications: action-state filter failed: %s", exc)
        return high_actions  # fallback: show all


async def _fetch_recent_insights(
    session: AsyncSession, portfolio_id: str,
) -> list[dict[str, Any]]:
    """Phase 13 — read recent ``new`` / ``escalated`` insight snapshots.

    Each row is paired with the live :class:`InsightCard` body re-
    rendered through the Phase 12 generator so the inbox always
    shows the current title / summary / evidence — never a stale
    copy.  Errors fall through to an empty list so the inbox surface
    never breaks.
    """
    try:
        from src.database.models import InsightSnapshot
        from src.intelligence.insights import build_insights
        from sqlalchemy import select
    except Exception:  # pragma: no cover — defensive
        return []

    try:
        snap_rows = (await session.execute(
            select(InsightSnapshot).where(
                InsightSnapshot.portfolio_id == portfolio_id,
                InsightSnapshot.status.in_(("new", "escalated")),
            ).order_by(InsightSnapshot.last_seen_at.desc()).limit(20)
        )).scalars().all()
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning(
            "notifications: insight snapshots load failed for %s: %s",
            portfolio_id, exc,
        )
        return []
    if not snap_rows:
        return []
    snap_by_key = {r.card_key: r for r in snap_rows}

    try:
        response = await build_insights(
            session, portfolio_id=portfolio_id, limit=60,
        )
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning(
            "notifications: live insights build failed for %s: %s",
            portfolio_id, exc,
        )
        return []

    from src.intelligence.insights.fingerprint import card_key
    out: list[dict[str, Any]] = []
    for card in response.insights:
        key = card_key(card)
        snap = snap_by_key.get(key)
        if snap is None:
            continue
        out.append({
            "card_key": key,
            "state": snap.status,
            "card": card.model_dump(),
            "previous_severity": snap.notified_severity or snap.severity,
        })
    return out


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("", response_model=InboxResponse)
async def get_inbox(
    portfolio_id: str = Query(..., min_length=1, max_length=128),
    session: AsyncSession = Depends(get_session),
) -> InboxResponse:
    """Compose the notification inbox for a portfolio.

    Reads already-trusted rows (alerts, latest digest, recent
    operator audit rows, high-priority recommended actions), pipes
    them through :func:`src.intelligence.notifications.build_inbox`,
    and returns a JSON-safe response with a per-item read flag.
    """
    read_keys = await _fetch_read_keys(session, portfolio_id)
    alerts = await _fetch_alerts(session, portfolio_id)
    digests = await _fetch_latest_digest(session, portfolio_id)
    operator_entries = await _fetch_recent_operator_entries(
        session, portfolio_id, window_hours=OPERATOR_WINDOW_HOURS,
    )
    actions = await _fetch_high_priority_actions(session, portfolio_id)
    insights = await _fetch_recent_insights(session, portfolio_id)

    items = build_inbox(InboxInputs(
        portfolio_id=portfolio_id,
        alerts=alerts,
        digests=digests,
        operator_entries=operator_entries,
        recommended_actions=actions,
        insights=insights,
        read_keys=read_keys,
    ))

    summary = summarise_inbox(items)
    return InboxResponse(
        portfolio_id=portfolio_id,
        items=[NotificationItemResponse(**item.to_dict()) for item in items],
        summary=summary,
    )


@router.post("/mark-read", response_model=MarkReadResponse)
async def mark_read(
    payload: MarkReadRequest,
    session: AsyncSession = Depends(get_session),
) -> MarkReadResponse:
    """Mark a single inbox item read for a portfolio.

    Idempotent — calling it twice is a no-op (the unique constraint
    on (portfolio_id, notification_key) keeps the table clean).
    """
    key = payload.key
    portfolio_id = payload.portfolio_id

    # Parse source_type + source_id out of the key for back-referencing.
    # Keys are always ``<source_type>:<source_id>`` where source_id may
    # itself contain colons (e.g. Phase 9N action keys).
    if ":" not in key:
        raise HTTPException(status_code=400, detail="Invalid notification key")
    source_type, source_id = key.split(":", 1)
    if not source_type or not source_id:
        raise HTTPException(status_code=400, detail="Invalid notification key")

    now = datetime.now(timezone.utc).isoformat()

    # Upsert: check if the row already exists first.
    existing_stmt = select(NotificationRead).where(
        NotificationRead.portfolio_id == portfolio_id,
        NotificationRead.notification_key == key,
    )
    existing = (await session.execute(existing_stmt)).scalars().first()
    if existing is not None:
        return MarkReadResponse(
            key=key,
            portfolio_id=portfolio_id,
            read=True,
            read_at=existing.read_at,
        )

    row = NotificationRead(
        id=str(uuid.uuid4()),
        portfolio_id=portfolio_id,
        notification_key=key,
        source_type=source_type,
        source_id=source_id,
        read_at=now,
        created_at=now,
    )
    session.add(row)
    await session.commit()
    return MarkReadResponse(
        key=key, portfolio_id=portfolio_id, read=True, read_at=now,
    )


@router.post("/mark-all-read", response_model=MarkAllReadResponse)
async def mark_all_read(
    payload: MarkAllReadRequest,
    session: AsyncSession = Depends(get_session),
) -> MarkAllReadResponse:
    """Mark every CURRENTLY VISIBLE inbox item as read for a portfolio.

    Rebuilds the current inbox server-side and inserts a
    ``notification_reads`` row for every unread key that the caller
    would see.  This is both more predictable (the inbox state
    matches what the operator just saw) and keeps the table bounded
    to keys that actually exist.

    Returns the number of rows newly marked read (i.e. excluding
    rows that were already read).
    """
    portfolio_id = payload.portfolio_id

    existing = await _fetch_read_keys(session, portfolio_id)
    alerts = await _fetch_alerts(session, portfolio_id)
    digests = await _fetch_latest_digest(session, portfolio_id)
    operator_entries = await _fetch_recent_operator_entries(
        session, portfolio_id, window_hours=OPERATOR_WINDOW_HOURS,
    )
    actions = await _fetch_high_priority_actions(session, portfolio_id)
    insights = await _fetch_recent_insights(session, portfolio_id)

    items = build_inbox(InboxInputs(
        portfolio_id=portfolio_id,
        alerts=alerts,
        digests=digests,
        operator_entries=operator_entries,
        recommended_actions=actions,
        insights=insights,
        read_keys=existing,
    ))

    now = datetime.now(timezone.utc).isoformat()
    marked = 0
    for item in items:
        if not item.unread:
            continue
        if item.key in existing:
            continue  # defensive — shouldn't happen
        if ":" not in item.key:
            continue
        source_type, source_id = item.key.split(":", 1)
        session.add(NotificationRead(
            id=str(uuid.uuid4()),
            portfolio_id=portfolio_id,
            notification_key=item.key,
            source_type=source_type,
            source_id=source_id,
            read_at=now,
            created_at=now,
        ))
        marked += 1

    if marked > 0:
        await session.commit()

    return MarkAllReadResponse(
        portfolio_id=portfolio_id, marked=marked, read_at=now,
    )
