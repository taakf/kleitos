"""
SQLAlchemy 2.0 ORM models for the Axion database.

All IDs are TEXT (UUID strings).  All timestamps are TEXT (ISO-8601).
Uses DeclarativeBase with mapped_column for type-safe column definitions.
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Shared declarative base for every Axion model."""

    pass


# ---------------------------------------------------------------------------
# Portfolio — top-level entity that scopes holdings, trades, alerts, digests
# ---------------------------------------------------------------------------


class Portfolio(Base):
    """A named portfolio that contains holdings, trades, and derived data."""
    __tablename__ = "portfolios"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    base_currency: Mapped[str] = mapped_column(Text, nullable=False, server_default="USD")
    is_default: Mapped[int] = mapped_column(default=0)  # 1 = default portfolio
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)

    # Relationships
    holdings: Mapped[list[Holding]] = relationship(back_populates="portfolio")

    __table_args__ = (
        Index("ix_portfolios_is_default", "is_default"),
    )


# ---------------------------------------------------------------------------
# Canonical tables — source of truth
# ---------------------------------------------------------------------------


class Holding(Base):
    __tablename__ = "holdings"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    ticker: Mapped[str] = mapped_column(Text, nullable=False)
    isin: Mapped[str | None] = mapped_column(Text)
    venue: Mapped[str | None] = mapped_column(Text)
    currency: Mapped[str] = mapped_column(Text, nullable=False)
    quantity: Mapped[float] = mapped_column(nullable=False)
    avg_cost_basis: Mapped[float | None] = mapped_column()
    current_price: Mapped[float | None] = mapped_column()
    market_value: Mapped[float | None] = mapped_column()
    weight_pct: Mapped[float | None] = mapped_column()
    portfolio_id: Mapped[str] = mapped_column(
        Text, ForeignKey("portfolios.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="active"
    )
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)

    # Relationships
    portfolio: Mapped[Portfolio] = relationship(back_populates="holdings")
    trades: Mapped[list[Trade]] = relationship(back_populates="holding")
    analysis_notes: Mapped[list[AnalysisNote]] = relationship(
        back_populates="holding"
    )
    coverage_reports: Mapped[list[CoverageReport]] = relationship(
        back_populates="holding"
    )

    __table_args__ = (
        Index("ix_holdings_ticker", "ticker"),
        Index("ix_holdings_portfolio_status", "portfolio_id", "status"),
        Index("ix_holdings_isin", "isin"),
    )


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    holding_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("holdings.id")
    )
    ticker: Mapped[str] = mapped_column(Text, nullable=False)
    trade_type: Mapped[str] = mapped_column(Text, nullable=False)
    quantity: Mapped[float] = mapped_column(nullable=False)
    price: Mapped[float | None] = mapped_column()
    currency: Mapped[str | None] = mapped_column(Text)
    trade_date: Mapped[str] = mapped_column(Text, nullable=False)
    settlement_date: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)

    # Relationships
    holding: Mapped[Holding | None] = relationship(back_populates="trades")

    __table_args__ = (
        Index("ix_trades_ticker", "ticker"),
        Index("ix_trades_holding_id", "holding_id"),
        Index("ix_trades_trade_date", "trade_date"),
    )


