"""
`live.urls`: `get_glossary_base_url`, `build_pager_query`'s pagination fragment,
and `build_search_url`'s filter-to-URL assembly.
"""

import pytest

from slb_glossary.live.urls import build_pager_query, build_search_url, get_glossary_base_url
from slb_glossary.types import Language

pytestmark = pytest.mark.unit


class TestGetGlossaryBaseUrl:
    def test_english_is_the_default(self) -> None:
        """With no argument, returns the English search URL."""
        assert get_glossary_base_url() == "https://glossary.slb.com/en/search"

    def test_builds_url_for_given_language(self) -> None:
        """Builds the search URL for the given `Language` member."""
        assert get_glossary_base_url(Language.SPANISH) == "https://glossary.slb.com/es/search"


class TestBuildPagerQuery:
    def test_tab_one_returns_empty_string(self) -> None:
        """Tab 1 (the default, and anything below 2) needs no pagination fragment."""
        assert build_pager_query(tab_number=1) == ""
        assert build_pager_query(tab_number=0) == ""

    def test_tab_two_offsets_by_one_page(self) -> None:
        """Tab 2 offsets by one page's worth of terms."""
        assert build_pager_query(tab_number=2, terms_per_tab=12) == "first=12&"

    def test_tab_three_offsets_by_two_pages(self) -> None:
        """Tab 3 offsets by two pages' worth of terms."""
        assert build_pager_query(tab_number=3, terms_per_tab=12) == "first=24&"

    def test_respects_custom_terms_per_tab(self) -> None:
        """A non-default `terms_per_tab` scales the offset accordingly."""
        assert build_pager_query(tab_number=2, terms_per_tab=20) == "first=20&"


class TestBuildSearchUrl:
    def test_no_filters_returns_base_url_unchanged(self) -> None:
        """With no `topic`/`query`/`start_letter`, returns `base_url` as-is."""
        base_url = "https://glossary.slb.com/en/search"
        assert build_search_url(base_url=base_url) == base_url

    def test_query_only(self) -> None:
        """A bare `query` produces a `q=`-prefixed fragment, sorted by relevancy."""
        result = build_search_url(base_url="https://x.com/search", query="porosity")
        assert result == "https://x.com/search#q=porosity&sort=relevancy"

    def test_query_is_url_encoded(self) -> None:
        """`query` is URL-encoded (spaces, punctuation)."""
        result = build_search_url(base_url="https://x.com/search", query="mud weight")
        assert "q=mud%20weight&" in result

    def test_topic_only(self) -> None:
        """A bare `topic` (with no query/start_letter) still builds a fragment."""
        result = build_search_url(base_url="https://x.com/search", topic="Geology")
        assert result == "https://x.com/search#sort=relevancy&f:DisciplineFacet=[Geology]"

    def test_start_letter_only(self) -> None:
        """A bare `start_letter` still builds a fragment."""
        result = build_search_url(base_url="https://x.com/search", start_letter="p")
        assert result == "https://x.com/search#sort=relevancy&f:TermStartLetterFacet=[P]"

    def test_start_letter_uses_only_the_first_character_uppercased(self) -> None:
        """Only `start_letter[0]`, uppercased, is used - a longer string is truncated."""
        result = build_search_url(base_url="https://x.com/search", start_letter="porosity")
        assert "f:TermStartLetterFacet=[P]" in result

    def test_query_topic_and_start_letter_combine(self) -> None:
        """`query`, `topic`, and `start_letter` all combine into one fragment."""
        result = build_search_url(
            base_url="https://x.com/search", query="mud", topic="Drilling", start_letter="m"
        )
        assert result == (
            "https://x.com/search#q=mud&sort=relevancy"
            "&f:DisciplineFacet=[Drilling]&f:TermStartLetterFacet=[M]"
        )

    def test_pager_query_is_inserted_before_sort(self) -> None:
        """A given `pager_query` fragment is inserted right before `sort=relevancy`."""
        result = build_search_url(
            base_url="https://x.com/search", query="mud", pager_query="first=12&"
        )
        assert result == "https://x.com/search#q=mud&first=12&sort=relevancy"

    def test_topic_is_url_encoded(self) -> None:
        """`topic` is URL-encoded too, same as `query`."""
        result = build_search_url(base_url="https://x.com/search", topic="Oil & Gas")
        assert "f:DisciplineFacet=[Oil%20%26%20Gas]" in result
