"""Low-level DOM extractors and parsers for glossary pages."""

import logging
import typing
from urllib.parse import urljoin

from patchright.async_api import Page

from slb_glossary.errors import ParsingError
from slb_glossary.live.urls import BASE_URL
from slb_glossary.types import Language, RelatedTerm
from slb_glossary.utils import parse_int

logger = logging.getLogger(__name__)


__all__ = [
    "FACET_EXPAND_SELECTOR",
    "FACET_HEADER_SELECTOR",
    "RESULTS_HEADER_SELECTOR",
    "RESULT_LINK_SELECTOR",
    "TERM_DETAIL_SELECTOR",
    "TERM_IMAGE_CAPTION_SELECTOR",
    "TERM_IMAGE_SELECTOR",
    "TERM_NAME_SELECTOR",
    "TERM_SECTION_SELECTOR",
    "TOPIC_VALUE_SELECTOR",
    "TOTAL_COUNT_SELECTOR",
    "TermBlock",
    "TermImage",
    "get_element_text",
    "get_facet_topics",
    "get_glossary_size",
    "get_result_links",
    "get_results_header_text",
    "get_term_detail_blocks",
    "get_term_images",
    "get_term_name",
    "get_total_term_count",
    "resolve_grammatical_label",
]


FACET_HEADER_SELECTOR = ".CoveoFacet .coveo-facet-header"
"""Header of the discipline/topic facet panel; empty until facets have loaded."""

FACET_EXPAND_SELECTOR = ".CoveoFacet .coveo-facet-footer .coveo-facet-more"
""""Show more" button that reveals every topic in the facet panel."""

TOPIC_VALUE_SELECTOR = "#discipline-facet .coveo-facet-value"
"""One entry (topic name + term count) in the discipline facet panel."""

RESULTS_HEADER_SELECTOR = ".coveo-results-header"
"""Header shown above the search results list once a search has resolved."""

TOTAL_COUNT_SELECTOR = ".CoveoQuerySummary .coveo-highlight-total-count"
"""Element holding the total number of terms matched by the current search."""

RESULT_LINK_SELECTOR = ".CoveoResult .CoveoResultLink"
"""Anchor linking to a term's detail page, one per search result."""

TERM_NAME_SELECTOR = ".row .small-12 h1 strong"
"""Heading holding the term name on a term detail page."""

TERM_DETAIL_SELECTOR = ".content-two-col__text"
"""One definition block's text on a term detail page; a term may have several."""

TERM_SECTION_SELECTOR = ".content-two-col"
"""
One definition section wrapper on a term detail page - a term can have
several (one per topic it's filed under), and each carries its own text
(`TERM_DETAIL_SELECTOR`) and, independently, its own illustrative image
if it has one - not one image shared across the whole page.
"""

TERM_IMAGE_SELECTOR = ".bordered-img .image img"
"""A definition section's own illustrative image, scoped within `TERM_SECTION_SELECTOR`."""

TERM_IMAGE_CAPTION_SELECTOR = ".bordered-img .desc"
"""Caption text accompanying `TERM_IMAGE_SELECTOR`, scoped the same way."""


INVISIBLE_CHAR_REPLACEMENTS: dict[str, str] = {
    "\u00a0": " ",  # non-breaking space - renders as a space, but isn't
    # treated as one by whitespace-based splitting/matching, so it can
    # silently break a word-boundary search or just look like a stray
    # gap in a definition. Common in scraped web text (browsers insert
    # it next to units, inline elements, etc.).
    "\u200b": "",  # zero-width space
    "\ufeff": "",  # zero-width no-break space / byte-order mark
    "\u00ad": "",  # soft hyphen - invisible unless the browser wrapped a line there
}


def clean_text(text: str) -> str:
    """
    Replace/remove invisible characters DOM text extraction can leave behind.

    Most commonly U+00A0 (non-breaking space) which is visually identical to a
    normal space, but not treated as whitespace by string
    splitting/matching, so it can silently break a word-boundary search
    or just look like a stray gap in a definition. Also handles a few
    other invisible characters that turn up in scraped web content for
    similar reasons. See `INVISIBLE_CHAR_REPLACEMENTS`.

    Only touches characters invisible in normal rendering and doesn't
    collapse legitimate internal whitespace or change casing.

    :param text: Raw text extracted from the page.
    :return: `text` with invisible characters replaced/removed and ends trimmed.
    """
    for char, replacement in INVISIBLE_CHAR_REPLACEMENTS.items():
        if char in text:
            text = text.replace(char, replacement)
    return text.strip()


