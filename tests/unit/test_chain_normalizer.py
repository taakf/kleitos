"""Unit tests for the Phase 9B causal-chain normalizer."""

from __future__ import annotations

import json

import pytest

from src.intelligence.chains.normalize import (
    build_chain_for_link,
    normalize_chain_dict,
    normalize_relationship_chain_dict,
)


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------


class TestNormalizeChainDict:
    def test_accepts_valid_phase9a_payload(self):
        payload = {
            "event": {"id": "e1", "title": "Fed hikes"},
            "factor": {
                "key": "interest_rate",
                "direction": "up",
                "magnitude": "major",
                "confidence": 0.95,
                "rationale": ["matched: federal reserve"],
            },
            "holding": {"id": "h1", "ticker": "AAPL", "portfolio_id": "default"},
            "sensitivity": {"value": -0.6, "source": "default", "sector": "technology"},
            "expected_effect": {"direction": "negative", "confidence": 0.29},
        }
        out = normalize_chain_dict(payload)
        assert out is not None
        assert out["factor"]["key"] == "interest_rate"

    def test_accepts_json_string(self):
        payload_str = json.dumps({
            "event": {"id": "e1"},
            "factor": {"key": "oil_energy", "direction": "up", "magnitude": "major"},
            "holding": {"id": "h1"},
        })
        out = normalize_chain_dict(payload_str)
        assert out is not None
        assert out["factor"]["key"] == "oil_energy"

    def test_rejects_none(self):
        assert normalize_chain_dict(None) is None

    def test_rejects_invalid_json(self):
        assert normalize_chain_dict("{not json") is None

    def test_rejects_missing_factor_block(self):
        assert normalize_chain_dict({"holding": {"id": "h1"}}) is None

    def test_rejects_missing_factor_key(self):
        assert normalize_chain_dict({
            "factor": {"direction": "up"},
            "holding": {"id": "h1"},
        }) is None

    def test_rejects_invalid_direction(self):
        assert normalize_chain_dict({
            "factor": {"key": "interest_rate", "direction": "sideways"},
            "holding": {"id": "h1"},
        }) is None

    def test_rejects_invalid_magnitude(self):
        assert normalize_chain_dict({
            "factor": {"key": "interest_rate", "direction": "up", "magnitude": "huge"},
            "holding": {"id": "h1"},
        }) is None

    def test_rejects_invalid_effect_direction(self):
        assert normalize_chain_dict({
            "factor": {"key": "interest_rate", "direction": "up", "magnitude": "major"},
            "holding": {"id": "h1"},
            "expected_effect": {"direction": "catastrophic"},
        }) is None

    def test_non_dict_payload_rejected(self):
        assert normalize_chain_dict(["not", "a", "dict"]) is None
        assert normalize_chain_dict(42) is None


# ---------------------------------------------------------------------------
# build_chain_for_link — per link_type
# ---------------------------------------------------------------------------


def _valid_factor_payload(factor="interest_rate", direction="up", magnitude="major"):
    return json.dumps({
        "event": {"id": "e1", "title": "Fed hikes"},
        "factor": {
            "key": factor,
            "direction": direction,
            "magnitude": magnitude,
            "confidence": 0.92,
            "rationale": ["matched: federal reserve", "parsed: 50 bps"],
        },
        "holding": {"id": "h1", "ticker": "AAPL", "portfolio_id": "default"},
        "sensitivity": {"value": -0.6, "source": "default", "sector": "technology"},
        "expected_effect": {"direction": "negative", "confidence": 0.29},
    })