class Security(Base):
    __tablename__ = "securities"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    ticker: Mapped[str] = mapped_column(Text, nullable=False)
    isin: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str | None] = mapped_column(Text)
    venue: Mapped[str | None] = mapped_column(Text)
    currency: Mapped[str] = mapped_column(Text, nullable=False)
    issuer: Mapped[str | None] = mapped_column(Text)
    sector: Mapped[str | None] = mapped_column(Text)
    subsector: Mapped[str | None] = mapped_column(Text)
    industry: Mapped[str | None] = mapped_column(Text)
    geography: Mapped[str | None] = mapped_column(Text)
    domicile: Mapped[str | None] = mapped_column(Text)
    market_cap_bucket: Mapped[str | None] = mapped_column(Text)
    themes: Mapped[str | None] = mapped_column(Text)  # JSON array
    classification_source: Mapped[str | None] = mapped_column(Text)
    classification_confidence: Mapped[str | None] = mapped_column(Text)
    classified_at: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        Index("ix_securities_ticker", "ticker"),
        Index("ix_securities_isin", "isin"),
        Index("ix_securities_sector", "sector"),
        Index("ix_securities_geography", "geography"),
    )


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    domain: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str | None] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    parser_id: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[int] = mapped_column(default=5)
    trust_level: Mapped[str] = mapped_column(Text, server_default="standard")
    enabled: Mapped[int] = mapped_column(default=1)
    rate_limit_rpm: Mapped[int] = mapped_column(default=10)
    requires_auth: Mapped[int] = mapped_column(default=0)
    auth_type: Mapped[str | None] = mapped_column(Text)
    last_fetched_at: Mapped[str | None] = mapped_column(Text)
    last_status: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)

    # Relationships
    events: Mapped[list[Event]] = relationship(back_populates="source")

    __table_args__ = (
        Index("ix_sources_domain", "domain"),
        Index("ix_sources_enabled", "enabled"),
    )


# ---------------------------------------------------------------------------
# Event tables — timestamped, immutable
# ---------------------------------------------------------------------------


class Event(Base):
    __tablename__ = "events"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    source_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("sources.id")
    )
    external_id: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    content: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[str | None] = mapped_column(Text)
    fetched_at: Mapped[str] = mapped_column(Text, nullable=False)
    event_type: Mapped[str | None] = mapped_column(Text)
    scope: Mapped[str | None] = mapped_column(Text)
    direction: Mapped[str | None] = mapped_column(Text)
    horizon: Mapped[str | None] = mapped_column(Text)
    materiality: Mapped[str] = mapped_column(
        Text, server_default="unscored"
    )
    confidence: Mapped[str] = mapped_column(
        Text, server_default="unscored"
    )
    dedup_hash: Mapped[str | None] = mapped_column(Text, unique=True)
    raw_data: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)

    # Relationships
    source: Mapped[Source | None] = relationship(back_populates="events")
    event_links: Mapped[list[EventLink]] = relationship(
        back_populates="event"
    )
    analysis_notes: Mapped[list[AnalysisNote]] = relationship(
        back_populates="event"
    )

    __table_args__ = (
        Index("ix_events_source_id", "source_id"),
        Index("ix_events_published_at", "published_at"),
        Index("ix_events_fetched_at", "fetched_at"),
        Index("ix_events_event_type", "event_type"),
        Index("ix_events_materiality", "materiality"),
    )


class EventLink(Base):
    __tablename__ = "event_links"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    event_id: Mapped[str] = mapped_column(
        Text, ForeignKey("events.id"), nullable=False
    )
    link_type: Mapped[str] = mapped_column(Text, nullable=False)
    link_target: Mapped[str] = mapped_column(Text, nullable=False)
    relevance_score: Mapped[float | None] = mapped_column()
    impact_channel: Mapped[str | None] = mapped_column(Text)
    link_source: Mapped[str | None] = mapped_column(Text)
    # Phase 9A: optional structured context for factor-driven links.
    # `channel` is a redundant-on-purpose human-friendly label (factor key
    # for macro_factor links, free text otherwise); `details_json` carries
    # the structured causal chain for macro_factor links and is otherwise
    # null for backward-compatible link types.
    channel: Mapped[str | None] = mapped_column(Text)
    details_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)

    # Relationships
    event: Mapped[Event] = relationship(back_populates="event_links")

    __table_args__ = (
        Index("ix_event_links_event_id", "event_id"),
        Index("ix_event_links_link_target", "link_target"),
        Index("ix_event_links_link_type", "link_type"),
    )


# ---------------------------------------------------------------------------
# Derived / analysis tables
# ---------------------------------------------------------------------------


