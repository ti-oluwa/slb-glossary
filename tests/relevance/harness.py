"""
Reusable evaluation harness: seed the benchmark corpus, run a search
function over the benchmark dataset, score the results.

```python
from slb_glossary.local.connection import database
from slb_glossary.local.lexical import lexical_search
from tests.relevance.harness import evaluate, seed_corpus

async with database(":memory:") as db:
    await seed_corpus(db)
    report = await evaluate(db, lexical_search)
    print(report.format_table())
```
"""

import dataclasses
import typing

from slb_glossary.local.api import upsert_results
from slb_glossary.local.types import Database
from slb_glossary.types import SearchResult
from tests.relevance.corpus import CORPUS
from tests.relevance.dataset import CATEGORIES, DATASET, BenchmarkQuery
from tests.relevance.metrics import mean_reciprocal_rank, ndcg_at_k, recall_at_k

__all__ = ["EvaluationReport", "evaluate", "seed_corpus"]

SearchFn = typing.Callable[
    [Database, str], typing.Awaitable[list[tuple[SearchResult, float]]]
]
"""
Signature every `slb_glossary.local` search function
(`lexical_search`/`vector_search`/`hybrid_search`) already satisfies:
`(db, query, ...) -> [(result, score), ...]`, best first. `evaluate`
calls it as `search_fn(db, query.query, limit=limit)`.
"""


async def seed_corpus(db: Database, *, url_prefix: str = "https://benchmark.local/terms/") -> None:
    """
    Upsert `tests.relevance.corpus.CORPUS` into `db`, one synthetic URL per entry.

    :param db: The (typically temporary/in-memory) local database to seed.
    :param url_prefix: Prefix for each entry's synthetic URL, `{url_prefix}{n}`.
    """
    results = [
        SearchResult(
            term=entry["term"],
            definition=entry.get("definition"),
            grammatical_label=entry.get("grammatical_label"),
            topic=entry.get("topic"),
            url=f"{url_prefix}{index}",
            language=entry.get("language", "en"),
        )
        for index, entry in enumerate(CORPUS)
    ]
    await upsert_results(db, results)


@dataclasses.dataclass(frozen=True)
class CategoryMetrics:
    """Metrics for one benchmark category (or the whole dataset, as `"overall"`)."""

    category: str
    n: int
    recall_at_1: float
    recall_at_3: float
    recall_at_5: float
    mrr: float
    ndcg_at_5: float


@dataclasses.dataclass(frozen=True)
class QueryOutcome:
    """One query's raw outcome, kept around for failure-case inspection."""

    query: BenchmarkQuery
    ranked_terms: list[str]
    rank_of_first_hit: int | None


@dataclasses.dataclass(frozen=True)
class EvaluationReport:
    """Full `evaluate()` result: per-category and overall metrics, plus raw outcomes."""

    mode_label: str
    overall: CategoryMetrics
    by_category: dict[str, CategoryMetrics]
    outcomes: list[QueryOutcome]

    def failures(self, *, min_rank: int = 4) -> list[QueryOutcome]:
        """
        Queries whose first relevant result ranked worse than `min_rank`
        (or never appeared at all).

        :param min_rank: A query whose first hit ranks at or below this
            (or has no hit) counts as a failure. Default `4` means
            "outside the top 3".
        :return: Matching `QueryOutcome`s, in dataset order.
        """
        return [
            outcome
            for outcome in self.outcomes
            if outcome.rank_of_first_hit is None or outcome.rank_of_first_hit >= min_rank
        ]

    def format_table(self) -> str:
        """Render per-category and overall metrics as a plain-text table."""
        header = f"{'category':<20}{'n':>4}{'R@1':>8}{'R@3':>8}{'R@5':>8}{'MRR':>8}{'NDCG@5':>8}"
        lines = [f"-- {self.mode_label} --", header, "-" * len(header)]
        for category in (*CATEGORIES, "overall"):
            metrics = self.by_category.get(category) if category != "overall" else self.overall
            if metrics is None:
                continue
            lines.append(
                f"{metrics.category:<20}{metrics.n:>4}"
                f"{metrics.recall_at_1:>8.2f}{metrics.recall_at_3:>8.2f}"
                f"{metrics.recall_at_5:>8.2f}{metrics.mrr:>8.2f}{metrics.ndcg_at_5:>8.2f}"
            )
        return "\n".join(lines)


def _metrics_for(category: str, ranked: list[list[str]], expecteds: list[frozenset[str]]) -> CategoryMetrics:
    return CategoryMetrics(
        category=category,
        n=len(ranked),
        recall_at_1=recall_at_k(ranked, expecteds, 1),
        recall_at_3=recall_at_k(ranked, expecteds, 3),
        recall_at_5=recall_at_k(ranked, expecteds, 5),
        mrr=mean_reciprocal_rank(ranked, expecteds),
        ndcg_at_5=ndcg_at_k(ranked, expecteds, 5),
    )


async def evaluate(
    db: Database,
    search_fn: SearchFn,
    *,
    dataset: list[BenchmarkQuery] | None = None,
    limit: int = 5,
    mode_label: str = "",
    **search_kwargs: typing.Any,
) -> EvaluationReport:
    """
    Run `search_fn` over every query in `dataset`, score it, and report per-category and overall metrics.

    :param db: A local database already seeded (typically via `seed_corpus`).
    :param search_fn: `lexical_search`/`vector_search`/`hybrid_search`,
        or any callable with the same `(db, query, *, limit=...) -> [(result, score), ...]` shape.
    :param dataset: Queries to run. `None` (the default) uses the full
        `tests.relevance.dataset.DATASET`.
    :param limit: `limit` passed through to `search_fn`. `5`, matching
        the deepest metric computed here (`NDCG@5`/`Recall@5`).
    :param mode_label: Label for `EvaluationReport.format_table`'s header, e.g. `"lexical"`.
    :param search_kwargs: Extra keyword arguments forwarded to `search_fn` (e.g. `topic=`).
    :return: The full evaluation report.
    """
    queries = dataset if dataset is not None else DATASET
    outcomes: list[QueryOutcome] = []
    for benchmark_query in queries:
        results = await search_fn(db, benchmark_query.query, limit=limit, **search_kwargs)
        ranked_terms = [result.term for result, _ in results if result.term]
        rank = None
        for rank_index, term in enumerate(ranked_terms, start=1):
            if term in benchmark_query.expected:
                rank = rank_index
                break
        outcomes.append(
            QueryOutcome(query=benchmark_query, ranked_terms=ranked_terms, rank_of_first_hit=rank)
        )

    all_ranked = [outcome.ranked_terms for outcome in outcomes]
    all_expected = [outcome.query.expected for outcome in outcomes]
    overall = _metrics_for("overall", all_ranked, all_expected)

    by_category: dict[str, CategoryMetrics] = {}
    for category in CATEGORIES:
        subset = [outcome for outcome in outcomes if outcome.query.category == category]
        if not subset:
            continue
        by_category[category] = _metrics_for(
            category,
            [outcome.ranked_terms for outcome in subset],
            [outcome.query.expected for outcome in subset],
        )

    return EvaluationReport(
        mode_label=mode_label, overall=overall, by_category=by_category, outcomes=outcomes
    )
