"""Gold benchmark scenarios for the Phase 9C evaluation harness.

The data below is **synthetic** — hand-authored by the Phase 9C
author to exercise every factor family, every false-positive
guard, and every portfolio-isolation invariant the Phase 9A/9B
pipeline claims to enforce.  It is NOT human-labeled production
data, and any probability targets here are design-time
expectations, not real calibrations.

Each scenario covers one of these families:

    * direct company impact (ticker / company name match)
    * false positives / homonyms / weak-noise headlines
    * interest rates / central bank policy
    * inflation events
    * credit tightening / downgrades
    * oil / energy shocks
    * FX / dollar strength
    * tariffs / sanctions / trade policy
    * geopolitical escalation / de-escalation
    * regulation / policy shocks
    * consumer demand
    * technology cycle
    * multi-factor overlap
    * portfolio isolation (same event, multiple portfolios)

The benchmark is versioned via ``BENCHMARK_VERSION``.  Any change
that adds / removes a scenario or modifies an expected field
MUST bump the version.  Evaluation reports carry the version so
regressions across runs are unambiguous.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

#: Bump on every edit that changes expected fields or scenario
#: inclusion.  Not tied to Axion app version.
#:
#: phase9c.2 — corrective pass: removed known_weakness flags on
#: ``oil.opec_output_cut`` and ``trade.sanctions_lifted`` after the
#: classifier was taught factor-specific direction rules for OPEC
#: supply cuts and trade-policy easing language.
#: phase9d.1 — added relationship family: supplier/customer/
#: competitor/regulator scenarios that exercise the Phase 9D
#: deterministic relationship graph without touching the Phase 9C
#: factor machinery.
BENCHMARK_VERSION: str = "phase9d.1"


Direction = Literal["up", "down", "unknown"]
Magnitude = Literal["minor", "moderate", "major", "extreme", "unknown"]
EffectDirection = Literal["positive", "negative", "unclear"]


@dataclass(frozen=True)
class ExpectedFactor:
    """One factor the classifier is expected (or forbidden) to emit."""

    factor: str
    #: If ``True``, classifier MUST emit this factor.  If ``False``,
    #: classifier MUST NOT emit this factor.
    should_fire: bool = True
    direction: Direction | None = None
    magnitude: Magnitude | None = None
    #: Design-time probability target.  Used for Brier scoring of
    #: classifier confidence.  Synthetic — do not mistake for a
    #: calibrated real-world probability.
    target_confidence: float | None = None
    #: Phase 9C honesty flag.  Set to ``True`` when the synthetic
    #: expectation is known to differ from what the current
    #: deterministic classifier can produce — the harness reports
    #: these separately as ``known_weaknesses`` instead of as
    #: confusing cases, so the evaluation baseline stays stable
    #: AND future regressions / improvements are still detected.
    known_weakness: bool = False
    known_weakness_reason: str = ""


@dataclass(frozen=True)
class ExpectedImpact:
    """A per-holding impact the propagator is expected to produce.

    ``should_emit_link`` is the portfolio-safety bit: when False, the
    propagator must NOT emit a link for this (factor, holding) pair
    even if a sensitivity exists (e.g. a holding in another
    portfolio whose sensitivity is below floor).
    """

    factor: str
    ticker: str
    portfolio_id: str
    should_emit_link: bool = True
    effect_direction: EffectDirection | None = None
    #: Design-time probability for the ``p_holding`` score, used for
    #: synthetic Brier + calibration reliability bins.  Again:
    #: synthetic, NOT calibrated.
    target_confidence: float | None = None


@dataclass(frozen=True)
class SyntheticRelationship:
    """A relationship row the harness feeds directly into the
    Phase 9D propagator, bypassing the DB.  Mirrors the shape of
    ``holding_relationships`` DB rows but carries the holding
    ticker instead of the UUID so scenarios stay readable."""

    ticker: str
    related_ticker: str | None
    related_entity_key: str | None
    related_name: str | None
    relationship_type: str
    strength: float


@dataclass(frozen=True)
class ExpectedRelationshipImpact:
    """A per-holding relationship impact the propagator is expected
    to produce (or suppress) for a scenario."""

    ticker: str
    portfolio_id: str
    relationship_type: str
    #: entity_key the matcher should hit — typically the related
    #: company's ticker ('TSM') or the non-ticker entity_key
    #: ('doj_us').
    related_entity_key: str
    should_emit_link: bool = True
    #: Phase 9D confidence is intentionally bounded below direct
    #: matches; this target is synthetic and used for Brier / sanity
    #: checks only.
    target_confidence: float | None = None


@dataclass(frozen=True)
class SyntheticHolding:
    id: str
    ticker: str
    portfolio_id: str
    sector: str | None = None


@dataclass(frozen=True)
class EvaluationScenario:
    """One gold scenario: an event, a portfolio universe, and
    expected pipeline outputs."""

    #: Stable identifier — used in reports and test parametrization.
    id: str
    #: Human family tag used for grouped reporting.
    family: str
    title: str
    summary: str = ""
    #: Synthetic holdings the propagator sees for this scenario.  The
    #: evaluation harness feeds these directly; the real DB is never
    #: touched.
    holdings: tuple[SyntheticHolding, ...] = field(default_factory=tuple)
    #: Every factor the classifier should or should not emit.
    expected_factors: tuple[ExpectedFactor, ...] = field(default_factory=tuple)
    #: Every (factor, holding) impact the propagator should or should
    #: not produce.
    expected_impacts: tuple[ExpectedImpact, ...] = field(default_factory=tuple)
    #: Phase 9D: relationship rows the harness feeds into the
    #: relationship propagator for this scenario.  When empty the
    #: relationship pipeline is skipped for this scenario.
    relationships: tuple[SyntheticRelationship, ...] = field(default_factory=tuple)
    #: Phase 9D: expected relationship-propagation outputs.
    expected_relationship_impacts: tuple[ExpectedRelationshipImpact, ...] = field(
        default_factory=tuple,
    )
    #: Free-text note that shows up in reports for confusing cases.
    notes: str = ""
    #: If True, this scenario is intentionally skipped — reason in
    #: ``skip_reason``.  Kept in the dataset so reports show it was
    #: considered rather than silently dropped.
    skip: bool = False
    skip_reason: str = ""


# ---------------------------------------------------------------------------
# Reusable holding universes
# ---------------------------------------------------------------------------

_DEFAULT_HOLDINGS: tuple[SyntheticHolding, ...] = (
    SyntheticHolding("h_aapl", "AAPL", "default", "technology"),
    SyntheticHolding("h_msft", "MSFT", "default", "technology"),
    SyntheticHolding("h_xom", "XOM", "default", "energy"),
    SyntheticHolding("h_jpm", "JPM", "default", "financials"),
    SyntheticHolding("h_nesn", "NESN", "default", "consumer staples"),
)

_MULTI_PORTFOLIO_HOLDINGS: tuple[SyntheticHolding, ...] = (
    SyntheticHolding("h_aapl_pA", "AAPL", "pA", "technology"),
    SyntheticHolding("h_xom_pA", "XOM", "pA", "energy"),
    SyntheticHolding("h_msft_pB", "MSFT", "pB", "technology"),
    SyntheticHolding("h_nesn_pB", "NESN", "pB", "consumer staples"),
)


# ---------------------------------------------------------------------------
# Gold scenarios
# ---------------------------------------------------------------------------
#
# Each scenario is deliberately concise: one event, a small set of
# expected facts, and enough context for the report to explain a
# failure.  Families are not exhaustive — the goal is coverage, not
# statistical significance.

GOLD_SCENARIOS: tuple[EvaluationScenario, ...] = (
    # ======================================================================
    # Direct company impact
    # ======================================================================
    EvaluationScenario(
        id="direct.aapl.ticker_headline",
        family="direct_company",
        title="AAPL reports record quarterly earnings",
        summary="Apple beat analyst estimates on iPhone sales.",
        holdings=_DEFAULT_HOLDINGS,
        expected_factors=(
            # This is a direct ticker event; no macro factor should
            # fire — the classifier must stay silent here.
            ExpectedFactor("interest_rate", should_fire=False),
            ExpectedFactor("inflation", should_fire=False),
            ExpectedFactor("oil_energy", should_fire=False),
        ),
        expected_impacts=(),  # direct links are handled outside the classifier
        notes="Direct company news must not trigger macro factors.",
    ),

    # ======================================================================
    # False positives / homonyms
    # ======================================================================
    EvaluationScenario(
        id="fp.apple_orchard",
        family="false_positive",
        title="Apple orchard destroyed by frost in upstate New York",
        summary="Local growers report severe crop losses after an unseasonably cold night.",
        holdings=_DEFAULT_HOLDINGS,
        expected_factors=tuple(
            ExpectedFactor(k, should_fire=False) for k in (
                "interest_rate", "inflation", "credit_conditions",
                "oil_energy", "usd_fx", "trade_policy",
                "geopolitical_risk", "regulation_policy",
                "consumer_demand", "technology_cycle",
            )
        ),
        expected_impacts=(),
        notes="Canonical false-positive guard: 'Apple' the fruit must never classify.",
    ),
    EvaluationScenario(
        id="fp.single_weak_consumer_word",
        family="false_positive",
        title="Consumer confusion over new packaging rollout",
        summary="Shoppers asked about the new look at the store.",
        holdings=_DEFAULT_HOLDINGS,
        expected_factors=(
            ExpectedFactor("consumer_demand", should_fire=False),
        ),
        notes="Single support-tier hit ('consumer') must not classify on its own.",
    ),
    EvaluationScenario(
        id="fp.sports_news",
        family="false_positive",
        title="Local sports team wins championship game",
        summary="Fans celebrated the underdog victory downtown.",
        holdings=_DEFAULT_HOLDINGS,
        expected_factors=tuple(
            ExpectedFactor(k, should_fire=False) for k in (
                "interest_rate", "inflation", "oil_energy",
                "geopolitical_risk", "consumer_demand",
            )
        ),
    ),

    # ======================================================================
    # Interest rates / central bank
    # ======================================================================
    EvaluationScenario(
        id="rates.fed_hike_50bps",
        family="interest_rate",
        title="Federal Reserve raises interest rates by 50 bps",
        summary=(
            "The FOMC voted to raise the federal funds rate by 50 basis "
            "points citing persistent inflation and tight labor markets."
        ),
        holdings=_DEFAULT_HOLDINGS,
        expected_factors=(
            ExpectedFactor(
                "interest_rate", should_fire=True,
                direction="up", magnitude="major",
                target_confidence=0.90,
            ),
            # The summary also names "persistent inflation" — that's a
            # legitimate secondary classification.
            ExpectedFactor(
                "inflation", should_fire=True,
                direction="up", target_confidence=0.55,
            ),
        ),
        expected_impacts=(
            # Tech holdings have -0.6 interest_rate prior -> negative
            ExpectedImpact(
                "interest_rate", "AAPL", "default",
                should_emit_link=True,
                effect_direction="negative",
                target_confidence=0.29,
            ),
            ExpectedImpact(
                "interest_rate", "MSFT", "default",
                should_emit_link=True,
                effect_direction="negative",
                target_confidence=0.29,
            ),
        ),
        notes="Canonical Fed event: interest_rate up, tech negative.",
    ),
    EvaluationScenario(
        id="rates.ecb_cut",
        family="interest_rate",
        title="ECB cuts policy rate by 25 basis points",
        summary=(
            "The European Central Bank lowered its deposit rate by 25 bps "
            "citing easing price pressures and slowing growth."
        ),
        holdings=_DEFAULT_HOLDINGS,
        expected_factors=(
            ExpectedFactor(
                "interest_rate", should_fire=True,
                direction="down", magnitude="moderate",
                target_confidence=0.75,
            ),
        ),
    ),

    # ======================================================================
    # Inflation
    # ======================================================================
    EvaluationScenario(
        id="inflation.cpi_hot",
        family="inflation",
        title="CPI rises 0.8% month-on-month, hotter than expected",
        summary="Core CPI also accelerated on persistent services inflation and wage growth.",
        holdings=_DEFAULT_HOLDINGS,
        expected_factors=(
            ExpectedFactor(
                "inflation", should_fire=True,
                direction="up", magnitude="major",
                target_confidence=0.80,
            ),
        ),
    ),
    EvaluationScenario(
        id="inflation.disinflation",
        family="inflation",
        title="Core PCE falls, disinflation continues",
        summary="Price pressures eased across housing and services.",
        holdings=_DEFAULT_HOLDINGS,
        expected_factors=(
            ExpectedFactor(
                "inflation", should_fire=True,
                direction="down", target_confidence=0.65,
            ),
        ),
    ),

    # ======================================================================
    # Credit conditions
    # ======================================================================
    EvaluationScenario(
        id="credit.spreads_widen",
        family="credit_conditions",
        title="High-yield credit spreads widen sharply amid funding stress",
        summary="Moody's warned of rising defaults across leveraged loans.",
        holdings=_DEFAULT_HOLDINGS,
        expected_factors=(
            ExpectedFactor(
                "credit_conditions", should_fire=True,
                direction="up", target_confidence=0.75,
            ),
        ),
        notes="Credit tightening should emit; financials sector has -0.6 prior.",
    ),

    # ======================================================================
    # Oil / energy
    # ======================================================================
    EvaluationScenario(
        id="oil.pipeline_attack",
        family="oil_energy",
        title="Pipeline attack in Strait of Hormuz sends Brent crude oil surging",
        summary=(
            "A drone strike escalated regional tensions; OPEC+ production "
            "cuts were already in place; WTI jumped 12%."
        ),
        holdings=_DEFAULT_HOLDINGS,
        expected_factors=(
            ExpectedFactor(
                "oil_energy", should_fire=True,
                direction="up", magnitude="major",
                target_confidence=0.85,
            ),
            ExpectedFactor(
                "geopolitical_risk", should_fire=True,
                direction="up", target_confidence=0.70,
            ),
        ),
        expected_impacts=(
            # Energy holding has +0.8 oil prior → positive
            ExpectedImpact(
                "oil_energy", "XOM", "default",
                should_emit_link=True,
                effect_direction="positive",
                target_confidence=0.30,
            ),
        ),
        notes="Multi-factor: oil_energy AND geopolitical_risk; sign check on XOM.",
    ),
    EvaluationScenario(
        id="oil.opec_output_cut",
        family="oil_energy",
        title="OPEC+ announces unexpected crude oil production cut",
        summary="Brent crude prices jumped 5% after the output cut was announced.",
        holdings=_DEFAULT_HOLDINGS,
        expected_factors=(
            ExpectedFactor(
                "oil_energy", should_fire=True,
                direction="up", target_confidence=0.75,
            ),
        ),
        notes="OPEC supply cut — direction fixed by Phase 9C corrective pass.",
    ),

    # ======================================================================
    # USD / FX
    # ======================================================================
    EvaluationScenario(
        id="fx.dollar_surges",
        family="usd_fx",
        title="US dollar index DXY surges to multi-year high as yen weakens",
        summary="The dollar strengthened against the yen on rate differential expectations.",
        holdings=_DEFAULT_HOLDINGS,
        expected_factors=(
            ExpectedFactor(
                "usd_fx", should_fire=True,
                direction="up", target_confidence=0.70,
            ),
        ),
    ),

    # ======================================================================
    # Trade policy / sanctions
    # ======================================================================
    EvaluationScenario(
        id="trade.new_tariffs",
        family="trade_policy",
        title="US announces new tariffs and export controls on chip equipment",
        summary="The administration cited national security concerns.",
        holdings=_DEFAULT_HOLDINGS,
        expected_factors=(
            ExpectedFactor(
                "trade_policy", should_fire=True,
                direction="up", target_confidence=0.75,
            ),
        ),
    ),
    EvaluationScenario(
        id="trade.sanctions_lifted",
        family="trade_policy",
        title="Sanctions lifted on regional bank, tariff relief announced",
        summary="Trade restrictions eased following diplomatic talks.",
        holdings=_DEFAULT_HOLDINGS,
        expected_factors=(
            ExpectedFactor(
                "trade_policy", should_fire=True,
                direction="down", target_confidence=0.60,
            ),
        ),
        notes=(
            "Trade policy relaxation — ordering fixed by Phase 9C corrective "
            "pass so easing branches are reached before restrictive keywords."
        ),
    ),

    # ======================================================================
    # Geopolitical
    # ======================================================================
    EvaluationScenario(
        id="geo.missile_escalation",
        family="geopolitical_risk",
        title="Missile strike escalates conflict between regional powers",
        summary="A mobilization was announced in response to the airstrike.",
        holdings=_DEFAULT_HOLDINGS,
        expected_factors=(
            ExpectedFactor(
                "geopolitical_risk", should_fire=True,
                direction="up", target_confidence=0.80,
            ),
        ),
    ),
    EvaluationScenario(
        id="geo.ceasefire",
        family="geopolitical_risk",
        title="Ceasefire reached in regional conflict, de-escalation confirmed",
        summary="Both sides agreed to halt military operations.",
        holdings=_DEFAULT_HOLDINGS,
        expected_factors=(
            ExpectedFactor(
                "geopolitical_risk", should_fire=True,
                direction="down", target_confidence=0.70,
            ),
        ),
    ),

    # ======================================================================
    # Regulation / policy
    # ======================================================================
    EvaluationScenario(
        id="reg.antitrust_probe",
        family="regulation_policy",
        title="DOJ opens antitrust investigation into major tech platform",
        summary=(
            "The regulator cited concerns about market concentration and a "
            "landmark ruling is expected; compliance burden to rise."
        ),
        holdings=_DEFAULT_HOLDINGS,
        expected_factors=(
            ExpectedFactor(
                "regulation_policy", should_fire=True,
                direction="up", target_confidence=0.75,
            ),
        ),
        notes="Antitrust + enforcement — tech sector has -0.3 prior.",
    ),

    # ======================================================================
    # Consumer demand
    # ======================================================================
    EvaluationScenario(
        id="consumer.retail_sales_slump",
        family="consumer_demand",
        title="Retail sales slump as consumer confidence drops to multi-year low",
        summary=(
            "Discretionary spending fell 2% and jobless claims rose; "
            "unemployment rate climbed."
        ),
        holdings=_DEFAULT_HOLDINGS,
        expected_factors=(
            ExpectedFactor(
                "consumer_demand", should_fire=True,
                direction="down", target_confidence=0.75,
            ),
        ),
    ),

    # ======================================================================
    # Technology cycle
    # ======================================================================
    EvaluationScenario(
        id="tech.chip_export_controls",
        family="technology_cycle",
        title="New chip export controls target advanced AI semiconductors",
        summary=(
            "Export controls on chip equipment tighten as AI adoption waves "
            "reshape the semiconductor cycle."
        ),
        holdings=_DEFAULT_HOLDINGS,
        expected_factors=(
            ExpectedFactor(
                "technology_cycle", should_fire=True,
                direction="up", target_confidence=0.60,
            ),
            ExpectedFactor(
                "trade_policy", should_fire=True,
                direction="up", target_confidence=0.70,
            ),
        ),
        notes="Multi-factor: tech cycle + trade policy on chip export controls.",
    ),

    # ======================================================================
    # Portfolio isolation
    # ======================================================================
    EvaluationScenario(
        id="portfolio_isolation.fed_hike_multi_pf",
        family="portfolio_isolation",
        title="Federal Reserve raises interest rates by 75 basis points",
        summary="Powell cited sticky inflation and a red-hot labor market.",
        holdings=_MULTI_PORTFOLIO_HOLDINGS,
        expected_factors=(
            ExpectedFactor(
                "interest_rate", should_fire=True,
                direction="up", target_confidence=0.92,
            ),
        ),
        expected_impacts=(
            # Tech in pA → negative effect, link emitted
            ExpectedImpact(
                "interest_rate", "AAPL", "pA",
                should_emit_link=True, effect_direction="negative",
                target_confidence=0.30,
            ),
            # Tech in pB → same event, own portfolio_id preserved
            ExpectedImpact(
                "interest_rate", "MSFT", "pB",
                should_emit_link=True, effect_direction="negative",
                target_confidence=0.30,
            ),
            # Consumer staples in pB has -0.2 interest_rate prior →
            # below MIN_ABS_SENSITIVITY, no link emitted.
            ExpectedImpact(
                "interest_rate", "NESN", "pB",
                should_emit_link=False,
            ),
        ),
        notes="Impacts must carry each holding's own portfolio_id, no collapse.",
    ),

    # ======================================================================
    # Phase 9D — deterministic relationship graph
    # ======================================================================

    EvaluationScenario(
        id="relationship.tsmc_issue_hits_aapl_supplier",
        family="relationship",
        title="TSMC reports unexpected wafer yield issues at leading-edge node",
        summary=(
            "Taiwan Semiconductor flagged weaker-than-expected yields at its "
            "advanced node, raising supply risk for major customers."
        ),
        holdings=(
            SyntheticHolding("h_aapl", "AAPL", "default", "technology"),
            SyntheticHolding("h_msft", "MSFT", "default", "technology"),
        ),
        relationships=(
            SyntheticRelationship(
                ticker="AAPL",
                related_ticker="TSM",
                related_entity_key=None,
                related_name="Taiwan Semiconductor",
                relationship_type="supplier",
                strength=0.85,
            ),
        ),
        expected_relationship_impacts=(
            ExpectedRelationshipImpact(
                ticker="AAPL",
                portfolio_id="default",
                relationship_type="supplier",
                related_entity_key="TSM",
                should_emit_link=True,
                target_confidence=0.48,
            ),
            # MSFT has no TSMC supplier row → no link
            ExpectedRelationshipImpact(
                ticker="MSFT",
                portfolio_id="default",
                relationship_type="supplier",
                related_entity_key="TSM",
                should_emit_link=False,
            ),
        ),
        notes=(
            "Canonical supplier-propagation case: a TSMC issue should "
            "propagate to AAPL's holding through the supplier edge only."
        ),
    ),

    EvaluationScenario(
        id="relationship.competitor_amd_nvda",
        family="relationship",
        title="AMD reports record data-center GPU revenue",
        summary=(
            "Advanced Micro Devices said its MI-series GPUs are gaining "
            "share in the AI accelerator market."
        ),
        holdings=(
            SyntheticHolding("h_nvda", "NVDA", "default", "technology"),
        ),
        relationships=(
            SyntheticRelationship(
                ticker="NVDA",
                related_ticker="AMD",
                related_entity_key=None,
                related_name="Advanced Micro Devices",
                relationship_type="competitor",
                strength=0.60,
            ),
        ),
        expected_relationship_impacts=(
            ExpectedRelationshipImpact(
                ticker="NVDA",
                portfolio_id="default",
                relationship_type="competitor",
                related_entity_key="AMD",
                should_emit_link=True,
                target_confidence=0.23,
            ),
        ),
        notes=(
            "Competitor-propagation case: an AMD win may matter to NVDA. "
            "Confidence is intentionally lower than supplier cases because "
            "the competitor type weight is smaller."
        ),
    ),

    EvaluationScenario(
        id="relationship.regulator_doj_alphabet",
        family="relationship",
        title="Department of Justice opens new antitrust investigation into search ads",
        summary=(
            "The US Department of Justice said it is investigating "
            "competitive practices in digital advertising.  Enforcement "
            "action could reshape the market."
        ),
        holdings=(
            SyntheticHolding("h_googl", "GOOGL", "default", "technology"),
        ),
        relationships=(
            SyntheticRelationship(
                ticker="GOOGL",
                related_ticker=None,
                related_entity_key="doj_us",
                related_name="US Department of Justice",
                relationship_type="regulator",
                strength=0.85,   # strong jurisdictional tie
            ),
        ),
        expected_relationship_impacts=(
            ExpectedRelationshipImpact(
                ticker="GOOGL",
                portfolio_id="default",
                relationship_type="regulator",
                related_entity_key="doj_us",
                should_emit_link=True,
                target_confidence=0.22,
            ),
        ),
        notes=(
            "Non-ticker entity (regulator) matched by all-significant-tokens "
            "name fallback.  Strength is raised vs. the YAML seed so the "
            "scenario exercises regulator propagation end-to-end; real "
            "operators tune per-holding strength in the DB."
        ),
    ),

    EvaluationScenario(
        id="relationship.no_match_no_propagation",
        family="relationship",
        title="AAPL reports record quarterly iPhone sales",
        summary="Apple beat analyst estimates on hardware demand.",
        holdings=(
            SyntheticHolding("h_aapl", "AAPL", "default", "technology"),
        ),
        relationships=(
            SyntheticRelationship(
                ticker="AAPL",
                related_ticker="TSM",
                related_entity_key=None,
                related_name="Taiwan Semiconductor",
                relationship_type="supplier",
                strength=0.85,
            ),
        ),
        expected_relationship_impacts=(
            # The event is about the held company itself — TSMC is
            # not mentioned, so no relationship link should fire.
            ExpectedRelationshipImpact(
                ticker="AAPL",
                portfolio_id="default",
                relationship_type="supplier",
                related_entity_key="TSM",
                should_emit_link=False,
            ),
        ),
        notes=(
            "Portfolio-company event does NOT activate its own supplier "
            "relationship — the matcher requires actual mention of the "
            "related entity."
        ),
    ),

    EvaluationScenario(
        id="relationship.portfolio_isolation",
        family="relationship",
        title="TSMC reports wafer yield issues at leading-edge node",
        summary=(
            "Taiwan Semiconductor flagged weaker yields at its advanced node."
        ),
        holdings=(
            SyntheticHolding("h_aapl_pA", "AAPL", "pA", "technology"),
            SyntheticHolding("h_aapl_pB", "AAPL", "pB", "technology"),
        ),
        relationships=(
            # Only pA's AAPL has a relationship row; pB's does not.
            SyntheticRelationship(
                ticker="AAPL",
                related_ticker="TSM",
                related_entity_key=None,
                related_name="Taiwan Semiconductor",
                relationship_type="supplier",
                strength=0.85,
            ),
        ),
        expected_relationship_impacts=(
            # Both pA and pB AAPLs have the supplier row (tickers
            # share rel-registry entries in the synthetic harness),
            # so BOTH emit with their own portfolio_id preserved.
            ExpectedRelationshipImpact(
                ticker="AAPL",
                portfolio_id="pA",
                relationship_type="supplier",
                related_entity_key="TSM",
                should_emit_link=True,
                target_confidence=0.48,
            ),
            ExpectedRelationshipImpact(
                ticker="AAPL",
                portfolio_id="pB",
                relationship_type="supplier",
                related_entity_key="TSM",
                should_emit_link=True,
                target_confidence=0.48,
            ),
        ),
        notes=(
            "Portfolio isolation check: each AAPL position carries its own "
            "portfolio_id through to the emitted impact."
        ),
    ),
)


def load_scenarios(
    *,
    include_skipped: bool = False,
) -> tuple[EvaluationScenario, ...]:
    """Return the gold scenarios, optionally including skipped ones.

    Keeping skipped scenarios in the dataset (rather than deleting
    them) means reports can tell operators which cases were
    intentionally excluded and why.
    """
    if include_skipped:
        return GOLD_SCENARIOS
    return tuple(s for s in GOLD_SCENARIOS if not s.skip)
