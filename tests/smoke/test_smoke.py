"""Smoke tests – verify basic imports and instantiation work."""

import importlib


class TestImports:
    """Ensure all core modules can be imported."""

    MODULES = [
        "src.config",
        "src.database.connection",
        "src.database.models",
        "src.database.migrations",
        "src.sources.registry",
        "src.sources.fetcher",
        "src.sources.parsers.base",
        "src.sources.parsers.rss_generic",
        "src.sources.parsers.newsapi",
        "src.impact.rules",
        "src.impact.scoring",
        "src.impact.engine",
        "src.ledger.portfolio",
        "src.events.store",
        "src.events.dedup",
        "src.reporting.digests",
        "src.reporting.alerts",
        "src.security_master.classifier",
        "src.security_master.exposure",
        "src.scheduler.jobs",
        "src.agents.base",
        "src.agents.intake",
        "src.agents.classification",
        "src.agents.collection",
        "src.agents.coverage_qa",
        "src.agents.analysis",
        "src.agents.risk",
    ]

    def test_all_modules_importable(self):
        failures = []
        for mod_name in self.MODULES:
            try:
                importlib.import_module(mod_name)
            except Exception as e:
                failures.append(f"{mod_name}: {e}")
        if failures:
            raise AssertionError(
                f"{len(failures)} module(s) failed to import:\n"
                + "\n".join(failures)
            )


class TestInstantiation:
    """Verify key classes can be instantiated."""

    def test_rule_engine(self):
        from src.impact.rules import RuleEngine
        engine = RuleEngine()
        assert engine is not None

    def test_security_classifier(self):
        from src.security_master.classifier import SecurityClassifier
        cls = SecurityClassifier()
        assert cls is not None

    def test_exposure_calculator(self):
        from src.security_master.exposure import ExposureCalculator
        calc = ExposureCalculator()
        assert calc is not None

    def test_dedup_engine(self):
        from src.events.dedup import DeduplicationEngine
        engine = DeduplicationEngine()
        assert engine is not None

    def test_alert_manager(self):
        from src.reporting.alerts import AlertManager
        mgr = AlertManager()
        assert mgr is not None

    def test_rss_parser(self):
        from src.sources.parsers.rss_generic import RSSGenericParser
        parser = RSSGenericParser()
        assert parser is not None
