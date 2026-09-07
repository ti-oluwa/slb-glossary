"""Term-name matching tiers, shared by `local.lexical_search` and `live.relevance`."""

import enum
import re
import typing

from slb_glossary.constants import constants
from slb_glossary.utils import normalize_text

__all__ = [
    "NameMatch",
    "NameMatchTier",
    "classify_name_match",
    "score_name_match",
    "token_overlap_ratio",
]


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
    score: float
    """`[0.0, 1.0]`, already tier-scaled."""

    overlap: float
    """Fraction of query tokens covered. `1.0` except for `PARTIAL_TOKENS`."""


def _contains_as_phrase(haystack: str, needle: str) -> bool:
    """Whether `needle` appears in `haystack` as a whitespace/start/end-bounded phrase."""
    if not needle:
        return False
    pattern = r"(?:^|\s)" + re.escape(needle) + r"(?:$|\s)"
    return re.search(pattern, haystack) is not None


def _token_is_covered(term_tokens: typing.Sequence[str], query_token: str) -> bool:
    """Whether `query_token` equals or is a prefix of some token in `term_tokens`."""
    return any(
        term_token == query_token or term_token.startswith(query_token)
        for term_token in term_tokens
    )


def token_overlap_ratio(query_norm: str, term_norm: str) -> float:
    """Fraction of `query_norm`'s tokens present (see `_token_is_covered`) in `term_norm`."""
    query_tokens = query_norm.split()
    if not query_tokens:
        return 0.0
    term_tokens = term_norm.split()
    covered = sum(1 for token in query_tokens if _token_is_covered(term_tokens, token))
    return covered / len(query_tokens)


def classify_name_match(query: str, term: str) -> NameMatch | None:
    """Classify how strongly `query` name-matches `term`, best tier first. `None` if no overlap at all."""
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
    """`classify_name_match`'s score alone, or `None` if there was no match at all."""
    match = classify_name_match(query, term)
    return match.score if match else None