async def get_element_text(page: Page, selector: str, *, timeout: float = 5_000) -> str:
    """
    Return the trimmed text content of the first element matching `selector`.

    :param page: The page to search.
    :param selector: A CSS selector.
    :param timeout: Milliseconds to wait for the element to appear.
    :return: The element's text content, or `""` if it never appears or has
        no text.
    """
    locator = page.locator(selector).first
    try:
        text = await locator.text_content(timeout=timeout)
    except Exception as exc:
        logger.debug("Selector %r had no text within %sms", selector, timeout, exc_info=exc)
        return ""
    return clean_text(text or "")


async def get_facet_topics(page: Page) -> dict[str, int]:
    """
    Read every topic and its term count out of the discipline facet panel.

    Call `get_element_text(page, FACET_EXPAND_SELECTOR)` and click it first
    if you need every topic; otherwise only the topics visible by default
    are returned.

    :param page: A page currently showing the glossary search screen.
    :return: Mapping of topic name to number of terms filed under it.
    """
    entries = await page.eval_on_selector_all(
        TOPIC_VALUE_SELECTOR,
        """
        (elements) => elements.map((element) => {
            const label = element.querySelector(
                ".coveo-facet-value-label .coveo-facet-value-caption"
            );
            const count = element.querySelector(
                ".coveo-facet-value-label .coveo-facet-value-count"
            );
            if (!label || !count) {
                return null;
            }
            return [label.textContent.trim(), count.textContent.trim()];
        }).filter((entry) => entry !== null)
        """,
    )
    topics: dict[str, int] = {}
    for name, count in entries:
        try:
            topics[clean_text(name)] = parse_int(clean_text(count))
        except ValueError:
            logger.debug("Could not parse term count %r for topic %r", count, name)
            continue
    logger.debug("Read %d topics from the facet panel", len(topics))
    return topics


async def get_glossary_size(page: Page) -> int:
    """
    Read the total number of terms in the glossary off the search screen.

    :param page: A page currently showing the glossary search screen.
    :return: The total term count, or `0` if it could not be read.
    """
    text = await get_element_text(page, TOTAL_COUNT_SELECTOR)
    if not text:
        return 0
    try:
        return parse_int(text)
    except ValueError:
        return 0


async def get_results_header_text(page: Page) -> str:
    """Return the text of the search results header, or `""` if not loaded."""
    return await get_element_text(page, RESULTS_HEADER_SELECTOR)


async def get_total_term_count(page: Page) -> int | None:
    """
    Read the number of terms matched by the current search.

    :param page: A page currently showing glossary search results.
    :return: The matched term count, or `None` if it could not be read.
    """
    text = await get_element_text(page, TOTAL_COUNT_SELECTOR)
    if not text:
        return None
    try:
        return parse_int(text)
    except ValueError:
        return None


async def get_result_links(page: Page) -> list[str]:
    """
    Return the term detail page URLs listed on the current results page.

    :param page: A page currently showing glossary search results.
    :return: Ordered list of term detail URLs, one per result on the page.
    """
    hrefs = await page.eval_on_selector_all(
        RESULT_LINK_SELECTOR,
        "(elements) => elements.map((element) => element.getAttribute('href'))",
    )
    links = [href for href in hrefs if href]
    logger.debug("Read %d result links from the current page", len(links))
    return links


async def get_term_name(page: Page) -> str:
    """
    Return the term name heading on a term detail page.

    :param page: A page currently showing a term detail page.
    :return: The term name.
    :raises ParsingError: If the page has no term name heading. Every
        real term detail page has one, so this almost always means the
        glossary's markup changed underneath this selector rather than
        this particular term genuinely having no name.
    """
    text = await get_element_text(page, TERM_NAME_SELECTOR)
    if not text:
        raise ParsingError(
            f"Could not parse a term name from {page.url}: expected heading "
            f"{TERM_NAME_SELECTOR!r} was not found or was empty. The "
            f"glossary's page structure may have changed."
        )
    return text


class TermBlock(typing.NamedTuple):
    """One paragraph of a term detail block, with any terms it links to."""

    text: str
    """The paragraph's trimmed text content."""

    links: tuple[RelatedTerm, ...]
    """
    Terms linked from this paragraph, e.g. in a "See related terms:" list.
    Empty if the paragraph has no links.
    """


