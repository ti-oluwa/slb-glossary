#!/usr/bin/env python
"""
Run the relevance benchmark (`tests/relevance/`) and print before/after tables.

```bash
python scripts/relevance_bench.py            # lexical only (no extra deps)
python scripts/relevance_bench.py --semantic  # + semantic/hybrid (needs `semantic` extra)
python scripts/relevance_bench.py --rrf-sweep # + grid-search RRF weights/k (implies --semantic)
```
"""

import argparse
import asyncio
import logging
import sys
import tempfile
import typing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from slb_glossary.constants import constants
from slb_glossary.local.api import row_to_result
from slb_glossary.local.connection import database
from slb_glossary.local.types import Database
from slb_glossary.phrasing import clean_query
from slb_glossary.types import SearchResult
from slb_glossary.utils import normalize_text
from tests.relevance.harness import evaluate, seed_corpus

logging.getLogger("slb_glossary").setLevel(logging.WARNING)


async def _legacy_lexical_search(
    db: Database, query: str, *, limit: int | None = 20, **_: typing.Any
) -> list[tuple[SearchResult, float]]:
    """Frozen pre-tiering copy of `lexical_search`: exact/prefix only, then plain bm25, no fuzzy fallback."""
    from slb_glossary.local.lexical import build_fts_query

    normalized_query = clean_query(query)
    query_norm = normalize_text(normalized_query)
    weights = ", ".join(str(weight) for weight in (10.0, 1.5, 3.0))
    sql = f"""
        SELECT terms.*,
            (LOWER(terms.term) = ?) AS is_exact,
            (? != '' AND LOWER(terms.term) LIKE ? || '%') AS is_prefix,
            bm25(terms_fts, {weights}) AS bm25_score
        FROM terms
        JOIN terms_fts ON terms.rowid = terms_fts.rowid
        WHERE terms_fts MATCH ?
        ORDER BY is_exact DESC, is_prefix DESC, bm25_score ASC
    """
    params = [query_norm, query_norm, query_norm, build_fts_query(normalized_query)]
    async with db.connection.execute(sql, params) as cursor:
        rows = await cursor.fetchall()

    others_bm25 = [
        row["bm25_score"] for row in rows if not row["is_exact"] and not row["is_prefix"]
    ]
    worst = max(others_bm25, default=0.0)
    best = min(others_bm25, default=0.0)
    spread = (worst - best) or 1.0

    scored: list[tuple[SearchResult, float]] = []
    for row in rows:
        if row["is_exact"]:
            score = constants.exact_match_score
        elif row["is_prefix"]:
            score = constants.prefix_match_score
        else:
            score = round(
                constants.content_match_score_cap * (worst - row["bm25_score"]) / spread, 4
            )
        scored.append((row_to_result(row), score))

    if limit:
        scored = scored[:limit]
    return scored


async def run_lexical(db: Database) -> None:
    from slb_glossary.local.lexical import lexical_search

    before = await evaluate(db, _legacy_lexical_search, mode_label="lexical (before)")
    after = await evaluate(db, lexical_search, mode_label="lexical (after)")
    print(before.format_table())
    print()
    print(after.format_table())
    print()
    print(f"Failures still outside the top 3, after ({len(after.failures())}):")
    for outcome in after.failures():
        print(
            f"  [{outcome.query.category}] {outcome.query.query!r} -> {outcome.ranked_terms[:5]!r}"
        )


async def run_semantic_and_hybrid(db: Database) -> bool:
    try:
        from slb_glossary.local.hybrid import hybrid_search
        from slb_glossary.local.vector import embed_terms, vector_search
    except ImportError:
        print(
            "Skipping semantic/hybrid: `semantic` extra not installed "
            "(`pip install slb-glossary[semantic]`)."
        )
        return False

    try:
        await embed_terms(db, only_missing=False)
    except Exception as exc:
        print(f"Skipping semantic/hybrid: could not embed the corpus ({exc!r}).")
        return False

    semantic = await evaluate(db, vector_search, mode_label="semantic")
    hybrid = await evaluate(db, hybrid_search, mode_label="hybrid")
    print(semantic.format_table())
    print()
    print(hybrid.format_table())
    return True


async def run_rrf_sweep(db: Database) -> None:
    """Grid-search `lexical_weight`/`semantic_weight`/`rrf_k` against the benchmark; report the best by NDCG@5."""
    from slb_glossary.constants import constants
    from slb_glossary.local.hybrid import hybrid_search

    lexical_weights = (0.75, 1.0, 1.25, 1.5, 2.0)
    semantic_weights = (0.75, 1.0, 1.25, 1.5)
    rrf_ks = (20, 30, 40, 60)

    original = (constants.lexical_weight, constants.semantic_weight, constants.rrf_k)
    best: tuple[float, tuple[float, float, int]] | None = None
    rows: list[tuple[float, float, int, float, float]] = []
    try:
        for lexical_weight in lexical_weights:
            for semantic_weight in semantic_weights:
                for rrf_k in rrf_ks:
                    constants.lexical_weight = lexical_weight
                    constants.semantic_weight = semantic_weight
                    constants.rrf_k = rrf_k
                    report = await evaluate(db, hybrid_search, mode_label="hybrid")
                    rows.append(
                        (
                            lexical_weight,
                            semantic_weight,
                            rrf_k,
                            report.overall.ndcg_at_5,
                            report.overall.recall_at_5,
                        )
                    )
                    if best is None or report.overall.ndcg_at_5 > best[0]:
                        best = (report.overall.ndcg_at_5, (lexical_weight, semantic_weight, rrf_k))
    finally:
        constants.lexical_weight, constants.semantic_weight, constants.rrf_k = original

    rows.sort(key=lambda row: row[3], reverse=True)
    print(f"{'lexical_w':>10}{'semantic_w':>12}{'rrf_k':>8}{'NDCG@5':>10}{'R@5':>8}")
    for lexical_weight, semantic_weight, rrf_k, ndcg, recall in rows[:10]:
        print(
            f"{lexical_weight:>10.2f}{semantic_weight:>12.2f}{rrf_k:>8}{ndcg:>10.3f}{recall:>8.3f}"
        )
    if best:
        _, (lw, sw, k) = best
        print(
            f"\nBest by NDCG@5: lexical_weight={lw}, semantic_weight={sw}, rrf_k={k} "
            f"(current baseline: lexical_weight={original[0]}, semantic_weight={original[1]}, rrf_k={original[2]})"
        )


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--semantic",
        action="store_true",
        help="Also run semantic/hybrid (needs the `semantic` extra).",
    )
    parser.add_argument(
        "--rrf-sweep",
        action="store_true",
        help="Grid-search lexical_weight/semantic_weight/rrf_k for hybrid_search "
        "(implies --semantic; needs the `semantic` extra and a successful embed).",
    )
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "relevance_bench.db"
        async with database(db_path) as db:
            await seed_corpus(db)
            await run_lexical(db)
            if args.semantic or args.rrf_sweep:
                print()
                embedded = await run_semantic_and_hybrid(db)
                if args.rrf_sweep and embedded:
                    print()
                    print("-- RRF sweep (hybrid, by NDCG@5) --")
                    await run_rrf_sweep(db)


if __name__ == "__main__":
    asyncio.run(main())