class AnalysisNote(Base):
    __tablename__ = "analysis_notes"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    event_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("events.id")
    )
    holding_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("holdings.id")
    )
    note_type: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    materiality: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[str | None] = mapped_column(Text)
    agent_id: Mapped[str] = mapped_column(Text, nullable=False)
    model_id: Mapped[str | None] = mapped_column(Text)
    prompt_hash: Mapped[str | None] = mapped_column(Text)
    source_trace: Mapped[str | None] = mapped_column(Text)  # JSON
    created_at: Mapped[str] = mapped_column(Text, nullable=False)

    # Relationships
    event: Mapped[Event | None] = relationship(back_populates="analysis_notes")
    holding: Mapped[Holding | None] = relationship(
        back_populates="analysis_notes"
    )

    __table_args__ = (
        Index("ix_analysis_notes_event_id", "event_id"),
        Index("ix_analysis_notes_holding_id", "holding_id"),
        Index("ix_analysis_notes_agent_id", "agent_id"),
        Index("ix_analysis_notes_note_type", "note_type"),
    )


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    portfolio_id: Mapped[str | None] = mapped_column(Text, ForeignKey("portfolios.id"))
    alert_type: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    related_holdings: Mapped[str | None] = mapped_column(Text)  # JSON array
    related_events: Mapped[str | None] = mapped_column(Text)  # JSON array
    acknowledged: Mapped[int] = mapped_column(default=0)
    acknowledged_at: Mapped[str | None] = mapped_column(Text)
    delivered: Mapped[int] = mapped_column(default=0)
    delivered_at: Mapped[str | None] = mapped_column(Text)
    agent_id: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        Index("ix_alerts_alert_type", "alert_type"),
        Index("ix_alerts_severity", "severity"),
        Index("ix_alerts_acknowledged", "acknowledged"),
        Index("ix_alerts_created_at", "created_at"),
    )


class Digest(Base):
    __tablename__ = "digests"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    portfolio_id: Mapped[str | None] = mapped_column(Text, ForeignKey("portfolios.id"))
    digest_type: Mapped[str] = mapped_column(Text, nullable=False)
    period_start: Mapped[str] = mapped_column(Text, nullable=False)
    period_end: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    event_count: Mapped[int | None] = mapped_column()
    alert_count: Mapped[int | None] = mapped_column()
    holding_count: Mapped[int | None] = mapped_column()
    delivered: Mapped[int] = mapped_column(default=0)
    delivered_at: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        Index("ix_digests_digest_type", "digest_type"),
        Index("ix_digests_period", "period_start", "period_end"),
    )


# ---------------------------------------------------------------------------
# Audit & system tables
# ---------------------------------------------------------------------------


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    old_value: Mapped[str | None] = mapped_column(Text)  # JSON
    new_value: Mapped[str | None] = mapped_column(Text)  # JSON
    agent_id: Mapped[str | None] = mapped_column(Text)
    user_id: Mapped[str] = mapped_column(Text, server_default="operator")
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        Index("ix_audit_log_entity", "entity_type", "entity_id"),
        Index("ix_audit_log_action", "action"),
        Index("ix_audit_log_created_at", "created_at"),
    )


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    agent_id: Mapped[str] = mapped_column(Text, nullable=False)
    run_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[str] = mapped_column(Text, nullable=False)
    completed_at: Mapped[str | None] = mapped_column(Text)
    items_processed: Mapped[int] = mapped_column(default=0)
    items_failed: Mapped[int] = mapped_column(default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column()

    __table_args__ = (
        Index("ix_agent_runs_agent_id", "agent_id"),
        Index("ix_agent_runs_status", "status"),
        Index("ix_agent_runs_started_at", "started_at"),
    )


class CoverageReport(Base):
    __tablename__ = "coverage_reports"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    holding_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("holdings.id")
    )
    ticker: Mapped[str] = mapped_column(Text, nullable=False)
    has_recent_earnings: Mapped[int] = mapped_column(default=0)
    has_recent_dividend: Mapped[int] = mapped_column(default=0)
    has_recent_analyst: Mapped[int] = mapped_column(default=0)
    has_recent_news: Mapped[int] = mapped_column(default=0)
    last_event_at: Mapped[str | None] = mapped_column(Text)
    gap_days: Mapped[int | None] = mapped_column()
    quality_score: Mapped[float | None] = mapped_column()
    flag: Mapped[str | None] = mapped_column(Text)
    checked_at: Mapped[str] = mapped_column(Text, nullable=False)

    # Relationships
    holding: Mapped[Holding | None] = relationship(
        back_populates="coverage_reports"
    )

    __table_args__ = (
        Index("ix_coverage_reports_holding_id", "holding_id"),
        Index("ix_coverage_reports_ticker", "ticker"),
        Index("ix_coverage_reports_flag", "flag"),
    )


