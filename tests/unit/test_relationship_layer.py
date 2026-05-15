"""Unit tests for the Phase 9D deterministic relationship graph."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.intelligence.relationships.matcher import (
    EntityMatch,
    RelationshipEntityMatcher,
    _significant_tokens,
)
from src.intelligence.relationships.propagation import (
    RELATIONSHIP_LINK_TYPE_WEIGHTS,
    RELATIONSHIP_MAX_CONFIDENCE,
    RELATIONSHIP_MIN_EMIT,
    RelationshipImpact,
    RelationshipPropagator,
    RelationshipRow,
    propagate_relationship_impacts,
)
from src.intelligence.relationships.seeds import (
    RELATIONSHIPS_YAML_PATH,
    SeedRelationship,
    VALID_RELATIONSHIP_TYPES,
    group_by_holding_ticker,
    load_seed_relationships,
)


# ---------------------------------------------------------------------------
# Seed loader
# ---------------------------------------------------------------------------


class TestSeedLoader:
    def test_loads_repo_registry(self):
        seeds = load_seed_relationships()
        # Should be non-empty — the repo ships a baseline registry.
        assert len(seeds) > 0
        # Every loaded row must be a SeedRelationship with a valid type.
        for s in seeds:
            assert isinstance(s, SeedRelationship)
            assert s.relationship_type in VALID_RELATIONSHIP_TYPES
            assert 0.0 <= s.strength <= 1.0
            assert s.ticker == s.ticker.upper()
            # Must have at least one of the two identity keys.
            assert s.related_ticker or s.related_entity_key

    def test_loader_is_idempotent(self):
        a = load_seed_relationships()
        b = load_seed_relationships()
        assert [tuple(sorted(vars(x).items())) for x in a] == [
            tuple(sorted(vars(x).items())) for x in b
        ]

    def test_loader_rejects_nonexistent_path(self, tmp_path):
        result = load_seed_relationships(tmp_path / "nope.yaml")
        assert result == []

    def test_loader_rejects_invalid_yaml(self, tmp_path):
        p = tmp_path / "bad.yaml"
        p.write_text("not: a: valid: yaml: {[[", encoding="utf-8")
        result = load_seed_relationships(p)
        assert result == []

    def test_loader_drops_invalid_rows(self, tmp_path):
        p = tmp_path / "mixed.yaml"
        p.write_text(
            """
version: 1
relationships:
  - ticker: AAPL
    related_ticker: TSM
    type: supplier
    strength: 0.85
  - ticker: AAPL
    related_ticker: BAD
    type: not_a_real_type
    strength: 0.5
  - ticker: AAPL
    type: supplier
    strength: 0.5
  - related_ticker: TSM
    type: supplier
    strength: 0.5
  - ticker: AAPL
    related_ticker: TSM
    type: supplier
    strength: 5.0
