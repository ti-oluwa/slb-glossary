#!/usr/bin/env python
"""
Run the relevance benchmark (`tests/relevance/`) and print before/after tables.

```bash
python scripts/relevance_bench.py            # lexical only (no extra deps)
python scripts/relevance_bench.py --semantic  # + semantic/hybrid (needs `semantic` extra
                                               #   and network access to download the
                                               #   embedding model on first run)
```

"Before" for lexical search is `_legacy_lexical_search` below: a frozen
copy of the two-tier (exact/prefix, then bm25) algorithm this task
started from, kept here specifically so this script can report a real
before/after comparison without needing git history at runtime.
"""

import argparse
import asyncio
import logging
import sys
import tempfile
import typing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from slb_glossary.constants import constants  # noqa: E402
from slb_glossary.local.connection import database  # noqa: E402
from slb_glossary.local.types import Database  # noqa: E402
from slb_glossary.phrasing import clean_query  # noqa: E402
from slb_glossary.types import SearchResult  # noqa: E402
from slb_glossary.utils import normalize_text  # noqa: E402
from tests.relevance.harness import evaluate, seed_corpus  # noqa: E402

logging.getLogger("slb_glossary").setLevel(logging.WARNING)


async def _legacy_lexical_search(
    db: Database, query: str, *, limit: int | None = 20, **_: typing.Any
) -> list[tuple[SearchResult, float]]:
    """
    Frozen copy of `slb_glossary.local.lexical.lexical_search` as it
    stood before this benchmark/tiering work: two tiers only
    (exact/prefix name match, computed in SQL, then plain bm25 for
    everything else), no contains/all-tokens/partial-token tiers, no
    fuzzy-typo fallback, no `AND`-then-`OR` retrieval fallback.
    """
    from slb_glossary.local.api import _row_to_result
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

    others_bm25 = [row["bm25_score"] for row in rows if not row["is_exact"] and not row["is_prefix"]]
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
            score = round(constants.content_match_score_cap * (worst - row["bm25_score"]) / spread, 4)
        scored.append((_row_to_result(row), score))

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
        print(f"  [{outcome.query.category}] {outcome.query.query!r} -> {outcome.ranked_terms[:5]!r}")


async def run_semantic_and_hybrid(db: Database) -> None:
    try:
        from slb_glossary.local.hybrid import hybrid_search
        from slb_glossary.local.vector import embed_terms, vector_search
    except ImportError:
        print("Skipping semantic/hybrid: `semantic` extra not installed "
              "(`pip install slb-glossary[semantic]`).")
        return

    try:
        await embed_terms(db, only_missing=False)
    except Exception as exc:  # noqa: BLE001
        print(f"Skipping semantic/hybrid: could not embed the corpus ({exc!r}).")
        print("This is expected in a network-restricted sandbox: embedding needs a "
              "one-time download of the model2vec model from Hugging Face. Run this "
              "script in an environment with that access for real semantic/hybrid numbers.")
        return

    semantic = await evaluate(db, vector_search, mode_label="semantic")
    hybrid = await evaluate(db, hybrid_search, mode_label="hybrid")
    print(semantic.format_table())
    print()
    print(hybrid.format_table())


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--semantic", action="store_true", help="Also run semantic/hybrid (needs the `semantic` extra)."
    )
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "relevance_bench.db"
        async with database(db_path) as db:
            await seed_corpus(db)
            await run_lexical(db)
            if args.semantic:
                print()
                await run_semantic_and_hybrid(db)


if __name__ == "__main__":
    asyncio.run(main())