class TestBuildChainFactor:
    def test_full_factor_chain(self):
        chain = build_chain_for_link(
            link_id="lnk1",
            link_type="macro_factor",
            link_target="h1",
            relevance_score=0.29,
            impact_channel="interest_rate",
            link_source="deterministic_factor",
            channel="interest_rate",
            details_json=_valid_factor_payload(),
            holding_ticker="AAPL",
            holding_portfolio_id="default",
        )
        assert chain["origin"] == "deterministic_factor"
        assert chain["factor_key"] == "interest_rate"
        assert chain["factor_label"] == "Interest Rates"  # from taxonomy
        assert chain["factor_direction"] == "up"
        assert chain["factor_magnitude"] == "major"
        assert chain["factor_confidence"] == pytest.approx(0.92)
        assert chain["sensitivity_value"] == pytest.approx(-0.6)
        assert chain["sensitivity_source"] == "default"
        assert chain["effect_direction"] == "negative"
        assert chain["holding_ticker"] == "AAPL"
        assert chain["holding_portfolio_id"] == "default"
        assert chain["rationale"]
        assert "Interest Rates" in chain["summary"]
        assert "AAPL" in chain["summary"]

    def test_factor_chain_with_malformed_details_json(self):
        """A factor link whose details_json is missing/corrupt must
        still produce a deterministic_factor chain — with null factor
        metadata — rather than crashing."""
        chain = build_chain_for_link(
            link_id="lnk1",
            link_type="macro_factor",
            link_target="h1",
            relevance_score=0.3,
            impact_channel="oil_energy",
            link_source="deterministic_factor",
            channel="oil_energy",
            details_json="{broken json",
            holding_ticker="XOM",
            holding_portfolio_id="default",
        )
        assert chain["origin"] == "deterministic_factor"
        assert chain["factor_key"] == "oil_energy"
        assert chain["factor_direction"] is None
        assert chain["factor_magnitude"] is None
        assert chain["rationale"] == []
        assert "XOM" in chain["summary"]

    def test_factor_chain_rationale_cap(self):
        """Rationale list is capped at 6 items to keep UI tidy."""
        payload = json.dumps({
            "event": {"id": "e1"},
            "factor": {
                "key": "interest_rate",
                "direction": "up",
                "magnitude": "major",
                "confidence": 0.9,
                "rationale": [f"item {i}" for i in range(20)],
            },
            "holding": {"id": "h1"},
        })
        chain = build_chain_for_link(
            link_id="lnk1", link_type="macro_factor",
            link_target="h1", relevance_score=0.3,
            impact_channel="interest_rate", link_source="deterministic_factor",
            channel="interest_rate", details_json=payload,
            holding_ticker="AAPL", holding_portfolio_id="default",
        )
        assert len(chain["rationale"]) == 6


class TestBuildChainDirectMatch:
    def test_ticker_match(self):
        chain = build_chain_for_link(
            link_id="lnk2",
            link_type="ticker_match",
            link_target="h1",
            relevance_score=1.0,
            impact_channel=None,
            link_source=None,
            channel=None,
            details_json=None,
            holding_ticker="AAPL",
            holding_portfolio_id="default",
        )
        assert chain["origin"] == "direct_match"
        assert chain["factor_key"] is None
        assert chain["channel_label"] == "Ticker match"
        assert chain["effect_confidence"] == pytest.approx(1.0)
        assert chain["holding_ticker"] == "AAPL"
        assert "AAPL" in chain["rationale"][0]

    def test_sector_geo_match(self):
        chain = build_chain_for_link(
            link_id="lnk3",
            link_type="sector_geo_match",
            link_target="h2",
            relevance_score=0.4,
            impact_channel=None,
            link_source=None,
            channel=None,
            details_json=None,
            holding_ticker="MSFT",
            holding_portfolio_id="pA",
        )
        assert chain["origin"] == "direct_match"
        assert chain["channel_label"] == "Sector × geography match"
        assert chain["holding_portfolio_id"] == "pA"


class TestBuildChainOther:
    def test_macro_screen(self):
        chain = build_chain_for_link(
            link_id="lnk4",
            link_type="macro_screen",
            link_target="h1",
            relevance_score=0.35,
            impact_channel=None,
            link_source="llm",
            channel=None,
            details_json=None,
            holding_ticker="NVDA",
            holding_portfolio_id="default",
        )
        assert chain["origin"] == "llm_screen"
        assert chain["channel_label"] == "LLM macro screen"
        assert "NVDA" in chain["summary"]

    def test_unknown_link_type(self):
        chain = build_chain_for_link(
            link_id="lnk5",
            link_type="mystery_link",
            link_target="h1",
            relevance_score=0.5,
            impact_channel=None,
            link_source=None,
            channel="whatever",
            details_json=None,
            holding_ticker="???",
            holding_portfolio_id="default",
        )
        assert chain["origin"] == "unknown"
        assert "mystery_link" in chain["summary"]


