"""
CI-run regression tests pinned to the relevance benchmark (`tests/relevance/`).

Only `lexical_search` is exercised here (needs nothing beyond the base
install and runs in milliseconds); see `scripts/relevance_bench.py` for
the full lexical/semantic/hybrid before/after comparison, which needs
the `semantic` extra and network access to run its semantic/hybrid half.
"""

import pytest

from slb_glossary.local.lexical import lexical_search
from slb_glossary.local.types import Database
from tests.relevance.harness import evaluate, seed_corpus

pytestmark = [pytest.mark.anyio, pytest.mark.integration]


@pytest.fixture
async def seeded_db(db: Database) -> Database:
    """The `db` fixture (see `tests/local/conftest.py`), seeded with the benchmark corpus."""
    await seed_corpus(db)
    return db


class TestLexicalRelevanceBenchmark:
    async def test_exact_queries_always_rank_the_term_first(self, seeded_db: Database) -> None:
        """Every "exact" category query must find its term as the very first result."""
        from tests.relevance.dataset import DATASET

        exact_only = [q for q in DATASET if q.category == "exact"]
        report = await evaluate(seeded_db, lexical_search, dataset=exact_only, mode_label="lexical")
        assert report.overall.recall_at_1 == 1.0

    async def test_natural_language_queries_always_find_their_term_in_top_3(
        self, seeded_db: Database
    ) -> None:
        """A "what is X"/"define X"-style query should never need more than 3 results to find X."""
        from tests.relevance.dataset import DATASET

        nl_only = [q for q in DATASET if q.category == "natural_language"]
        report = await evaluate(seeded_db, lexical_search, dataset=nl_only, mode_label="lexical")
        assert report.overall.recall_at_3 == 1.0

    async def test_misspelling_queries_mostly_recover_the_term_in_top_3(
        self, seeded_db: Database
    ) -> None:
        """
        Regression guard for the fuzzy-typo fallback: before it existed,
        this category's Recall@3 was 0.17 (one lucky prefix-tolerant
        typo out of six); it should stay comfortably recovered now.
        """
        from tests.relevance.dataset import DATASET

        misspelling_only = [q for q in DATASET if q.category == "misspelling"]
        report = await evaluate(
            seeded_db, lexical_search, dataset=misspelling_only, mode_label="lexical"
        )
        assert report.overall.recall_at_3 >= 0.8

    async def test_overall_recall_at_5_does_not_regress(self, seeded_db: Database) -> None:
        """
        Floor for the full benchmark's Recall@5, comfortably below the
        current measured value (~0.90) so this only fires on a real
        regression, not routine fluctuation from dataset/corpus edits.
        """
        report = await evaluate(seeded_db, lexical_search, mode_label="lexical")
        assert report.overall.recall_at_5 >= 0.80

    async def test_exact_category_never_regresses_below_the_pre_tiering_baseline(
        self, seeded_db: Database
    ) -> None:
        """Named-term retrieval (the spec's top priority) must never get worse than it was."""
        from tests.relevance.dataset import DATASET

        exact_only = [q for q in DATASET if q.category == "exact"]
        report = await evaluate(seeded_db, lexical_search, dataset=exact_only, mode_label="lexical")
        assert report.overall.ndcg_at_5 == 1.0