class SystemHealth(Base):
    __tablename__ = "system_health"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    component: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    message: Mapped[str | None] = mapped_column(Text)
    checked_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        Index("ix_system_health_component", "component"),
        Index("ix_system_health_checked_at", "checked_at"),
    )



# NOTE: PriceHistory and PortfolioSnapshot models were removed in v1.0
# as they were unused placeholder tables.  They may be reintroduced in a
# future release when price-data integration and daily snapshots are built.


# ---------------------------------------------------------------------------
# Phase 9A — deterministic macro factor reasoning
# ---------------------------------------------------------------------------


class HoldingFactorSensitivity(Base):
    """Per-holding sensitivity to a named macro factor.

    Sensitivity is a signed weight in [-1, 1]:
        +1.0  holding benefits maximally from the factor going "up"
         0.0  no first-order exposure
        -1.0  holding is hurt maximally by the factor going "up"

    A row is only required when the sensitivity differs from the
    sector default (see ``src/intelligence/factors/sensitivity.py``).
    Holdings without a row fall back to sector defaults at propagation
    time; if neither is available, they are skipped entirely so the
    system fails safe rather than emitting low-confidence noise.
    """

    __tablename__ = "holding_factor_sensitivities"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    holding_id: Mapped[str] = mapped_column(
        Text, ForeignKey("holdings.id"), nullable=False
    )
    factor: Mapped[str] = mapped_column(Text, nullable=False)
    sensitivity: Mapped[float] = mapped_column(nullable=False)
    source: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="default"
    )  # default | ai_inferred | manual
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "holding_id", "factor",
            name="uq_holding_factor_sensitivities_holding_factor",
        ),
        Index("ix_holding_factor_sensitivities_holding_id", "holding_id"),
        Index("ix_holding_factor_sensitivities_factor", "factor"),
    )


class MacroFactorEvent(Base):
    """An event's deterministic classification against a macro factor.

    One row per (event, factor) pair; a single event can carry multiple
    rows when it touches several factors simultaneously (e.g. a
    pipeline attack in a sanctioned region is both ``oil_energy`` and
    ``geopolitical_risk``).

    These rows survive even when no holding-level link passes the
    relevance gate — they are the persistent ground truth for what
    factors the event touched, independent of the current portfolio
    composition.
    """

    __tablename__ = "macro_factor_events"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    event_id: Mapped[str] = mapped_column(
        Text, ForeignKey("events.id"), nullable=False
    )
    factor: Mapped[str] = mapped_column(Text, nullable=False)
    direction: Mapped[str] = mapped_column(Text, nullable=False)  # up | down | unknown
    magnitude: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="unknown"
    )  # minor | moderate | major | extreme | unknown
    confidence: Mapped[float] = mapped_column(nullable=False)  # 0.0–1.0
    rationale: Mapped[str | None] = mapped_column(Text)  # JSON array of matched patterns
    created_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "event_id", "factor",
            name="uq_macro_factor_events_event_factor",
        ),
        Index("ix_macro_factor_events_event_id", "event_id"),
        Index("ix_macro_factor_events_factor", "factor"),
    )


# ---------------------------------------------------------------------------
# Phase 9D — deterministic relationship graph
# ---------------------------------------------------------------------------


