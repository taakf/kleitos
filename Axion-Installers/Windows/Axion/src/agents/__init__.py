"""Axion domain agents for portfolio intelligence.

Exports
-------
BaseAgent            -- abstract base with permissions, lifecycle logging, audit
IntakeAgent          -- ingests CSV/JSON portfolio data
ClassificationAgent  -- enriches holdings with sector/geography/theme via LLM
CollectionAgent      -- fetches events from external sources
CoverageQAAgent      -- identifies event-coverage gaps across holdings
AnalysisAgent        -- LLM-driven impact analysis and digest generation
RiskAgent            -- concentration, clustering, and thesis-drift detection
IntakeResult         -- structured result dataclass from IntakeAgent
RuleBasedClassifier  -- fallback classifier when LLM is unavailable
"""

from .base import BaseAgent, AgentPermissionError
from .intake import IntakeAgent, IntakeResult
from .classification import ClassificationAgent
from .collection import CollectionAgent
from .coverage_qa import CoverageQAAgent
from .analysis import AnalysisAgent
from .risk import RiskAgent
from .fallbacks import RuleBasedClassifier, rule_based_analysis, rule_based_digest

__all__ = [
    "BaseAgent",
    "AgentPermissionError",
    "IntakeAgent",
    "IntakeResult",
    "ClassificationAgent",
    "CollectionAgent",
    "CoverageQAAgent",
    "AnalysisAgent",
    "RiskAgent",
    "RuleBasedClassifier",
    "rule_based_analysis",
    "rule_based_digest",
]
