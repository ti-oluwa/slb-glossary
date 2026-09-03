"""
`live.parsers`: `clean_text`'s invisible-character cleanup, every DOM
extractor (`get_element_text`, `get_facet_topics`, `get_glossary_size`,
`get_results_header_text`, `get_total_term_count`, `get_result_links`,
`get_term_name`, `get_term_detail_blocks`, `get_term_images`), and
`resolve_grammatical_label`'s abbreviation lookup.

Uses a minimal fake `Page`/`Locator` rather than a real Playwright page:
`eval_on_selector_all` normally runs the given JS string against the
real DOM, but every caller in this module only cares about its *return
value* (a plain Python-JSON-shaped structure) - so faking that return
value directly, without ever executing the JS or needing a real browser,
exercises exactly the same Python-side parsing logic these functions are
actually responsible for.
"""

import pytest

from slb_glossary.errors import ParsingError
from slb_glossary.live.parsers import (
    RESULT_LINK_SELECTOR,
    RESULTS_HEADER_SELECTOR,
    TERM_DETAIL_SELECTOR,
    TERM_NAME_SELECTOR,
    TERM_SECTION_SELECTOR,
    TOPIC_VALUE_SELECTOR,
    TOTAL_COUNT_SELECTOR,
    TermBlock,
    TermImage,
    clean_text,
    get_element_text,
    get_facet_topics,
    get_glossary_size,
    get_result_links,
    get_results_header_text,
    get_term_detail_blocks,
    get_term_images,
    get_term_name,
    get_total_term_count,
    resolve_grammatical_label,
)
from slb_glossary.types import Language, RelatedTerm

pytestmark = pytest.mark.unit


class MockLocator:
    """Stands in for `page.locator(selector).first`."""

    def __init__(self, text: str | None = None, should_timeout: bool = False) -> None:
        self._text = text
        self._should_timeout = should_timeout
        self.first = self

    async def text_content(self, timeout: float | None = None) -> str | None:
        if self._should_timeout:
            raise TimeoutError("locator never appeared")
        return self._text


class MockPage:
    """
    Stands in for `patchright.async_api.Page`. `locators` maps a selector to
    the `MockLocator` `page.locator(selector)` should return; `eval_results`
    maps a selector to what `page.eval_on_selector_all(selector, ...)`
    should return (the JS string itself is accepted but ignored).
    """

    def __init__(self, url: str = "https://x.com/porosity") -> None:
        self.url = url
        self.locators: dict[str, MockLocator] = {}
        self.eval_results: dict[str, object] = {}
        self.eval_should_raise: set[str] = set()

    def locator(self, selector: str) -> MockLocator:
        return self.locators.get(selector, MockLocator(text=None))

    async def eval_on_selector_all(self, selector: str, script: str) -> object:
        if selector in self.eval_should_raise:
            raise RuntimeError("evaluation failed")
        return self.eval_results.get(selector, [])


class TestCleanText:
    def test_strips_surrounding_whitespace(self):
        """Surrounding whitespace is trimmed."""
        assert clean_text("  Porosity  ") == "Porosity"

    def test_replaces_non_breaking_space_with_plain_space(self):
        """A non-breaking space (U+00A0) becomes a plain space, not vanishes."""
        assert clean_text("A\u00a0rock") == "A rock"

    def test_removes_zero_width_space(self):
        """A zero-width space (U+200B) is removed entirely."""
        assert clean_text("A\u200brock") == "Arock"

    def test_removes_byte_order_mark(self):
        """A zero-width no-break space/BOM (U+FEFF) is removed entirely."""
        assert clean_text("\ufeffPorosity") == "Porosity"

    def test_removes_soft_hyphen(self):
        """A soft hyphen (U+00AD) is removed entirely."""
        assert clean_text("po\u00adrosity") == "porosity"

    def test_leaves_ordinary_internal_whitespace_untouched(self):
        """Ordinary internal whitespace isn't collapsed."""
        assert clean_text("A  rock  property") == "A  rock  property"

    def test_does_not_change_casing(self):
        """Casing is left exactly as given."""
        assert clean_text("Porosity") == "Porosity"


@pytest.mark.anyio
class TestGetElementText:
    async def test_returns_cleaned_text(self, anyio_backend_asyncio_only):
        """Returns the locator's text, cleaned via `clean_text`."""
        page = MockPage()
        page.locators["h1"] = MockLocator(text="  Porosity\u00a0Index  ")
        assert await get_element_text(page, "h1") == "Porosity Index"

    async def test_returns_empty_string_when_locator_has_no_text(self, anyio_backend_asyncio_only):
        """A locator resolving to `None`/no text returns `""`."""
        page = MockPage()
        page.locators["h1"] = MockLocator(text=None)
        assert await get_element_text(page, "h1") == ""

    async def test_returns_empty_string_on_timeout(self, anyio_backend_asyncio_only):
        """A locator that never appears (raises) returns `""`, not an error."""
        page = MockPage()
        page.locators["h1"] = MockLocator(should_timeout=True)
        assert await get_element_text(page, "h1") == ""

    async def test_missing_selector_returns_empty_string(self, anyio_backend_asyncio_only):
        """A selector never registered on the fake page behaves like "no text"."""
        page = MockPage()
        assert await get_element_text(page, "nope") == ""


