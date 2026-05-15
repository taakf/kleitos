"""Phase 9E unit tests for the grounded AI synthesis layer.

Covers every testable contract the Phase 9E brief required:

* prompt builder refuses unsupported invention (grounding contract
  text is present verbatim + constraint lines are explicit)
* explanation prompts include deterministic chain fields
* digest prompts include factor touchpoints
* chat system prompts are portfolio-scoped and carry factor +
  relationship touchpoints
* deterministic fallback for events uses chain data
* deterministic fallback for digests uses factor touchpoints
* deterministic fallback for chat does not invent data
* 'unclear' effect direction is not forced to positive/negative
* rationale / key_factors lists never contain made-up factor keys
"""

from __future__ import annotations

import pytest

from src.llm.grounded import (
    GROUNDING_CONTRACT,
    GroundedChain,
    GroundedChatContext,
    GroundedDigestContext,
    GroundedEventContext,
    GroundedFactorTag,
    build_chat_system_prompt,
    build_digest_prompt,
    build_event_analysis_prompt,
    render_deterministic_chat_answer,
    render_deterministic_digest,
    render_deterministic_explanation,
)


# ---------------------------------------------------------------------------
# Fixtures / builders
# ---------------------------------------------------------------------------


def _factor_tag(key="interest_rate", direction="up", magnitude="major", conf=0.95):
    return GroundedFactorTag(
        key=key,
        label=key.replace("_", " ").title(),
        direction=direction,
        magnitude=magnitude,
        confidence=conf,
    )


def _factor_chain(effect="negative", conf=0.29, direction="up"):
    return GroundedChain(
        origin="deterministic_factor",
        link_type="macro_factor",
        channel="interest_rate",
        channel_label="Interest Rates",
        holding_ticker="AAPL",
        effect_direction=effect,
        effect_confidence=conf,
        rationale_summary="matched: federal reserve; parsed: 50 bps",
        factor_direction=direction,
        factor_magnitude="major",
    )


def _relationship_chain(effect="unclear", conf=0.36):
    return GroundedChain(
        origin="relationship",
        link_type="relationship",
        channel="supplier",
        channel_label="Supplier relationship",
        holding_ticker="AAPL",
        effect_direction=effect,
        effect_confidence=conf,
        rationale_summary="Taiwan Semiconductor is a supplier to AAPL",
        related_entity="Taiwan Semiconductor",
    )


def _event_ctx(**overrides):
    base = dict(
        event_id="e1",
        event_title="Federal Reserve raises interest rates by 50 bps",
        event_summary="FOMC voted to raise rates.",
        event_type="rates",
        holding_id="h_aapl",
        holding_ticker="AAPL",
        holding_portfolio_id="default",
        holding_sector="Information Technology",
        holding_geography="united states",
        holding_themes=["ai", "hardware"],
        factor_tags=[_factor_tag()],
        chains=[_factor_chain()],
    )
    base.update(overrides)
    return GroundedEventContext(**base)


# ---------------------------------------------------------------------------
# GROUNDING CONTRACT presence (every prompt this module builds MUST carry it)
# ---------------------------------------------------------------------------


class TestGroundingContract:
    def test_contract_prohibits_invention(self):
        assert "no new" in GROUNDING_CONTRACT.lower() or \
               "do not introduce new" in GROUNDING_CONTRACT.lower()

    def test_contract_forbids_direction_override(self):
        assert "unclear" in GROUNDING_CONTRACT.lower()
        assert "force" in GROUNDING_CONTRACT.lower()

    def test_contract_requires_insufficient_data_fallback(self):
        assert "insufficient data" in GROUNDING_CONTRACT.lower()


# ---------------------------------------------------------------------------
# Event analysis prompt builder
# ---------------------------------------------------------------------------


