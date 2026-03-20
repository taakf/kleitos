"""Tests for the deduplication engine."""

from src.events.dedup import DeduplicationEngine


class TestDeduplicationEngine:
    """Test dedup logic (non-DB parts)."""

    def test_compute_hash_deterministic(self):
        h1 = DeduplicationEngine.compute_hash("src1", "ext1", "Title A")
        h2 = DeduplicationEngine.compute_hash("src1", "ext1", "Title A")
        assert h1 == h2

    def test_compute_hash_differs(self):
        h1 = DeduplicationEngine.compute_hash("src1", "ext1", "Title A")
        h2 = DeduplicationEngine.compute_hash("src1", "ext1", "Title B")
        assert h1 != h2

    def test_normalize_title(self):
        assert DeduplicationEngine.normalize_title("Hello, World!") == "hello world"
        assert DeduplicationEngine.normalize_title("  Multiple   Spaces  ") == "multiple spaces"
        assert DeduplicationEngine.normalize_title("ALL-CAPS: Test") == "allcaps test"
