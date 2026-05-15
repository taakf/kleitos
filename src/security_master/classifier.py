"""Security classifier – sector, geography, theme tagging."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ── canonical sector taxonomy ────────────────────────────────────────────
SECTOR_TAXONOMY = {
    "technology": ["software", "hardware", "semiconductors", "internet", "it services", "consumer electronics"],
    "financials": ["banks", "insurance", "asset management", "fintech", "exchanges"],
    "healthcare": ["pharma", "biotech", "medical devices", "health services", "diagnostics"],
    "consumer discretionary": ["retail", "automotive", "luxury", "apparel", "travel"],
    "consumer staples": ["food", "beverages", "household", "personal care", "tobacco"],
    "industrials": ["aerospace", "defense", "machinery", "transportation", "construction"],
    "energy": ["oil & gas", "renewables", "utilities", "nuclear", "mining services"],
    "materials": ["chemicals", "metals", "mining", "paper", "construction materials"],
    "real estate": ["reits", "development", "property management"],
    "utilities": ["electric", "gas", "water", "multi-utility"],
    "communication services": ["telecom", "media", "entertainment", "advertising"],
}

GEOGRAPHY_MAP = {
    "US": "united states", "GB": "united kingdom", "DE": "germany",
    "FR": "france", "CH": "switzerland", "JP": "japan",
    "CN": "china", "KR": "south korea", "TW": "taiwan",
    "IN": "india", "BR": "brazil", "AU": "australia",
    "CA": "canada", "HK": "hong kong", "SG": "singapore",
    "NL": "netherlands", "SE": "sweden", "NO": "norway",
    "DK": "denmark", "FI": "finland", "IE": "ireland",
    "IT": "italy", "ES": "spain", "GR": "greece",
}

# Reverse map for ISIN prefix → geography.
#
# IMPORTANT — Phase 9 distinction:
# ``geography_from_isin`` returns the **listing / domicile country**
# implied by the ISIN prefix.  This is the country whose CSD assigned
# the identifier — it is **not** the company's revenue geography.
# Revenue geography (where the issuer actually sells) is a separate,
# unimplemented concept tracked in :mod:`src.intelligence.listing`.
ISIN_COUNTRY_MAP = {
    "US": "united states", "GB": "united kingdom", "DE": "germany",
    "FR": "france", "CH": "switzerland", "JP": "japan",
    "CN": "china", "KR": "south korea", "TW": "taiwan",
    "IN": "india", "BR": "brazil", "AU": "australia",
    "CA": "canada", "HK": "hong kong", "SG": "singapore",
    "NL": "netherlands", "SE": "sweden", "NO": "norway",
    "DK": "denmark", "FI": "finland", "IE": "ireland",
    "IT": "italy", "ES": "spain", "LU": "luxembourg",
    "AT": "austria", "BE": "belgium", "PT": "portugal",
    "IL": "israel", "ZA": "south africa", "MX": "mexico",
    "CL": "chile", "CO": "colombia", "PE": "peru",
    "AR": "argentina", "NZ": "new zealand",
    # Phase 9 — required for ATHEX corporate-events fetcher to identify
    # Greek-listed holdings by ISIN prefix.
    "GR": "greece",
}


@dataclass
class SecurityProfile:
    """Enriched security profile from reference data."""
    ticker: str
    isin: Optional[str] = None
    name: Optional[str] = None
    sector: Optional[str] = None
    subsector: Optional[str] = None
    geography: Optional[str] = None
    themes: list[str] = field(default_factory=list)
    currency: Optional[str] = None
    market_cap_bucket: Optional[str] = None  # mega / large / mid / small / micro
    exchange: Optional[str] = None


class SecurityClassifier:
    """Classifies securities into sector, geography, and themes.

    This operates on locally-held reference data.  It does NOT call
    any external API – the intake agent is responsible for enriching
    raw holdings and writing them to the securities table.
    """

    def __init__(self, db=None):
        self._db = db

    # ── geography from ISIN ──────────────────────────────────────────
    @staticmethod
    def geography_from_isin(isin: str) -> Optional[str]:
        """Derive geography from the 2-letter ISIN prefix."""
        if not isin or len(isin) < 2:
            return None
        prefix = isin[:2].upper()
        return ISIN_COUNTRY_MAP.get(prefix)

    # ── sector validation ────────────────────────────────────────────
    @staticmethod
    def validate_sector(sector: str) -> tuple[Optional[str], Optional[str]]:
        """Return (canonical_sector, subsector) or (None, None)."""
        sector_lower = sector.lower().strip()
        for canonical, subs in SECTOR_TAXONOMY.items():
            if sector_lower == canonical:
                return canonical, None
            if sector_lower in subs:
                return canonical, sector_lower
        return None, None

    # ── themes parsing ───────────────────────────────────────────────
    @staticmethod
    def parse_themes(themes_raw) -> list[str]:
        """Parse themes from various formats (JSON string, list, CSV)."""
        if isinstance(themes_raw, list):
            return [t.strip().lower() for t in themes_raw if t.strip()]
        if isinstance(themes_raw, str):
            themes_raw = themes_raw.strip()
            if themes_raw.startswith("["):
                try:
                    parsed = json.loads(themes_raw)
                    return [t.strip().lower() for t in parsed if isinstance(t, str)]
                except json.JSONDecodeError:
                    pass
            # Comma-separated fallback
            return [t.strip().lower() for t in themes_raw.split(",") if t.strip()]
        return []

    # ── full classification ──────────────────────────────────────────
    def classify(self, raw: dict) -> SecurityProfile:
        """Build a SecurityProfile from raw reference data."""
        ticker = raw.get("ticker", "").upper()
        isin = raw.get("isin", "")
        name = raw.get("name", "")
        sector_input = raw.get("sector", "")
        subsector_input = raw.get("subsector", "")
        geography = raw.get("geography", "")
        themes_raw = raw.get("themes", "[]")
        currency = raw.get("currency", "")
        market_cap = raw.get("market_cap")

        # Validate / normalise sector
        canonical_sector, canonical_sub = self.validate_sector(sector_input)
        if canonical_sector is None and sector_input:
            # Fall back to raw value – let humans fix later
            canonical_sector = sector_input.lower().strip()
        if subsector_input and canonical_sub is None:
            canonical_sub = subsector_input.lower().strip()

        # Geography: explicit > ISIN-derived
        if not geography and isin:
            geography = self.geography_from_isin(isin) or ""

        # Themes
        themes = self.parse_themes(themes_raw)

        # Market cap bucket
        bucket = None
        if market_cap is not None:
            try:
                mc = float(market_cap)
                if mc >= 200_000_000_000:
                    bucket = "mega"
                elif mc >= 10_000_000_000:
                    bucket = "large"
                elif mc >= 2_000_000_000:
                    bucket = "mid"
                elif mc >= 300_000_000:
                    bucket = "small"
                else:
                    bucket = "micro"
            except (ValueError, TypeError):
                pass

        return SecurityProfile(
            ticker=ticker,
            isin=isin,
            name=name,
            sector=canonical_sector,
            subsector=canonical_sub,
            geography=geography.lower().strip() if geography else None,
            themes=themes,
            currency=currency.upper() if currency else None,
            market_cap_bucket=bucket,
            exchange=raw.get("exchange"),
        )

    # ── batch classify ───────────────────────────────────────────────
    def classify_batch(self, raw_list: list[dict]) -> list[SecurityProfile]:
        """Classify multiple securities."""
        return [self.classify(r) for r in raw_list]

    # ── DB helpers ───────────────────────────────────────────────────
    async def get_security(self, ticker: str) -> Optional[dict]:
        """Fetch a security record from the database."""
        if not self._db:
            return None
        row = await self._db.fetch_one(
            "SELECT * FROM securities WHERE ticker = ?", (ticker.upper(),)
        )
        return dict(row) if row else None

    async def get_all_securities(self) -> list[dict]:
        """Fetch all securities from the database."""
        if not self._db:
            return []
        rows = await self._db.fetch_all("SELECT * FROM securities")
        return [dict(r) for r in rows]

    async def upsert_security(self, profile: SecurityProfile, agent_id: str = "intake") -> str:
        """Insert or update a security record.  Returns ticker.

        Column names match the ORM Security model: ``venue`` (not exchange),
        and required NOT NULL fields ``id``, ``currency``, ``created_at``,
        ``updated_at`` are provided.
        """
        import uuid
        from datetime import datetime, timezone

        if not self._db:
            raise RuntimeError("No database connection")

        existing = await self.get_security(profile.ticker)
        themes_json = json.dumps(profile.themes)
        now = datetime.now(timezone.utc).isoformat()

        if existing:
            await self._db.execute(
                """UPDATE securities SET
                    isin = ?, name = ?, sector = ?, subsector = ?,
                    geography = ?, themes = ?, currency = ?,
                    market_cap_bucket = ?, venue = ?,
                    updated_at = ?
                WHERE ticker = ?""",
                (
                    profile.isin, profile.name, profile.sector, profile.subsector,
                    profile.geography, themes_json, profile.currency or "USD",
                    profile.market_cap_bucket, profile.exchange,
                    now, profile.ticker,
                ),
            )
            action = "updated"
        else:
            sec_id = str(uuid.uuid4())
            await self._db.execute(
                """INSERT INTO securities
                    (id, ticker, isin, name, sector, subsector, geography,
                     themes, currency, market_cap_bucket, venue,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    sec_id, profile.ticker, profile.isin, profile.name,
                    profile.sector, profile.subsector, profile.geography,
                    themes_json, profile.currency or "USD",
                    profile.market_cap_bucket, profile.exchange,
                    now, now,
                ),
            )
            action = "created"

        # Audit trail — column names match ORM AuditLog model
        audit_id = str(uuid.uuid4())
        await self._db.execute(
            """INSERT INTO audit_log
                (id, entity_type, entity_id, action, agent_id, new_value, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (audit_id, "securities", profile.ticker, action, agent_id, themes_json, now),
        )
        logger.info("Security %s %s by %s", profile.ticker, action, agent_id)
        return profile.ticker