@pytest.fixture
def anyio_backend(anyio_backend_asyncio_only):
    return anyio_backend_asyncio_only


@pytest.mark.anyio
class TestGetFacetTopics:
    async def test_parses_name_count_pairs(self):
        """Parses `[name, count]` pairs into a `{name: count}` dict."""
        page = MockPage()
        page.eval_results[TOPIC_VALUE_SELECTOR] = [["Geology", "120"], ["Drilling", "80"]]
        assert await get_facet_topics(page) == {"Geology": 120, "Drilling": 80}

    async def test_cleans_invisible_characters_in_names(self):
        """Topic names go through `clean_text` too - including the final
        `.strip()`, so a trailing invisible character disappears entirely,
        not just gets replaced with a trailing space."""
        page = MockPage()
        page.eval_results[TOPIC_VALUE_SELECTOR] = [["Geology\u00a0Basics", "120"]]
        assert await get_facet_topics(page) == {"Geology Basics": 120}

    async def test_skips_unparsable_counts(self):
        """A topic whose count can't be parsed is skipped, not an error."""
        page = MockPage()
        page.eval_results[TOPIC_VALUE_SELECTOR] = [
            ["Geology", "120"],
            ["Bad Topic", "not-a-number"],
        ]
        assert await get_facet_topics(page) == {"Geology": 120}

    async def test_no_entries_returns_empty_dict(self):
        """No facet entries at all returns `{}`."""
        assert await get_facet_topics(MockPage()) == {}


@pytest.mark.anyio
class TestGetGlossarySize:
    async def test_parses_total_count(self):
        """Parses the total-count element's text as an int."""
        page = MockPage()
        page.locators[TOTAL_COUNT_SELECTOR] = MockLocator(text="1,234")
        assert await get_glossary_size(page) == 1234

    async def test_empty_text_returns_zero(self):
        """No text at all returns `0`."""
        assert await get_glossary_size(MockPage()) == 0

    async def test_unparsable_text_returns_zero(self):
        """Unparsable text returns `0`, not an error."""
        page = MockPage()
        page.locators[TOTAL_COUNT_SELECTOR] = MockLocator(text="not a number")
        assert await get_glossary_size(page) == 0


@pytest.mark.anyio
class TestGetResultsHeaderText:
    async def test_delegates_to_get_element_text(self):
        """Returns the results-header element's cleaned text."""
        page = MockPage()
        page.locators[RESULTS_HEADER_SELECTOR] = MockLocator(text="Results 1-12 of 40")
        assert await get_results_header_text(page) == "Results 1-12 of 40"


@pytest.mark.anyio
class TestGetTotalTermCount:
    async def test_parses_total_count(self):
        """Parses the total-count element's text as an int."""
        page = MockPage()
        page.locators[TOTAL_COUNT_SELECTOR] = MockLocator(text="42")
        assert await get_total_term_count(page) == 42

    async def test_empty_text_returns_none(self):
        """No text at all returns `None` (unlike `get_glossary_size`'s `0`)."""
        assert await get_total_term_count(MockPage()) is None

    async def test_unparsable_text_returns_none(self):
        """Unparsable text returns `None`, not an error."""
        page = MockPage()
        page.locators[TOTAL_COUNT_SELECTOR] = MockLocator(text="not a number")
        assert await get_total_term_count(page) is None


@pytest.mark.anyio
class TestGetResultLinks:
    async def test_returns_ordered_hrefs(self):
        """Returns every result link's `href`, in document order."""
        page = MockPage()
        page.eval_results[RESULT_LINK_SELECTOR] = [
            "https://x.com/a",
            "https://x.com/b",
        ]
        assert await get_result_links(page) == ["https://x.com/a", "https://x.com/b"]

    async def test_filters_falsy_hrefs(self):
        """A `null`/empty `href` (no `href` attribute) is filtered out."""
        page = MockPage()
        page.eval_results[RESULT_LINK_SELECTOR] = ["https://x.com/a", None, ""]
        assert await get_result_links(page) == ["https://x.com/a"]

    async def test_no_results_returns_empty_list(self):
        """No result links at all returns `[]`."""
        assert await get_result_links(MockPage()) == []


