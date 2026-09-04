"""
`live.relevance`: `score_name_match`'s exact/prefix tiers, `score_content_overlap`'s
token-coverage scoring, and `score_result`'s lexical/semantic dispatch.
"""

import pytest

from slb_glossary.constants import constants
from slb_glossary.live import relevance
from slb_glossary.live.relevance import score_content_overlap, score_name_match, score_result
from slb_glossary.types import SearchMode
from tests.factories import make_search_result

pytestmark = pytest.mark.unit


class TestScoreNameMatch:
    def test_exact_match_scores_exact_match_score(self) -> None:
        """An exact (case/whitespace-insensitive) match scores `constants.exact_match_score`."""
        assert score_name_match("porosity", "Porosity") == constants.exact_match_score
        assert score_name_match("  Porosity  ", "porosity") == constants.exact_match_score

    def test_prefix_match_scores_prefix_match_score(self) -> None:
        """A term starting with (but not equal to) the query scores `prefix_match_score`."""
        assert score_name_match("poros", "Porosity") == constants.prefix_match_score

    def test_no_match_returns_none(self) -> None:
        """Neither exact nor prefix returns `None`, signaling "fall back to content scoring"."""
        assert score_name_match("porosity", "Permeability") is None

    def test_empty_query_or_term_returns_none(self) -> None:
        """An empty (post-normalization) `query` or `term` returns `None`."""
        assert score_name_match("", "Porosity") is None
        assert score_name_match("porosity", "") is None
        assert score_name_match("   ", "Porosity") is None


class TestScoreContentOverlap:
    def test_full_coverage_scores_content_match_score_cap(self) -> None:
        """Every query token present in the texts scores exactly the cap."""
        score = score_content_overlap("rock property", "A rock property of interest")
        assert score == constants.content_match_score_cap

    def test_partial_coverage_scores_proportionally(self) -> None:
        """Half the query tokens present scores half the cap."""
        score = score_content_overlap("rock unrelated", "A rock property")
        assert score == pytest.approx(constants.content_match_score_cap * 0.5)

    def test_no_coverage_scores_zero(self) -> None:
        """No query tokens present anywhere scores `0.0`."""
        assert score_content_overlap("xyz", "A rock property") == 0

    def test_empty_query_scores_zero(self) -> None:
        """An empty (post-normalization) query scores `0.0`."""
        assert score_content_overlap("", "A rock property") == 0

    def test_no_texts_scores_zero(self) -> None:
        """No texts (or all-empty texts) scores `0.0`."""
        assert score_content_overlap("porosity") == 0
        assert score_content_overlap("porosity", "", None or "") == 0

    def test_falsy_texts_are_skipped_not_erroring(self) -> None:
        """A falsy text among several (e.g. an empty string) is skipped, not an error."""
        score = score_content_overlap("rock", "", "A rock property")
        assert score == constants.content_match_score_cap

    def test_longer_text_does_not_score_higher_for_repeating_the_token(self) -> None:
        """Coverage measures whether a token appears, not how often - a text repeating
        one query token many times scores the same as one containing it once."""
        once = score_content_overlap("rock", "A rock property")
        many_times = score_content_overlap("rock", "rock rock rock rock rock rock")
        assert once == many_times == constants.content_match_score_cap


class TestScoreResult:
    def test_lexical_mode_uses_name_match_when_available(self) -> None:
        """LEXICAL mode returns the name-match score when the term matches."""
        result = make_search_result(term="Porosity", definition="unrelated")
        score = score_result("porosity", result, mode=SearchMode.LEXICAL)
        assert score == constants.exact_match_score

    def test_lexical_mode_falls_back_to_content_overlap(self) -> None:
        """LEXICAL mode falls back to `score_content_overlap` when the term doesn't match."""
        result = make_search_result(
            term="Unrelated Name", definition="about rock storage", topic=None
        )
        score = score_result("rock storage", result, mode=SearchMode.LEXICAL)
        assert score == constants.content_match_score_cap

    def test_lexical_is_the_default_mode(self) -> None:
        """Omitting `mode` behaves the same as `mode=SearchMode.LEXICAL`."""
        result = make_search_result(term="Porosity")
        assert score_result("porosity", result) == constants.exact_match_score

    def test_hybrid_mode_raises_value_error(self) -> None:
        """`mode=SearchMode.HYBRID` is explicitly unsupported (scores one result
        at a time; fusion needs every result's relative rank)."""
        result = make_search_result(term="Porosity")
        with pytest.raises(ValueError, match="HYBRID"):
            score_result("porosity", result, mode=SearchMode.HYBRID)  # type: ignore[arg-type]

    def test_semantic_mode_returns_cosine_similarity(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SEMANTIC mode embeds the result and returns cosine similarity to
        the (already-embedded) query vector."""
        import numpy as np

        monkeypatch.setattr(relevance, "embed", lambda texts: np.array([[1.0, 0.0, 0.0, 0.0]]))
        query_vector = np.array([1.0, 0.0, 0.0, 0.0], dtype="float32")
        result = make_search_result(term="Porosity", definition="A rock property")

        score = score_result(query_vector, result, mode=SearchMode.SEMANTIC)
        assert score == pytest.approx(1.0)