async def get_term_detail_blocks(page: Page) -> list[list[TermBlock]]:
    """
    Return the paragraphs (text and links) of every definition block on a term page.

    A term detail page holds one `TERM_DETAIL_SELECTOR` block per definition
    (a term can have several, one per topic). Each block's paragraphs are
    returned in document order; the first paragraph carries the grammatical
    label and source topic, the definition text follows in the next
    non-empty paragraph, and a later paragraph may link out to related
    terms (see `TermBlock.links`).

    :param page: A page currently showing a term detail page.
    :return: One list of `TermBlock`s per definition block. Never empty -
        see `:raises:` below.
    :raises ParsingError: If no definition sections were found at all.
        Every real term detail page has at least one, so this almost
        always means the glossary's markup changed underneath
        `TERM_DETAIL_SELECTOR` rather than this particular term
        genuinely having no definition.
    """
    sections = await page.eval_on_selector_all(
        TERM_DETAIL_SELECTOR,
        """
        (elements) => elements.map((element) =>
            Array.from(element.querySelectorAll("p,ul")).map((block) => ({
                text: block.textContent.trim(),
                links: Array.from(block.querySelectorAll("a"))
                    .map((anchor) => ({
                        term: anchor.textContent.trim(),
                        url: anchor.getAttribute("href") || "",
                    }))
                    .filter((link) => link.url),
            }))
        )
        """,
    )
    blocks = [
        [
            TermBlock(
                text=clean_text(block["text"]),
                links=tuple(
                    RelatedTerm(term=clean_text(link["term"]), url=link["url"])
                    for link in block["links"]
                ),
            )
            for block in section
        ]
        for section in sections
    ]
    if not blocks:
        raise ParsingError(
            f"Could not parse any definition sections from {page.url}: "
            f"expected blocks matching {TERM_DETAIL_SELECTOR!r} were not "
            f"found. The glossary's page structure may have changed."
        )
    return blocks


class TermImage(typing.NamedTuple):
    """A definition section's illustrative image, if it has one."""

    url: str
    """URL of the image."""

    caption: str
    """Caption text accompanying the image, or `""` if it has none."""


async def get_term_images(page: Page) -> list[TermImage | None]:
    """
    Read each definition section's own illustrative image, if it has one.

    A term detail page repeats one `TERM_SECTION_SELECTOR` wrapper per
    definition (one per topic the term is filed under), and each section
    carries its own image independently of the others. A term with
    several definitions can have a different image (or none at all) per
    section, not one image shared across the whole page. This reads every
    section's image in a single page evaluation, so the result lines up
    index-for-index with `get_term_detail_blocks`'s own per-section list.

    :param page: A page currently showing a term detail page.
    :return: One entry per definition section, in document order. A
        `TermImage` for a section that has one, `None` for a section that doesn't.
    """
    try:
        sections = await page.eval_on_selector_all(
            TERM_SECTION_SELECTOR,
            f"""
            (elements) => elements.map((element) => {{
                const img = element.querySelector("{TERM_IMAGE_SELECTOR}");
                const src = img ? img.getAttribute("src") : null;
                if (!src) return null;
                const desc = element.querySelector("{TERM_IMAGE_CAPTION_SELECTOR}");
                return {{ src, caption: desc ? desc.textContent.trim() : "" }};
            }})
            """,
        )
    except Exception as exc:
        logger.debug("No term sections/images found", exc_info=exc)
        return []

    return [
        TermImage(url=urljoin(BASE_URL, section["src"]), caption=clean_text(section["caption"]))
        if section is not None
        else None
        for section in sections
    ]


GRAMMATICAL_LABELS: dict[Language, dict[str, str]] = {
    Language.ENGLISH: {
        "n.": "Noun",
        "pron.": "Pronoun",
        "vb.": "Verb",
        "v.": "Verb",
        "vt.": "Transitive Verb",
        "vi.": "Intransitive Verb",
        "adj.": "Adjective",
        "adv.": "Adverb",
        "prep.": "Preposition",
        "conj.": "Conjunction",
        "interj.": "Interjection",
        "art.": "Article",
        "det.": "Determiner",
        "num.": "Numeral",
        "aux.": "Auxiliary Verb",
        "modal": "Modal Verb",
        "participle": "Participle",
        "gerund": "Gerund",
    },
    Language.SPANISH: {
        "s.": "Sustantivo",
        "pron.": "Pronombre",
        "v.": "Verbo",
        "adj.": "Adjetivo",
        "adv.": "Adverbio",
        "prep.": "Preposición",
        "conj.": "Conjunción",
        "interj.": "Interjección",
        "art.": "Artículo",
        "det.": "Determinante",
        "num.": "Número",
        "aux.": "Verbo Auxiliar",
        "modal": "Verbo Modal",
        "participio": "Participio",
        "gerundio": "Gerundio",
    },
}
"""Abbreviation-to-full-label mappings, keyed by glossary language."""


def resolve_grammatical_label(language: Language, abbreviation: str) -> str:
    """
    Return the non-abbreviated grammatical label for `abbreviation`.

    :param language: The glossary language `abbreviation` was found in.
    :param abbreviation: The abbreviated grammatical label as it appears on
        the glossary page, e.g. `"n."`.
    :return: The full label, e.g. `"Noun"`. Returns `abbreviation` unchanged
        if there is no known mapping for it.
    """
    return GRAMMATICAL_LABELS[language].get(abbreviation.lower(), abbreviation)