class TestBuildEventAnalysisPrompt:
    def test_prompt_includes_grounding_contract(self):
        prompt = build_event_analysis_prompt(_event_ctx())
        assert GROUNDING_CONTRACT.split("\n", 1)[0] in prompt

    def test_prompt_includes_deterministic_factor_block(self):
        prompt = build_event_analysis_prompt(_event_ctx())
        assert "deterministic_factors:" in prompt
        assert "interest_rate" in prompt
        assert "direction=up" in prompt
        assert "magnitude=major" in prompt
        assert "confidence=0.95" in prompt

    def test_prompt_includes_deterministic_chain_block(self):
        prompt = build_event_analysis_prompt(_event_ctx())
        assert "deterministic_chains:" in prompt
        assert "origin=deterministic_factor" in prompt
        assert "effect=negative" in prompt
        assert "confidence=0.29" in prompt

    def test_prompt_includes_holding_identity(self):
        prompt = build_event_analysis_prompt(_event_ctx())
        assert "holding.ticker       : AAPL" in prompt
        assert "holding.portfolio_id : default" in prompt
        assert "Information Technology" in prompt

    def test_prompt_forbids_new_tickers(self):
        prompt = build_event_analysis_prompt(_event_ctx())
        assert "any ticker other than the holding ticker" in prompt.lower()

    def test_prompt_requires_key_factors_from_data(self):
        prompt = build_event_analysis_prompt(_event_ctx())
        assert "deterministic_factors.key" in prompt or "must come only" in prompt.lower()

    def test_empty_chains_reflected_in_prompt(self):
        prompt = build_event_analysis_prompt(_event_ctx(chains=[], factor_tags=[]))
        assert "deterministic_factors: (none" in prompt
        assert "deterministic_chains: (none" in prompt

    def test_prompt_is_deterministic(self):
        """Same input → byte-identical output.  Guards against
        prompt drift across runs."""
        a = build_event_analysis_prompt(_event_ctx())
        b = build_event_analysis_prompt(_event_ctx())
        assert a == b


# ---------------------------------------------------------------------------
# Deterministic explanation fallback
# ---------------------------------------------------------------------------


class TestDeterministicExplanation:
    def test_negative_chain_yields_negative_direction(self):
        out = render_deterministic_explanation(
            _event_ctx(chains=[_factor_chain(effect="negative", conf=0.29)])
        )
        assert out["impact_direction"] == "negative"
        assert out["confidence"] == pytest.approx(0.29)

    def test_positive_chain_yields_positive_direction(self):
        out = render_deterministic_explanation(
            _event_ctx(chains=[_factor_chain(effect="positive", conf=0.35)])
        )
        assert out["impact_direction"] == "positive"

    def test_only_unclear_chains_yield_unclear_direction(self):
        """Phase 9E invariant: if every deterministic chain is
        unclear, the fallback must NOT force a direction."""
        out = render_deterministic_explanation(
            _event_ctx(
                factor_tags=[],
                chains=[
                    _relationship_chain(effect="unclear", conf=0.36),
                    _relationship_chain(effect="unclear", conf=0.30),
                ],
            )
        )
        assert out["impact_direction"] == "unclear"
        assert out["key_factors"] == ["supplier"]

    def test_key_factors_come_only_from_chains(self):
        """The fallback must never invent factor keys — every entry
        in ``key_factors`` comes from either a factor tag or a chain
        channel present in the input context."""
        out = render_deterministic_explanation(_event_ctx())
        for k in out["key_factors"]:
            assert k in {"interest_rate"}

    def test_uncertainty_note_always_present(self):
        out = render_deterministic_explanation(_event_ctx())
        assert out["uncertainty_note"]

    def test_no_chains_no_tags_yields_unclear(self):
        out = render_deterministic_explanation(
            _event_ctx(chains=[], factor_tags=[])
        )
        assert out["impact_direction"] == "unclear"

    def test_magnitude_max_across_tags(self):
        ctx = _event_ctx(factor_tags=[
            _factor_tag(key="inflation", direction="up", magnitude="moderate", conf=0.6),
            _factor_tag(key="interest_rate", direction="up", magnitude="extreme", conf=0.9),
        ])
        out = render_deterministic_explanation(ctx)
        # extreme → high level
        assert out["impact_magnitude"] == "high"


# ---------------------------------------------------------------------------
# Digest prompt builder
# ---------------------------------------------------------------------------


def _digest_ctx(**overrides):
    base = dict(
        period="daily",
        portfolio_id="default",
        notes=[
            {
                "ticker": "AAPL",
                "impact_direction": "negative",
                "impact_magnitude": "medium",
                "materiality": "important",
                "thesis_impact": "low",
                "earnings_impact": "medium",
                "risk_impact": "medium",
                "short_term_outlook": "Rate pressure on valuation.",
            },
        ],
        factor_touchpoints=[
            {
                "factor": "interest_rate",
                "label": "Interest Rates",
                "factor_direction": "up",
                "max_magnitude": "major",
                "max_link_relevance": 0.29,
                "affected_tickers": ["AAPL", "MSFT"],
            },
        ],
    )
    base.update(overrides)
    return GroundedDigestContext(**base)


