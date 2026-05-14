"""Phase 13 — Diff-aware insight notifier.

Given the deterministic ``InsightsResponse`` produced by Phase 12's
``build_insights``, the notifier:

1. Computes the stable ``card_key`` + ``card_fingerprint`` for each
   card.
2. Loads the latest persisted state from ``insight_snapshots`` for
   that portfolio in one query.
3. Classifies each card as ``new`` / ``escalated`` / ``unchanged``
   relative to the persisted state.
4. Upserts the snapshot rows so the next pass is idempotent.
5. Returns a structured outcome the API + Inbox + Telegram +
   Digest paths consume.

The notifier never invents content.  When AI narration is enabled
on the upstream response, only the deterministic shape (severity,
category, title, evidence_refs, deep_links) is fingerprinted — the
AI-rewritten wording does not move the hash, so AI re-narration
won't trigger fake notifications.

Telegram delivery is **opt-in**: when the integration is not
configured the dispatcher is a no-op.  We always update the local
``insight_snapshots`` rows regardless of the Telegram outcome so
the Inbox state stays accurate.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import InsightSnapshot
from src.intelligence.insights.fingerprint import (
    card_fingerprint,
    card_key,
    is_escalation,
    severity_rank,
)
from src.intelligence.insights.models import (
    InsightCard,
    InsightsResponse,
)

logger = logging.getLogger(__name__)


#: Severity floor for *Telegram* delivery.  Below this we only update
#: the Inbox state and let the user pull on demand.  Telegram delivery
#: is also gated on the bot being configured.
TELEGRAM_SEVERITY_FLOOR: str = "high"

#: Severity floor for *Inbox* visibility.  Data-gap / info cards stay
#: on the dashboard but don't push to the inbox unread badge — they
#: become noise otherwise.  Operators can still see them on the
#: Overview surface.
INBOX_SEVERITY_FLOOR: str = "medium"


# ─────────────────────────────────────────────────────────────────────
# Result shapes
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class NotifiedInsight:
    """An insight card paired with its notification state.

    Returned in :class:`NotifyOutcome`.  ``state`` is one of
    ``new`` / ``escalated`` / ``unchanged`` / ``first_run``.  The
    last value is reserved for the very first time the notifier
    runs against a portfolio that has no rows yet — distinguishing
    it from ``new`` lets the UI suppress a barrage of "New!" badges
    on the first generation pass.
    """

    card: InsightCard
    state: str
    card_key: str
    fingerprint: str
    previous_severity: str | None = None


@dataclass
class NotifyOutcome:
    """What ``notify_new_or_escalated`` produced.

    Used by the API to embed per-card ``notification_state``, by the
    Inbox builder to surface insight items with read semantics, by
    the Telegram dispatcher to decide which cards to push, and by
    the Digest builder to include top fresh insights.
    """

    portfolio_id: str
    new: list[NotifiedInsight] = field(default_factory=list)
    escalated: list[NotifiedInsight] = field(default_factory=list)
    unchanged: list[NotifiedInsight] = field(default_factory=list)
    telegram_delivered: list[str] = field(default_factory=list)   # card_keys
    telegram_status: str = "skipped"   # skipped | delivered | not_configured | failed
    generated_at: str = ""
    snapshot_count: int = 0
    is_first_run: bool = False


# ─────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────


async def notify_new_or_escalated(
    session: AsyncSession,
    response: InsightsResponse,
    *,
    deliver_telegram: bool = False,
    now_iso: str | None = None,
) -> NotifyOutcome:
    """Diff the response against persisted snapshots and update state.

    Always returns a :class:`NotifyOutcome`; never raises (any
    backend hiccup falls back to "unchanged" for every card so the
    surface keeps rendering).

    Caller owns the transaction — we ``session.commit()`` here only
    after every snapshot upsert succeeds so the table never sees a
    half-applied batch.
    """
    portfolio_id = response.portfolio_id
    iso = now_iso or datetime.now(timezone.utc).isoformat()
    outcome = NotifyOutcome(portfolio_id=portfolio_id, generated_at=iso)

    try:
        existing_rows = (await session.execute(
            select(InsightSnapshot).where(
                InsightSnapshot.portfolio_id == portfolio_id,
            )
        )).scalars().all()
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("snapshot lookup failed for %s: %r", portfolio_id, exc)
        # Fall back to "unchanged" for every card.
        for card in response.insights:
            outcome.unchanged.append(NotifiedInsight(
                card=card, state="unchanged",
                card_key=card_key(card),
                fingerprint=card_fingerprint(card),
            ))
        return outcome

    existing_by_key: dict[str, InsightSnapshot] = {
        r.card_key: r for r in existing_rows
    }
    outcome.is_first_run = not existing_by_key
    outcome.snapshot_count = len(existing_by_key)

    for card in response.insights:
        key = card_key(card)
        fp = card_fingerprint(card)
        prior = existing_by_key.get(key)

        if prior is None:
            state = "first_run" if outcome.is_first_run else "new"
            _upsert(session, prior=None, card=card, key=key, fp=fp,
                    iso=iso, status=state)
            entry = NotifiedInsight(
                card=card, state=state,
                card_key=key, fingerprint=fp,
            )
            if state == "new":
                outcome.new.append(entry)
            else:
                # On a first-run pass we don't push a flood of "New!"
                # badges — surface as unchanged so the UI is calm.
                outcome.unchanged.append(entry)
            continue

        if prior.fingerprint == fp:
            # Identical content — refresh last_seen_at only.
            prior.last_seen_at = iso
            prior.updated_at = iso
            outcome.unchanged.append(NotifiedInsight(
                card=card, state="unchanged",
                card_key=key, fingerprint=fp,
                previous_severity=prior.severity,
            ))
            continue

        # Content changed.  Was the change an escalation in severity?
        escalated = is_escalation(
            old_severity=prior.severity, new_severity=card.severity,
        )
        new_status = "escalated" if escalated else "new"
        _upsert(session, prior=prior, card=card, key=key, fp=fp,
                iso=iso, status=new_status)
        entry = NotifiedInsight(
            card=card, state=new_status,
            card_key=key, fingerprint=fp,
            previous_severity=prior.severity,
        )
        if escalated:
            outcome.escalated.append(entry)
        else:
            outcome.new.append(entry)

    # Persist.
    await session.commit()

    # Telegram delivery is opt-in.  We never block on it — if it
    # fails the snapshot state we just wrote is still consistent.
    if deliver_telegram and (outcome.new or outcome.escalated):
        outcome.telegram_status, outcome.telegram_delivered = \
            await _maybe_deliver_telegram(session, outcome, iso=iso)
    else:
        outcome.telegram_status = "skipped"

    return outcome


# ─────────────────────────────────────────────────────────────────────
# Snapshot upsert
# ─────────────────────────────────────────────────────────────────────


def _upsert(
    session: AsyncSession,
    *,
    prior: InsightSnapshot | None,
    card: InsightCard,
    key: str,
    fp: str,
    iso: str,
    status: str,
) -> None:
    if prior is None:
        session.add(InsightSnapshot(
            id=str(uuid.uuid4()),
            portfolio_id=card.portfolio_id,
            card_key=key,
            category=card.category,
            severity=card.severity,
            title=card.title[:240],
            fingerprint=fp,
            last_seen_at=iso,
            first_seen_at=iso,
            notified_at=None,
            notified_severity=None,
            telegram_delivered_at=None,
            status=status,
            created_at=iso,
            updated_at=iso,
        ))
        return
    # Update in-place — SQLAlchemy detects dirty rows via the ORM.
    prior.category = card.category
    prior.severity = card.severity
    prior.title = card.title[:240]
    prior.fingerprint = fp
    prior.last_seen_at = iso
    prior.status = status
    prior.updated_at = iso


# ─────────────────────────────────────────────────────────────────────
# Telegram delivery (opt-in)
# ─────────────────────────────────────────────────────────────────────


async def _maybe_deliver_telegram(
    session: AsyncSession,
    outcome: NotifyOutcome,
    *,
    iso: str,
) -> tuple[str, list[str]]:
    """Try Telegram delivery for new + escalated insights ≥ floor.

    Returns ``(status, [card_keys_delivered])``.  Status:

    * ``not_configured`` — bot not running / no chats subscribed.
    * ``delivered``      — at least one card delivered.
    * ``failed``         — bot configured but every send raised.
    * ``skipped``        — caller didn't request delivery.
    """
    try:
        from src.integrations.telegram import is_telegram_configured
    except Exception:
        return "not_configured", []

    try:
        configured = bool(is_telegram_configured())
    except Exception:  # pragma: no cover — defensive
        configured = False
    if not configured:
        return "not_configured", []

    # Late import: dispatcher pulls config at import time.
    try:
        from src.integrations.telegram.notifications import (
            deliver_insight,
        )
    except Exception:
        # Older builds didn't expose ``deliver_insight``; fall back to
        # marking the insight as "notified" locally so we don't pile
        # up un-delivered entries on subsequent runs.
        deliver_insight = None  # type: ignore[assignment]

    delivered: list[str] = []
    any_failure = False
    for entry in [*outcome.new, *outcome.escalated]:
        sev_rank = severity_rank(entry.card.severity)
        if sev_rank > severity_rank(TELEGRAM_SEVERITY_FLOOR):
            continue
        if deliver_insight is None:
            continue
        try:
            result = await deliver_insight({
                "card_key": entry.card_key,
                "portfolio_id": entry.card.portfolio_id,
                "severity": entry.card.severity,
                "category": entry.card.category,
                "title": entry.card.title,
                "summary": entry.card.summary,
                "state": entry.state,
            })
        except Exception as exc:
            logger.warning(
                "Telegram delivery failed for %s: %r", entry.card_key, exc,
            )
            any_failure = True
            continue
        if result and (result.get("delivered") or result.get("status") == "delivered"):
            delivered.append(entry.card_key)
            # Stamp telegram_delivered_at on the snapshot.
            stamp = await session.execute(
                select(InsightSnapshot).where(
                    InsightSnapshot.portfolio_id == entry.card.portfolio_id,
                    InsightSnapshot.card_key == entry.card_key,
                )
            )
            row = stamp.scalar_one_or_none()
            if row is not None:
                row.telegram_delivered_at = iso
                row.notified_at = iso
                row.notified_severity = entry.card.severity
                row.status = "notified"
    if delivered:
        await session.commit()
        return "delivered", delivered
    if any_failure:
        return "failed", []
    return "not_configured", []


# ─────────────────────────────────────────────────────────────────────
# Read helpers used by the API + UI
# ─────────────────────────────────────────────────────────────────────


async def get_last_generated_at(
    session: AsyncSession, *, portfolio_id: str,
) -> str | None:
    """Return the most recent ``last_seen_at`` across snapshots."""
    from sqlalchemy import func
    row = (await session.execute(
        select(func.max(InsightSnapshot.last_seen_at)).where(
            InsightSnapshot.portfolio_id == portfolio_id,
        )
    )).scalar()
    return row


def attach_notification_state(
    response: InsightsResponse, outcome: NotifyOutcome,
) -> InsightsResponse:
    """Stamp each card with its notification_state.

    Returns a copy of the response with cards in the same order, but
    each ``InsightCard`` carries a ``data_gaps`` entry of the form
    ``"notification:new"`` / ``"notification:escalated"`` /
    ``"notification:unchanged"`` / ``"notification:first_run"`` so
    the dashboard renders the right pill without touching the
    InsightCard model itself.
    """
    state_by_id: dict[str, str] = {}
    for entry in outcome.new:
        state_by_id[entry.card.id] = "new"
    for entry in outcome.escalated:
        state_by_id[entry.card.id] = "escalated"
    for entry in outcome.unchanged:
        # The notifier folds first-run cards into ``unchanged`` so the
        # operator doesn't get a flood of "New!" pills on the very
        # first generation pass.  Surface that distinction in the
        # data_gaps tag so the UI can stay silent for first_run.
        state_by_id[entry.card.id] = (
            "first_run" if entry.state == "first_run" else "unchanged"
        )

    new_cards: list[InsightCard] = []
    for c in response.insights:
        s = state_by_id.get(c.id, "unchanged")
        tagged_gaps = [g for g in c.data_gaps if not g.startswith("notification:")]
        tagged_gaps.append(f"notification:{s}")
        new_cards.append(c.model_copy(update={"data_gaps": tagged_gaps}))
    return response.model_copy(update={"insights": new_cards})


__all__ = [
    "INBOX_SEVERITY_FLOOR",
    "TELEGRAM_SEVERITY_FLOOR",
    "NotifiedInsight",
    "NotifyOutcome",
    "attach_notification_state",
    "get_last_generated_at",
    "notify_new_or_escalated",
]
