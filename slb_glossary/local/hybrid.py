"""Hybrid local search API. Lexical (bm25) and semantic (embedding) ranking, fused."""

import logging
import time
from collections.abc import Collection, Sequence

from slb_glossary.constants import constants
from slb_glossary.local.lexical import lexical_search
from slb_glossary.local.types import Database
from slb_glossary.local.vector import vector_search
from slb_glossary.scoring import token_overlap_ratio
from slb_glossary.types import SearchResult
from slb_glossary.utils import normalize_text

logger = logging.getLogger(__name__)

__all__ = ["hybrid_search"]

RERANK_TOKEN_OVERLAP_WEIGHT = 0.15
"""
How much weight `_rerank_fused_tier`'s cheap, deterministic name-overlap
signal gets when re-ordering the RRF-fused tier, on top of each
candidate's own (already `[0.0, 1.0]`-normalized) fused RRF score.

Small on purpose: RRF's own fused rank is still the dominant signal.
This only nudges the order among close-scoring candidates toward the
one that also happens to share more of its name with the query - a
genuine glossary-relevant signal RRF's rank-only view can't see on its
own - without letting it override a clear semantic or lexical winner.
`0.15` was chosen so that a full token-overlap match (`1.0`) can, at
most, close a `0.15`-wide RRF score gap; see `_rerank_fused_tier`.
"""


def get_result_key(result: SearchResult) -> tuple[str, str] | None:
    """
    A result's identity for ranking/deduplication purposes.
    Returned as `(url, topic)`, not `url` alone.

    :param result: The result to key.
    :return: `(result.url, result.topic or "")`, or `None` if `result` has no `url`.
    """
    if not result.url:
        return None
    return (result.url, result.topic or "")


def compute_rrf_scores(
    *rankings: Sequence[tuple[str, str]], weights: Sequence[float], k: float
) -> dict[tuple[str, str], float]:
    """
    Score every result appearing in any of `rankings` by weighted reciprocal rank fusion.

    :param rankings: One ranked sequence of `get_result_key`s (best first)
        per ranker. A key missing from a given ranking just does not get
        that ranker's term added to its score, rather than being
        penalized explicitly.
    :param weights: One weight per ranker, same length and order as `rankings`.
    :param k: The RRF `k` constant. See `constants.rrf_k`.
    :return: Result key to fused score, unsorted and not normalized to
        any particular range. `hybrid_search` min-max normalizes these
        relative to each other into a `[0.0, 1.0]`-ish band.
    """
    scores: dict[tuple[str, str], float] = {}
    for ranking, weight in zip(rankings, weights, strict=True):
        if not weight:
            continue
        for rank, key in enumerate(ranking, start=1):
            scores[key] = scores.get(key, 0.0) + weight / (k + rank)
    return scores


def _rerank_fused_tier(
    query: str, fused: list[tuple[SearchResult, float]]
) -> list[tuple[SearchResult, float]]:
    """
    Nudge the RRF-fused tier's order using each candidate's own name-overlap with `query`.

    A glossary-aware signal RRF's rank-only view can't see: two
    candidates can land at the same fused rank for entirely different
    reasons (one via a strong semantic match, one via a weak,
    coincidental lexical one), and RRF alone has no way to prefer the
    one whose *name* is actually closer to the query. This adds
    `slb_glossary.scoring.token_overlap_ratio` (query tokens present in
    the term name, `[0.0, 1.0]`) scaled by
    `RERANK_TOKEN_OVERLAP_WEIGHT` on top of each result's already
    `[0.0, 1.0]`-normalized fused score, then re-sorts.

    Deliberately cheap and deterministic - no embedding call, no
    cross-encoder, just a string comparison against data already in
    hand - and deliberately small relative to the fused score itself,
    so this only ever breaks ties/reorders close calls, never
    overturns a clear semantic or lexical winner. The tier this runs
    over already excludes every exact/prefix/contains name match
    (`hybrid_search` pulls those out ahead of fusion entirely), so this
    is choosing among candidates that were, at best, a partial lexical
    match or a purely semantic one.

    :param query: The original, not-yet-`clean_query`-processed search query.
    :param fused: `(result, score)` pairs from `hybrid_search`'s RRF
        fusion, already sorted best-first, `score` in `[0.0, 1.0]`.
    :return: The same pairs (scores unchanged, so the public score
        semantics don't shift), re-sorted by the combined key.
    """
    if not fused:
        return fused

    query_norm = normalize_text(query)

    def rerank_key(pair: tuple[SearchResult, float]) -> float:
        result, score = pair
        overlap = token_overlap_ratio(query_norm, normalize_text(result.term or ""))
        return score + RERANK_TOKEN_OVERLAP_WEIGHT * overlap

    return sorted(fused, key=rerank_key, reverse=True)


