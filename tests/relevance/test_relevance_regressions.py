"""CI-run regression tests pinned to the relevance benchmark (`tests/relevance/`). Lexical only; see `scripts/relevance_bench.py` for semantic/hybrid."""

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

        exact_only = [query for query in DATASET if query.category == "exact"]
        report = await evaluate(
            seeded_db, lexical_search, dataset=exact_only, mode_label="lexical"
        )
        assert report.overall.recall_at_1 == 1

    async def test_natural_language_queries_always_find_their_term_in_top_3(
        self, seeded_db: Database
    ) -> None:
        """A "what is X"/"define X"-style query should never need more than 3 results to find X."""
        from tests.relevance.dataset import DATASET

        natural_language_only = [
            query for query in DATASET if query.category == "natural_language"
        ]
        report = await evaluate(
            seeded_db, lexical_search, dataset=natural_language_only, mode_label="lexical"
        )
        assert report.overall.recall_at_3 == 1

    async def test_misspelling_queries_mostly_recover_the_term_in_top_3(
        self, seeded_db: Database
    ) -> None:
        """Regression guard for the fuzzy-typo fallback: misspelling Recall@3 must stay comfortably recovered."""
        from tests.relevance.dataset import DATASET

        misspelling_only = [query for query in DATASET if query.category == "misspelling"]
        report = await evaluate(
            seeded_db, lexical_search, dataset=misspelling_only, mode_label="lexical"
        )
        assert report.overall.recall_at_3 >= 0.8

    async def test_overall_recall_at_5_does_not_regress(self, seeded_db: Database) -> None:
        """Floor for the full benchmark's Recall@5, comfortably below the current measured value."""
        report = await evaluate(seeded_db, lexical_search, mode_label="lexical")
        assert report.overall.recall_at_5 >= 0.80

    async def test_exact_category_never_regresses_below_the_pre_tiering_baseline(
        self, seeded_db: Database
    ) -> None:
        """Named-term retrieval (the spec's top priority) must never get worse than it was."""
        from tests.relevance.dataset import DATASET

        exact_only = [query for query in DATASET if query.category == "exact"]
        report = await evaluate(
            seeded_db, lexical_search, dataset=exact_only, mode_label="lexical"
        )
        assert report.overall.ndcg_at_5 == 1
