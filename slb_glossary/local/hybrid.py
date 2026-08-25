"""Hybrid local search API. Lexical (bm25) and semantic (embedding) ranking, fused."""

import logging
import time
from collections.abc import Collection, Sequence

from slb_glossary.constants import constants
from slb_glossary.local.lexical import lexical_search
from slb_glossary.local.types import Database
from slb_glossary.local.vectors import vector_search
from slb_glossary.types import SearchResult

logger = logging.getLogger(__name__)

__all__ = ["hybrid_search"]


def _compute_rrf_scores(
    *rankings: Sequence[str], weights: Sequence[float], k: float
) -> dict[str, float]:
    """
    Score every URL appearing in any of `rankings` by weighted reciprocal rank fusion.

    :param rankings: One ranked sequence of URLs (best first) per ranker.
        A URL missing from a given ranking just doesn't get that
        ranker's term added to its score, rather than being penalized
        explicitly.
    :param weights: One weight per ranker, same length and order as `rankings`.
    :param k: The RRF `k` constant. See `constants.rrf_k`.
    :return: URL to fused score, unsorted and not normalized to any
        particular range. `hybrid_search` min-max normalizes these
        relative to each other into a `[0.0, 1.0]`-ish band.
    """
    scores: dict[str, float] = {}
    for ranking, weight in zip(rankings, weights, strict=True):
        if not weight:
            continue
        for rank, url in enumerate(ranking, start=1):
            scores[url] = scores.get(url, 0.0) + weight / (k + rank)
    return scores


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

    A term whose name exactly matches or starts with `query` is always
    ranked ahead of everything else, exactly like `lexical_search`, so a
    semantically related but differently named term never outranks the
    term actually named that. Everything else is ranked by reciprocal
    rank fusion between the lexical (bm25) and semantic (embedding)
    result orderings.

    RRF is the standard way to combine rankers whose raw scores aren't on
    comparable scales, which is exactly the situation here: bm25 is
    unbounded and corpus-dependent, cosine similarity is bounded but has its
    own distribution per embedding model, and any fixed formula over their
    raw scores tends to be tuned to one dataset and misbehave on another.
    RRF sidesteps that by only looking at each candidate's *rank* in each
    list, not its raw score:

        score = sum(weight / (k + rank), over every ranker that found it)

    which needs no calibration between the two rankers at all. See
    `slb_glossary.constants.Constants.rrf_k`/`lexical_weight`/`semantic_weight`
    to tune it.

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
        (e.g. `"en"`/`"es"`). `None` (the default) doesn't filter by language.
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
    :return: `(result, score)` pairs, best match first. `score` is on the
        same `[0.0, 1.0]` scale `lexical_search` uses: `1.0`/`0.9` for the
        exact/prefix name tier, everything else is scaled below
        `constants.relevance_threshold` via `constants.content_match_score_cap`,
        the same way a bm25-only match is capped there.
    :raises DatabaseError: If `sqlite-vec` isn't installed, or its
        extension can't be loaded.
    :raises EmbeddingError: If `model2vec` isn't installed, or the
        embedding model's output size doesn't match `constants.embedding_dim`.
    """
    started_at = time.monotonic()
    pool = candidate_pool if candidate_pool is not None else constants.hybrid_candidate_pool

    lexical = await lexical_search(
        db,
        query,
        topic=topic,
        start_letter=start_letter,
        language=language,
        limit=pool,
        fuzzy=fuzzy,
        exclude=exclude,
    )
    semantic = await vector_search(
        db,
        query,
        topic=topic,
        start_letter=start_letter,
        language=language,
        limit=pool,
        fuzzy=fuzzy,
        exclude=exclude,
    )

    name_tier = [
        (result, score) for result, score in lexical if score >= constants.prefix_match_score
    ]
    name_tier_urls = {result.url for result, _ in name_tier if result.url}

    lexical_ranking = [
        result.url
        for result, score in lexical
        if score < constants.prefix_match_score and result.url
    ]
    semantic_ranking = [
        result.url for result, _ in semantic if result.url and result.url not in name_tier_urls
    ]

    fused_scores = _compute_rrf_scores(
        lexical_ranking,
        semantic_ranking,
        weights=(constants.lexical_weight, constants.semantic_weight),
        k=constants.rrf_k,
    )

    results_by_url: dict[str, SearchResult] = {}
    for result, _ in lexical:
        if result.url:
            results_by_url[result.url] = result
    for result, _ in semantic:
        if result.url:
            results_by_url.setdefault(result.url, result)

    ranked_urls = sorted(fused_scores, key=lambda url: fused_scores[url], reverse=True)
    worst = min(fused_scores.values(), default=0.0)
    best = max(fused_scores.values(), default=0.0)
    spread = (best - worst) or 1.0

    fused_tier: list[tuple[SearchResult, float]] = [
        (
            results_by_url[url],
            round(constants.content_match_score_cap * (fused_scores[url] - worst) / spread, 4),
        )
        for url in ranked_urls
        if url in results_by_url
    ]

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