async def hybrid_search(
    db: Database,
    query: str,
    *,
    topic: str | None = None,
    start_letter: str | None = None,
    language: str | None = None,
    limit: int | None = 20,
    fuzzy: bool = False,
    exclude: Collection[str] | None = None,
    candidate_pool: int | None = None,
) -> list[tuple[SearchResult, float]]:
    """
    Search the local database by both lexical and semantic similarity to `query`,
    fused, best match first.

    A term whose name is an exact, prefix, or whole-phrase-containment
    match for `query` (`slb_glossary.scoring.classify_name_match`'s top
    three tiers) is always ranked ahead of everything else, exactly
    like `lexical_search`, so a semantically related but differently
    named term never outranks the term actually named that. A weaker
    lexical hit (all-tokens/partial-tokens; see `classify_name_match`)
    does not get this guarantee - it competes in the fused ranking
    below, on equal footing with the semantic evidence, since a partial
    or coincidental word overlap is not strong enough evidence on its
    own to override a genuinely better semantic match.

    Everything else is ranked by reciprocal rank fusion (RRF) between
    the lexical (bm25) and semantic (embedding) result orderings, then
    lightly re-ordered by `_rerank_fused_tier`'s cheap, deterministic
    name-overlap signal.

    RRF is the standard way to combine rankers whose raw scores aren't on
    comparable scales, which is exactly the situation here. bm25 is
    unbounded and corpus-dependent, cosine similarity is bounded but has its
    own distribution per embedding model, and any fixed formula over their
    raw scores tends to be tuned to one dataset and misbehave on another.
    RRF sidesteps that by only looking at each candidate's *rank* in each
    list, not its raw score:

    ```
    score = sum(weight / (k + rank), over every ranker that found it)
    ```

    which needs no calibration between the two rankers at all. See
    `constants.rrf_k`/`lexical_weight`/`semantic_weight` to tune it, and
    `scripts/relevance_bench.py --rrf-sweep` to sweep candidate values
    against `tests/relevance`'s benchmark and compare the results.

    Needs terms already embedded via `slb_glossary.local.embed_terms`. A
    term synced or imported since the last `embed_terms` call is only
    found here by its lexical ranking, never its semantic one.

    :param db: The local database to search.
    :param query: Free-text query, passed to both `lexical_search` and
        `vector_search` as-is.
    :param topic: Restrict results to this topic, or several
        comma-separated topics (case-insensitive exact match by default).
    :param start_letter: Restrict results to terms starting with this letter.
    :param language: Restrict results to this glossary language edition
        (e.g. `"en"`/`"es"`). `None` (the default) does not filter by language.
    :param limit: Maximum number of results to return. `None` for unlimited.
    :param fuzzy: If `True`, tolerate minor misspellings/partial names in
        `topic` by resolving it against locally stored topic names first.
        Has no effect if `topic` is falsy.
    :param exclude: URLs and/or term names to leave out of the results entirely.
    :param candidate_pool: Candidates pulled from each ranker before
        fusion. `None` (the default) uses `constants.hybrid_candidate_pool`.
        Raise this if a result that should be findable by one ranker, but
        ranks outside its top few there, is going missing from the fused
        results; lower it to search faster at the cost of that.
    :return: `(result, score)` pairs, best match first. `score` is one of
        `classify_name_match`'s tier scores for the guaranteed-top tier
        (exact/prefix/contains); everything else is the fused ranking,
        min-max normalized against its own result set into `[0.0, 1.0]`
        (the name-overlap rerank only affects ordering, not this
        reported score), not capped the way `lexical_search`'s own
        bm25-only score is, since a fused rank already reflects a
        genuine signal from two independent rankers rather than word
        overlap alone.
    :raises DatabaseError: If `sqlite-vec` is not installed, or its extension
        can not be loaded.
    :raises EmbeddingError: If `model2vec` is not installed, or the
        embedding model's output size does not match `constants.embedding_dim`.
    """
    started_at = time.monotonic()
    pool = candidate_pool if candidate_pool is not None else constants.hybrid_candidate_pool

    from slb_glossary.local.api import resolve_topic

    # Resolved once here rather than once each inside `lexical_search`/
    # `vector_search`. With `fuzzy=True`, resolving a topic reads every
    # locally stored topic name (`slb_glossary.local.api.get_topics`) to
    # fuzzy-match against, and both sub-searches would otherwise redo
    # that same read for the same `topic`/`language`. Passing the
    # already-resolved topic through with `fuzzy=False` means that
    # `get_topics` only runs at most once per `hybrid_search` call, on any
    # database size.
    resolved_topic = await resolve_topic(db, topic, fuzzy, language=language)

    lexical = await lexical_search(
        db,
        query,
        topic=resolved_topic,
        start_letter=start_letter,
        language=language,
        limit=pool,
        fuzzy=False,
        exclude=exclude,
    )
    semantic = await vector_search(
        db,
        query,
        topic=resolved_topic,
        start_letter=start_letter,
        language=language,
        limit=pool,
        fuzzy=False,
        exclude=exclude,
    )

    # The guaranteed-top tier: exact, prefix, or whole-phrase containment
    # (see `classify_name_match`). Deliberately *not* all-tokens/
    # partial-tokens too - those are weak enough evidence that they
    # should compete in the fused ranking below rather than
    # automatically outranking a strong semantic match.
    name_tier = [
        (result, score) for result, score in lexical if score >= constants.contains_match_score
    ]
    name_tier_keys = {key for result, _ in name_tier if (key := get_result_key(result))}

    lexical_ranking = [
        key
        for result, score in lexical
        if score < constants.contains_match_score and (key := get_result_key(result))
    ]
    semantic_ranking = [
        key
        for result, _ in semantic
        if (key := get_result_key(result)) and key not in name_tier_keys
    ]

    fused_scores = compute_rrf_scores(
        lexical_ranking,
        semantic_ranking,
        weights=(constants.lexical_weight, constants.semantic_weight),
        k=constants.rrf_k,
    )

    results_by_key: dict[tuple[str, str], SearchResult] = {}
    for result, _ in lexical:
        if key := get_result_key(result):
            results_by_key[key] = result
    for result, _ in semantic:
        if key := get_result_key(result):
            results_by_key.setdefault(key, result)

    ranked_keys = sorted(fused_scores, key=lambda key: fused_scores[key], reverse=True)
    worst = min(fused_scores.values(), default=0.0)
    best = max(fused_scores.values(), default=0.0)
    spread = (best - worst) or 1.0

    fused_tier: list[tuple[SearchResult, float]] = [
        (
            results_by_key[key],
            round((fused_scores[key] - worst) / spread, 4),
        )
        for key in ranked_keys
        if key in results_by_key
    ]
    fused_tier = _rerank_fused_tier(query, fused_tier)

    combined = name_tier + fused_tier
    if limit:
        combined = combined[:limit]

    elapsed = time.monotonic() - started_at
    logger.debug(
        "Local `hybrid_search` for %r yielded %d result(s) (%d name-tier, %d fused) in %.3fs",
        query,
        len(combined),
        len(name_tier),
        len(fused_tier),
        elapsed,
    )
    return combined
