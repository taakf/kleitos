"""Event deduplication engine."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)


class DeduplicationEngine:
    """Content-aware deduplication for incoming events.

    Uses a multi-layer approach:
      1. Exact hash match (SHA-256 of source_id + external_id + title)
      2. Near-duplicate detection via normalized title similarity
      3. Time-window grouping (same topic within N hours → cluster)
    """

    def __init__(self, db=None, window_hours: int = 24):
        self._db = db
        self._window_hours = window_hours

    @staticmethod
    def compute_hash(
        title: str,
        url: str = "",
        published_at: str = "",
        *,
        source_id: str = "",
        external_id: str = "",
    ) -> str:
        """Deterministic dedup hash.

        Uses ``title|url|published_at`` as the canonical key, matching
        the hash computed by the collection agent.  The legacy
        ``source_id`` / ``external_id`` params are accepted for
        backwards compatibility but no longer influence the hash.
        """
        raw = f"{title}|{url}|{published_at}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    @staticmethod
    def normalize_title(title: str) -> str:
        """Lowercase, strip punctuation, collapse whitespace."""
        import re
        t = title.lower().strip()
        t = re.sub(r"[^\w\s]", "", t)
        t = re.sub(r"\s+", " ", t)
        return t

    async def is_duplicate(
        self,
        source_id: str,
        external_id: str,
        title: str,
        *,
        url: str = "",
        published_at: str = "",
    ) -> tuple[bool, Optional[str]]:
        """Check if an event is a duplicate.

        Returns:
            (is_dup, existing_event_id)
        """
        dedup_hash = self.compute_hash(title, url, published_at)

        if not self._db:
            return False, None

        # Layer 1: exact hash match
        row = await self._db.fetch_one(
            "SELECT id FROM events WHERE dedup_hash = ?", (dedup_hash,)
        )
        if row:
            logger.debug("Exact duplicate found: %s", dedup_hash[:12])
            return True, row["id"]

        # Layer 2: near-duplicate by normalized title in time window
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=self._window_hours)).isoformat()
        norm = self.normalize_title(title)
        rows = await self._db.fetch_all(
            """SELECT id, title FROM events
               WHERE source_id = ? AND fetched_at >= ?""",
            (source_id, cutoff),
        )
        for r in rows:
            existing_norm = self.normalize_title(r["title"])
            if existing_norm == norm:
                logger.debug("Near-duplicate found: '%s'", title[:40])
                return True, r["id"]

        return False, None

    async def mark_cluster(self, event_ids: list[str], cluster_label: str) -> None:
        """Group related events into a cluster for downstream processing.

        NOTE: The Event model does not currently have a ``cluster_id``
        column.  Until a migration adds it, this method logs the cluster
        intent without writing to the database.
        """
        if not self._db or not event_ids:
            return
        logger.info(
            "Cluster intent: %d events → '%s' (cluster_id column pending migration)",
            len(event_ids), cluster_label,
        )