""",
            encoding="utf-8",
        )
        result = load_seed_relationships(p)
        assert len(result) == 1
        assert result[0].ticker == "AAPL"
        assert result[0].related_ticker == "TSM"

    def test_group_by_holding_ticker(self):
        seeds = [
            SeedRelationship("AAPL", "supplier", "TSM", None, "Taiwan Semi", 0.85),
            SeedRelationship("NVDA", "supplier", "TSM", None, "Taiwan Semi", 0.85),
            SeedRelationship("AAPL", "regulator", None, "doj_us", "DOJ", 0.40),
        ]
        grouped = group_by_holding_ticker(seeds)
        assert "AAPL" in grouped
        assert "NVDA" in grouped
        assert len(grouped["AAPL"]) == 2
        assert len(grouped["NVDA"]) == 1


# ---------------------------------------------------------------------------
# Matcher
# ---------------------------------------------------------------------------


class TestRelationshipEntityMatcher:
    def setup_method(self):
        self.m = RelationshipEntityMatcher()

    def test_ticker_in_title_beats_body(self):
        matches = self.m.find_matches(
            title="TSM reports strong wafer demand",
            summary="",
            entities=[("TSM", "TSM", "Taiwan Semiconductor")],
            excluded_tickers=set(),
        )
        assert len(matches) == 1
        assert matches[0].match_type == "ticker"
        assert matches[0].match_score >= 0.9

    def test_ticker_in_body_only(self):
        matches = self.m.find_matches(
            title="Semi industry update",
            summary="TSM said capacity is tight at leading edge.",
            entities=[("TSM", "TSM", "Taiwan Semiconductor")],
            excluded_tickers=set(),
        )
        assert len(matches) == 1
        assert matches[0].match_score == pytest.approx(0.85)

    def test_name_match_with_possessive(self):
        matches = self.m.find_matches(
            title="Taiwan Semiconductor's yield issues rattle tech",
            summary="",
            entities=[("TSM", "TSM", "Taiwan Semiconductor")],
            excluded_tickers=set(),
        )
        assert len(matches) == 1
        # Ticker not in text; name match should fire at title tier.
        assert matches[0].match_type in ("name", "ticker")
        assert matches[0].match_score >= 0.70

    def test_excluded_ticker_is_dropped(self):
        """A related entity that's already a held ticker must be
        dropped — direct matching already handles it and we would
        otherwise double-count."""
        matches = self.m.find_matches(
            title="TSM reports yield issue",
            summary="",
            entities=[("TSM", "TSM", "Taiwan Semiconductor")],
            excluded_tickers={"TSM"},
        )
        assert matches == []

    def test_short_ticker_requires_strict_context(self):
        """Short tickers (≤2 chars) must NOT match bare words."""
        matches = self.m.find_matches(
            title="Market update",
            summary="A broad market pullback.",
            entities=[("A", "A", None)],
            excluded_tickers=set(),
        )
        assert matches == []

    def test_short_ticker_cash_tag_matches(self):
        matches = self.m.find_matches(
            title="Agilent $A hits new high",
            summary="",
            entities=[("A", "A", None)],
            excluded_tickers=set(),
        )
        assert len(matches) == 1

    def test_name_too_short_skipped(self):
        """Names shorter than the min length must not match — the
        matcher errs toward precision."""
        matches = self.m.find_matches(
            title="HP reports earnings",
            summary="",
            entities=[("HPQ", "HPQ", "HP")],
            excluded_tickers=set(),
        )
        assert matches == []

    def test_multi_token_fallback_matches_doj(self):
        """The all-significant-tokens fallback must catch
        'Department of Justice' even though the full 'US Department
        of Justice' string doesn't appear verbatim."""
        matches = self.m.find_matches(
            title="Department of Justice opens new antitrust case",
            summary="The DOJ probe targets search ads.",
            entities=[("doj_us", None, "US Department of Justice")],
            excluded_tickers=set(),
        )
        assert len(matches) == 1
        assert matches[0].entity_key == "doj_us"
        assert matches[0].match_type == "name"
        # Fallback scores strictly lower than the exact-name scores.
        assert matches[0].match_score <= 0.70

    def test_best_match_wins_per_entity(self):
        """If both ticker and name match, return the higher-scoring one."""
        matches = self.m.find_matches(
            title="TSM and Taiwan Semiconductor announce expansion",
            summary="",
            entities=[("TSM", "TSM", "Taiwan Semiconductor")],
            excluded_tickers=set(),
        )
        assert len(matches) == 1
        # Ticker match at title scores 0.95, name match scores 0.80.
        assert matches[0].match_type == "ticker"

    def test_empty_text_returns_empty(self):
        matches = self.m.find_matches(
            title="", summary="",
            entities=[("TSM", "TSM", "Taiwan Semiconductor")],
            excluded_tickers=set(),
        )
        assert matches == []

    def test_empty_entities_returns_empty(self):
        matches = self.m.find_matches(
            title="TSM news", summary="", entities=[], excluded_tickers=set(),
        )
        assert matches == []


class TestSignificantTokens:
    def test_strips_stop_words(self):
        assert _significant_tokens("us department of justice") == ["department", "justice"]

    def test_drops_short_tokens(self):
        assert _significant_tokens("the inc co") == []

    def test_keeps_hyphenated_separation(self):
        # Non-letter chars are separators.
        assert "taiwan" in _significant_tokens("taiwan-semiconductor")


# ---------------------------------------------------------------------------
# Propagator
# ---------------------------------------------------------------------------


def _row(
    *, ticker: str = "AAPL", portfolio_id: str = "default",
    relationship_type: str = "supplier", related_ticker: str | None = "TSM",
    related_entity_key: str | None = None,
    related_name: str | None = "Taiwan Semiconductor",
    strength: float = 0.85,
) -> RelationshipRow:
    return RelationshipRow(
        id=f"rel_{ticker}_{relationship_type}_{related_ticker or related_entity_key}",
        holding_id=f"h_{ticker.lower()}_{portfolio_id}",
        ticker=ticker,
        portfolio_id=portfolio_id,
        relationship_type=relationship_type,
        related_ticker=related_ticker,
        related_entity_key=related_entity_key,
        related_name=related_name,
        strength=strength,
        source="seed",
    )


def _match(entity_key: str, score: float = 0.95, match_type: str = "ticker") -> EntityMatch:
    return EntityMatch(
        entity_key=entity_key,
        matched_value=entity_key,
        match_type=match_type,
        match_score=score,
    )


class TestRelationshipPropagator:
    def test_supplier_propagation_happy_path(self):
        impacts = propagate_relationship_impacts(
            entity_matches=[_match("TSM")],
            relationships=[_row()],
        )
        assert len(impacts) == 1
        i = impacts[0]
        assert i.ticker == "AAPL"
        assert i.relationship_type == "supplier"
        # p = 0.95 × 0.85 × 0.80 × 0.75
        assert i.holding_confidence == pytest.approx(0.4845)
        assert i.effect_direction == "unclear"
        assert i.rationale

    def test_competitor_propagation_happy_path(self):
        impacts = propagate_relationship_impacts(
            entity_matches=[_match("AMD")],
            relationships=[_row(
                ticker="NVDA", relationship_type="competitor",
                related_ticker="AMD", related_name="Advanced Micro Devices",
                strength=0.60,
            )],
        )
        assert len(impacts) == 1
        # 0.95 × 0.60 × 0.55 × 0.75 = 0.2351
        assert impacts[0].holding_confidence == pytest.approx(0.2351, rel=1e-3)
        assert impacts[0].holding_confidence >= RELATIONSHIP_MIN_EMIT

    def test_regulator_propagation_low_confidence(self):
        impacts = propagate_relationship_impacts(
            entity_matches=[_match("doj_us", score=0.60, match_type="name")],
            relationships=[_row(
                ticker="GOOGL", relationship_type="regulator",
                related_ticker=None, related_entity_key="doj_us",
                related_name="US Department of Justice", strength=0.85,
            )],
        )
        assert len(impacts) == 1
        # 0.60 × 0.85 × 0.50 × 0.75 = 0.1913
        assert impacts[0].holding_confidence == pytest.approx(0.1913, rel=1e-3)

    def test_confidence_ceiling_below_direct_match(self):
        """Even with maxed inputs, relationship confidence must stay
        strictly below the direct-match ceiling (1.0)."""
        # Use parent type (highest weight 0.85) and max match + strength.
        impacts = propagate_relationship_impacts(
            entity_matches=[_match("BIG", score=1.0)],
            relationships=[_row(
                ticker="SUB", relationship_type="parent",
                related_ticker="BIG", related_name="Big Holdings",
                strength=1.0,
            )],
        )
        assert len(impacts) == 1
        # p = 1.0 × 1.0 × 0.85 × 0.75 = 0.6375 -> clamped to 0.60
        assert impacts[0].holding_confidence == RELATIONSHIP_MAX_CONFIDENCE
        assert RELATIONSHIP_MAX_CONFIDENCE < 1.0

    def test_no_match_no_impact(self):
        impacts = propagate_relationship_impacts(
            entity_matches=[],
            relationships=[_row()],
        )
        assert impacts == []

    def test_match_without_relationship_skipped(self):
        impacts = propagate_relationship_impacts(
            entity_matches=[_match("MYSTERY")],
            relationships=[_row()],
        )
        assert impacts == []

    def test_portfolio_isolation_preserved_on_impact(self):
        """Two holdings with the same ticker in different portfolios
        must each carry their own portfolio_id on their impact."""
        impacts = propagate_relationship_impacts(
            entity_matches=[_match("TSM")],
            relationships=[
                _row(ticker="AAPL", portfolio_id="pA"),
                _row(ticker="AAPL", portfolio_id="pB"),
            ],
        )
        assert len(impacts) == 2
        assert {i.portfolio_id for i in impacts} == {"pA", "pB"}
        for i in impacts:
            # Each impact's ticker/portfolio_id matches the original row.
            assert i.ticker == "AAPL"

    def test_details_json_shape(self):
        impacts = propagate_relationship_impacts(
            entity_matches=[_match("TSM")],
            relationships=[_row()],
        )
        assert impacts
        details = impacts[0].to_details_json("evt-1", "Test event")
        assert set(details.keys()) == {
            "event", "related_entity", "relationship",
            "holding", "expected_effect", "rationale",
        }
        assert details["relationship"]["type"] == "supplier"
        assert details["related_entity"]["ticker"] == "TSM"
        assert details["holding"]["ticker"] == "AAPL"
        assert details["expected_effect"]["direction"] == "unclear"

    def test_type_weight_ordering(self):
        """Per-type weights must be strictly ordered:
        parent/subsidiary > supplier > customer > competitor > regulator."""
        w = RELATIONSHIP_LINK_TYPE_WEIGHTS
        assert w["parent"] == w["subsidiary"]
        assert w["parent"] > w["supplier"]
        assert w["supplier"] >= w["customer"]
        assert w["customer"] > w["competitor"]
        assert w["competitor"] > w["regulator"]

    def test_unknown_type_falls_back(self):
        # Unknown type should use the fallback weight (0.50).
        impacts = propagate_relationship_impacts(
            entity_matches=[_match("X")],
            relationships=[_row(
                ticker="Y", relationship_type="mystery",
                related_ticker="X", related_name="Mystery Co",
                strength=0.8,
            )],
        )
        assert len(impacts) == 1
        # 0.95 × 0.80 × 0.50 × 0.75 = 0.285
        assert impacts[0].holding_confidence == pytest.approx(0.285, rel=1e-3)


# ---------------------------------------------------------------------------
# Constants sanity
# ---------------------------------------------------------------------------


class TestRelationshipConstants:
    def test_ceiling_strictly_below_direct(self):
        # Direct-match ceiling is effectively 1.0; we require a clear gap.
        assert RELATIONSHIP_MAX_CONFIDENCE <= 0.70
        assert RELATIONSHIP_MAX_CONFIDENCE > RELATIONSHIP_MIN_EMIT

    def test_min_emit_above_bottom_clamp(self):
        assert RELATIONSHIP_MIN_EMIT >= 0.10
        assert RELATIONSHIP_MIN_EMIT < RELATIONSHIP_MAX_CONFIDENCE

    def test_registry_yaml_path_exists(self):
        assert RELATIONSHIPS_YAML_PATH.exists()
        assert RELATIONSHIPS_YAML_PATH.name == "relationships.yaml"
