"""Base parser interface for all source parsers."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class ParsedEvent:
    """A normalized event extracted from a source."""
    source_id: str
    external_id: str  # Source's own ID or URL
    title: str
    summary: str = ""
    content: str = ""
    url: str = ""
    published_at: str = ""  # ISO format
    event_type: str = ""  # earnings, dividend, macro, etc.
    tags: list[str] = field(default_factory=list)
    raw_data: str = ""  # Original content preserved for audit

    @property
    def dedup_hash(self) -> str:
        """Generate a hash for deduplication based on content.

        Uses ``title|url|published_at`` as the canonical key, matching
        the hash computed by the collection agent and DeduplicationEngine.
        """
        import hashlib
        key = f"{self.title}|{self.url}|{self.published_at}"
        return hashlib.sha256(key.encode()).hexdigest()


class BaseParser(ABC):
    """Abstract base class for source parsers.

    Each source type (RSS, API, scrape) has a parser that knows how to
    extract structured events from the raw content.
    """

    @abstractmethod
    def parse(self, raw_content: str, source_id: str) -> list[ParsedEvent]:
        """Parse raw content into a list of normalized events.

        Args:
            raw_content: Raw response content from the source
            source_id: The source registry ID

        Returns:
            List of parsed events
        """
        ...

    def classify_event_type(self, title: str, content: str) -> str:
        """Basic rule-based event type classification.

        Override in subclasses for source-specific classification.
        """
        text = f"{title} {content}".lower()

        # Keys match the canonical event types used by Coverage QA and Risk agents.
        # "analyst_action" (not "analyst") and default "news" (not "general")
        # so the pipeline is consistent end-to-end.
        type_keywords = {
            "earnings": ["earnings", "quarterly results", "q1 ", "q2 ", "q3 ", "q4 ",
                         "revenue", "profit", " eps ", " eps,", "income report", "beat estimates",
                         "missed estimates", "guidance", "outlook"],
            "dividend": ["dividend", "ex-dividend", "ex-date", "payout", "distribution",
                         "special dividend", "dividend yield"],
            "analyst_action": ["upgrade", "downgrade", "price target", "rating",
                               "initiate", "coverage", "buy rating", "sell rating",
                               "analyst", "overweight", "underweight", "outperform",
                               "underperform", "hold rating", "neutral rating"],
            "regulatory": ["regulation", "regulatory", "sec ", "fda ", "antitrust",
                          "compliance", "fine", "penalty", "investigation",
                          "approval", "approved", "rejected"],
            "macro": ["federal reserve", "fed ", "interest rate", "inflation",
                      "gdp ", "unemployment", "central bank", "monetary policy",
                      "fiscal", "treasury", "jobs report", "cpi ", "ppi ",
                      "yield curve", "rate cut", "rate hike"],
            "geopolitical": ["sanctions", "tariff", "trade war", "geopolitical",
                            "conflict", "election", "government", "war ",
                            "military", "embargo", "diplomatic"],
            "ma": ["acquisition", "merger", "takeover", "buyout", "deal",
                   "bid for", "acquires", "spin-off", "spinoff", "divestiture"],
            "management": ["ceo", "cfo", "cto", "appoints", "resigns",
                          "executive", "board of directors", "management change",
                          "steps down", "fired", "hired"],
            "supply_chain": ["supply chain", "shortage", "disruption",
                            "logistics", "inventory", "chip shortage",
                            "semiconductor shortage", "backlog"],
            "litigation": ["lawsuit", "litigation", "court", "legal",
                          "settlement", "verdict", "class action", "indictment"],
        }

        for event_type, keywords in type_keywords.items():
            if any(kw in text for kw in keywords):
                return event_type

        return "news"