class HoldingRelationship(Base):
    """Structured relationship row anchoring a holding to a related entity.

    Each row answers the question: "this holding is X to this related
    entity (identified by ticker and/or stable entity key), with this
    strength".  Relationship rows are anchored to a ``holding_id`` so
    portfolio correctness flows naturally through the FK — an event
    about a related entity only propagates to holdings whose portfolio
    actually contains that row.

    ``strength`` is a float in [0.0, 1.0] describing how tight the
    relationship is (a holding's sole foundry vs. one customer of
    many).  The propagator multiplies this in, plus a relationship-
    type weight, plus a conservative indirectness decay.

    Nothing in this model depends on an LLM or on an external
    knowledge graph — rows are authored (seeded) from a repo-managed
    registry or added by operators.  See
    ``src/intelligence/relationships/seeds.py``.
    """

    __tablename__ = "holding_relationships"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    holding_id: Mapped[str] = mapped_column(
        Text, ForeignKey("holdings.id"), nullable=False
    )
    #: supplier | customer | competitor | regulator | parent | subsidiary
    relationship_type: Mapped[str] = mapped_column(Text, nullable=False)
    #: Optional ticker for the related entity — may be null for
    #: non-listed entities (regulators, private companies).  When
    #: present, ticker is the strongest match signal we can use.
    related_ticker: Mapped[str | None] = mapped_column(Text)
    #: Stable repo-controlled identifier for the related entity.
    #: Used when ``related_ticker`` is null, or as a disambiguator.
    related_entity_key: Mapped[str | None] = mapped_column(Text)
    #: Human-readable name — surfaces in the causal chain summary and
    #: can be used for name-based matching on company mentions.
    related_name: Mapped[str | None] = mapped_column(Text)
    #: 0.0–1.0 inclusive — strength of this relationship.
    strength: Mapped[float] = mapped_column(nullable=False, server_default="0.5")
    #: ``seed`` | ``manual`` | ``ai_inferred`` — Phase 9D only uses
    #: ``seed`` at runtime; the others are reserved for future phases.
    source: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="seed"
    )
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "holding_id", "relationship_type", "related_ticker", "related_entity_key",
            name="uq_holding_relationships_unique_edge",
        ),
        Index("ix_holding_relationships_holding_id", "holding_id"),
        Index("ix_holding_relationships_related_ticker", "related_ticker"),
        Index("ix_holding_relationships_related_entity_key", "related_entity_key"),
        Index("ix_holding_relationships_type", "relationship_type"),
    )


# ---------------------------------------------------------------------------
# Phase 9F — Telegram session state + delivery bookkeeping
# ---------------------------------------------------------------------------


class TelegramSession(Base):
    """Per-Telegram-chat session state.

    Phase 9F pins each authorized Telegram chat to an *active portfolio*
    so `/portfolio`, `/holdings`, `/alerts`, `/digest`, `/events` and the
    free-text chat path all scope their reads to exactly that portfolio.

    A missing row is interpreted as "active portfolio = 'default'" so
    pre-9F installs keep working with zero configuration.  Writes only
    happen when the user explicitly switches portfolio via
    ``/portfolio_select <id>``.
    """

    __tablename__ = "telegram_sessions"

    chat_id: Mapped[int] = mapped_column(primary_key=True)
    active_portfolio_id: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="'default'"
    )
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class TelegramDelivery(Base):
    """Audit trail of every outbound Telegram alert delivery attempt.

    Phase 9F uses this table as the source of truth for deduplication
    and cooldown bookkeeping.  An alert is only considered "delivered"
    once a row exists here with status='sent'; failed sends write a
    status='failed' row without touching ``Alert.delivered``, so the
    next poll cycle will retry.

    Dedupe key = (chat_id, alert_id).  Cooldown key = (chat_id,
    event_id, holding_id, channel) — so if the same
    event+holding+channel tuple fires another alert within the cooldown
    window, we collapse it.
    """

    __tablename__ = "telegram_deliveries"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    chat_id: Mapped[int] = mapped_column(nullable=False)
    alert_id: Mapped[str] = mapped_column(Text, nullable=False)
    portfolio_id: Mapped[str | None] = mapped_column(Text)
    dedup_key: Mapped[str | None] = mapped_column(Text)  # event_id|holding_id|channel
    status: Mapped[str] = mapped_column(Text, nullable=False)  # sent|failed|skipped
    error: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "chat_id", "alert_id",
            name="uq_telegram_deliveries_chat_alert",
        ),
        Index("ix_telegram_deliveries_alert_id", "alert_id"),
        Index("ix_telegram_deliveries_dedup_key", "dedup_key"),
        Index("ix_telegram_deliveries_sent_at", "sent_at"),
    )


