"""
SQLAlchemy 2.0 ORM models for the Axion database.

All IDs are TEXT (UUID strings).  All timestamps are TEXT (ISO-8601).
Uses DeclarativeBase with mapped_column for type-safe column definitions.
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Text
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
