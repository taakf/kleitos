"""LLM-based impact scoring — second stage of the Impact Mapping Engine."""

import hashlib
import json
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ImpactScore:
    """Scored impact of an event on a portfolio entity."""
    event_id: str
    holding_id: str | None
    sector: str | None
    geography: str | None
    theme: str | None
    relevance: float  # 0.0 to 1.0
    impact_channel: str  # revenue, margins, regulation, fx, sentiment, etc.
    direction: str  # positive, negative, mixed, unclear
    horizon: str  # immediate, near_term, medium_term, long_term
    materiality: str  # immaterial, watch, important, critical
    confidence: str  # low, medium, high
    explanation: str  # Plain-English explanation
    model_id: str
    prompt_hash: str


SCORING_PROMPT_TEMPLATE = """You are a portfolio analyst. Analyze how a news event affects a portfolio holding.

EVENT:
Title: {event_title}
Summary: {event_summary}
Type: {event_type}
Published: {event_published}

HOLDING:
Ticker: {holding_ticker}
Name: {holding_name}
Sector: {holding_sector}
Geography: {holding_geography}
Weight: {holding_weight}%

MATCH CONTEXT:
Rule matches: {rule_matches}

Analyze the impact and respond with ONLY a JSON object (no markdown, no explanation outside JSON):
{{
  "relevance": <float 0.0-1.0, how directly this event affects this holding>,
  "impact_channel": "<one of: revenue, demand, margins, cost_inflation, supply_chain, regulation, financing_cost, fx_translation, valuation_multiple, sentiment, dividend_sustainability, balance_sheet, refinancing>",
  "direction": "<one of: positive, negative, mixed, unclear>",
  "horizon": "<one of: immediate, near_term, medium_term, long_term>",
  "materiality": "<one of: immaterial, watch, important, critical>",
  "confidence": "<one of: low, medium, high>",
  "explanation": "<2-3 sentence explanation of why and how this event affects this holding>"
}}
"""


class ImpactScorer:
    """LLM-based impact scoring for event-holding pairs.

    Uses the Anthropic API to assess relevance, materiality, and impact channels.
    All calls are logged with prompt hashes for auditability.
    """

    def __init__(self, anthropic_client, model: str = "claude-sonnet-4-6"):
        self._client = anthropic_client
        self._model = model

    async def score_impact(
        self,
        event: dict,
        holding: dict,
        security: dict,
        rule_matches: list[dict],
    ) -> ImpactScore | None:
        """Score the impact of a single event on a single holding.

        Args:
            event: Event dict with title, summary, event_type, published_at
            holding: Holding dict with id, ticker, weight_pct
            security: Security dict with name, sector, geography
            rule_matches: List of rule match dicts from Stage 1

        Returns:
            ImpactScore or None if scoring fails
        """
        prompt = SCORING_PROMPT_TEMPLATE.format(
            event_title=event.get("title", ""),
            event_summary=event.get("summary", "")[:500],
            event_type=event.get("event_type", "general"),
            event_published=event.get("published_at", ""),
            holding_ticker=holding.get("ticker", ""),
            holding_name=security.get("name", ""),
            holding_sector=security.get("sector", "Unknown"),
            holding_geography=security.get("geography", "Unknown"),
            holding_weight=f"{(holding.get('weight_pct', 0) or 0):.1f}",
            rule_matches=json.dumps(rule_matches, default=str),
        )

        prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()[:16]

        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=500,
                temperature=0.1,
                messages=[{"role": "user", "content": prompt}],
            )

            content = response.content[0].text.strip()

            # Parse JSON from response (handle potential markdown wrapping)
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
            content = content.strip()

            result = json.loads(content)

            return ImpactScore(
                event_id=event.get("id", ""),
                holding_id=holding.get("id"),
                sector=security.get("sector"),
                geography=security.get("geography"),
                theme=None,
                relevance=float(result.get("relevance", 0)),
                impact_channel=result.get("impact_channel", "unknown"),
                direction=result.get("direction", "unclear"),
                horizon=result.get("horizon", "near_term"),
                materiality=result.get("materiality", "watch"),
                confidence=result.get("confidence", "low"),
                explanation=result.get("explanation", ""),
                model_id=self._model,
                prompt_hash=prompt_hash,
            )

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM scoring response: {e}")
            return None
        except Exception as e:
            logger.error(f"LLM scoring failed: {e}")
            return None

    async def score_batch(
        self,
        event: dict,
        holdings_with_securities: list[tuple[dict, dict]],
        rule_matches_by_holding: dict[str, list[dict]],
        min_relevance: float = 0.3,
    ) -> list[ImpactScore]:
        """Score an event against multiple holdings.

        Filters out scores below min_relevance threshold.
        """
        scores = []
        for holding, security in holdings_with_securities:
            holding_id = holding.get("id", "")
            matches = rule_matches_by_holding.get(holding_id, [])
            score = await self.score_impact(event, holding, security, matches)
            if score and score.relevance >= min_relevance:
                scores.append(score)
            # Small delay to avoid rate limiting
            import asyncio
            await asyncio.sleep(0.5)
        return scores
