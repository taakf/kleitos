"""Collection agent -- fetches events from external sources.

Responsibilities:
- Iterate over enabled sources in the ``sources`` table
- Validate URLs against a domain allowlist
- Fetch content (respecting rate limits)
- Deduplicate events using content hashing
- Link events to holdings via ticker / sector / geography matching
- **Macro event screening**: use LLM to catch indirect impacts on
  holdings from events that don't mention any portfolio ticker directly
  (e.g. war → oil → inflation → tech stocks)
- Update ``source.last_fetched_at``
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, ClassVar
from urllib.parse import urlparse

from sqlalchemy import select, update

from src.database.models import Holding, Security, Source, Event, EventLink

from .base import BaseAgent

logger = logging.getLogger(__name__)

# Default seconds to wait between requests to the same source domain.
DEFAULT_RATE_LIMIT_SECONDS: float = 1.0

# Maximum headlines to batch-send to the LLM for macro screening.
MACRO_SCREEN_BATCH_SIZE: int = 20

# ---------------------------------------------------------------------------
# Macro event screening prompt
# ---------------------------------------------------------------------------
MACRO_SCREEN_PROMPT = """\
You are a senior portfolio analyst performing a macro event screen.

Below are news headlines.  Some may directly mention a portfolio ticker —
those already have a direct link.  Your job is to find **INDIRECT** impacts
that simple ticker matching would miss.  Think like a portfolio manager:

- "Apple places massive chip order" → directly about AAPL, but ALSO
  affects NVDA (TSMC capacity competition), GOOGL (chip supply pressure)
- "US sanctions Russian oil" → no ticker mentioned, but affects ALL
  holdings through: oil spike → inflation → Fed rates → valuations
- "EU passes AI regulation" → directly about regulation, but affects
  MSFT, GOOGL, NVDA through compliance costs and market access

Types of indirect connections to look for:
- Supply chain: supplier/customer relationships, shared manufacturing
- Competitive dynamics: one company's gain is another's loss
- Macro transmission: geopolitics → commodities → inflation → rates → sectors
- Regulatory spillover: regulation in one area affects adjacent industries
- Sector contagion: bad news for one bank can affect all financials
- Currency/trade: tariffs, FX moves, trade agreements
- Consumer behavior: shifts in spending patterns across sectors

Portfolio holdings (ticker → sector, geography):
{portfolio_summary}

Headlines to screen:
{headlines}

For EACH headline, identify holdings that could be INDIRECTLY affected
(skip connections that are obvious direct mentions — those are already handled).

Return a JSON array (empty array [] if nothing indirect is relevant):
[
  {{
    "headline_index": <0-based index>,
    "affected_tickers": ["TICK1", "TICK2"],
    "causal_chain": "<event> → <mechanism> → <impact on ticker>",
    "impact_direction": "positive|negative|mixed",
    "relevance_score": <0.1 to 0.6>,
    "confidence": <0.1 to 0.8>
  }}
]

