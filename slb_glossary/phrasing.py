"""
API for recognizing phrasing like; "what is X", "define X", "tell me about X", in queries and
reducing them to the term-like phrase they're actually about.
"""

import re

__all__ = ["clean_query"]

PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"^what\s+does\s+(?P<term>.+?)\s+mean\s*\??$",
        r"^what\s+(?:is|are|was|were)\s+(?:a|an|the)?\s*(?P<term>.+?)\s*\??$",
        r"^what'?s\s+(?:a|an|the)?\s*(?P<term>.+?)\s*\??$",
        r"^define\s+(?P<term>.+?)\s*\??$",
        r"^definition\s+of\s+(?:a|an|the)?\s*(?P<term>.+?)\s*\??$",
        r"^tell\s+me\s+about\s+(?:a|an|the)?\s*(?P<term>.+?)\s*\??$",
        r"^explain\s+(?:what\s+)?(?:a|an|the)?\s*(?P<term>.+?)\s+(?:is|are|means?)\s*\??$",
        r"^explain\s+(?P<term>.+?)\s*\??$",
        r"^meaning\s+of\s+(?:a|an|the)?\s*(?P<term>.+?)\s*\??$",
    )
)
"""
Recognized phrasings. Each is matched whole-string (not just a prefix),
so a query that merely contains one of these words somewhere ("geometric
mean", "explain and give an example") isn't mangled. Only a query that
is one of these phrasings, start to end, gets stripped down to its `term` group.
"""


def clean_query(query: str) -> str:
    """
    Strip a recognized natural-language wrapper off `query`, leaving just the term.

    Local and live matching both work against actual term names and
    words, not conversational phrasing. Unstripped, a query like "what is
    porosity" would be searched as the literal phrase "what is porosity",
    which typically matches nothing, since no term's name or text
    contains the word "what". Stripping first gives such a query the same
    shot at a real match that searching "porosity" directly would get.

    :param query: The raw query as given by the caller.
    :return: `query` with a recognized wrapper stripped and surrounding
        whitespace trimmed. Just whitespace-trimmed `query` if no wrapper
        was recognized.
    """
    stripped = query.strip()
    for pattern in PATTERNS:
        match = pattern.match(stripped)
        if match:
            term = match.group("term").strip()
            if term:
                return term
    return stripped
