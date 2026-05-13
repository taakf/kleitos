"""Phase 9C evaluation CLI runner.

Usage::

    python -m src.intelligence.evaluation.run
    python -m src.intelligence.evaluation.run --json report.json
    python -m src.intelligence.evaluation.run --quiet

Prints a compact human-readable summary and, optionally, writes a
JSON artifact for CI archiving.  Exit code is non-zero if the run
revealed any portfolio-isolation violations or any failure in the
scenarios the harness treats as invariants.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from src.intelligence.evaluation import run_evaluation
from src.intelligence.evaluation.metrics import EvaluationReport


# ---------------------------------------------------------------------------
# Text rendering
# ---------------------------------------------------------------------------


def _pct(value: float) -> str:
    return f"{value * 100:5.1f}%"


def format_text_report(report: EvaluationReport) -> str:
    fm = report.factor_metrics
    pm = report.propagation_metrics
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append("Axion Intelligence Evaluation — Phase 9C")
    lines.append("=" * 72)
    lines.append(f"  benchmark       : {report.benchmark_version}")
    lines.append(f"  harness         : {report.harness_version}")
    lines.append(f"  policy          : {report.policy_name} (v{report.policy_version})")
    lines.append(f"  scenarios run   : {report.scenarios_run}")
    lines.append(f"  scenarios skip  : {report.scenarios_skipped}")
    if report.is_synthetic_benchmark:
        lines.append("  NOTE            : synthetic benchmark — NOT real-world calibration")
    lines.append("")

    lines.append("-- Classifier ----------------------------------------------------------")
    lines.append(f"  true positives  : {fm.true_positives:4d}")
    lines.append(f"  false positives : {fm.false_positives:4d}")
    lines.append(f"  false negatives : {fm.false_negatives:4d}")
    lines.append(f"  true negatives  : {fm.true_negatives:4d}")
    lines.append(f"  precision       : {_pct(fm.precision)}")
    lines.append(f"  recall          : {_pct(fm.recall)}")
    lines.append(f"  F1              : {_pct(fm.f1)}")
    lines.append(f"  FP rate         : {_pct(fm.false_positive_rate)}")
    lines.append(f"  direction acc.  : {_pct(fm.direction_accuracy)}")
    lines.append(f"  magnitude acc.  : {_pct(fm.magnitude_accuracy)}")
    if fm.brier is not None:
        lines.append(
            f"  Brier (synth)   : {fm.brier.score:.4f}  "
            f"(n={fm.brier.n}, synthetic-benchmark)"
        )
    if fm.reliability_bins:
        lines.append("  reliability bins (synthetic):")
        lines.append("    bucket        count  mean_pred  mean_target")
        for b in fm.reliability_bins:
            lines.append(
                f"    [{b.lower:.1f}, {b.upper:.1f}]    "
                f"{b.count:4d}    {b.mean_predicted:.3f}      {b.mean_expected:.3f}"
            )
    lines.append("")

    lines.append("-- Propagation ---------------------------------------------------------")
    lines.append(f"  true links      : {pm.true_links:4d}")
    lines.append(f"  missed links    : {pm.missed_links:4d}")
    lines.append(f"  extra links     : {pm.extra_links:4d}")
    lines.append(f"  correctly supp. : {pm.correctly_suppressed:4d}")
    lines.append(f"  emit precision  : {_pct(pm.emission_precision)}")
    lines.append(f"  emit recall     : {_pct(pm.emission_recall)}")
    lines.append(f"  sign accuracy   : {_pct(pm.sign_accuracy)}")
    lines.append(
        f"  isolation pass  : {'YES' if pm.portfolio_isolation_pass else 'NO'}  "
        f"({pm.portfolio_isolation_violations} violations / "
        f"{pm.portfolio_isolation_checks} checks)"
    )
    if pm.brier is not None:
        lines.append(
            f"  Brier (synth)   : {pm.brier.score:.4f}  "
            f"(n={pm.brier.n}, synthetic-benchmark)"
        )
    lines.append("")

    rm = report.relationship_metrics
    lines.append("-- Relationships (Phase 9D) -------------------------------------------")
    lines.append(f"  true links      : {rm.true_links:4d}")
    lines.append(f"  missed links    : {rm.missed_links:4d}")
    lines.append(f"  extra links     : {rm.extra_links:4d}")
    lines.append(f"  correctly supp. : {rm.correctly_suppressed:4d}")
    lines.append(f"  emit precision  : {_pct(rm.emission_precision)}")
    lines.append(f"  emit recall     : {_pct(rm.emission_recall)}")
    lines.append(
        f"  max confidence  : {rm.max_predicted_confidence:.3f}  "
        f"(ceiling < direct-match 1.0 by design)"
    )
    lines.append(
        f"  isolation pass  : {'YES' if rm.portfolio_isolation_pass else 'NO'}  "
        f"({rm.portfolio_isolation_violations} violations / "
        f"{rm.portfolio_isolation_checks} checks)"
    )
    if rm.brier is not None:
        lines.append(
            f"  Brier (synth)   : {rm.brier.score:.4f}  "
            f"(n={rm.brier.n}, synthetic-benchmark)"
        )
    lines.append("")

    if report.confusing_cases:
        lines.append(
            f"-- Confusing cases ({len(report.confusing_cases)}) "
            "----------------------------------------"
        )
        for c in report.confusing_cases[:20]:
            lines.append(f"  [{c.family}] {c.scenario_id}: {c.reason}")
        if len(report.confusing_cases) > 20:
            lines.append(f"  ... and {len(report.confusing_cases) - 20} more")
        lines.append("")
    else:
        lines.append("-- Confusing cases: none -----------------------------------------------")
        lines.append("")

    if report.known_weaknesses:
        lines.append(
            f"-- Known weaknesses ({len(report.known_weaknesses)}) "
            "---------------------------------------"
        )
        lines.append("     (documented classifier limits — NOT counted as failures)")
        for kw in report.known_weaknesses:
            lines.append(f"  * {kw.scenario_id} [{kw.factor}/{kw.aspect}]")
            lines.append(f"      {kw.reason}")
        lines.append("")

    lines.append("=" * 72)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m src.intelligence.evaluation.run",
        description=(
            "Run the Phase 9C intelligence evaluation against the synthetic "
            "gold benchmark and print a summary.  Synthetic benchmark — not "
            "real-world calibration."
        ),
    )
    parser.add_argument(
        "--json", type=Path, default=None,
        help="Write a machine-readable JSON report to this path.",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Suppress the human-readable summary (still writes JSON if --json given).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    report = run_evaluation()

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(report.summary_dict(), indent=2, sort_keys=True),
            encoding="utf-8",
        )

    if not args.quiet:
        print(format_text_report(report))

    # Exit non-zero on portfolio-isolation violations — this is the
    # one hard safety invariant the harness guards.
    if not report.propagation_metrics.portfolio_isolation_pass:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