@pytest.mark.anyio
class TestGetTermName:
    async def test_returns_the_heading_text(self):
        """Returns the term-name heading's cleaned text."""
        page = MockPage()
        page.locators[TERM_NAME_SELECTOR] = MockLocator(text="Porosity")
        assert await get_term_name(page) == "Porosity"

    async def test_raises_parsing_error_when_no_heading(self):
        """No heading text at all raises `ParsingError`, not a silent `None`."""
        with pytest.raises(ParsingError, match="term name"):
            await get_term_name(MockPage())

    async def test_parsing_error_names_the_page_url(self):
        """The raised error carries the page's URL for diagnosis."""
        with pytest.raises(ParsingError, match="https://x.com/broken"):
            await get_term_name(MockPage(url="https://x.com/broken"))


@pytest.mark.anyio
class TestGetTermDetailBlocks:
    async def test_parses_paragraphs_with_links(self):
        """Parses each section's paragraphs, with any links each carries."""
        page = MockPage()
        page.eval_results[TERM_DETAIL_SELECTOR] = [
            [
                {"text": "n. Geology", "links": []},
                {
                    "text": "A rock property. See related.",
                    "links": [{"term": "Permeability", "url": "https://x.com/permeability"}],
                },
            ]
        ]
        blocks = await get_term_detail_blocks(page)
        assert blocks == [
            [
                TermBlock(text="n. Geology", links=()),
                TermBlock(
                    text="A rock property. See related.",
                    links=(RelatedTerm(term="Permeability", url="https://x.com/permeability"),),
                ),
            ]
        ]

    async def test_raises_parsing_error_when_no_sections(self):
        """No definition sections at all raises `ParsingError`, not a silent `[]`."""
        with pytest.raises(ParsingError, match="definition sections"):
            await get_term_detail_blocks(MockPage())

    async def test_parsing_error_names_the_page_url(self):
        """The raised error carries the page's URL for diagnosis."""
        with pytest.raises(ParsingError, match="https://x.com/broken"):
            await get_term_detail_blocks(MockPage(url="https://x.com/broken"))

    async def test_cleans_invisible_characters_in_paragraph_text(self):
        """Each paragraph's text goes through `clean_text` too."""
        from slb_glossary.live.parsers import TERM_DETAIL_SELECTOR

        page = MockPage()
        page.eval_results[TERM_DETAIL_SELECTOR] = [[{"text": "A\u00a0rock", "links": []}]]
        blocks = await get_term_detail_blocks(page)
        assert blocks[0][0].text == "A rock"


@pytest.mark.anyio
class TestGetTermImages:
    async def test_parses_image_and_caption(self):
        """Parses a section's image URL (resolved against the site's base URL) and caption."""
        page = MockPage()
        page.eval_results[TERM_SECTION_SELECTOR] = [
            {"src": "/images/porosity.png", "caption": "A porosity diagram"}
        ]
        images = await get_term_images(page)
        assert images == [
            TermImage(
                url="https://glossary.slb.com/images/porosity.png", caption="A porosity diagram"
            )
        ]

    async def test_section_with_no_image_yields_none(self):
        """A section with no image yields `None` at that index, not an omission."""
        page = MockPage()
        page.eval_results[TERM_SECTION_SELECTOR] = [None]
        assert await get_term_images(page) == [None]

    async def test_result_indices_line_up_with_multiple_sections(self):
        """Several sections' results line up index-for-index, mixed image/no-image."""
        page = MockPage()
        page.eval_results[TERM_SECTION_SELECTOR] = [
            {"src": "/a.png", "caption": ""},
            None,
        ]
        images = await get_term_images(page)
        assert images[0] is not None
        assert images[1] is None

    async def test_evaluation_error_returns_empty_list(self):
        """A page evaluation error (e.g. no term sections at all) returns `[]`, not an error."""
        page = MockPage()
        page.eval_should_raise.add(TERM_SECTION_SELECTOR)
        assert await get_term_images(page) == []


class TestResolveGrammaticalLabel:
    def test_resolves_known_english_abbreviation(self):
        """A known English abbreviation resolves to its full label."""
        assert resolve_grammatical_label(Language.ENGLISH, "n.") == "Noun"

    def test_resolves_known_spanish_abbreviation(self):
        """A known Spanish abbreviation resolves to its full label."""
        assert resolve_grammatical_label(Language.SPANISH, "s.") == "Sustantivo"

    def test_matching_is_case_insensitive(self):
        """Matching is case-insensitive."""
        assert resolve_grammatical_label(Language.ENGLISH, "N.") == "Noun"

    def test_unknown_abbreviation_returned_unchanged(self):
        """An abbreviation with no known mapping is returned exactly as given."""
        assert resolve_grammatical_label(Language.ENGLISH, "xyz.") == "xyz."