class TestBuildDigestPrompt:
    def test_prompt_has_grounding_contract(self):
        prompt = build_digest_prompt(_digest_ctx())
        assert "GROUNDING CONTRACT" in prompt

    def test_prompt_includes_factor_touchpoints(self):
        prompt = build_digest_prompt(_digest_ctx())
        assert "deterministic_factor_touchpoints:" in prompt
        assert "interest_rate" in prompt
        assert "AAPL" in prompt and "MSFT" in prompt
        assert "direction=up" in prompt

    def test_prompt_includes_per_holding_notes(self):
        prompt = build_digest_prompt(_digest_ctx())
        assert "per_holding_notes:" in prompt
        assert "Rate pressure on valuation" in prompt

    def test_prompt_forbids_ticker_invention(self):
        prompt = build_digest_prompt(_digest_ctx())
        assert "Every ticker you mention must appear" in prompt
        assert "Every factor you mention must appear" in prompt

    def test_empty_notes_handled(self):
        prompt = build_digest_prompt(_digest_ctx(notes=[]))
        assert "per_holding_notes: (none" in prompt

    def test_empty_touchpoints_handled(self):
        prompt = build_digest_prompt(_digest_ctx(factor_touchpoints=[]))
        assert "deterministic_factor_touchpoints: (none" in prompt


# ---------------------------------------------------------------------------
# Digest deterministic fallback
# ---------------------------------------------------------------------------


class TestDeterministicDigest:
    def test_negative_majority_yields_negative_sentiment(self):
        out = render_deterministic_digest(_digest_ctx())
        assert "negative" in out["portfolio_assessment"].lower()
        assert "AAPL" in out["holdings_requiring_attention"]

    def test_touchpoint_only_digest_produces_headline(self):
        ctx = _digest_ctx(notes=[])
        out = render_deterministic_digest(ctx)
        assert "macro signal" in out["headline"].lower()
        assert out["holdings_requiring_attention"] == []

    def test_empty_digest_no_crash(self):
        out = render_deterministic_digest(_digest_ctx(notes=[], factor_touchpoints=[]))
        assert out["headline"]
        assert out["risk_flags"] == []

    def test_interest_rate_up_triggers_duration_risk(self):
        out = render_deterministic_digest(_digest_ctx())
        assert any("duration risk" in f.lower() or "interest rates" in f.lower()
                   for f in out["risk_flags"])


# ---------------------------------------------------------------------------
# Chat system prompt builder
# ---------------------------------------------------------------------------


def _chat_ctx(portfolio_id="pA", **overrides):
    base = dict(
        portfolio_id=portfolio_id,
        holding_count=3,
        total_value=100000.0,
        sector_count=2,
        currency_count=1,
        holdings=[
            {"ticker": "AAPL", "sector": "IT", "weight_pct": 25.0, "market_value": 25000},
            {"ticker": "MSFT", "sector": "IT", "weight_pct": 25.0, "market_value": 25000},
        ],
        active_alerts=[],
        recent_events=[
            {"title": "Fed raises 50 bps", "materiality": "important", "event_type": "rates"},
        ],
        factor_touchpoints=[
            {
                "factor": "interest_rate", "label": "Interest Rates",
                "direction": "up", "holdings": ["AAPL", "MSFT"],
                "max_relevance": 0.29,
            },
        ],
        relationship_touchpoints=[
            {
                "ticker": "AAPL", "relationship_type": "supplier",
                "related_entity": "Taiwan Semiconductor",
                "portfolio_id": portfolio_id,
                "max_relevance": 0.48,
            },
        ],
        analysis_highlights=[],
    )
    base.update(overrides)
    return GroundedChatContext(**base)


