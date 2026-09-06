"""
Shared term-name matching signals for lexical ranking.

`slb_glossary.local.lexical.lexical_search` and (eventually)
`slb_glossary.live.relevance` both need to answer the same question:
"how strongly does this query name-match this term?" This module is the
one place that logic lives, so both stay consistent and get improved
together instead of drifting apart.

The tiers, best to worst, mirror a technical glossary's own intuition
about what a "found the term" match looks like:

1. `EXACT` - the query *is* the term name (normalized).
2. `PREFIX` - the term name starts with the query, e.g. a truncated
   query ("gas lif") against its term ("Gas Lift").
3. `CONTAINS` - the query appears as a whitespace-bounded phrase inside
   the term name, or vice-versa (a longer, unstripped query containing
   the term as a phrase), e.g. "lift" inside "Gas Lift", or
   "gas lift valve system" containing "Gas Lift Valve".
4. `ALL_TOKENS` - every query token is present in the term name (as a
   whole token, or a prefix of one, tolerating a trailing typo/
   truncation on that token), just not contiguously as a phrase.
5. `PARTIAL_TOKENS` - some, but not all, query tokens are present.

A term with none of these is not a name match at all; the caller falls
back to its own content-overlap/bm25 scoring for that case.
"""

import enum
import re
import typing

from slb_glossary.constants import constants
from slb_glossary.utils import normalize_text

__all__ = ["NameMatch", "NameMatchTier", "classify_name_match", "score_name_match", "token_overlap_ratio"]


class NameMatchTier(enum.Enum):
    """How strongly a query matches a term's name, best to worst."""

    EXACT = "exact"
    PREFIX = "prefix"
    CONTAINS = "contains"
    ALL_TOKENS = "all_tokens"
    PARTIAL_TOKENS = "partial_tokens"


class NameMatch(typing.NamedTuple):
    """One `classify_name_match` result."""

    tier: NameMatchTier
    """Which tier matched."""

    score: float
    """This match's `[0.0, 1.0]` score, already tier-scaled."""

    overlap: float
    """
    Fraction of the query's tokens covered by the term name, `1.0` for
    every tier except `PARTIAL_TOKENS`, where it is the actual
    (0.0, 1.0) coverage fraction the score was scaled by.
    """


def _contains_as_phrase(haystack: str, needle: str) -> bool:
    """
    Whether `needle` appears in `haystack` as a whitespace/start/end-bounded phrase.

    Deliberately not a plain substring check: that would let "gas"
    match inside "biogas". Requiring a boundary on both sides means
    only a genuine whole-word (or whole-phrase) occurrence counts.

    :param haystack: Normalized text to search within.
    :param needle: Normalized text to search for.
    :return: `True` if `needle` occurs in `haystack` at word boundaries.
    """
    if not needle:
        return False
    pattern = r"(?:^|\s)" + re.escape(needle) + r"(?:$|\s)"
    return re.search(pattern, haystack) is not None


def _token_is_covered(term_tokens: typing.Sequence[str], query_token: str) -> bool:
    """
    Whether `query_token` counts as present in `term_tokens`.

    A token counts as covered if it equals a term token outright, or is
    a prefix of one - the same tolerance FTS5's own prefix queries give
    (`slb_glossary.local.lexical.build_fts_query`), so a truncated or
    lightly misspelled trailing token ("logg" for "logging") still
    counts as coverage rather than falling all the way through to a
    fuzzy-match fallback.

    :param term_tokens: The term name's own tokens.
    :param query_token: One token from the query.
    :return: `True` if `query_token` is covered by some term token.
    """
    return any(term_token == query_token or term_token.startswith(query_token) for term_token in term_tokens)


def token_overlap_ratio(query_norm: str, term_norm: str) -> float:
    """
    Fraction of `query_norm`'s tokens present (see `_token_is_covered`) in `term_norm`.

    :param query_norm: Normalized (`slb_glossary.utils.normalize_text`) query text.
    :param term_norm: Normalized term name.
    :return: `0.0` if `query_norm` has no tokens; otherwise the fraction
        (`0.0` to `1.0`) of its tokens covered by `term_norm`.
    """
    query_tokens = query_norm.split()
    if not query_tokens:
        return 0.0
    term_tokens = term_norm.split()
    covered = sum(1 for token in query_tokens if _token_is_covered(term_tokens, token))
    return covered / len(query_tokens)


def classify_name_match(query: str, term: str) -> NameMatch | None:
    """
    Classify how strongly `query` name-matches `term`, best tier first.

    :param query: Free-text query, as typed (not yet normalized).
    :param term: A candidate result's term name.
    :return: A `NameMatch`, or `None` if `term` shares no meaningful
        overlap with `query` at all (the caller should fall back to
        content-overlap/bm25 scoring in that case).
    """
    query_norm = normalize_text(query)
    term_norm = normalize_text(term)
    if not query_norm or not term_norm:
        return None

    if term_norm == query_norm:
        return NameMatch(NameMatchTier.EXACT, constants.exact_match_score, 1.0)

    if term_norm.startswith(query_norm):
        return NameMatch(NameMatchTier.PREFIX, constants.prefix_match_score, 1.0)

    if _contains_as_phrase(term_norm, query_norm) or _contains_as_phrase(query_norm, term_norm):
        return NameMatch(NameMatchTier.CONTAINS, constants.contains_match_score, 1.0)

    overlap = token_overlap_ratio(query_norm, term_norm)
    if overlap >= 1.0:
        return NameMatch(NameMatchTier.ALL_TOKENS, constants.all_tokens_match_score, 1.0)
    if overlap > 0.0:
        score = round(constants.token_overlap_score_cap * overlap, 4)
        return NameMatch(NameMatchTier.PARTIAL_TOKENS, score, overlap)

    return None


def score_name_match(query: str, term: str) -> float | None:
    """
    `classify_name_match`'s score alone, or `None` if there was no match at all.

    :param query: Free-text query, as typed.
    :param term: A candidate result's term name.
    :return: The match's score, or `None`.
    """
    match = classify_name_match(query, term)
    return match.score if match else None
