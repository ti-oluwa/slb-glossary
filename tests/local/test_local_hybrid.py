"""
`get_result_key`, `compute_rrf_scores`, and `hybrid_search`'s name-tier-first,
reciprocal-rank-fusion ranking of lexical and semantic results.
"""

import pytest

from slb_glossary.constants import constants
from slb_glossary.local.api import upsert_results
from slb_glossary.local.hybrid import compute_rrf_scores, get_result_key, hybrid_search
from slb_glossary.local.types import Database
from slb_glossary.local.vector import embed_terms
from tests.factories import make_search_result
from tests.local.conftest import MockEmbeddings

pytestmark = pytest.mark.unit


class TestGetResultKey:
    def test_returns_url_topic_tuple(self) -> None:
        """Returns `(result.url, result.topic or "")` for a result with both set."""
        result = make_search_result(url="https://x.com/a", topic="Geology")
        assert get_result_key(result) == ("https://x.com/a", "Geology")

    def test_falls_back_to_empty_string_topic(self) -> None:
        """A falsy `topic` (`None`) becomes `""` in the key, not `None`."""
        result = make_search_result(url="https://x.com/a", topic=None)
        assert get_result_key(result) == ("https://x.com/a", "")

    def test_returns_none_for_result_with_no_url(self) -> None:
        """A result with no `url` has no identity to key by, so this returns `None`."""
        result = make_search_result(url=None)
        assert get_result_key(result) is None


class TestComputeRrfScores:
    def test_single_ranker_scores_by_reciprocal_rank(self) -> None:
        """One ranker's score for a key is `weight / (k + rank)`."""
        ranking = [("a", ""), ("b", "")]
        scores = compute_rrf_scores(ranking, weights=[1.0], k=60)
        assert scores["a", ""] == pytest.approx(1.0 / 61)
        assert scores["b", ""] == pytest.approx(1.0 / 62)

    def test_scores_from_multiple_rankers_sum(self) -> None:
        """A key appearing in more than one ranking gets both rankers' terms summed."""
        ranking_a = [("x", "")]
        ranking_b = [("x", "")]
        scores = compute_rrf_scores(ranking_a, ranking_b, weights=[1.0, 1.0], k=60)
        assert scores[("x", "")] == pytest.approx(2 * (1.0 / 61))

    def test_key_missing_from_a_ranking_gets_no_penalty_term(self) -> None:
        """A key present in only one ranking still gets scored - no penalty for the
        ranking it's absent from, just no term added from that side."""
        ranking_a = [("x", "")]
        ranking_b: list[tuple[str, str]] = []
        scores = compute_rrf_scores(ranking_a, ranking_b, weights=[1.0, 1.0], k=60)
        assert scores == {("x", ""): pytest.approx(1.0 / 61)}

    def test_zero_weight_ranker_is_skipped_entirely(self) -> None:
        """A ranker with `weight=0` contributes nothing, even for keys only it ranks."""
        ranking_a = [("x", "")]
        ranking_b = [("y", "")]
        scores = compute_rrf_scores(ranking_a, ranking_b, weights=[1.0, 0.0], k=60)
        assert ("y", "") not in scores
        assert scores[("x", "")] == pytest.approx(1.0 / 61)

    def test_empty_rankings_return_empty_scores(self) -> None:
        """No rankings (or all-empty rankings) return an empty score dict."""
        assert compute_rrf_scores(weights=[], k=60) == {}
        assert compute_rrf_scores([], weights=[1.0], k=60) == {}

    def test_lower_k_weighs_top_ranks_more_heavily(self) -> None:
        """A lower `k` widens the score gap between rank 1 and rank 2."""
        ranking = [("a", ""), ("b", "")]
        low_k_scores = compute_rrf_scores(ranking, weights=[1.0], k=1)
        high_k_scores = compute_rrf_scores(ranking, weights=[1.0], k=1000)
        low_k_gap = low_k_scores[("a", "")] - low_k_scores[("b", "")]
        high_k_gap = high_k_scores[("a", "")] - high_k_scores[("b", "")]
        assert low_k_gap > high_k_gap