class TestBuildChatSystemPrompt:
    def test_prompt_scopes_to_active_portfolio(self):
        prompt = build_chat_system_prompt(_chat_ctx("pA"))
        assert "Active portfolio: pA" in prompt

    def test_prompt_forbids_out_of_portfolio_tickers(self):
        prompt = build_chat_system_prompt(_chat_ctx("pA"))
        assert "not in the active portfolio" in prompt

    def test_prompt_contains_factor_touchpoints(self):
        prompt = build_chat_system_prompt(_chat_ctx("pA"))
        assert "Interest Rates" in prompt
        assert "AAPL, MSFT" in prompt or "AAPL" in prompt

    def test_prompt_contains_relationship_touchpoints(self):
        prompt = build_chat_system_prompt(_chat_ctx("pA"))
        assert "supplier" in prompt
        assert "Taiwan Semiconductor" in prompt

    def test_prompt_contains_grounding_contract(self):
        prompt = build_chat_system_prompt(_chat_ctx("pA"))
        assert "GROUNDING CONTRACT" in prompt

    def test_prompt_forbids_recommendations(self):
        prompt = build_chat_system_prompt(_chat_ctx("pA"))
        assert "Never recommend" in prompt

    def test_prompt_different_portfolios_are_distinct(self):
        p_a = build_chat_system_prompt(_chat_ctx("pA"))
        p_b = build_chat_system_prompt(_chat_ctx("pB"))
        assert "Active portfolio: pA" in p_a
        assert "Active portfolio: pB" in p_b
        assert p_a != p_b


# ---------------------------------------------------------------------------
# Chat deterministic fallback
# ---------------------------------------------------------------------------


class TestDeterministicChatAnswer:
    def test_fallback_includes_portfolio_id_and_holdings(self):
        ans = render_deterministic_chat_answer(
            _chat_ctx("pA"), "what's in my portfolio?",
        )
        assert "Portfolio pA" in ans
        assert "3 holdings" in ans

    def test_fallback_includes_factor_touchpoints(self):
        ans = render_deterministic_chat_answer(
            _chat_ctx("pA"), "any macro risks?",
        )
        assert "Factor Touchpoints" in ans
        assert "Interest Rates" in ans

    def test_fallback_includes_relationship_touchpoints(self):
        ans = render_deterministic_chat_answer(
            _chat_ctx("pA"), "how are my suppliers?",
        )
        assert "Relationship Touchpoints" in ans
        assert "Taiwan Semiconductor" in ans

    def test_fallback_no_llm_banner_when_llm_unavailable(self):
        ctx = _chat_ctx("pA")
        ctx.llm_available = False
        ans = render_deterministic_chat_answer(ctx, "hello")
        assert "rule-based mode" in ans.lower()

    def test_fallback_does_not_mention_holdings_not_in_context(self):
        """No-hallucination guard: render() only produces content
        derived from the context — so e.g. NVDA must not appear
        unless it is in the context."""
        ctx = _chat_ctx("pA")
        ans = render_deterministic_chat_answer(ctx, "how is NVDA doing?")
        assert "NVDA" not in ans

    def test_fallback_reflects_query_without_inventing(self):
        """Even when the user asks about a specific ticker that's
        out-of-portfolio, the fallback must not invent a claim
        about it."""
        ctx = _chat_ctx("pA")
        ans = render_deterministic_chat_answer(ctx, "what about XYZ?")
        assert "XYZ" not in ans


# ---------------------------------------------------------------------------
# Cross-portfolio leakage guard (pure-layer test; DB guard covered
# by the integration test below)
# ---------------------------------------------------------------------------


class TestCrossPortfolioIsolationPureLayer:
    def test_chat_prompt_for_one_portfolio_does_not_carry_other_portfolio_data(self):
        """Two contexts built from disjoint data must produce prompts
        that do not share holding tickers or factor touchpoints."""
        ctx_a = _chat_ctx(
            "pA",
            holdings=[{"ticker": "AAPL", "sector": "IT", "weight_pct": 50.0}],
            factor_touchpoints=[{
                "factor": "interest_rate", "label": "Interest Rates",
                "direction": "up", "holdings": ["AAPL"],
                "max_relevance": 0.29,
            }],
            relationship_touchpoints=[],
        )
        ctx_b = _chat_ctx(
            "pB",
            holdings=[{"ticker": "XOM", "sector": "Energy", "weight_pct": 50.0}],
            factor_touchpoints=[{
                "factor": "oil_energy", "label": "Oil & Energy",
                "direction": "up", "holdings": ["XOM"],
                "max_relevance": 0.31,
            }],
            relationship_touchpoints=[],
        )
        p_a = build_chat_system_prompt(ctx_a)
        p_b = build_chat_system_prompt(ctx_b)
        assert "AAPL" in p_a and "AAPL" not in p_b
        assert "XOM" in p_b and "XOM" not in p_a
        assert "interest_rate" not in p_b.lower() or "Interest Rates" not in p_b
        assert "oil_energy" not in p_a.lower() and "Oil & Energy" not in p_a
