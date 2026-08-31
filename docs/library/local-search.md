# Local Search and Cache

This page covers `slb_glossary.local`: a SQLite database on your own disk that answers instantly and needs no network or browser once something is in it.

---

## Opening a database

```python
import asyncio

import slb_glossary as slb


async def main() -> None:
    async with slb.local.database("glossary.db") as db:
        results = await slb.local.search(db, "porosity")
        for result in results:
            print(result.term, ":", result.definition)


asyncio.run(main())
```

Pass no path (`slb.local.database()`) and it opens the same default-location database the CLI uses, resolved via [`platformdirs`](https://github.com/tox-dev/platformdirs) (`slb local path` prints exactly where that is). Passing an explicit path, as above, is how you keep a script's own database separate from your everyday CLI usage.

Unlike `slb_glossary.live`, `slb_glossary.local.search` isn't an async generator: it returns a plain `list[SearchResult]`, since there's no network round trip to stream results out of incrementally.

---

## Filling it up

An empty database answers nothing. Get local search results in three ways:

### 1. Cache live results as you go

```python
async with slb.live.session() as session, slb.local.database("glossary.db") as db:
    results = [r async for r in slb.live.search(session, "porosity")]
    await slb.local.upsert_results(db, results)
```

`upsert_results` keys each row by `(url, topic)`, since one page can carry more than one definition (one per topic). A result with no `url` is skipped outright, since there'd be nothing stable to upsert it against.

For a stream you want written incrementally, rather than only once the whole thing has been consumed, wrap it in `upsert_results_incrementally` instead:

```python
async for result in slb.local.upsert_results_incrementally(
    db, slb.live.search(session, "flooding", limit=None), batch_size=20
):
    print("cached:", result.term)
```

This writes every `batch_size` results as they arrive rather than buffering the entire stream in memory until the end, so a run that dies partway through (a browser crash, a dropped connection) still keeps whatever was already fetched, instead of losing all of it. This is exactly what the CLI's `--cache`/`--cache-batch-size` flags do underneath.

### 2. `slb_glossary.query`'s `persist=True`

Covered in full on [Combined Search with slb_glossary.query](query.md#persist-caching-live-results-as-you-go); the short version is that `slb.search(query, db=db, session=session, persist=True)` does the fetch-then-`upsert_results_incrementally` dance above for you.

### 3. Import your own data

```python
written = await slb.local.load_file(
    db, "terms.json", term_field="term", definition_field="definition",
    source="internal-wordlist",
)
print(f"Imported {written} row(s)")
```

This is the library counterpart of `slb local import`, useful for seeding the database from an internal wordlist or a dataset that never touched the live glossary at all. A row's own URL (or one synthesized from its term) and topic together are the local database's primary key, so importing the same file twice updates existing rows rather than duplicating them. See [`local import`](../cli/sync.md#importing-your-own-data) for the full set of `*_field` options, all of which are keyword arguments here too.

---

## Search modes: lexical, semantic, hybrid

```python
results = await slb.local.search(db, "reservoir rock", mode="lexical")   # the default
```

`mode="lexical"` (bm25 full-text ranking) is the default and needs nothing beyond the base install. `"semantic"` (embedding similarity) and `"hybrid"` (both, fused) need the `semantic` extra installed, and terms already embedded:

```python
await slb.local.embed_terms(db)   # compute and store embeddings for everything cached so far

results = await slb.local.search(db, "rock that holds fluid", mode="hybrid")
```

That embedding step is why a paraphrase like *"rock that holds fluid"* can match *"porosity"* under `"semantic"`/`"hybrid"` mode, but not under `"lexical"`, which only ever matches on the words actually present. See [Search Modes](../concepts/search-modes.md) for how the three modes actually differ under the hood, and what `embed_terms` costs to run.

### Getting scores alongside results

```python
scored_results = await slb.local.search(db, "porosity", scored=True)
for result, score in scored_results:
    print(result.term, round(score, 3))
```

---

## Checking what's stored

```python
total = await slb.local.count(db)
topics = await slb.local.get_topics(db)
print(total, "terms across", len(topics), "topics")
```

```python
terms = await slb.local.get_terms_on(db, "Drilling Fluids", limit=10)
```

`get_terms_on` mirrors `slb_glossary.live.get_terms_on`'s shape, but reads only what's already local, exactly like every other `slb_glossary.local` function: no live fallback here, ever, regardless of what's cached or not. That fallback behavior is what `slb_glossary.query` adds. See [Combined Search with slb_glossary.query](query.md).