# ---------------------------------------------------------------------------
# Phase 9P — Notification Center read state
# ---------------------------------------------------------------------------


class NotificationRead(Base):
    """Per-portfolio read state for Phase 9P inbox items.

    Inbox items are composed on-the-fly from existing trusted rows
    (alerts, digests, operator audit rows, recommended actions); they
    do NOT live in their own table.  What we *do* need to persist is
    the operator's read state per ``notification_key`` so marking an
    item read survives reloads and tab switches.

    Design rules:
      * portfolio-safe — every row carries ``portfolio_id`` and the
        unique constraint is per-portfolio, so marking an item read
        in pA never affects pB.
      * grounded — ``source_type`` + ``source_id`` back-reference the
        row that produced the inbox item, so the audit trail stays
        intact and we never drift from deterministic data.
      * additive — nothing else in the schema changes.
      * SQLite-safe — no compound foreign keys, no check constraints
        that SQLite would reject.
    """

    __tablename__ = "notification_reads"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    portfolio_id: Mapped[str] = mapped_column(Text, nullable=False)
    notification_key: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[str] = mapped_column(Text, nullable=False)
    read_at: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "portfolio_id", "notification_key",
            name="uq_notification_reads_portfolio_key",
        ),
        Index("ix_notification_reads_portfolio_id", "portfolio_id"),
        Index("ix_notification_reads_source_type", "source_type"),
        Index("ix_notification_reads_read_at", "read_at"),
    )


# ---------------------------------------------------------------------------
# Phase 9T — Recommended action dismiss/read state
# ---------------------------------------------------------------------------


class ActionState(Base):
    """Per-portfolio lifecycle state for Phase 9N recommended actions.

    Each row tracks whether an operator has marked a specific action
    key as ``read`` or ``dismissed``.  The ``fingerprint`` field
    captures a deterministic hash of the action's grounded evidence
    at dismiss time so the reappearance rule can detect material
    changes: same key + same fingerprint → stays handled; same key +
    different fingerprint → reappears as new.

    Portfolio isolation is enforced by the unique constraint on
    ``(portfolio_id, action_key)`` — dismissing an action in pA
    never affects pB.
    """

    __tablename__ = "action_states"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    portfolio_id: Mapped[str] = mapped_column(Text, nullable=False)
    action_key: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False)  # "read" | "dismissed"
    fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "portfolio_id", "action_key",
            name="uq_action_states_portfolio_key",
        ),
        Index("ix_action_states_portfolio_id", "portfolio_id"),
        Index("ix_action_states_state", "state"),
    )


# ---------------------------------------------------------------------------
# Phase 9U — Saved analytical views
# ---------------------------------------------------------------------------


class SavedView(Base):
    """Per-portfolio named saved view for Phase 9U.

    Each row is a compact snapshot of a restorable analytical context
    (surface, subtab, filters).  The ``payload_json`` field carries the
    same shape as a ``NavigationTarget.to_dict()`` so restoring a saved
    view is identical to following a deep link.

    Portfolio isolation: unique on ``(portfolio_id, name)`` so pA's
    saved views never clash with pB's.
    """

    __tablename__ = "saved_views"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    portfolio_id: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    surface: Mapped[str] = mapped_column(Text, nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "portfolio_id", "name",
            name="uq_saved_views_portfolio_name",
        ),
        Index("ix_saved_views_portfolio_id", "portfolio_id"),
    )


# ---------------------------------------------------------------------------
# Schema versioning
# ---------------------------------------------------------------------------

class SchemaVersion(Base):
    """Tracks database schema version for migration compatibility.

    A single row stores the current schema version.  The application
    checks this on startup and refuses to run if the DB is from a
    newer incompatible version.
    """
    __tablename__ = "_schema_version"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    version: Mapped[int] = mapped_column(nullable=False, default=1)
    applied_at: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