class TestChainSummaryDeterminism:
    """Given identical inputs, summary + flow must be byte-identical."""

    def test_deterministic_summary(self):
        payload = _valid_factor_payload()
        a = build_chain_for_link(
            link_id="lnk1", link_type="macro_factor",
            link_target="h1", relevance_score=0.3,
            impact_channel="interest_rate", link_source="deterministic_factor",
            channel="interest_rate", details_json=payload,
            holding_ticker="AAPL", holding_portfolio_id="default",
        )
        b = build_chain_for_link(
            link_id="lnk1", link_type="macro_factor",
            link_target="h1", relevance_score=0.3,
            impact_channel="interest_rate", link_source="deterministic_factor",
            channel="interest_rate", details_json=payload,
            holding_ticker="AAPL", holding_portfolio_id="default",
        )
        assert a == b


# ---------------------------------------------------------------------------
# Phase 9D — relationship chain validator + builder
# ---------------------------------------------------------------------------


def _valid_relationship_payload(
    rel_type: str = "supplier",
    related_ticker: str | None = "TSM",
    related_name: str | None = "Taiwan Semiconductor",
    related_entity_key: str | None = None,
    match_type: str = "ticker",
    match_score: float = 0.95,
    strength: float = 0.85,
    effect_direction: str = "unclear",
    confidence: float = 0.4845,
) -> str:
    return json.dumps({
        "event": {"id": "evt-1", "title": "TSMC reports yield issue"},
        "related_entity": {
            "key": related_ticker or related_entity_key or "?",
            "ticker": related_ticker,
            "entity_key": related_entity_key,
            "name": related_name,
            "matched_value": related_ticker or related_name or related_entity_key,
            "match_type": match_type,
            "match_score": match_score,
        },
        "relationship": {
            "id": "rel1",
            "type": rel_type,
            "strength": strength,
        },
        "holding": {"id": "h_aapl", "ticker": "AAPL", "portfolio_id": "default"},
        "expected_effect": {
            "direction": effect_direction,
            "confidence": confidence,
        },
        "rationale": [
            "matched: TSM (via ticker)",
            "Taiwan Semiconductor is a supplier to AAPL",
            "p=0.48",
        ],
    })


class TestNormalizeRelationshipChainDict:
    def test_accepts_valid_payload(self):
        out = normalize_relationship_chain_dict(_valid_relationship_payload())
        assert out is not None
        assert out["relationship"]["type"] == "supplier"

    def test_accepts_json_string_or_dict(self):
        s = _valid_relationship_payload()
        assert normalize_relationship_chain_dict(s) is not None
        assert normalize_relationship_chain_dict(json.loads(s)) is not None

    def test_rejects_missing_relationship_block(self):
        assert normalize_relationship_chain_dict({
            "related_entity": {"key": "X"},
            "holding": {"id": "h1"},
        }) is None

    def test_rejects_missing_related_entity_block(self):
        assert normalize_relationship_chain_dict({
            "relationship": {"type": "supplier"},
            "holding": {"id": "h1"},
        }) is None

    def test_rejects_missing_holding_id(self):
        assert normalize_relationship_chain_dict({
            "related_entity": {"key": "X"},
            "relationship": {"type": "supplier"},
            "holding": {},
        }) is None

    def test_rejects_invalid_effect_direction(self):
        assert normalize_relationship_chain_dict({
            "related_entity": {"key": "X"},
            "relationship": {"type": "supplier"},
            "holding": {"id": "h1"},
            "expected_effect": {"direction": "catastrophic"},
        }) is None

    def test_rejects_non_dict(self):
        assert normalize_relationship_chain_dict(None) is None
        assert normalize_relationship_chain_dict("{broken json") is None
        assert normalize_relationship_chain_dict([1, 2, 3]) is None


