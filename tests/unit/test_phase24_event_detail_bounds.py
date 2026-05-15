"""Phase 24 — event-detail query-bound regression tests.

The ``GET /api/v1/events/{event_id}`` handler used to run two
unbounded sub-queries:

* related analysis notes — ``WHERE event_id = ?`` with no ``LIMIT``;
* related alerts — an unindexed ``related_events LIKE '%event_id%'``
  scan with no ``LIMIT``.

Phase 24 caps both at ``EVENT_DETAIL_RELATED_LIMIT`` (200) and adds two
purely-additive boolean fields to ``EventDetailResponse``
(``related_analyses_truncated`` / ``related_alerts_truncated``) so a
caller knows when the list is a most-recent-first slice.

These tests seed an event with more than the cap's worth of related
rows in a throwaway temp DB and assert the response is bounded, flags
the truncation, and stays un-truncated for small datasets. They also
re-confirm the other ``events`` list endpoints keep their existing
``limit`` caps (unchanged by Phase 24).
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import uuid
from datetime import datetime, timedelta, timezone

import pytest


# ─────────────────────────────────────────────────────────────────────
# TestClient on a throwaway temp DB
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def client():
    prior_db = os.environ.get("KLEITOS_DB_PATH")
    prior_data = os.environ.get("KLEITOS_DATA_DIR")
    prior_log = os.environ.get("KLEITOS_LOG_LEVEL")

    tmp_dir = tempfile.mkdtemp(prefix="axion_phase24_")
    os.environ["KLEITOS_DB_PATH"] = os.path.join(tmp_dir, "test_phase24.db")
    os.environ["KLEITOS_DATA_DIR"] = tmp_dir
    os.environ["KLEITOS_LOG_LEVEL"] = "WARNING"

    from src.config import get_settings
    get_settings.cache_clear()
    settings = get_settings()
    settings.api.auth_enabled = False

    import src.database.connection as connection
    connection._engine = None
    connection._session_factory = None

    from fastapi.testclient import TestClient
    from src.main import app

    with TestClient(app, raise_server_exceptions=False) as tc:
        yield tc

    for key, prior in (
        ("KLEITOS_DB_PATH", prior_db),
        ("KLEITOS_DATA_DIR", prior_data),
        ("KLEITOS_LOG_LEVEL", prior_log),
    ):
        if prior is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = prior
    get_settings.cache_clear()
    connection._engine = None
    connection._session_factory = None


# ─────────────────────────────────────────────────────────────────────
# Seeding helpers
# ─────────────────────────────────────────────────────────────────────


def _seed_event_with_related(event_id: str, note_count: int, alert_count: int):
    """Seed one event plus ``note_count`` analysis notes and
    ``alert_count`` alerts that reference it via ``related_events``."""
    from src.database.connection import get_db
    from src.database.models import Alert, AnalysisNote, Event

    base = datetime(2026, 1, 1, tzinfo=timezone.utc)

    async def _seed():
        async with get_db() as session:
            session.add(Event(
                id=event_id,
                title=f"Phase24 event {event_id}",
                fetched_at=base.isoformat(),
                created_at=base.isoformat(),
            ))
            await session.commit()
        async with get_db() as session:
            notes = []
            for i in range(note_count):
                ts = (base + timedelta(seconds=i)).isoformat()
                notes.append(AnalysisNote(
                    id=str(uuid.uuid4()),
                    event_id=event_id,
                    holding_id=None,
                    note_type="impact",
                    content=f"note {i}",
                    materiality="medium",
                    confidence="medium",
                    agent_id="analysis",
                    created_at=ts,
                ))
            alerts = []
            for i in range(alert_count):
                ts = (base + timedelta(seconds=i)).isoformat()
                alerts.append(Alert(
                    id=str(uuid.uuid4()),
                    portfolio_id=None,
                    alert_type="event_link",
                    severity="info",
                    title=f"alert {i}",
                    body=f"alert body {i}",
                    related_events=json.dumps([event_id]),
                    agent_id="risk",
                    created_at=ts,
                ))
            session.add_all(notes + alerts)
            await session.commit()

    asyncio.run(_seed())


# ─────────────────────────────────────────────────────────────────────
# Constant
# ─────────────────────────────────────────────────────────────────────


class TestConstant:
    def test_related_limit_is_generous(self):
        from src.api.routes.events import EVENT_DETAIL_RELATED_LIMIT
        assert EVENT_DETAIL_RELATED_LIMIT == 200


# ─────────────────────────────────────────────────────────────────────
# Truncation when the related rows exceed the cap
# ─────────────────────────────────────────────────────────────────────


class TestEventDetailTruncation:
    def test_analysis_notes_capped_and_flagged(self, client):
        from src.api.routes.events import EVENT_DETAIL_RELATED_LIMIT
        eid = "ph24_big_notes"
        _seed_event_with_related(eid, note_count=EVENT_DETAIL_RELATED_LIMIT + 5,
                                 alert_count=0)
        r = client.get(f"/api/v1/events/{eid}")
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body["related_analyses"]) == EVENT_DETAIL_RELATED_LIMIT
        assert body["related_analyses_truncated"] is True

    def test_related_alerts_capped_and_flagged(self, client):
        from src.api.routes.events import EVENT_DETAIL_RELATED_LIMIT
        eid = "ph24_big_alerts"
        _seed_event_with_related(eid, note_count=0,
                                 alert_count=EVENT_DETAIL_RELATED_LIMIT + 5)
        r = client.get(f"/api/v1/events/{eid}")
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body["related_alerts"]) == EVENT_DETAIL_RELATED_LIMIT
        assert body["related_alerts_truncated"] is True

    def test_response_never_exceeds_cap(self, client):
        # The capped lists must never come back longer than the cap,
        # whatever the dataset size.
        from src.api.routes.events import EVENT_DETAIL_RELATED_LIMIT
        eid = "ph24_big_both"
        _seed_event_with_related(eid,
                                 note_count=EVENT_DETAIL_RELATED_LIMIT + 50,
                                 alert_count=EVENT_DETAIL_RELATED_LIMIT + 50)
        body = client.get(f"/api/v1/events/{eid}").json()
        assert len(body["related_analyses"]) <= EVENT_DETAIL_RELATED_LIMIT
        assert len(body["related_alerts"]) <= EVENT_DETAIL_RELATED_LIMIT


# ─────────────────────────────────────────────────────────────────────
# No truncation for small datasets
# ─────────────────────────────────────────────────────────────────────


class TestEventDetailNoTruncation:
    def test_small_dataset_not_truncated(self, client):
        eid = "ph24_small"
        _seed_event_with_related(eid, note_count=3, alert_count=3)
        body = client.get(f"/api/v1/events/{eid}").json()
        assert len(body["related_analyses"]) == 3
        assert len(body["related_alerts"]) == 3
        assert body["related_analyses_truncated"] is False
        assert body["related_alerts_truncated"] is False

    def test_event_with_no_related_rows(self, client):
        eid = "ph24_empty"
        _seed_event_with_related(eid, note_count=0, alert_count=0)
        body = client.get(f"/api/v1/events/{eid}").json()
        assert body["related_analyses"] == []
        assert body["related_alerts"] == []
        assert body["related_analyses_truncated"] is False
        assert body["related_alerts_truncated"] is False


# ─────────────────────────────────────────────────────────────────────
# Additive-contract guarantees
# ─────────────────────────────────────────────────────────────────────


class TestAdditiveContract:
    def test_truncation_fields_always_present(self, client):
        # The two flags are always in the payload (additive, defaulted).
        eid = "ph24_contract"
        _seed_event_with_related(eid, note_count=1, alert_count=1)
        body = client.get(f"/api/v1/events/{eid}").json()
        assert "related_analyses_truncated" in body
        assert "related_alerts_truncated" in body
        assert isinstance(body["related_analyses_truncated"], bool)
        assert isinstance(body["related_alerts_truncated"], bool)

    def test_existing_fields_unchanged(self, client):
        # Phase 24 must not remove or rename existing EventDetailResponse
        # fields — the modal contract is preserved.
        eid = "ph24_fields"
        _seed_event_with_related(eid, note_count=2, alert_count=2)
        body = client.get(f"/api/v1/events/{eid}").json()
        for field in ("id", "title", "related_analyses", "related_alerts",
                      "links", "affected_holdings", "factor_tags",
                      "why_it_matters", "explanation_grounded_in"):
            assert field in body, f"existing field {field!r} disappeared"


# ─────────────────────────────────────────────────────────────────────
# Other events list endpoints keep their caps (unchanged by Phase 24)
# ─────────────────────────────────────────────────────────────────────


class TestOtherEndpointsStillCapped:
    def test_events_list_rejects_oversize_limit(self, client):
        # GET /events has limit le=500 — an oversize limit is a 422.
        r = client.get("/api/v1/events", params={"limit": 99999})
        assert r.status_code == 422

    def test_events_list_accepts_capped_limit(self, client):
        r = client.get("/api/v1/events", params={"limit": 500})
        assert r.status_code == 200

    def test_events_recent_rejects_oversize_limit(self, client):
        # GET /events/recent has limit le=100.
        r = client.get("/api/v1/events/recent", params={"limit": 9999})
        assert r.status_code == 422
