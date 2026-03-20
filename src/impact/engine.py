"""Impact Mapping Engine — orchestrates rule-based matching and LLM scoring."""

import json
import logging
import uuid
from datetime import datetime, timezone

from src.impact.rules import RuleEngine, RuleMatch
from src.impact.scoring import ImpactScorer, ImpactScore

logger = logging.getLogger(__name__)


class ImpactMappingEngine:
    """Central engine that determines which events affect which portfolio entities.

    Two-stage process:
    1. Rule-based matching (deterministic, fast) → candidate links
    2. LLM-based scoring (nuanced, slower) → scored and classified links

    All decisions are logged with full audit traces.
    """

    def __init__(self, rule_engine: RuleEngine, scorer: ImpactScorer | None = None):
        self._rules = rule_engine
        self._scorer = scorer

    async def map_event(
        self,
        event: dict,
        holdings: list[dict],
        securities: list[dict],
        use_llm: bool = True,
        min_relevance: float = 0.3,
    ) -> dict:
        """Map a single event to affected portfolio entities.

        Args:
            event: Event dict with id, title, summary/content, event_type
            holdings: List of holding dicts
            securities: List of security dicts
            use_llm: Whether to use LLM scoring (Stage 2)
            min_relevance: Minimum relevance threshold

        Returns:
            Dict with:
                - rule_matches: list of rule match dicts
                - scope: event scope classification
                - impact_scores: list of scored impacts (if LLM used)
                - event_links: list of link records ready for DB insertion
                - trace: full audit trace
        """
        title = event.get("title", "")
        content = event.get("summary", "") or event.get("content", "")
        event_id = event.get("id", "")

        # Stage 1: Rule-based matching
        rule_matches = self._rules.find_matches(title, content, holdings, securities)

        # Classify scope
        scope = self._rules.classify_scope(rule_matches, len(holdings))

        # Prepare result
        result = {
            "event_id": event_id,
            "rule_matches": [self._match_to_dict(m) for m in rule_matches],
            "scope": scope,
            "impact_scores": [],
            "event_links": [],
            "trace": {
                "event_id": event_id,
                "event_title": title[:200],
                "stage_1_matches": len(rule_matches),
                "scope": scope,
                "stage_2_used": False,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        }

        if scope == "unrelated":
            return result

        # Stage 2: LLM scoring (if enabled and scorer available)
        if use_llm and self._scorer:
            result["trace"]["stage_2_used"] = True

            # Get holdings that were matched by rules
            matched_holding_ids = {m.holding_id for m in rule_matches if m.holding_id}

            if matched_holding_ids:
                # Build holding-security pairs for scoring
                holdings_map = {h["id"]: h for h in holdings}
                securities_map = {s["ticker"].upper(): s for s in securities}

                pairs = []
                matches_by_holding = {}
                for hid in matched_holding_ids:
                    h = holdings_map.get(hid)
                    if h:
                        sec = securities_map.get(h["ticker"].upper(), {})
                        pairs.append((h, sec))
                        matches_by_holding[hid] = [
                            self._match_to_dict(m) for m in rule_matches if m.holding_id == hid
                        ]

                scores = await self._scorer.score_batch(
                    event, pairs, matches_by_holding, min_relevance
                )
                result["impact_scores"] = [self._score_to_dict(s) for s in scores]
                result["trace"]["stage_2_scores"] = len(scores)

        # Build event_links for DB insertion
        result["event_links"] = self._build_event_links(
            event_id, rule_matches, result["impact_scores"]
        )

        return result

    def _build_event_links(
        self,
        event_id: str,
        rule_matches: list[RuleMatch],
        impact_scores: list[dict],
    ) -> list[dict]:
        """Build event_link records for database insertion."""
        links = []
        seen = set()  # Prevent duplicate links

        # Links from LLM scores (higher quality)
        for score in impact_scores:
            key = f"{event_id}:{score.get('holding_id', '')}:holding"
            if key not in seen:
                seen.add(key)
                links.append({
                    "id": str(uuid.uuid4()),
                    "event_id": event_id,
                    "link_type": "holding",
                    "link_target": score.get("holding_id", ""),
                    "relevance_score": score.get("relevance", 0),
                    "impact_channel": score.get("impact_channel", ""),
                    "link_source": "llm",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                })

        # Links from rules (for non-holding matches like sector, geography)
        for match in rule_matches:
            if match.match_type in ("sector", "geography", "theme", "currency", "market_wide"):
                key = f"{event_id}:{match.matched_value}:{match.match_type}"
                if key not in seen:
                    seen.add(key)
                    links.append({
                        "id": str(uuid.uuid4()),
                        "event_id": event_id,
                        "link_type": match.match_type,
                        "link_target": match.matched_value,
                        "relevance_score": match.confidence,
                        "impact_channel": "",
                        "link_source": "rules",
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    })
            elif match.match_type in ("ticker", "isin", "company_name") and match.holding_id:
                # Only add rule-based holding links if no LLM score exists
                key = f"{event_id}:{match.holding_id}:holding"
                if key not in seen:
                    seen.add(key)
                    links.append({
                        "id": str(uuid.uuid4()),
                        "event_id": event_id,
                        "link_type": "holding",
                        "link_target": match.holding_id,
                        "relevance_score": match.confidence,
                        "impact_channel": "",
                        "link_source": "rules",
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    })

        return links

    def _match_to_dict(self, match: RuleMatch) -> dict:
        return {
            "rule_name": match.rule_name,
            "match_type": match.match_type,
            "matched_value": match.matched_value,
            "holding_id": match.holding_id,
            "sector": match.sector,
            "geography": match.geography,
            "theme": match.theme,
            "currency": match.currency,
            "confidence": match.confidence,
        }

    def _score_to_dict(self, score) -> dict:
        if isinstance(score, dict):
            return score
        return {
            "event_id": score.event_id,
            "holding_id": score.holding_id,
            "relevance": score.relevance,
            "impact_channel": score.impact_channel,
            "direction": score.direction,
            "horizon": score.horizon,
            "materiality": score.materiality,
            "confidence": score.confidence,
            "explanation": score.explanation,
            "model_id": score.model_id,
            "prompt_hash": score.prompt_hash,
        }