class TestBuildChainRelationship:
    def test_full_relationship_chain(self):
        chain = build_chain_for_link(
            link_id="lnk1",
            link_type="relationship",
            link_target="h_aapl",
            relevance_score=0.4845,
            impact_channel="supplier",
            link_source="deterministic_relationship",
            channel="supplier",
            details_json=_valid_relationship_payload(),
            holding_ticker="AAPL",
            holding_portfolio_id="default",
        )
        assert chain["origin"] == "relationship"
        assert chain["channel"] == "supplier"
        assert chain["channel_label"] == "Supplier relationship"
        # No factor block on relationship chains
        assert chain["factor_key"] is None
        assert chain["factor_direction"] is None
        # effect_confidence carries through from the payload
        assert chain["effect_confidence"] == pytest.approx(0.4845)
        assert "AAPL" in chain["summary"]
        assert "Taiwan Semiconductor" in chain["summary"]
        assert chain["rationale"]

    def test_competitor_chain(self):
        chain = build_chain_for_link(
            link_id="lnk2",
            link_type="relationship",
            link_target="h_nvda",
            relevance_score=0.235,
            impact_channel="competitor",
            link_source="deterministic_relationship",
            channel="competitor",
            details_json=_valid_relationship_payload(
                rel_type="competitor",
                related_ticker="AMD",
                related_name="Advanced Micro Devices",
                strength=0.60,
                confidence=0.235,
            ),
            holding_ticker="NVDA",
            holding_portfolio_id="default",
        )
        assert chain["origin"] == "relationship"
        assert chain["channel_label"] == "Competitor"

    def test_regulator_chain_no_ticker(self):
        chain = build_chain_for_link(
            link_id="lnk3",
            link_type="relationship",
            link_target="h_googl",
            relevance_score=0.22,
            impact_channel="regulator",
            link_source="deterministic_relationship",
            channel="regulator",
            details_json=_valid_relationship_payload(
                rel_type="regulator",
                related_ticker=None,
                related_entity_key="doj_us",
                related_name="US Department of Justice",
                strength=0.85,
                match_type="name",
                match_score=0.60,
                confidence=0.22,
            ),
            holding_ticker="GOOGL",
            holding_portfolio_id="default",
        )
        assert chain["origin"] == "relationship"
        assert chain["channel_label"] == "Regulator"
        assert "US Department of Justice" in chain["summary"]

    def test_malformed_relationship_details_degrades_gracefully(self):
        chain = build_chain_for_link(
            link_id="lnk4",
            link_type="relationship",
            link_target="h1",
            relevance_score=0.3,
            impact_channel="supplier",
            link_source="deterministic_relationship",
            channel="supplier",
            details_json="{broken",
            holding_ticker="AAPL",
            holding_portfolio_id="default",
        )
        assert chain["origin"] == "relationship"
        assert chain["channel_label"] == "Supplier relationship"
        # No rationale, null factor fields, but still a chain
        assert chain["rationale"] == []
        assert "AAPL" in chain["summary"]

    def test_rationale_cap(self):
        details = {
            "event": {"id": "e1"},
            "related_entity": {"key": "X", "ticker": "X", "name": "X Corp"},
            "relationship": {"id": "r1", "type": "supplier", "strength": 0.5},
            "holding": {"id": "h1", "ticker": "Y"},
            "expected_effect": {"direction": "unclear", "confidence": 0.3},
            "rationale": [f"item {i}" for i in range(20)],
        }
        chain = build_chain_for_link(
            link_id="lnk5", link_type="relationship", link_target="h1",
            relevance_score=0.3, impact_channel="supplier",
            link_source="deterministic_relationship", channel="supplier",
            details_json=json.dumps(details),
            holding_ticker="Y", holding_portfolio_id="default",
        )
        assert len(chain["rationale"]) == 6
