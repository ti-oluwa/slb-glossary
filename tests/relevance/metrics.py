"""Standard ranking metrics for the relevance benchmark: Recall@k, MRR, NDCG@5."""

import math
import typing

__all__ = ["compute_mean_reciprocal_rank", "compute_ndcg_at_k", "compute_recall_at_k"]


def get_first_hit_rank(ranked_terms: typing.Sequence[str], expected: frozenset[str]) -> int | None:
    """
    Do a 1-based rank of the first term in `ranked_terms` that's in `expected`, or `None`.

    :param ranked_terms: Result term names, best match first.
    :param expected: Term name(s) that count as relevant.
    :return: `1` if the top result is relevant, `2` if the second is
        the first relevant one, and so on; `None` if nothing in
        `ranked_terms` is in `expected`.
    """
    for rank, term in enumerate(ranked_terms, start=1):
        if term in expected:
            return rank
    return None


def compute_recall_at_k(
    results: typing.Sequence[typing.Sequence[str]],
    expecteds: typing.Sequence[frozenset[str]],
    k: int,
) -> float:
    """
    Compute the fraction of queries with at least one relevant term in their top `k` results.

    :param results: One ranked list of result term names per query, best first.
    :param expecteds: One relevant-term-set per query, same order as `results`.
    :param k: How many top results to consider per query.
    :return: `0.0` to `1.0`. `0.0` if `results` is empty.
    """
    if not results:
        return 0.0
    hits = sum(
        1
        for ranked_terms, expected in zip(results, expecteds, strict=True)
        if get_first_hit_rank(ranked_terms[:k], expected) is not None
    )
    return hits / len(results)


def compute_mean_reciprocal_rank(
    results: typing.Sequence[typing.Sequence[str]],
    expecteds: typing.Sequence[frozenset[str]],
) -> float:
    """
    Compute the mean, across queries, of `1 / rank` of the first relevant result (`0` if none found).

    :param results: One ranked list of result term names per query, best first.
    :param expecteds: One relevant-term-set per query, same order as `results`.
    :return: `0.0` to `1.0`. `0.0` if `results` is empty.
    """
    if not results:
        return 0.0
    reciprocal_ranks = []
    for ranked_terms, expected in zip(results, expecteds, strict=True):
        rank = get_first_hit_rank(ranked_terms, expected)
        reciprocal_ranks.append(1.0 / rank if rank else 0.0)
    return sum(reciprocal_ranks) / len(reciprocal_ranks)


def compute_dcg_at_k(
    ranked_terms: typing.Sequence[str], expected: frozenset[str], k: int
) -> float:
    """
    Binary-relevance DCG@k: `sum(1 / log2(rank + 1))` over relevant hits in the top `k`.
    """
    return sum(
        1.0 / math.log2(rank + 1)
        for rank, term in enumerate(ranked_terms[:k], start=1)
        if term in expected
    )


def compute_ndcg_at_k(
    results: typing.Sequence[typing.Sequence[str]],
    expecteds: typing.Sequence[frozenset[str]],
    k: int = 5,
) -> float:
    """
    Compute the mean, across queries, of normalized DCG@k with binary relevance
    (every expected term worth the same).

    The ideal DCG for a query with any expected terms at all is
    `1 / log2(2) == 1` (the best possible ranking puts a relevant
    result first), so this reduces to plain DCG@k averaged across
    queries that have at least one expected term - which every
    benchmark query here does.

    :param results: One ranked list of result term names per query, best first.
    :param expecteds: One relevant-term-set per query, same order as `results`.
    :param k: Cutoff rank.
    :return: `0.0` to `1.0`. `0.0` if `results` is empty.
    """
    if not results:
        return 0.0
    scores = []
    for ranked_terms, expected in zip(results, expecteds, strict=True):
        if not expected:
            continue
        ideal_dcg = 1.0 / math.log2(2)  # a single relevant hit at rank 1
        dcg = compute_dcg_at_k(ranked_terms, expected, k)
        scores.append(min(dcg, ideal_dcg) / ideal_dcg)
    return sum(scores) / len(scores) if scores else 0.0
