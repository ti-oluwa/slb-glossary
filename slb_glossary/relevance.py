"""
Shared relevance-scoring building blocks for search results, local or live.

`slb_glossary.local.scored_search` computes its scores in SQL (bm25 plus
an exact/prefix name-match tier). A live search has no database to run
that kind of query against, so `slb_glossary.query` scores live results
here instead, using the same tiers and the same `constants.content_match_score_cap`
so a local score and a live score mean roughly the same thing to a caller comparing the two.

The actual score values (`constants.exact_match_score`,
`constants.prefix_match_score`, `constants.content_match_score_cap`) live
on `slb_glossary.constants.constants`, alongside every other tunable
constant in the package, rather than as bare module-level values here.
"""

from slb_glossary.constants import constants
from slb_glossary.types import SearchResult

__all__ = [
    "score_name_match",
    "score_content_overlap",
    "score_result",
]


def _normalize(text: str) -> str:
    """Lowercase `text` and collapse its whitespace, for name-match comparisons."""
    return " ".join(text.strip().lower().split())


def score_name_match(query: str, term: str) -> float | None:
    """
    Score `term` against `query` on the exact/prefix name tiers only.

    :param query: The free-text query.
    :param term: A result's term name.
    :return: `constants.exact_match_score`, `constants.prefix_match_score`,
        or `None` if `term` is neither an exact nor a prefix match. `None`
        tells the caller to fall back to `score_content_overlap`, or an
        equivalent bm25 pass.
    """
    query_norm = _normalize(query)
    term_norm = _normalize(term)
    if not query_norm or not term_norm:
        return None
    if term_norm == query_norm:
        return constants.exact_match_score
    if term_norm.startswith(query_norm):
        return constants.prefix_match_score
    return None


def score_content_overlap(query: str, *texts: str) -> float:
    """
    Score `query` against `texts` by token overlap, capped at `constants.content_match_score_cap`.

    Used where there's no larger corpus to rank against (a single live
    result, scored on its own), so a proper bm25-style pass isn't
    possible. What's measured is coverage: how many of `query`'s own
    tokens turn up somewhere in `texts`, not how long `texts` are or how
    often each token repeats. A short exact phrase match and a long one
    both score about as well, so a longer definition doesn't win purely
    for containing more words.

    :param query: The free-text query.
    :param texts: The result's other fields to check for overlap (its
        definition, topic, and so on). Assumed not to be the term name
        itself. Use `score_name_match` for that.
    :return: A score in `[0.0, constants.content_match_score_cap]`.
    """
    query_tokens = _normalize(query).split()
    if not query_tokens:
        return 0.0

    haystack = " ".join(_normalize(text) for text in texts if text)
    if not haystack:
        return 0.0

    matched = sum(1 for token in query_tokens if token in haystack)
    coverage = matched / len(query_tokens)
    return round(constants.content_match_score_cap * coverage, 4)


def score_result(query: str, result: SearchResult) -> float:
    """
    Score `result` against `query`, combining the name and content tiers.

    :param query: The free-text query `result` was found for.
    :param result: The result to score.
    :return: A score in `[0.0, 1.0]`.
    """
    name_score = score_name_match(query, result.term or "")
    if name_score is not None:
        return name_score
    return score_content_overlap(query, result.definition or "", result.topic or "")