@pytest.mark.anyio
class TestHybridSearch:
    async def test_exact_name_match_is_always_ranked_first(
        self, db: Database, mock_embeddings: MockEmbeddings
    ) -> None:
        """A term whose name exactly matches `query` ranks ahead of everything else,
        same as `lexical_search`'s own name tier."""
        await upsert_results(
            db,
            [
                make_search_result(url="https://x.com/a", term="Porosity"),
                make_search_result(
                    url="https://x.com/b", term="Unrelated", definition="mentions porosity"
                ),
            ],
        )
        results = await hybrid_search(db, "porosity")
        assert results[0][0].term == "Porosity"
        assert results[0][1] == constants.exact_match_score

    async def test_name_tier_result_is_not_duplicated_in_the_fused_tier(
        self, db: Database, mock_embeddings: MockEmbeddings
    ) -> None:
        """A result already in the name tier does not also appear in the fused tier."""
        await upsert_results(db, [make_search_result(url="https://x.com/a", term="Porosity")])
        results = await hybrid_search(db, "porosity")
        assert len(results) == 1

    async def test_semantic_only_match_still_surfaces_via_fusion(
        self, db: Database, mock_embeddings: MockEmbeddings
    ) -> None:
        """A term with no lexical overlap at all can still surface, purely from the
        semantic ranker, once embedded."""
        await upsert_results(
            db,
            [
                make_search_result(
                    url="https://x.com/a",
                    term="Rock Storage Capacity",
                    definition=None,
                    topic=None,
                )
            ],
        )
        mock_embeddings.set("Rock Storage Capacity", [1.0, 0.0, 0.0, 0.0])
        mock_embeddings.set("porosity meaning", [0.9, 0.1, 0.0, 0.0])
        await embed_terms(db)

        results = await hybrid_search(db, "porosity meaning")
        assert [r.term for r, _ in results] == ["Rock Storage Capacity"]

    async def test_fused_scores_are_normalized_into_zero_one_range(
        self, db: Database, mock_embeddings: MockEmbeddings
    ) -> None:
        """Every fused-tier score lands in `[0.0, 1.0]`."""
        await upsert_results(
            db,
            [
                make_search_result(
                    url="https://x.com/a",
                    term="Alpha Concept",
                    definition="fluid storage in rock",
                    topic=None,
                ),
                make_search_result(
                    url="https://x.com/b",
                    term="Bravo Concept",
                    definition="fluid storage in rock too",
                    topic=None,
                ),
            ],
        )
        await embed_terms(db)
        results = await hybrid_search(db, "fluid storage")
        for _, score in results:
            assert 0.0 <= score <= 1.0

    async def test_unembedded_database_still_returns_lexical_only_results(
        self, db: Database, mock_embeddings: MockEmbeddings
    ) -> None:
        """With nothing run through `embed_terms` yet, semantic contributes nothing,
        but lexical-ranked results still come back via RRF on their own."""
        await upsert_results(
            db,
            [
                make_search_result(
                    url="https://x.com/a",
                    term="Unrelated Name",
                    definition="drilling fluid systems",
                )
            ],
        )
        results = await hybrid_search(db, "drilling")
        assert [r.term for r, _ in results] == ["Unrelated Name"]

    async def test_semantic_weight_zero_ignores_semantic_ranking(
        self, db: Database, mock_embeddings: MockEmbeddings
    ) -> None:
        """`constants.semantic_weight = 0` drops the semantic ranker's contribution,
        so a purely-semantic match (no lexical overlap) never surfaces."""
        await upsert_results(
            db,
            [
                make_search_result(
                    url="https://x.com/a",
                    term="Rock Storage Capacity",
                    definition=None,
                    topic=None,
                )
            ],
        )
        mock_embeddings.set("Rock Storage Capacity", [1.0, 0.0, 0.0, 0.0])
        mock_embeddings.set("porosity meaning", [1.0, 0.0, 0.0, 0.0])
        await embed_terms(db)

        constants.semantic_weight = 0.0
        results = await hybrid_search(db, "porosity meaning")
        assert results == []

    async def test_respects_limit(self, db: Database, mock_embeddings: MockEmbeddings) -> None:
        """`limit` caps the total number of results, name tier plus fused tier."""
        await upsert_results(
            db,
            [make_search_result(url=f"https://x.com/{i}", term=f"Porosity {i}") for i in range(5)],
        )
        results = await hybrid_search(db, "porosity", limit=2)
        assert len(results) == 2

    async def test_respects_topic_filter(
        self, db: Database, mock_embeddings: MockEmbeddings
    ) -> None:
        """`topic` restricts both the lexical and semantic sides to that topic."""
        await upsert_results(
            db,
            [
                make_search_result(url="https://x.com/a", term="Porosity", topic="Geology"),
                make_search_result(url="https://x.com/b", term="Porosity Log", topic="Drilling"),
            ],
        )
        results = await hybrid_search(db, "porosity", topic="Geology")
        assert [r.term for r, _ in results] == ["Porosity"]

    async def test_respects_exclude(self, db: Database, mock_embeddings: MockEmbeddings) -> None:
        """`exclude` filters out matching URLs/term names on both sides before fusion."""
        await upsert_results(
            db,
            [
                make_search_result(url="https://x.com/a", term="Porosity"),
                make_search_result(url="https://x.com/b", term="Porosity Log"),
            ],
        )
        results = await hybrid_search(db, "porosity", exclude=["Porosity"])
        assert [r.term for r, _ in results] == ["Porosity Log"]

    async def test_fuzzy_topic_resolves_against_stored_topics(
        self, db: Database, mock_embeddings: MockEmbeddings
    ) -> None:
        """`fuzzy=True` resolves a misspelled `topic` against stored topic names,
        exactly once, then reuses that resolved topic for both rankers."""
        await upsert_results(
            db, [make_search_result(url="https://x.com/a", term="Porosity", topic="Geology")]
        )
        results = await hybrid_search(db, "porosity", topic="geologyy", fuzzy=True)
        assert [r.term for r, _ in results] == ["Porosity"]

    async def test_no_match_returns_empty_list(
        self, db: Database, mock_embeddings: MockEmbeddings
    ) -> None:
        """A query matching nothing on either side returns an empty list, not an error."""
        await upsert_results(db, [make_search_result(url="https://x.com/a", term="Porosity")])
        results = await hybrid_search(db, "zzz_no_such_term_zzz")
        assert results == []
