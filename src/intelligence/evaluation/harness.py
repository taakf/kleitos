"""Phase 9C evaluation harness.

Wires the deterministic classifier + propagator + sensitivity
resolver to the gold scenarios in ``scenarios.py``, produces
observations, and aggregates them into an ``EvaluationReport``.

No database, no LLM, no network — the harness is pure Python and
fast enough to run in every CI pass.

Design notes
------------
* The harness calls the live ``FactorClassifier`` and
  ``FactorPropagator`` classes so any regression in the real
  pipeline shows up here immediately.
* The active ``ConfidencePolicy`` governs every threshold.  Run
  against the baseline by default; evaluation sweeps can pass a
  tuned policy in via ``run_evaluation(policy=...)``.
* Observations are collected into plain dataclasses and handed to
  the pure metric functions in ``metrics.py`` — no hidden state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from src.intelligence.factors.classifier import (
    FactorClassification,
    FactorClassifier,
)
from src.intelligence.factors.propagation import FactorPropagator
from src.intelligence.factors.sensitivity import SensitivityResolver
from src.intelligence.policy import BASELINE_POLICY, ConfidencePolicy
from src.intelligence.evaluation.metrics import (
    ConfusingCase,
    EvaluationReport,
    FactorObservation,
    ImpactObservation,
    KnownWeakness,
    RelationshipObservation,
    compute_factor_metrics,
    compute_propagation_metrics,
    compute_relationship_metrics,
)
from src.intelligence.evaluation.scenarios import (
    BENCHMARK_VERSION,
    EvaluationScenario,
    load_scenarios,
)
from src.intelligence.relationships.matcher import RelationshipEntityMatcher
from src.intelligence.relationships.propagation import (
    RELATIONSHIP_MIN_EMIT,
    RelationshipPropagator,
    RelationshipRow,
)


#: Harness contract version — bump if the harness output schema
#: changes shape in a way external consumers would notice.
HARNESS_VERSION: str = "phase9c.1"


# ---------------------------------------------------------------------------
# Per-run observation bundle
# ---------------------------------------------------------------------------


@dataclass
class EvaluationRunResult:
    """Raw run state — useful for deeper test introspection."""

    scenarios_run: int
    scenarios_skipped: int
    factor_observations: list[FactorObservation] = field(default_factory=list)
    impact_observations: list[ImpactObservation] = field(default_factory=list)
    relationship_observations: list[RelationshipObservation] = field(
        default_factory=list,
    )
    confusing_cases: list[ConfusingCase] = field(default_factory=list)
    known_weaknesses: list[KnownWeakness] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# The harness
# ---------------------------------------------------------------------------


def run_evaluation(
    scenarios: Iterable[EvaluationScenario] | None = None,
    *,
    policy: ConfidencePolicy | None = None,
) -> EvaluationReport:
    """Run the full Phase 9C evaluation and return a report.

    Parameters
    ----------
    scenarios:
        Iterable of scenarios.  Defaults to the full gold benchmark
        (excluding skipped entries).
    policy:
        Optional ``ConfidencePolicy``.  Defaults to the baseline
        policy — that's what the live runtime uses.
    """
    policy = policy or BASELINE_POLICY
    all_scenarios = tuple(load_scenarios(include_skipped=True))
    active = tuple(scenarios) if scenarios is not None else tuple(
        s for s in all_scenarios if not s.skip
    )
    skipped_ids = [s.id for s in all_scenarios if s.skip]

    run = EvaluationRunResult(
        scenarios_run=len(active),
        scenarios_skipped=len(skipped_ids),
    )
    for sid in skipped_ids:
        run.notes.append(f"scenario skipped: {sid}")

    classifier = FactorClassifier(policy=policy)

    for scenario in active:
        _evaluate_scenario(scenario, classifier, policy, run)

    factor_metrics = compute_factor_metrics(run.factor_observations)
    propagation_metrics = compute_propagation_metrics(run.impact_observations)
    relationship_metrics = compute_relationship_metrics(run.relationship_observations)

    # Convert impact-level isolation bookkeeping into top-line figures.
    propagation_metrics.portfolio_isolation_checks = sum(
        1 for s in active if s.family == "portfolio_isolation"
        for _ in s.expected_impacts
    )
    # Detect actual isolation violations — any impact observation
    # where the propagator emitted a link whose portfolio_id did not
    # match the expected per-impact portfolio.  We reconstruct this
    # from the observations collected during the run.
    propagation_metrics.portfolio_isolation_violations = sum(
        1 for o in run.impact_observations
        if o.predicted_link and o.expected_link and o.portfolio_id
        and o.expected_effect and o.predicted_effect
        and o.expected_effect != "unclear"
        and o.expected_effect != o.predicted_effect
        and o.factor  # narrow to cases that can be checked
        and False  # placeholder — real isolation check below in scenario loop
    )
    # Real isolation violations are recorded as confusing cases and
    # counted there; the structured per-impact count above stays at
    # zero unless the per-scenario logic flipped it via confusing cases.
    isolation_violations = sum(
        1 for c in run.confusing_cases if c.reason.startswith("portfolio_isolation:")
    )
    propagation_metrics.portfolio_isolation_violations = isolation_violations

    # Count relationship portfolio-isolation checks: one per expected
    # impact in any ``relationship`` scenario marked as isolation.
    relationship_metrics.portfolio_isolation_checks = sum(
        1 for s in active
        if s.family == "relationship" and "isolation" in s.id
        for _ in s.expected_relationship_impacts
    )
    rel_isolation_violations = sum(
        1 for c in run.confusing_cases
        if c.reason.startswith("relationship_isolation:")
    )
    relationship_metrics.portfolio_isolation_violations = rel_isolation_violations

    return EvaluationReport(
        benchmark_version=BENCHMARK_VERSION,
        policy_name=policy.name,
        policy_version=policy.version,
        harness_version=HARNESS_VERSION,
        scenarios_run=run.scenarios_run,
        scenarios_skipped=run.scenarios_skipped,
        factor_metrics=factor_metrics,
        propagation_metrics=propagation_metrics,
        relationship_metrics=relationship_metrics,
        confusing_cases=run.confusing_cases,
        known_weaknesses=run.known_weaknesses,
        notes=run.notes,
    )


# ---------------------------------------------------------------------------
# Per-scenario evaluation
# ---------------------------------------------------------------------------


def _evaluate_scenario(
    scenario: EvaluationScenario,
    classifier: FactorClassifier,
    policy: ConfidencePolicy,
    run: EvaluationRunResult,
) -> None:
    """Run one scenario and append observations to the run bundle."""

    # --- 1. Classify the event -----------------------------------------
    classifications = classifier.classify(
        title=scenario.title, summary=scenario.summary,
    )
    cls_by_factor: dict[str, FactorClassification] = {
        c.factor: c for c in classifications
    }

    # Record expected factors (positives and negatives).
    for exp in scenario.expected_factors:
        predicted = cls_by_factor.get(exp.factor)
        obs = FactorObservation(
            scenario_id=scenario.id,
            factor=exp.factor,
            expected=exp.should_fire,
            predicted=predicted is not None,
            expected_direction=exp.direction,
            predicted_direction=predicted.direction if predicted else None,
            expected_magnitude=exp.magnitude,
            predicted_magnitude=predicted.magnitude if predicted else None,
            target_confidence=exp.target_confidence,
            predicted_confidence=predicted.confidence if predicted else None,
            known_weakness=exp.known_weakness,
            known_weakness_reason=exp.known_weakness_reason,
        )
        run.factor_observations.append(obs)

        # Record confusing cases for reporting.
        if obs.expected and not obs.predicted:
            # A missed factor is ALWAYS a confusing case — even if the
            # expectation was originally flagged as a direction-level
            # known weakness, losing it entirely is a real regression.
            run.confusing_cases.append(ConfusingCase(
                scenario_id=scenario.id,
                family=scenario.family,
                reason=f"missed factor: {exp.factor}",
            ))
        elif obs.predicted and not obs.expected:
            run.confusing_cases.append(ConfusingCase(
                scenario_id=scenario.id,
                family=scenario.family,
                reason=(
                    f"false-positive factor: {exp.factor} "
                    f"(confidence={obs.predicted_confidence:.2f})"
                ) if obs.predicted_confidence is not None else (
                    f"false-positive factor: {exp.factor}"
                ),
            ))
        elif obs.expected and obs.predicted and exp.direction and predicted.direction != exp.direction:
            if exp.known_weakness:
                # Documented known weakness — report it in the
                # separate section so the baseline stays stable AND
                # a future improvement OR regression is still visible.
                run.known_weaknesses.append(KnownWeakness(
                    scenario_id=scenario.id,
                    factor=exp.factor,
                    aspect="direction",
                    reason=(
                        exp.known_weakness_reason
                        or f"expected {exp.direction}, got {predicted.direction}"
                    ),
                ))
            else:
                run.confusing_cases.append(ConfusingCase(
                    scenario_id=scenario.id,
                    family=scenario.family,
                    reason=(
                        f"wrong direction for {exp.factor}: "
                        f"expected {exp.direction}, got {predicted.direction}"
                    ),
                ))

    # Unexpected factors: anything the classifier produced that isn't
    # in the expected list at all.  These count as false positives
    # unless the scenario already asserted them should_fire=False.
    expected_factor_keys = {e.factor for e in scenario.expected_factors}
    for cls in classifications:
        if cls.factor in expected_factor_keys:
            continue
        # Implicit expectation: if a scenario doesn't mention a
        # factor at all, that factor was implicitly allowed (we don't
        # penalize incidental multi-factor hits).  However for
        # false-positive-family scenarios, any unexpected factor IS
        # a failure.
        if scenario.family == "false_positive":
            run.factor_observations.append(FactorObservation(
                scenario_id=scenario.id,
                factor=cls.factor,
                expected=False,
                predicted=True,
                predicted_direction=cls.direction,
                predicted_magnitude=cls.magnitude,
                predicted_confidence=cls.confidence,
            ))
            run.confusing_cases.append(ConfusingCase(
                scenario_id=scenario.id,
                family=scenario.family,
                reason=(
                    f"false-positive factor on noise scenario: "
                    f"{cls.factor} (confidence={cls.confidence:.2f})"
                ),
            ))

    # --- 2. Propagate to holdings --------------------------------------
    if scenario.holdings and classifications:
        resolver = SensitivityResolver()  # no manual overrides in the harness
        propagator = FactorPropagator(resolver, policy=policy)
        holdings_dicts = [
            {
                "id": h.id,
                "ticker": h.ticker,
                "portfolio_id": h.portfolio_id,
                "sector": h.sector,
            }
            for h in scenario.holdings
        ]
        impacts = propagator.propagate(classifications, holdings_dicts)

        # Which impacts would actually become EventLinks under the
        # active policy's emission floor?
        link_min = policy.macro_factor_link_min
        emitted_links: set[tuple[str, str, str]] = {
            (i.factor, i.ticker, i.portfolio_id)
            for i in impacts
            if i.holding_confidence >= link_min
        }
        impact_by_key: dict[tuple[str, str, str], object] = {
            (i.factor, i.ticker, i.portfolio_id): i for i in impacts
        }

        for exp in scenario.expected_impacts:
            key = (exp.factor, exp.ticker, exp.portfolio_id)
            actual = impact_by_key.get(key)
            emitted = key in emitted_links

            predicted_effect = actual.effect_direction if actual else None
            predicted_confidence = actual.holding_confidence if actual else None

            obs = ImpactObservation(
                scenario_id=scenario.id,
                factor=exp.factor,
                ticker=exp.ticker,
                portfolio_id=exp.portfolio_id,
                expected_link=exp.should_emit_link,
                predicted_link=emitted,
                expected_effect=exp.effect_direction,
                predicted_effect=predicted_effect,
                target_confidence=exp.target_confidence,
                predicted_confidence=predicted_confidence,
            )
            run.impact_observations.append(obs)

            if obs.expected_link and not obs.predicted_link:
                run.confusing_cases.append(ConfusingCase(
                    scenario_id=scenario.id, family=scenario.family,
                    reason=(
                        f"missed link: {exp.factor} → {exp.ticker} "
                        f"({exp.portfolio_id})"
                    ),
                ))
            elif obs.predicted_link and not obs.expected_link:
                run.confusing_cases.append(ConfusingCase(
                    scenario_id=scenario.id, family=scenario.family,
                    reason=(
                        f"unexpected link: {exp.factor} → {exp.ticker} "
                        f"({exp.portfolio_id})"
                    ),
                ))
            elif (
                obs.expected_link and obs.predicted_link
                and exp.effect_direction
                and actual is not None
                and actual.effect_direction != exp.effect_direction
            ):
                run.confusing_cases.append(ConfusingCase(
                    scenario_id=scenario.id, family=scenario.family,
                    reason=(
                        f"wrong sign: {exp.factor} → {exp.ticker}: "
                        f"expected {exp.effect_direction}, "
                        f"got {actual.effect_direction}"
                    ),
                ))

        # Portfolio-isolation invariant check.  For any impact that
        # actually emitted, its portfolio_id MUST match the holding's
        # real portfolio_id (the synthetic scenario lists them, so a
        # mismatch would mean the propagator collapsed portfolios —
        # the exact Phase 9A safety invariant).
        if scenario.family == "portfolio_isolation":
            # Only the impacts that survived the emit gate are written
            # into ``emitted_links``; that's what the runtime would
            # persist as an EventLink.
            for (factor, ticker, pid) in emitted_links:
                # If the ticker appears in multiple portfolios (it
                # does in our isolation fixture), both rows should be
                # present with their own pid.  Violation = pid on
                # impact doesn't equal ANY holding carrying that
                # ticker in that portfolio.
                matching_holdings = [
                    h for h in scenario.holdings
                    if h.ticker == ticker and h.portfolio_id == pid
                ]
                if not matching_holdings:
                    run.confusing_cases.append(ConfusingCase(
                        scenario_id=scenario.id, family=scenario.family,
                        reason=(
                            f"portfolio_isolation: impact tagged pid={pid} "
                            f"for {ticker} which has no such holding row"
                        ),
                    ))

    # --- 3. Relationship graph pass (Phase 9D) -------------------------
    if scenario.relationships and scenario.holdings:
        _evaluate_relationships(scenario, run)


def _evaluate_relationships(
    scenario: EvaluationScenario, run: EvaluationRunResult,
) -> None:
    """Run the deterministic relationship graph against a scenario.

    Uses the pure ``RelationshipEntityMatcher`` + ``RelationshipPropagator``
    — no DB involvement.  The scenario ships a ``relationships`` tuple
    that the harness flattens into ``RelationshipRow`` dataclasses;
    every (scenario holding, seed relationship) pair that shares a
    ticker becomes a row, so portfolio-isolation is enforced purely
    by the (holding_id, portfolio_id) pairing.
    """
    # Ticker → list[holdings] lookup so we can explode seed rows
    # across every matching held position.
    holdings_by_ticker: dict[str, list] = {}
    for h in scenario.holdings:
        holdings_by_ticker.setdefault(h.ticker, []).append(h)

    # Flatten scenario relationships → runtime RelationshipRow dataclasses.
    rows: list[RelationshipRow] = []
    for sr in scenario.relationships:
        for h in holdings_by_ticker.get(sr.ticker, []):
            rows.append(RelationshipRow(
                id=f"rel_{h.id}_{sr.relationship_type}_{sr.related_ticker or sr.related_entity_key}",
                holding_id=h.id,
                ticker=h.ticker,
                portfolio_id=h.portfolio_id,
                relationship_type=sr.relationship_type,
                related_ticker=sr.related_ticker,
                related_entity_key=sr.related_entity_key,
                related_name=sr.related_name,
                strength=float(sr.strength),
                source="seed",
            ))
    if not rows:
        return

    # Distinct entities for the matcher.  Same key convention as
    # the live runtime: uppercase ticker if present, else the lowercase
    # entity_key.
    seen_keys: set[str] = set()
    entities: list[tuple[str, str | None, str | None]] = []
    for row in rows:
        if row.related_ticker:
            key = row.related_ticker.upper()
        elif row.related_entity_key:
            key = row.related_entity_key
        else:
            continue
        if key in seen_keys:
            continue
        seen_keys.add(key)
        entities.append((key, row.related_ticker, row.related_name))

    # Excluded tickers = held tickers (direct-match double-count guard).
    excluded = {h.ticker.upper() for h in scenario.holdings}

    matcher = RelationshipEntityMatcher()
    matches = matcher.find_matches(
        title=scenario.title,
        summary=scenario.summary,
        entities=entities,
        excluded_tickers=excluded,
    )

    propagator = RelationshipPropagator()
    impacts = propagator.propagate(entity_matches=matches, relationships=rows)

    # Which impacts clear the emission gate?
    emitted: dict[tuple[str, str, str, str], object] = {}
    for i in impacts:
        if i.holding_confidence < RELATIONSHIP_MIN_EMIT:
            continue
        key = (
            i.ticker,
            i.portfolio_id,
            i.relationship_type,
            (i.related_ticker or i.related_entity_key or "?"),
        )
        emitted[key] = i

    # Compare against expected_relationship_impacts.
    for exp in scenario.expected_relationship_impacts:
        key = (
            exp.ticker,
            exp.portfolio_id,
            exp.relationship_type,
            exp.related_entity_key,
        )
        impact = emitted.get(key)
        predicted = impact is not None

        obs = RelationshipObservation(
            scenario_id=scenario.id,
            ticker=exp.ticker,
            portfolio_id=exp.portfolio_id,
            relationship_type=exp.relationship_type,
            related_entity_key=exp.related_entity_key,
            expected_link=exp.should_emit_link,
            predicted_link=predicted,
            target_confidence=exp.target_confidence,
            predicted_confidence=(
                impact.holding_confidence if impact else None
            ),
        )
        run.relationship_observations.append(obs)

        if obs.expected_link and not obs.predicted_link:
            run.confusing_cases.append(ConfusingCase(
                scenario_id=scenario.id, family=scenario.family,
                reason=(
                    f"missed relationship link: {exp.relationship_type} "
                    f"{exp.related_entity_key} → {exp.ticker}/{exp.portfolio_id}"
                ),
            ))
        elif obs.predicted_link and not obs.expected_link:
            run.confusing_cases.append(ConfusingCase(
                scenario_id=scenario.id, family=scenario.family,
                reason=(
                    f"unexpected relationship link: {exp.relationship_type} "
                    f"{exp.related_entity_key} → {exp.ticker}/{exp.portfolio_id}"
                ),
            ))

    # Portfolio-isolation invariant: every emitted impact's portfolio_id
    # must match an actual holding's portfolio_id in the scenario.
    if "isolation" in scenario.id:
        for (ticker, pid, rel_type, key), _impact in emitted.items():
            matching = [
                h for h in scenario.holdings
                if h.ticker == ticker and h.portfolio_id == pid
            ]
            if not matching:
                run.confusing_cases.append(ConfusingCase(
                    scenario_id=scenario.id, family=scenario.family,
                    reason=(
                        f"relationship_isolation: impact tagged pid={pid} "
                        f"for {ticker} has no holding row"
                    ),
                ))
