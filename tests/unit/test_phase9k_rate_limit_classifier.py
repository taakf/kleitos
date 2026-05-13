"""Phase 9K unit tests for the rate-limit bucket classifier.

Pure-function tests: no DB, no HTTP, no live server.  Locks down the
classification contract so a refactor can't silently drop a GET from
the dashboard_read lane into the public lane (which would re-introduce
the Phase 9J 429 regression).
"""

from __future__ import annotations

from src.api.middleware import (
    _LOOPBACK_HOSTS,
    _MUTATION_VERBS,
    _classify_request,
    _limit_for_bucket,
)
from src.config import get_settings


# ---------------------------------------------------------------------------
# Classification contract
# ---------------------------------------------------------------------------


class TestRequestClassification:
    def test_health_is_always_exempt(self):
        assert _classify_request("GET", "127.0.0.1", "/api/v1/health") == "exempt"
        assert _classify_request("GET", "1.2.3.4", "/api/v1/health") == "exempt"
        # Even a POST to health (unusual) is still exempt — the limiter
        # is not the right layer to police wrong methods.
        assert _classify_request("POST", "127.0.0.1", "/api/v1/health") == "exempt"

    def test_non_api_paths_are_exempt(self):
        assert _classify_request("GET", "127.0.0.1", "/dashboard/index.html") == "exempt"
        assert _classify_request("GET", "127.0.0.1", "/") == "exempt"
        assert _classify_request("GET", "1.2.3.4", "/dashboard/css/styles.css") == "exempt"

    def test_loopback_get_is_dashboard_read(self):
        """Every GET from loopback is classified as dashboard_read —
        this is the bucket that used to 429 the dashboard in Phase 9J."""
        assert _classify_request("GET", "127.0.0.1", "/api/v1/portfolio/holdings") == "dashboard_read"
        assert _classify_request("GET", "::1", "/api/v1/intelligence/summary") == "dashboard_read"
        assert _classify_request("GET", "localhost", "/api/v1/events/recent") == "dashboard_read"
        # Starlette's TestClient / httpx.AsyncClient default client host
        # is "testclient" — treat it as loopback so fastapi TestClient
        # tests don't accidentally trip the public bucket.
        assert _classify_request("GET", "testclient", "/api/v1/alerts/active") == "dashboard_read"

    def test_non_loopback_get_is_public(self):
        """A GET from a non-loopback IP falls into the public bucket —
        this is where abuse traffic gets the legacy rpm ceiling."""
        assert _classify_request("GET", "203.0.113.5", "/api/v1/portfolio/holdings") == "public"
        assert _classify_request("GET", "8.8.8.8", "/api/v1/events") == "public"

    def test_every_mutation_verb_is_mutation_bucket(self):
        """Writes ALWAYS land in the mutation bucket, regardless of
        origin.  That's the whole point — a write-flood from any IP
        hits a bounded ceiling even when loopback would otherwise lift
        it into dashboard_read."""
        for verb in _MUTATION_VERBS:
            assert _classify_request(verb, "127.0.0.1", "/api/v1/operator/backfill") == "mutation", verb
            assert _classify_request(verb, "1.2.3.4", "/api/v1/operator/backfill") == "mutation", verb
            assert _classify_request(verb, "::1", "/api/v1/portfolio/holdings") == "mutation", verb

    def test_loopback_hosts_is_stable_set(self):
        """The loopback set must include the three canonical forms +
        httpx TestClient.  A refactor that drops any of these would
        break a whole class of tests."""
        assert "127.0.0.1" in _LOOPBACK_HOSTS
        assert "::1" in _LOOPBACK_HOSTS
        assert "localhost" in _LOOPBACK_HOSTS
        assert "testclient" in _LOOPBACK_HOSTS

    def test_mutation_verbs_is_stable_set(self):
        assert _MUTATION_VERBS == frozenset({"POST", "PUT", "PATCH", "DELETE"})


# ---------------------------------------------------------------------------
# Bucket ceiling resolution
# ---------------------------------------------------------------------------


class TestBucketCeilings:
    def test_default_ceilings(self):
        """Lock in the Phase 9K production-safe defaults.  If anyone
        bumps these in ``src/config.py``, this test fails loudly so
        the change is explicit."""
        s = get_settings()
        assert _limit_for_bucket("dashboard_read", s) == 1_200
        assert _limit_for_bucket("mutation", s) == 240
        assert _limit_for_bucket("public", s) == 100
        assert _limit_for_bucket("exempt", s) == 0

    def test_dashboard_ceiling_is_higher_than_public(self):
        """The whole point of the split: dashboard reads must never
        be bounded by the tight public ceiling."""
        s = get_settings()
        assert _limit_for_bucket("dashboard_read", s) > _limit_for_bucket("public", s)
        assert _limit_for_bucket("dashboard_read", s) > _limit_for_bucket("mutation", s)

    def test_mutation_ceiling_is_tighter_than_dashboard_but_above_public(self):
        """Write-flood guard: tighter than dashboard but still
        allowing realistic operator workflows (9H/9I bulk actions)."""
        s = get_settings()
        assert _limit_for_bucket("mutation", s) < _limit_for_bucket("dashboard_read", s)
        assert _limit_for_bucket("mutation", s) > _limit_for_bucket("public", s)
