"""Exposure calculator – portfolio concentration analysis."""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ExposureBucket:
    """A single exposure bucket (e.g. sector=technology)."""
    dimension: str  # sector, geography, currency, theme, single_name
    value: str
    weight_pct: float
    holdings_count: int
    tickers: list[str] = field(default_factory=list)


@dataclass
class ExposureReport:
    """Full portfolio exposure breakdown."""
    by_sector: list[ExposureBucket]
    by_geography: list[ExposureBucket]
    by_currency: list[ExposureBucket]
    by_theme: list[ExposureBucket]
    top_single_names: list[ExposureBucket]
    total_market_value: float
    holdings_count: int
    concentration_alerts: list[dict] = field(default_factory=list)


class ExposureCalculator:
    """Calculates portfolio exposures and concentration risks."""

    def __init__(self, risk_thresholds: Optional[dict] = None):
        self._thresholds = risk_thresholds or {
            "single_name_max_pct": 10.0,
            "sector_max_pct": 30.0,
            "geography_max_pct": 40.0,
            "currency_max_pct": 50.0,
            "theme_max_pct": 25.0,
        }

    def calculate(
        self,
        holdings: list[dict],
        securities: list[dict],
    ) -> ExposureReport:
        """Calculate full exposure report.

        Args:
            holdings: list of holding dicts with at least ticker, market_value, weight_pct
            securities: list of security dicts with sector, geography, themes, etc.
        """
        sec_map = {s["ticker"]: s for s in securities}
        total_mv = sum(float(h.get("market_value", 0)) for h in holdings)

        # Accumulators: dimension → value → {weight, count, tickers}
        accum: dict[str, dict[str, dict]] = {
            "sector": defaultdict(lambda: {"weight": 0.0, "count": 0, "tickers": []}),
            "geography": defaultdict(lambda: {"weight": 0.0, "count": 0, "tickers": []}),
            "currency": defaultdict(lambda: {"weight": 0.0, "count": 0, "tickers": []}),
            "theme": defaultdict(lambda: {"weight": 0.0, "count": 0, "tickers": []}),
            "single_name": defaultdict(lambda: {"weight": 0.0, "count": 0, "tickers": []}),
        }

        for h in holdings:
            ticker = h.get("ticker", "")
            weight = float(h.get("weight_pct", 0))
            currency = h.get("currency", "unknown").upper()
            sec = sec_map.get(ticker, {})

            # Single name
            accum["single_name"][ticker]["weight"] += weight
            accum["single_name"][ticker]["count"] = 1
            accum["single_name"][ticker]["tickers"] = [ticker]

            # Sector
            sector = sec.get("sector", "unclassified") or "unclassified"
            accum["sector"][sector]["weight"] += weight
            accum["sector"][sector]["count"] += 1
            accum["sector"][sector]["tickers"].append(ticker)

            # Geography
            geo = sec.get("geography", "unclassified") or "unclassified"
            accum["geography"][geo]["weight"] += weight
            accum["geography"][geo]["count"] += 1
            accum["geography"][geo]["tickers"].append(ticker)

            # Currency
            accum["currency"][currency]["weight"] += weight
            accum["currency"][currency]["count"] += 1
            accum["currency"][currency]["tickers"].append(ticker)

            # Themes
            themes_raw = sec.get("themes", "[]")
            if isinstance(themes_raw, str):
                try:
                    themes = json.loads(themes_raw)
                except (json.JSONDecodeError, TypeError):
                    themes = []
            elif isinstance(themes_raw, list):
                themes = themes_raw
            else:
                themes = []

            for theme in themes:
                if theme:
                    t = theme.lower().strip()
                    accum["theme"][t]["weight"] += weight
                    accum["theme"][t]["count"] += 1
                    accum["theme"][t]["tickers"].append(ticker)

        # Build buckets
        def _build(dim: str) -> list[ExposureBucket]:
            return sorted(
                [
                    ExposureBucket(
                        dimension=dim,
                        value=val,
                        weight_pct=round(data["weight"], 2),
                        holdings_count=data["count"],
                        tickers=list(set(data["tickers"])),
                    )
                    for val, data in accum[dim].items()
                ],
                key=lambda b: b.weight_pct,
                reverse=True,
            )

        by_sector = _build("sector")
        by_geography = _build("geography")
        by_currency = _build("currency")
        by_theme = _build("theme")
        top_names = _build("single_name")[:20]  # Top 20 single names

        # Concentration alerts
        alerts = self._check_concentration(
            by_sector, by_geography, by_currency, by_theme, top_names
        )

        return ExposureReport(
            by_sector=by_sector,
            by_geography=by_geography,
            by_currency=by_currency,
            by_theme=by_theme,
            top_single_names=top_names,
            total_market_value=total_mv,
            holdings_count=len(holdings),
            concentration_alerts=alerts,
        )

    def _check_concentration(
        self,
        by_sector: list[ExposureBucket],
        by_geography: list[ExposureBucket],
        by_currency: list[ExposureBucket],
        by_theme: list[ExposureBucket],
        top_names: list[ExposureBucket],
    ) -> list[dict]:
        """Check exposure buckets against risk thresholds."""
        alerts = []
        checks = [
            ("single_name", top_names, self._thresholds["single_name_max_pct"]),
            ("sector", by_sector, self._thresholds["sector_max_pct"]),
            ("geography", by_geography, self._thresholds["geography_max_pct"]),
            ("currency", by_currency, self._thresholds["currency_max_pct"]),
            ("theme", by_theme, self._thresholds["theme_max_pct"]),
        ]
        for dim, buckets, threshold in checks:
            for b in buckets:
                if b.weight_pct > threshold:
                    severity = "critical" if b.weight_pct > threshold * 1.5 else "warning"
                    alerts.append({
                        "type": "concentration",
                        "dimension": dim,
                        "value": b.value,
                        "weight_pct": b.weight_pct,
                        "threshold_pct": threshold,
                        "severity": severity,
                        "tickers": b.tickers,
                        "message": (
                            f"{dim.replace('_', ' ').title()} concentration: "
                            f"{b.value} at {b.weight_pct:.1f}% "
                            f"(threshold {threshold:.0f}%)"
                        ),
                    })
        return alerts

    def to_dict(self, report: ExposureReport) -> dict:
        """Serialise an ExposureReport to a JSON-safe dict."""
        def _buckets(buckets):
            return [
                {
                    "dimension": b.dimension,
                    "value": b.value,
                    "weight_pct": b.weight_pct,
                    "holdings_count": b.holdings_count,
                    "tickers": b.tickers,
                }
                for b in buckets
            ]

        return {
            "by_sector": _buckets(report.by_sector),
            "by_geography": _buckets(report.by_geography),
            "by_currency": _buckets(report.by_currency),
            "by_theme": _buckets(report.by_theme),
            "top_single_names": _buckets(report.top_single_names),
            "total_market_value": report.total_market_value,
            "holdings_count": report.holdings_count,
            "concentration_alerts": report.concentration_alerts,
        }