Rules:
- Focus on NON-OBVIOUS connections — the valuable insight is what a human might miss
- A single headline can produce multiple entries if different tickers are affected through different causal chains
- relevance_score: 0.2-0.3 for plausible but uncertain, 0.3-0.5 for likely, 0.5-0.6 for strong indirect
- confidence should reflect how certain the causal chain is
- Keep causal_chain concise (one line showing the full chain)
- Do NOT include the directly mentioned ticker — only indirect ones
- Return ONLY valid JSON
"""


class CollectionAgent(BaseAgent):
    """Collects events from registered external sources."""

    agent_name: ClassVar[str] = "collection"
    read_permissions: ClassVar[list[str]] = ["holdings", "securities", "sources"]
    write_permissions: ClassVar[list[str]] = ["events", "event_links", "sources", "agent_runs"]

    async def run(self, **kwargs: Any) -> dict[str, Any]:
        """Entry point -- delegates to :meth:`collect_all`."""
        return await self.collect_all()

    async def collect_all(self) -> dict[str, Any]:
        """Fetch events from every enabled source.

        Returns
        -------
        dict
            Summary with ``events_created``, ``duplicates_skipped``,
            ``links_created``, ``errors``.
        """
        await self._log_run_start()
        events_created: list[dict[str, Any]] = []
        duplicates_skipped: int = 0
        links_created: int = 0
        errors: list[str] = []

        try:
            self._check_permission("sources", "read")
            sources = await self._get_enabled_sources()

            for source in sources:
                try:
                    created, dupes, links = await self._process_source(source)
                    events_created.extend(created)
                    duplicates_skipped += dupes
                    links_created += links
                except Exception as exc:
                    msg = f"Error processing source {source['id']} ({source['name']}): {exc}"
                    logger.error(msg, exc_info=True)
                    errors.append(msg)
                    # Mark source as failed
                    try:
                        await self._update_source_status(source["id"], "error")
                    except Exception:
                        pass  # Don't let status update failure mask the real error

            # --- Pass 2: Macro event screening (LLM) ---
            # ALL new events are sent to the LLM to find indirect impacts.
            # Even a direct "AAPL earnings" article could affect NVDA, GOOGL
            # through supply chain, competitive, or sector-wide dynamics.
            macro_links = 0
            macro_screened = 0
            try:
                macro_links, macro_screened = await self._macro_screen_events(
                    [e["event_id"] for e in events_created]
                )
                links_created += macro_links
            except Exception as exc:
                logger.warning("Macro screening failed (non-fatal): %s", exc)
                errors.append(f"Macro screening error: {exc}")

            summary = {
                "events_created": len(events_created),
                "duplicates_skipped": duplicates_skipped,
                "links_created": links_created,
                "macro_screened": macro_screened,
                "macro_links_created": macro_links,
                "sources_processed": len(sources),
                "errors": len(errors),
            }
            await self._log_run_complete(result_summary=summary)
            return summary

        except Exception as exc:
            await self._log_run_error(exc)
            raise

    # -- source processing -------------------------------------------------

    async def _get_enabled_sources(self) -> list[dict[str, Any]]:
        """Fetch all sources where ``enabled`` is True."""
        async with self._get_db() as session:
            stmt = select(Source).where(Source.enabled.is_(True))
            rows = (await session.execute(stmt)).scalars().all()

        return [
            {
                "id": s.id,
                "name": s.name,
                "url": s.url or "",
                "domain": s.domain,
                "parser_id": s.parser_id,
                "source_type": s.source_type,
                "rate_limit_rpm": s.rate_limit_rpm,
            }
            for s in rows
        ]

    async def _process_source(
        self, source: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], int, int]:
        """Fetch, validate, deduplicate, and persist events from one source.

        Returns (created_events, duplicates_skipped, links_created).
        """
        url = source["url"]
        # Enforce domain allowlist: use the source's registered domain as the
        # minimum allowlist. If the source has no domain, allow any (backward compat).
        source_domain = source.get("domain", "")
        allowed = [source_domain] if source_domain else []
        if not self._validate_url(url, allowed):
            raise ValueError(f"URL {url} is not in the allowed domain list for source {source['id']}")

        # Respect rate limit
        rate_limit = source.get("rate_limit", DEFAULT_RATE_LIMIT_SECONDS)
        await asyncio.sleep(rate_limit)

        raw_events = await self._fetch_events_from_source(source)

        created: list[dict[str, Any]] = []
        dupes = 0
        links = 0

        for raw in raw_events:
            content_hash = self._compute_content_hash(raw)

            if await self._is_duplicate(content_hash):
                dupes += 1
                continue

            event_id = await self._persist_event(raw, source, content_hash)
            created.append({"event_id": event_id, "title": raw.get("title")})

            enriched = self._enrich_event_metadata(raw)
            link_count = await self._link_event_to_holdings(event_id, enriched)
            links += link_count

        # Mark source as successfully fetched
        await self._update_source_status(source["id"], "ok")

        return created, dupes, links

    # -- URL validation ----------------------------------------------------

    @staticmethod
    def _validate_url(url: str, allowed_domains: list[str]) -> bool:
        """Return True if *url*'s domain is in the allowlist.

        If the allowlist is empty, all domains are permitted (open source).
        """
        if not allowed_domains:
            return True
        parsed = urlparse(url)
        domain = parsed.hostname or ""
        return any(domain.endswith(d) for d in allowed_domains)

    @staticmethod
    def _extract_tickers_from_text(text: str, portfolio_tickers: set[str]) -> list[tuple[str, float]]:
        """Scan text for mentions of portfolio tickers.

        Returns a list of (ticker, relevance_score) tuples.
        Uses strict word-boundary matching to avoid false positives.
        Short tickers (1-2 chars) require a cash-tag prefix ($PG) or
        parenthetical context to match, preventing noise from common
        letter combinations appearing in normal text.
        """
        import re
        if not text or not portfolio_tickers:
            return []

        found: list[tuple[str, float]] = []
        text_upper = text.upper()

        for ticker in portfolio_tickers:
            escaped = re.escape(ticker)
            if len(ticker) <= 2:
                # Short tickers: require cash-tag ($PG), parenthetical (PG),
                # or explicit ticker context (ticker: PG) to avoid false matches
                strict = (
                    r'(?:\$' + escaped + r'(?![A-Z0-9])'  # $PG
                    r'|\(' + escaped + r'\)'                # (PG)
                    r'|(?:ticker|symbol|stock)[:\s]+' + escaped + r'(?![A-Z0-9])'  # ticker: PG
                    r')'
                )
                if re.search(strict, text_upper):
                    found.append((ticker, 0.9))
            else:
                # 3+ char tickers: standard word-boundary matching
                pattern = r'(?<![A-Z0-9])' + escaped + r'(?![A-Z0-9])'
                if re.search(pattern, text_upper):
                    found.append((ticker, 1.0))

        return found

    # -- content hashing / deduplication -----------------------------------

    @staticmethod
    def _compute_content_hash(event_data: dict[str, Any]) -> str:
        """SHA-256 hash of the event's distinguishing content."""
        canonical = f"{event_data.get('title', '')}|{event_data.get('url', '')}|{event_data.get('published_at', '')}"
        return hashlib.sha256(canonical.encode()).hexdigest()

    async def _is_duplicate(self, content_hash: str) -> bool:
        """Check whether an event with this hash already exists."""
        async with self._get_db() as session:
            stmt = select(Event).where(Event.dedup_hash == content_hash)
            row = (await session.execute(stmt)).scalars().first()
        return row is not None

    # -- persistence -------------------------------------------------------

    async def _persist_event(
        self,
        raw: dict[str, Any],
        source: dict[str, Any],
        content_hash: str,
    ) -> str:
        """Insert a new Event row and return its ID."""
        self._check_permission("events", "write")
        event_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        new_event = Event(
            id=event_id,
            source_id=source["id"],
            title=raw.get("title"),
            url=raw.get("url"),
            summary=raw.get("summary"),
            event_type=raw.get("event_type"),
            published_at=raw.get("published_at"),
            dedup_hash=content_hash,
            raw_data=json.dumps(raw) if isinstance(raw, dict) else str(raw),
            fetched_at=now,
            created_at=now,
        )

        async with self._get_db() as session:
            session.add(new_event)
            await session.commit()

        logger.info("Persisted event  id=%s  title=%s", event_id, raw.get("title"))
        return event_id

    @staticmethod
    def _enrich_event_metadata(raw: dict[str, Any]) -> dict[str, Any]:
        """Add inferred sectors and geographies to event metadata from keywords.

        This fills the ``sectors`` and ``geographies`` lists that the
        sector/geography matching path in _link_event_to_holdings uses.
        Without this, those matching paths are dead code because RSS
        parsers never populate these fields.
        """
        text = f"{raw.get('title', '')} {raw.get('summary', '')}".lower()
        if not text.strip():
            return raw

        # Sector keyword map — conservative, high-precision keywords only
        _SECTOR_KEYWORDS: dict[str, list[str]] = {
            "Information Technology": ["semiconductor", "chip maker", "software", "cloud computing", "cybersecurity", "saas", "ai chip", "data center"],
            "Financials": ["banking", "central bank", "interest rate", "fed funds", "monetary policy", "credit", "loan", "mortgage", "insurance"],
            "Health Care": ["pharma", "drug approval", "fda", "clinical trial", "biotech", "vaccine", "healthcare"],
            "Energy": ["oil price", "crude oil", "natural gas", "opec", "refinery", "energy sector", "petroleum", "lng"],
            "Consumer Discretionary": ["retail sales", "consumer spending", "auto sales", "housing market", "e-commerce"],
            "Consumer Staples": ["food price", "grocery", "consumer goods", "cpg", "fmcg"],
            "Communication Services": ["streaming", "social media", "telecom", "broadband", "advertising revenue"],
            "Industrials": ["manufacturing", "supply chain", "logistics", "aerospace", "defense contract", "infrastructure"],
            "Materials": ["mining", "commodity price", "steel", "lithium", "rare earth", "copper"],
            "Real Estate": ["real estate", "reit", "property market", "housing", "commercial real estate"],
            "Utilities": ["utility", "power grid", "electricity", "renewable energy", "solar", "wind energy"],
        }
        _GEO_KEYWORDS: dict[str, list[str]] = {
            "united states": ["us economy", "wall street", "u.s.", "federal reserve", "american", "us dollar", "nasdaq", "s&p 500", "dow jones"],
            "china": ["china", "chinese", "beijing", "shanghai"],
            "european union": ["eurozone", "ecb", "european", "eu economy", "euro area"],
            "united kingdom": ["uk economy", "bank of england", "british", "ftse", "london"],
            "japan": ["japan", "boj", "tokyo", "nikkei", "yen"],
        }

        sectors = []
        for sector, keywords in _SECTOR_KEYWORDS.items():
            if any(kw in text for kw in keywords):
                sectors.append(sector)

        geos = []
        for geo, keywords in _GEO_KEYWORDS.items():
            if any(kw in text for kw in keywords):
                geos.append(geo)

        enriched = dict(raw)
        if sectors:
            enriched["sectors"] = sectors
        if geos:
            enriched["geographies"] = geos
        return enriched

    async def _link_event_to_holdings(
        self, event_id: str, raw: dict[str, Any]
    ) -> int:
        """Create EventLink rows by matching tickers in event text and metadata.

        Uses ticker extraction from title/summary with relevance scoring:
        - 1.0: ticker mentioned in title
        - 0.7: ticker mentioned in summary only
        - 0.5: sector/geography match only

        Returns the number of links created.
        """
        self._check_permission("event_links", "write")
        self._check_permission("holdings", "read")
        self._check_permission("securities", "read")

        linked_holding_ids: set[str] = set()
        link_count = 0

        async with self._get_db() as session:
            # Get all portfolio tickers
            h_stmt = select(Holding).where(Holding.status == "active")
            all_holdings = (await session.execute(h_stmt)).scalars().all()
            ticker_to_holdings: dict[str, list] = {}
            for h in all_holdings:
                ticker_to_holdings.setdefault(h.ticker, []).append(h)

            portfolio_tickers = set(ticker_to_holdings.keys())

            # Extract tickers from event text
            title = raw.get("title", "")
            summary = raw.get("summary", "")

            # Check title first (highest relevance)
            title_matches = self._extract_tickers_from_text(title, portfolio_tickers)
            # Check summary (lower relevance)
            summary_matches = self._extract_tickers_from_text(summary, portfolio_tickers)

            # Merge: title score takes priority
            ticker_scores: dict[str, float] = {}
            for ticker, score in summary_matches:
                ticker_scores[ticker] = max(ticker_scores.get(ticker, 0), 0.7)
            for ticker, score in title_matches:
                ticker_scores[ticker] = max(ticker_scores.get(ticker, 0), 1.0)

            # Also include explicitly tagged tickers from raw data
            for t in raw.get("tickers", []):
                t_upper = t.strip().upper()
                if t_upper in portfolio_tickers:
                    ticker_scores[t_upper] = max(ticker_scores.get(t_upper, 0), 1.0)

            # Company name matching: check if security names appear in event text
            # Uses word-boundary regex to avoid "Apple" matching "pineapple" etc.
            import re as _re
            text_lower = f"{title} {summary}".lower()
            if text_lower.strip():
                sec_stmt = select(Security).where(
                    Security.ticker.in_(list(portfolio_tickers))
                )
                securities = (await session.execute(sec_stmt)).scalars().all()
                for sec in securities:
                    name = sec.name
                    if name and len(name) > 3:
                        # Word-boundary match: "nvidia" matches "Nvidia's" but
                        # not "Nvidia" inside "nonvidia" (unlikely but safe)
                        pattern = r'\b' + _re.escape(name.lower()) + r'\b'
                        if _re.search(pattern, text_lower):
                            if sec.ticker not in ticker_scores:
                                ticker_scores[sec.ticker] = 0.8

            # Create links for ticker matches
            for ticker, score in ticker_scores.items():
                for h in ticker_to_holdings.get(ticker, []):
                    if h.id not in linked_holding_ids:
                        link_id = str(uuid.uuid4())
                        session.add(EventLink(
                            id=link_id,
                            event_id=event_id,
                            link_type="ticker_match",
                            link_target=h.id,
                            relevance_score=score,
                            created_at=datetime.now(timezone.utc).isoformat(),
                        ))
                        linked_holding_ids.add(h.id)
                        link_count += 1

            # Sector/geography matching for holdings not yet linked.
            # PRECISION POLICY: Require BOTH sector AND geography to match.
            # Geography-only matching is too broad (all US events → all US
            # holdings).  Sector-only is also too broad.  Only the
            # intersection produces useful signal.
            sectors_mentioned: list[str] = raw.get("sectors", [])
            geos_mentioned: list[str] = raw.get("geographies", [])

            if sectors_mentioned and geos_mentioned:
                # Both dimensions present — require AND match
                sec_stmt = (
                    select(Security)
                    .where(Security.sector.in_(sectors_mentioned))
                    .where(Security.geography.in_(geos_mentioned))
                )
                securities = (await session.execute(sec_stmt)).scalars().all()
                matched_tickers = {s.ticker for s in securities}

                for ticker in matched_tickers:
                    for h in ticker_to_holdings.get(ticker, []):
                        if h.id not in linked_holding_ids:
                            link_id = str(uuid.uuid4())
                            session.add(EventLink(
                                id=link_id,
                                event_id=event_id,
                                link_type="sector_geo_match",
                                link_target=h.id,
                                relevance_score=0.4,
                                created_at=datetime.now(timezone.utc).isoformat(),
                            ))
                            linked_holding_ids.add(h.id)
                            link_count += 1

            if link_count:
                await session.commit()

        return link_count

    async def _update_source_status(
        self, source_id: str, status: str = "ok"
    ) -> None:
        """Update ``last_fetched_at`` and ``last_status`` on the source row."""
        self._check_permission("sources", "write")
        now = datetime.now(timezone.utc).isoformat()
        async with self._get_db() as session:
            stmt = (
                update(Source)
                .where(Source.id == source_id)
                .values(last_fetched_at=now, last_status=status)
            )
            await session.execute(stmt)
            await session.commit()

    # -- macro event screening (LLM) ----------------------------------------

    async def _macro_screen_events(
        self, event_ids: list[str]
    ) -> tuple[int, int]:
        """Screen ALL new events for indirect portfolio impact.

        Even events that already matched a ticker directly (e.g. "Apple
        earnings beat") may indirectly affect OTHER holdings through
        supply chain, competitive, or macro dynamics.

        Uses the LLM to reason about causal chains:
        "Apple massive chip order" → TSMC capacity tight → NVDA supply risk

        Returns (links_created, events_screened).
        """
        from src.llm.client import is_llm_available

        if not is_llm_available():
            logger.info("Macro screening skipped — LLM unavailable")
            return 0, 0

        if not event_ids:
            return 0, 0

        # Fetch ALL new events for screening
        all_events = await self._get_events_for_screening(event_ids)
        if not all_events:
            return 0, 0

        # Build portfolio summary for the prompt
        portfolio_summary = await self._build_portfolio_summary()
        if not portfolio_summary:
            logger.debug("Macro screening: no active holdings, skipping")
            return 0, 0

        # Process in batches
        total_links = 0
        total_screened = 0

        for i in range(0, len(all_events), MACRO_SCREEN_BATCH_SIZE):
            batch = all_events[i : i + MACRO_SCREEN_BATCH_SIZE]
            links = await self._screen_batch(batch, portfolio_summary)
            total_links += links
            total_screened += len(batch)

        logger.info(
            "Macro screening complete: %d events screened, %d indirect links created",
            total_screened,
            total_links,
        )
        return total_links, total_screened

    async def _get_events_for_screening(self, event_ids: list[str]) -> list[dict[str, Any]]:
        """Return all events by *event_ids* for macro screening."""
        async with self._get_db() as session:
            stmt = select(Event).where(Event.id.in_(event_ids))
            rows = (await session.execute(stmt)).scalars().all()

        return [
            {
                "id": e.id,
                "title": e.title or "",
                "summary": e.summary or "",
                "event_type": e.event_type or "",
            }
            for e in rows
        ]

    async def _build_portfolio_summary(self) -> str:
        """Build a concise portfolio summary string for the LLM prompt."""
        async with self._get_db() as session:
            h_stmt = select(Holding).where(Holding.status == "active")
            holdings = (await session.execute(h_stmt)).scalars().all()

            if not holdings:
                return ""

            # Get security info for each holding
            tickers = [h.ticker for h in holdings]
            s_stmt = select(Security).where(Security.ticker.in_(tickers))
            securities = (await session.execute(s_stmt)).scalars().all()

        sec_map = {s.ticker: s for s in securities}

        lines = []
        for h in holdings:
            sec = sec_map.get(h.ticker)
            sector = sec.sector if sec and sec.sector else "Unknown"
            geo = sec.geography if sec and sec.geography else "Unknown"
            themes = ""
            if sec and sec.themes:
                try:
                    theme_list = json.loads(sec.themes) if isinstance(sec.themes, str) else sec.themes
                    themes = f" [{', '.join(theme_list)}]" if theme_list else ""
                except (json.JSONDecodeError, TypeError):
                    themes = ""
            lines.append(f"  {h.ticker} → {sector}, {geo}{themes}")

        return "\n".join(lines)

    async def _screen_batch(
        self,
        events: list[dict[str, Any]],
        portfolio_summary: str,
    ) -> int:
        """Send a batch of headlines to the LLM for macro screening.

        Returns the number of links created.
        """
        from src.llm.client import call_llm_json

        # Format headlines
        headline_lines = []
        for idx, evt in enumerate(events):
            title = evt["title"]
            summary_snippet = (evt["summary"] or "")[:150]
            headline_lines.append(f"  [{idx}] {title}")
            if summary_snippet:
                headline_lines.append(f"      {summary_snippet}")

        headlines_text = "\n".join(headline_lines)

        prompt = MACRO_SCREEN_PROMPT.format(
            portfolio_summary=portfolio_summary,
            headlines=headlines_text,
        )

        try:
            result = await call_llm_json(prompt)
        except Exception as exc:
            logger.warning("Macro screening LLM call failed: %s", exc)
            return 0

        # result should be a list of screening hits
        if not isinstance(result, list):
            # Sometimes the LLM wraps it in an object
            result = result.get("results", result.get("screenings", []))
            if not isinstance(result, list):
                logger.warning("Macro screening: unexpected LLM response format")
                return 0

        return await self._create_macro_links(events, result)

    async def _create_macro_links(
        self,
        events: list[dict[str, Any]],
        screenings: list[dict[str, Any]],
    ) -> int:
        """Create EventLink rows from macro screening results.

        Skips links that already exist from Pass 1 (direct ticker matching)
        to avoid duplicates.
        """
        self._check_permission("event_links", "write")
        self._check_permission("holdings", "read")

        if not screenings:
            return 0

        # Build ticker → holding map
        async with self._get_db() as session:
            h_stmt = select(Holding).where(Holding.status == "active")
            all_holdings = (await session.execute(h_stmt)).scalars().all()
            ticker_to_holdings: dict[str, list] = {}
            for h in all_holdings:
                ticker_to_holdings.setdefault(h.ticker, []).append(h)

            # Get ALL existing links for these events so we don't duplicate
            event_ids = [e["id"] for e in events]
            existing_stmt = select(EventLink).where(EventLink.event_id.in_(event_ids))
            existing_links = (await session.execute(existing_stmt)).scalars().all()
            existing_pairs: set[tuple[str, str]] = {
                (el.event_id, el.link_target) for el in existing_links
            }

            link_count = 0

            for screening in screenings:
                try:
                    idx = screening.get("headline_index", -1)
                    if idx < 0 or idx >= len(events):
                        continue

                    event_id = events[idx]["id"]
                    affected_tickers = screening.get("affected_tickers", [])
                    relevance = min(max(float(screening.get("relevance_score", 0.3)), 0.1), 0.6)
                    causal_chain = screening.get("causal_chain", "")

                    for ticker in affected_tickers:
                        if not isinstance(ticker, str):
                            continue
                        ticker = ticker.strip().upper()
                        for h in ticker_to_holdings.get(ticker, []):
                            # Skip if this event↔holding link already exists
                            if (event_id, h.id) in existing_pairs:
                                logger.debug(
                                    "Macro screening: skipping %s → %s (already linked directly)",
                                    ticker, events[idx]["title"][:40],
                                )
                                continue

                            link_id = str(uuid.uuid4())
                            session.add(EventLink(
                                id=link_id,
                                event_id=event_id,
                                link_type="macro_screen",
                                link_target=h.id,
                                relevance_score=relevance,
                                created_at=datetime.now(timezone.utc).isoformat(),
                            ))
                            existing_pairs.add((event_id, h.id))
                            link_count += 1
                            logger.info(
                                "Macro link: %s → %s (%.2f) | %s",
                                events[idx]["title"][:60],
                                ticker,
                                relevance,
                                causal_chain,
                            )
                except Exception as exc:
                    logger.warning("Failed to create macro link: %s", exc)
                    continue

            if link_count:
                await session.commit()

        return link_count

    # -- fetching -----------------------------------------------------------

    async def _fetch_events_from_source(
        self, source: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Retrieve raw event dicts from an external source via HTTP.

        Uses :class:`SourceFetcher` for rate-limited HTTP, then dispatches
        to the appropriate parser based on ``source_type``.
        """
        import os
        from src.sources.fetcher import SourceFetcher, FetchResult
        from src.sources.registry import SourceConfig, SourceRegistry
        from src.sources.parsers.rss_generic import RSSGenericParser
        from src.sources.parsers.newsapi import NewsAPIParser
        from src.config import PROJECT_ROOT

        # Build a SourceConfig from the DB-sourced dict
        src_config = SourceConfig(
            id=source["id"],
            name=source["name"],
            domain=source.get("domain", ""),
            type=source.get("source_type", "rss"),
            url=source.get("url", ""),
            parser=source.get("parser_id", "rss_generic"),
            rate_limit_rpm=int(60 / max(source.get("rate_limit", 1), 0.1)),
        )

        # Build a minimal registry from the sources YAML (for URL allowlist)
        sources_yaml = PROJECT_ROOT / "config" / "sources.yaml"
        registry = SourceRegistry(sources_yaml)

        fetcher = SourceFetcher(registry=registry, timeout=30.0, max_retries=3)

        # Collect API keys from environment
        api_keys: dict[str, str] = {}
        for var in ("NEWSAPI_KEY", "ALPHAVANTAGE_KEY"):
            val = os.environ.get(var, "")
            if val:
                api_keys[var] = val

        try:
            result: FetchResult = await fetcher.fetch_source(src_config, api_keys=api_keys)
        finally:
            await fetcher.close()

        if not result.success or not result.content:
            if result.error:
                logger.warning("Fetch failed for source %s: %s", source["id"], result.error)
            return []

        # Dispatch to parser
        parser_id = source.get("parser_id", "rss_generic")
        if parser_id == "newsapi":
            parser = NewsAPIParser()
        else:
            parser = RSSGenericParser()

        parsed_events = parser.parse(result.content, source["id"])

        # Convert ParsedEvent dataclasses to plain dicts for downstream processing
        events: list[dict[str, Any]] = []
        for pe in parsed_events:
            events.append({
                "title": pe.title,
                "url": pe.url,
                "summary": pe.summary,
                "event_type": pe.event_type,
                "published_at": pe.published_at,
                "tickers": [],  # parsers don't extract tickers; linking happens downstream
                "sectors": [],
                "geographies": [],
                "tags": pe.tags,
                "raw_data": pe.raw_data,
                "external_id": pe.external_id,
            })

        logger.info("Fetched %d events from source %s (%s)", len(events), source["id"], source["name"])
        return events
