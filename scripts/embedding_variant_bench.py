#!/usr/bin/env python
"""
Compare candidate `build_embed_text` representations against the semantic-only benchmark.

Needs the `semantic` extra and network access to download the embedding
model on first run.

```bash
python scripts/embedding_variant_bench.py
```
"""

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from slb_glossary.local.connection import database
from slb_glossary.local.types import Database
from tests.relevance.harness import evaluate, seed_corpus


def variant_baseline(term: str, definition: str | None, topic: str | None) -> str:
    """Current production `build_embed_text`: `"term. definition. topic"`."""
    parts = [part for part in (term, definition, topic) if part]
    return ". ".join(parts)


def variant_term_emphasized(term: str, definition: str | None, topic: str | None) -> str:
    """Term repeated, to weight it more heavily in the pooled embedding."""
    parts = [part for part in (definition, topic) if part]
    return f"{term}. {term}: " + ". ".join(parts) if parts else term


def variant_no_topic(term: str, definition: str | None, topic: str | None) -> str:
    """Drop `topic` (shared across many unrelated terms, may dilute distinctiveness)."""
    parts = [part for part in (term, definition) if part]
    return ". ".join(parts)


def variant_definition_only(term: str, definition: str | None, topic: str | None) -> str:
    """Drop `term` entirely - isolates how much the term name itself matters."""
    parts = [part for part in (definition, topic) if part]
    return ". ".join(parts) if parts else term


def variant_term_only(term: str, definition: str | None, topic: str | None) -> str:
    """Drop `definition`/`topic` entirely - isolates the definition's marginal contribution."""
    return term


VARIANTS = {
    "baseline": variant_baseline,
    "term_emphasized": variant_term_emphasized,
    "no_topic": variant_no_topic,
    "definition_only": variant_definition_only,
    "term_only": variant_term_only,
}


async def embed_with_variant(db: Database, build_text) -> None:
    """Embed every term in `db` using `build_text` instead of the production `build_embed_text`."""
    from slb_glossary.local import vector as vector_module

    original = vector_module.build_embed_text
    vector_module.build_embed_text = build_text
    try:
        await vector_module.embed_terms(db, only_missing=False)
    finally:
        vector_module.build_embed_text = original


async def main() -> None:
    from slb_glossary.local.vector import vector_search

    summaries = []
    with tempfile.TemporaryDirectory() as tmp_dir:
        for name, build_text in VARIANTS.items():
            db_path = Path(tmp_dir) / f"{name}.db"
            async with database(db_path) as db:
                await seed_corpus(db)
                try:
                    await embed_with_variant(db, build_text)
                except Exception as exc:
                    print(f"Skipping all variants: could not embed ({exc!r}).")
                    print("Needs the `semantic` extra and network access to the embedding model.")
                    return

                report = await evaluate(db, vector_search, mode_label=name)
                print(report.format_table())
                print()
                summaries.append((name, report.overall))

    header = f"{'variant':<18}{'R@1':>8}{'R@3':>8}{'R@5':>8}{'MRR':>8}{'NDCG@5':>8}"
    print("-- summary (overall, semantic-only) --")
    print(header)
    print("-" * len(header))
    for name, m in summaries:
        print(
            f"{name:<18}{m.recall_at_1:>8.2f}{m.recall_at_3:>8.2f}"
            f"{m.recall_at_5:>8.2f}{m.mrr:>8.2f}{m.ndcg_at_5:>8.2f}"
        )


if __name__ == "__main__":
    asyncio.run(main())
