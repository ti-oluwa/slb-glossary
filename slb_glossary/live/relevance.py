"""Scores a live search result against the query that found it."""

import typing

from slb_glossary.constants import constants
from slb_glossary.embeddings import build_embed_text, cosine_similarity, embed
from slb_glossary.scoring import classify_name_match
from slb_glossary.types import SearchMode, SearchResult
from slb_glossary.utils import normalize_text

__all__ = ["score_content_overlap", "score_name_match", "score_result"]

if typing.TYPE_CHECKING:
    import numpy as np


def score_name_match(query: str, term: str) -> float | None:
    """
    Score `term` against `query` on the name-match tiers only (see
    `slb_glossary.scoring.classify_name_match`): exact, prefix,
    whole-phrase containment either way, all query tokens present, or
    some query tokens present.

    :param query: The free-text query.
    :param term: A result's term name.
    :return: The match's tier score (see `classify_name_match`), or
        `None` if `term` shares no meaningful overlap with `query` at
        all. `None` tells the caller to fall back to `score_content_overlap`.
    """
    match = classify_name_match(query, term)
    return match.score if match else None


def score_content_overlap(query: str, *texts: str) -> float:
    """
    Score `query` against `texts` by token overlap, capped at `constants.content_match_score_cap`.

    Measures coverage, that is, how many of `query`'s own tokens turn up
    somewhere in `texts`, not how long `texts` are or how often each
    token repeats. A short exact phrase match and a long one both score
    about as well, so a longer definition does not win purely for
    containing more words.

    :param query: The free-text query.
    :param texts: The result's other fields to check for overlap (its
        definition, topic, and so on). Assumed not to be the term name
        itself. Use `score_name_match` for that.
    :return: A score in `[0.0, constants.content_match_score_cap]`.
    """
    query_tokens = normalize_text(query).split()
    if not query_tokens:
        return 0.0

    haystack = " ".join(normalize_text(text) for text in texts if text)
    if not haystack:
        return 0.0

    matched = sum(1 for token in query_tokens if token in haystack)
    coverage = matched / len(query_tokens)
    return round(constants.content_match_score_cap * coverage, 4)


@typing.overload
def score_result(
    query: str,
    result: SearchResult,
    *,
    mode: typing.Literal[SearchMode.LEXICAL] = SearchMode.LEXICAL,
) -> float: ...
@typing.overload
def score_result(
    query: "np.ndarray",
    result: SearchResult,
    *,
    mode: typing.Literal[SearchMode.SEMANTIC],
) -> float: ...


def score_result(
    query: "str | np.ndarray",
    result: SearchResult,
    *,
    mode: SearchMode = SearchMode.LEXICAL,
) -> float:
    """
    Score `result` against `query`.

    :param query: With `mode=SearchMode.LEXICAL` (the default), the
        free-text query `result` was found for, as a string. With
        `mode=SearchMode.SEMANTIC`, that query's own embedding vector
        instead (`slb_glossary.embeddings.embed`) scoring a stream of
        results this way, embed the query once, up front, and pass the
        same vector to every call, rather than a fresh one each time.
    :param result: The result to score.
    :param mode: `SearchMode.LEXICAL` (the default) or `SearchMode.SEMANTIC`.
        `SearchMode.HYBRID` is not supported: fusing a lexical and a
        semantic ranking needs every result's rank relative to the
        others, which a single result scored on its own can not provide.
    :return: With `mode=SearchMode.LEXICAL`, a score in `[0.0, 1.0]`:
        one of `slb_glossary.scoring.classify_name_match`'s tier scores
        for a name match (exact, prefix, phrase-contains, all-tokens,
        or partial-tokens), otherwise capped at `constants.content_match_score_cap`.
        With `mode=SearchMode.SEMANTIC`, a cosine similarity in
        `[-1.0, 1.0]`, in practice close to `[0.0, 1.0]` for real text,
        not capped.
    :raises ValueError: If `mode` is `SearchMode.HYBRID`.
    :raises EmbeddingError: With `mode=SearchMode.SEMANTIC`, if the
        `semantic` extra is not installed, or the embedding model's
        output size does not match `constants.embedding_dim`.
    """
    if mode is SearchMode.HYBRID:
        raise ValueError(
            "`score_result` does not support `mode=SearchMode.HYBRID`. It scores "
            "one result at a time, but a fused ranking needs every result's "
            "rank relative to the others first."
        )

    if mode is SearchMode.SEMANTIC:
        import numpy as np

        assert isinstance(query, np.ndarray)
        text = build_embed_text(result.term or "", result.definition, result.topic)
        result_vector = embed([text])[0]
        return cosine_similarity(query, result_vector)

    assert isinstance(query, str)
    name_score = score_name_match(query, result.term or "")
    if name_score is not None:
        return name_score
    return score_content_overlap(query, result.definition or "", result.topic or "")
