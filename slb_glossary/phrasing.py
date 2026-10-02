"""
API for recognizing phrasing like; "what is X", "define X", "tell me about X", in queries and
reducing them to the term-like phrase they're actually about.
"""

import re

from slb_glossary.utils import fold_text

__all__ = ["clean_query", "query_variants", "trim_symbols"]

PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"^what\s+does\s+(?P<term>.+?)\s+mean\s*\??$",
        r"^what\s+(?:is|are|was|were)\s+(?:a|an|the)?\s*(?P<term>.+?)\s*\??$",
        r"^what'?s\s+(?:a|an|the)?\s*(?P<term>.+?)\s*\??$",
        r"^define[\s:,]+(?P<term>.+?)\s*\??$",
        r"^definition\s+of[\s:,]+(?:a|an|the)?\s*(?P<term>.+?)\s*\??$",
        r"^tell\s+me\s+about[\s:,]+(?:a|an|the)?\s*(?P<term>.+?)\s*\??$",
        r"^explain\s+(?:what\s+)?(?:a|an|the)?\s*(?P<term>.+?)\s+(?:is|are|means?)\s*\??$",
        r"^explain[\s:,]+(?P<term>.+?)\s*\??$",
        r"^meaning\s+of[\s:,]+(?:a|an|the)?\s*(?P<term>.+?)\s*\??$",
    )
)
"""Regex patterns for common query phrasings"""


EDGE_SYMBOLS_RE = re.compile(r"^[\W_]+|[\W_]+$")
"""Leading/trailing runs of anything that is not a letter or digit."""


def trim_symbols(query: str) -> str:
    """
    Trim leading/trailing punctuation and symbols off `query`, keeping any inside it.

    `"capillary-"`, `":rig"`, `"porous?"` and `"(porosity)"` become `"capillary"`, `"rig"`,
    `"porous"` and `"porosity"`, while `"gas-oil ratio"` is left alone.

    :param query: The raw query.
    :return: `query` with edge symbols and whitespace trimmed. Just whitespace-trimmed
        `query` if it has no letters or digits at all, so a symbol-only query is not
        turned into an empty one.
    """
    stripped = query.strip()
    trimmed = EDGE_SYMBOLS_RE.sub("", stripped)
    return trimmed or stripped


def clean_query(query: str) -> str:
    """
    Reduce `query` to the term-like phrase it is actually about.

    Local and live matching both work best against actual term names and
    words. Unstripped, a query like \"what is porosity?\" would be searched as
    the literal phrase, which may not match anything. So this:

    1. trims stray leading/trailing punctuation and symbols (`"porous?"`, `":rig"`),
    2. strips a recognized natural-language wrapper (\"what is X\", \"define X\", ...),
    3. trims edge symbols off what is left.

    Symbols *inside* the term (`"capillary-pressure"`) are kept here, as the term
    might really contain them; matching itself is symbol-insensitive, see
    `slb_glossary.utils.fold_text`.

    :param query: The raw query as given by the caller.
    :return: The cleaned query. Just whitespace-trimmed `query` if there was
        nothing to clean.
    """
    stripped = trim_symbols(query)
    for pattern in PATTERNS:
        match = pattern.match(stripped)
        if match:
            term = trim_symbols(match.group("term"))
            if term:
                return term
    return stripped


def query_variants(query: str) -> tuple[str, ...]:
    """
    The distinct spellings of `query` worth embedding or matching, most literal first.

    A term named `"Water-cut"` and a query of `"water cut"` mean the same thing, but
    an embedding model sees different tokens. Embedding both spellings (and merging the
    vectors) makes the match robust to whichever way the term was written.

    :param query: A query, normally already passed through `clean_query`.
    :return: The cleaned query, plus its `fold_text` form if that differs by more than
        case or whitespace. Empty if
        `query` has no letters or digits at all.
    """
    cleaned = clean_query(query)
    folded = fold_text(cleaned)
    if not folded:
        return ()
    # A difference of just case/whitespace is not worth a second embedding.
    return (cleaned,) if " ".join(cleaned.lower().split()) == folded else (cleaned, folded)
