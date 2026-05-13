"""Automatic seed → DB reconciler for the relationship graph (Phase 9D
corrective pass).

The Phase 9D drop shipped a repo-managed YAML seed registry
(``config/relationships.yaml``) and a runtime read path that reads
``holding_relationships`` rows during collection, but it did NOT
ship an automatic bridge between the two.  Operators had to hand-
edit the DB or write ad-hoc upsert scripts for the feature to
actually do anything.  This module closes that gap.

Design
------
Single entry point: ``async def reconcile_seed_relationships(...)``.
Runs at startup via ``src/main.py``'s lifespan hook and is safe to
call repeatedly.  Behavior is deterministic, idempotent, and narrow:

* **Identity**: a seed row is matched to a DB row by the tuple
  ``(holding_id, relationship_type, related_ticker,
  related_entity_key)`` — the same tuple the DB unique constraint
  covers.  Because seeds address holdings by ticker and a ticker
  can live in multiple portfolios, a single YAML row expands into
  one DB row per matching held position.
* **Source scoping**: the reconciler ONLY touches rows where
  ``source = 'seed'``.  Rows with ``source = 'manual'`` or
  ``source = 'ai_inferred'`` are never read, updated, or deleted.
  This is the primary safety rail.
* **Upsert rules**: if a DB row matching the identity tuple exists
  AND its source is ``'seed'``, the reconciler updates only the
  mutable fields (``strength``, ``related_name``, ``description``,
  ``updated_at``) IF at least one has changed.  If no matching
  seed row exists, it inserts a new one with ``source = 'seed'``.
  If a matching row exists with ``source != 'seed'``, the
  reconciler logs a debug message and moves on — operator intent
  (manual or AI-inferred) always wins over seed data.
* **Prune rule**: seed rows that no longer appear in the YAML are
  deleted on the next reconcile pass — but ONLY if they still
  carry ``source = 'seed'``.  This makes YAML authoritative for
  its own namespace without ever touching manual rows.
* **Missing holdings**: seeds referencing a ticker that no
  portfolio holds are silently counted as ``skipped_no_holding``.
  Log line at info level with the count; no warnings per row so
  startup stays quiet on common cases.

Everything is wrapped in try/except at the caller so a broken
YAML cannot take down collection.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.connection import get_db
from src.database.models import Holding, HoldingRelationship
from src.intelligence.relationships.seeds import (
    SeedRelationship,
    load_seed_relationships,
)

logger = logging.getLogger(__name__)


class ReconcileInProgressError(Exception):
    """Raised when ``reconcile_seed_relationships`` is called while a
    prior reconcile is still running in the same process.

    Phase 9K hardening: a process-local asyncio lock protects the
    public reconcile entry point so operator double-clicks or
    simultaneous API requests can't cause two reconcile passes to
    race on the same tables.  Callers catch this and return a
    409/423-style response.
    """


# Process-local in-flight guard.  See the matching primitive in
# ``src/intelligence/backfill.py`` for the full rationale.
_RECONCILE_LOCK: asyncio.Lock = asyncio.Lock()


def is_reconcile_running() -> bool:
    """Return True iff a reconcile is currently in flight in this process."""
    return _RECONCILE_LOCK.locked()


#: The source tag the reconciler writes and owns.  Rows with any
#: other source value are left strictly alone.
_SEED_SOURCE: str = "seed"


@dataclass
class ReconcileStats:
    """Deterministic summary of a reconciliation pass.

    Every counter is mutually exclusive — a given (seed, holding)
    pair lands in exactly one bucket.  Counters are per expanded
    row, not per YAML entry: one YAML row that matches three
    holdings produces three entries across the counters.
    """

    seed_rows_loaded: int = 0
    seed_rows_invalid: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped_no_holding: int = 0
    skipped_manual_row: int = 0
    pruned: int = 0

    def as_dict(self) -> dict:
        return {
            "seed_rows_loaded": self.seed_rows_loaded,
            "seed_rows_invalid": self.seed_rows_invalid,
            "created": self.created,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "skipped_no_holding": self.skipped_no_holding,
            "skipped_manual_row": self.skipped_manual_row,
            "pruned": self.pruned,
        }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def reconcile_seed_relationships(
    *,
    yaml_path: Path | str | None = None,
    session: AsyncSession | None = None,
    prune: bool = True,
) -> ReconcileStats:
    """Reconcile the YAML seed registry into ``holding_relationships``.

    Parameters
    ----------
    yaml_path:
        Optional override for the YAML file path.  Defaults to
        ``config/relationships.yaml`` via the seed loader.
    session:
        Optional caller-provided session.  When None, the reconciler
        opens a fresh session via ``get_db()``.  The caller-session
        path exists for tests that want to run inside an outer
        transaction.
    prune:
        When True (default), seed rows whose identity tuple no
        longer appears in the YAML are deleted.  Only ``source =
        'seed'`` rows are ever pruned — manual / ai_inferred rows
        are immune.  Tests set this to False when they want to
        verify "no delete" invariants in isolation.

    Returns
    -------
    ReconcileStats
        Bucketed summary of the pass.  Safe to log, safe to
        serialize to JSON.

    Raises
    ------
    ReconcileInProgressError
        If a prior reconcile is still running in this process.  Phase
        9K hardening: the public entry point is protected by a
        process-local ``asyncio.Lock`` so double-clicks from the
        operator UI can't race two reconcile passes on the same tables.
    """
    # Phase 9K: in-flight guard.  Non-blocking fail-fast check so a
    # second caller gets a clear error instead of silently waiting on
    # the first caller's lock.
    if _RECONCILE_LOCK.locked():
        raise ReconcileInProgressError(
            "A relationship reconcile is already running in this process. "
            "Wait for it to finish before starting another."
        )
    async with _RECONCILE_LOCK:
        if session is None:
            async with get_db() as owned:
                return await _reconcile_with_session(
                    session=owned, yaml_path=yaml_path, prune=prune,
                )
        return await _reconcile_with_session(
            session=session, yaml_path=yaml_path, prune=prune,
        )


async def _reconcile_with_session(
    *,
    session: AsyncSession,
    yaml_path: Path | str | None,
    prune: bool,
) -> ReconcileStats:
    stats = ReconcileStats()

    seeds = load_seed_relationships(yaml_path)
    stats.seed_rows_loaded = len(seeds)

    # --- 1. Bulk-load active holdings + existing seed rows ---------------
    #
    # We always load existing seed rows, even when ``seeds`` is empty,
    # because an empty YAML is a legitimate way to clear every
    # seed-managed row and the prune pass below must still run.
    holdings_by_ticker = await _load_active_holdings_by_ticker(session)
    existing_by_identity = await _load_existing_seed_rows(session)

    # --- 2. Walk the YAML seeds, compute desired identity tuples --------
    #
    # ``desired`` is the set of identity tuples the reconciler will
    # end up owning after this pass; any existing seed row whose
    # identity isn't in this set gets pruned (when prune=True).
    now_iso = datetime.now(timezone.utc).isoformat()
    desired: set[tuple[str, str, str | None, str | None]] = set()

    for seed in seeds:
        holdings = holdings_by_ticker.get(seed.ticker, [])
        if not holdings:
            stats.skipped_no_holding += 1
            continue

        for holding in holdings:
            identity = (
                holding.id,
                seed.relationship_type,
                seed.related_ticker,
                seed.related_entity_key,
            )
            desired.add(identity)

            existing = existing_by_identity.get(identity)
            if existing is None:
                # Also check for a NON-seed row at the same identity —
                # if present, the reconciler must NOT stomp on it.
                non_seed = await _find_non_seed_row(session, identity)
                if non_seed is not None:
                    stats.skipped_manual_row += 1
                    logger.debug(
                        "reconcile: skipping %s/%s/%s — already held by source=%s",
                        holding.ticker, seed.relationship_type,
                        seed.related_ticker or seed.related_entity_key,
                        non_seed.source,
                    )
                    continue

                session.add(HoldingRelationship(
                    id=str(uuid.uuid4()),
                    holding_id=holding.id,
                    relationship_type=seed.relationship_type,
                    related_ticker=seed.related_ticker,
                    related_entity_key=seed.related_entity_key,
                    related_name=seed.related_name,
                    strength=seed.strength,
                    source=_SEED_SOURCE,
                    description=seed.description,
                    created_at=now_iso,
                    updated_at=now_iso,
                ))
                stats.created += 1
                continue

            # Existing seed row — compare mutable fields and update if
            # anything actually differs.  This preserves idempotency:
            # a reconcile that changes nothing writes nothing.
            if _seed_row_matches_db(seed, existing):
                stats.unchanged += 1
                continue

            existing.strength = seed.strength
            existing.related_name = seed.related_name
            existing.description = seed.description
            existing.updated_at = now_iso
            stats.updated += 1

    # --- 3. Prune seed rows no longer in the YAML -----------------------
    if prune:
        for identity, row in existing_by_identity.items():
            if identity in desired:
                continue
            # Double-check the source tag before deleting.  This is
            # defense-in-depth — ``_load_existing_seed_rows`` already
            # filters by source, but the extra guard here documents
            # the invariant at the point of deletion.
            if row.source != _SEED_SOURCE:
                continue
            await session.delete(row)
            stats.pruned += 1

    await session.commit()

    logger.info(
        "Relationship reconcile: loaded=%d created=%d updated=%d unchanged=%d "
        "skipped_no_holding=%d skipped_manual_row=%d pruned=%d",
        stats.seed_rows_loaded,
        stats.created,
        stats.updated,
        stats.unchanged,
        stats.skipped_no_holding,
        stats.skipped_manual_row,
        stats.pruned,
    )
    return stats


# ---------------------------------------------------------------------------
# Bulk loaders
# ---------------------------------------------------------------------------


async def _load_active_holdings_by_ticker(
    session: AsyncSession,
) -> dict[str, list[Holding]]:
    """Return active holdings grouped by ticker.

    A single ticker can appear in multiple portfolios; every matching
    holding receives its own relationship row so portfolio isolation
    is preserved structurally.
    """
    stmt = select(Holding).where(Holding.status == "active")
    result: dict[str, list[Holding]] = {}
    for h in (await session.execute(stmt)).scalars().all():
        result.setdefault(h.ticker.upper(), []).append(h)
    return result


async def _load_existing_seed_rows(
    session: AsyncSession,
) -> dict[tuple[str, str, str | None, str | None], HoldingRelationship]:
    """Return the full set of ``source='seed'`` rows keyed by identity.

    ONLY seed rows are loaded — manual and AI-inferred rows are
    excluded from the reconciler's view entirely, which means the
    reconciler is structurally incapable of reading, updating, or
    deleting them.
    """
    stmt = select(HoldingRelationship).where(
        HoldingRelationship.source == _SEED_SOURCE
    )
    result: dict[tuple[str, str, str | None, str | None], HoldingRelationship] = {}
    for row in (await session.execute(stmt)).scalars().all():
        identity = (
            row.holding_id,
            row.relationship_type,
            row.related_ticker,
            row.related_entity_key,
        )
        # Defensive: if the DB somehow contains two seed rows at the
        # same identity (possible under SQLite's NULL-distinct UNIQUE
        # semantics when one or both nullable columns are NULL), keep
        # the first and log a warning for the rest.  A later pass can
        # decide whether to prune extras; for now we do not.
        if identity in result:
            logger.warning(
                "reconcile: duplicate seed row detected at identity %s "
                "(keeping id=%s, ignoring id=%s)",
                identity, result[identity].id, row.id,
            )
            continue
        result[identity] = row
    return result


async def _find_non_seed_row(
    session: AsyncSession,
    identity: tuple[str, str, str | None, str | None],
) -> HoldingRelationship | None:
    """Check whether a non-seed row exists at the given identity.

    Used when the reconciler is about to INSERT a fresh seed row —
    we must not collide with an operator-authored manual row at the
    same identity.  Returns the first non-seed row it finds or None.
    """
    holding_id, rel_type, related_ticker, related_entity_key = identity
    stmt = (
        select(HoldingRelationship)
        .where(HoldingRelationship.holding_id == holding_id)
        .where(HoldingRelationship.relationship_type == rel_type)
        .where(HoldingRelationship.source != _SEED_SOURCE)
    )
    # NULL-safe equality for the two nullable identity columns.
    if related_ticker is None:
        stmt = stmt.where(HoldingRelationship.related_ticker.is_(None))
    else:
        stmt = stmt.where(HoldingRelationship.related_ticker == related_ticker)
    if related_entity_key is None:
        stmt = stmt.where(HoldingRelationship.related_entity_key.is_(None))
    else:
        stmt = stmt.where(HoldingRelationship.related_entity_key == related_entity_key)
    return (await session.execute(stmt)).scalars().first()


# ---------------------------------------------------------------------------
# Pure comparison helpers — used by tests + the upsert path
# ---------------------------------------------------------------------------


def _seed_row_matches_db(
    seed: SeedRelationship, row: HoldingRelationship,
) -> bool:
    """Return True when an existing seed row is byte-equivalent to
    the seed's current desired state.  Used to skip writes that
    would be no-ops, keeping the reconciler idempotent at the SQL
    level (not just the logical level).
    """
    # Strength is the numerically-sensitive field — use a tiny
    # tolerance so float round-trips don't trigger spurious updates.
    if abs(float(row.strength or 0.0) - float(seed.strength)) > 1e-9:
        return False
    if (row.related_name or None) != (seed.related_name or None):
        return False
    if (row.description or None) != (seed.description or None):
        return False
    return True
