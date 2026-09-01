# Combined Search with slb_glossary.query

This page covers `slb_glossary.query`, the module that reads from [Local Search and Cache](local-search.md) and [Live Search](live-search.md) together, so you don't have to write that combining logic yourself.

Every function on this page (and the top-level `slb_glossary` package, which re-exports all of them) shares the same core keyword arguments, described once here rather than repeated on every function.

---

## `source`: which of local or live actually gets used

```python
from slb_glossary import Source

async with slb.local.database("glossary.db") as db, slb.live.session() as session:
    async for lookup in slb.search("porosity", db=db, session=session, source=Source.AUTO):
        ...
```

- **`Source.LOCAL`** never touches the network. Requires `db`.
- **`Source.LIVE`** never touches the local database. Requires `session`.
- **`Source.AUTO`** (the default) tries local first, falling back to live only when needed. What "needed" means differs slightly by function:
    - For `search`, the local database's best-scoring result is checked against `relevance_threshold` (`0.0`–`1.0`, default from `constants.relevance_threshold`). If it clears that bar, only local results are yielded; otherwise, live results are yielded first, with local results filling in any remaining slots.
    - For single-value lookups (`get_term`, `compare`, `related_terms`, `get_random_term`), it's simpler: use the cached copy if it exists, otherwise fetch live.

You only need to pass whichever of `db`/`session` the resolved `source` actually requires; passing both and leaving `source` at its `AUTO` default is the normal way to get "fast when possible, correct when not" without thinking about it further.

---

## `persist`: caching live results as you go

```python
async for lookup in slb.search("water saturation", db=db, session=session, persist=True):
    print(lookup.source, lookup.value.term)
```

`persist=True` writes any result that came from a live fetch back into `db`, so the next call for the same term doesn't need the network at all. This is exactly the mechanism [Local Search and Cache](local-search.md#1-cache-live-results-as-you-go)'s `upsert_results_incrementally` implements, wired up automatically. A local-only result (`source=Source.LOCAL`, or an `AUTO` call that never fell through to live) is never re-persisted, since it's already there.

`persist` defaults to `False` when not passed explicitly, from `constants.persist_by_default`: a write to your local database is a side effect worth opting into deliberately, not one a library call should do silently. Set `SLB_GLOSSARY_PERSIST_BY_DEFAULT=true` (or `constants.persist_by_default = True` directly) to flip that default for every call site in a process that doesn't pass `persist=` itself, rather than adding it to every call individually.

---

## `QueryResult`: knowing where an answer came from

Every function here wraps its answer in a `QueryResult`, rather than handing back a bare `SearchResult`:

```python
lookup = await slb.get_term("porosity", db=db, session=session)
print(lookup.value)  # the SearchResult itself (or None if not found)
print(lookup.source)  # Source.LOCAL or Source.LIVE - which one actually answered
print(lookup.persisted)  # whether this result was just written to `db`
```

For a streamed lookup (`search`, `get_terms_on`), `persisted` reflects whether persistence was *requested* for the call as a whole, not a per-item write confirmation.

---

## `search`: the one you'll reach for most

```python
async for lookup in slb.search("water saturation", db=db, session=session, persist=True):
    print(lookup.source, ":", lookup.value.term, "-", lookup.value.definition)
```

Everything from [`local.search`](local-search.md#search-modes-lexical-semantic-hybrid)'s `mode` parameter applies here too, with one restriction: a live fallback can't be scored with `mode="hybrid"`, since hybrid scoring needs a whole result set's ranks computed up front, and live results stream in one page at a time. Use `"lexical"` or `"semantic"` if a call might fall through to live.

```python
async for lookup in slb.search(
    "reservoir rock",
    db=db,
    session=session,
    mode="hybrid",
    relevance_threshold=0.7,
):
    ...  # trust local results less readily; augment with live more often
```

---

## The rest of the module, at a glance

Each of these is a thinner, more specific tool than `search`, sharing the same `db`/`session`/`source`/`persist` arguments described above:

```python
lookup = await slb.get_term("black oil", db=db, session=session)  # one exact term
results = await slb.compare(
    ["water flooding", "gas flooding"], db=db, session=session
)  # several, concurrently
related = await slb.related_terms(
    "water saturation", db=db, session=session
)  # just the related-term links
async for lookup in slb.get_terms_on(
    "Drilling Fluids", db=db, session=session
):  # every term under a topic
    ...
term = await slb.get_random_term(db=db, session=session)
topics = await slb.get_topics(db=db, session=session)
```

`compare` looks up its terms concurrently (`concurrency`, default from `constants.compare_concurrency`), each through its own `get_term` call, so an error partway through still leaves the terms looked up before it with their results intact:

```python
results = await slb.compare(
    ["shale", "sandstone", "limestone", "dolomite"],
    db=db,
    session=session,
    concurrency=4,
)
for name, lookup in results.items():
    print(name, "->", lookup.value.term if lookup.value else "not found")
```

`related_terms` is a thin convenience wrapper: it calls `get_term` and returns just the `.related` field, rather than something you'd need to write yourself on top of `get_term`.

## Getting similar results alongside an exact match

`get_term` and `compare` both accept `with_similar=True`, resolving to a `SimilarResult` instead of a bare `SearchResult | None`: an exact match (if any), plus up to `max_similar_terms` alternatives found along the way, best match first.

```python
lookup = await slb.get_term("porocity", db=db, session=session, with_similar=True)  # a typo
result = lookup.value  # a SimilarResult, not a SearchResult

if result.exact is not None:
    print("Found:", result.exact.value.term)
elif result.similar:
    print("Not found. Did you mean:", result.similar[0].value.term, "?")
    for alt in result.similar:
        print(f"  {alt.value.term} (score={alt.score:.2f})")
```

This is the "did you mean" building block: `result.exact` is `None` exactly when a plain call would have returned `None` too, and `result.similar` is populated the same way either way, so you don't need a separate lookup to get alternatives only when the exact match fails.

```python
results = await slb.compare(["porocity", "permeabilty"], db=db, session=session, with_similar=True)
for term, lookup in results.items():
    similar_result = lookup.value
    if similar_result.exact is None and similar_result.similar:
        print(f"{term}: not found, closest match is {similar_result.similar[0].value.term}")
```

`similar_pool_size` (how many candidates are pulled while looking for the exact match and to draw alternatives from) and `max_similar_terms` (how many alternatives are actually returned) both default to `constants.similar_terms_pool_size`/`constants.max_similar_terms`, and accept a per-call override:

```python
lookup = await slb.get_term(
    "porocity",
    db=db,
    session=session,
    with_similar=True,
    similar_pool_size=10,
    max_similar_terms=5,
)
```

See [The Data Model](../concepts/data-model.md#similarresult) for `SimilarResult`'s full field list, and [`local.get_term`](local-search.md#with_similar-nearby-alternatives-on-a-miss) for the local-only equivalent, which returns a plain tuple rather than a `SimilarResult`.

---

## Where to go from here

For the config file / `Config` object that lets you set `db`/`session` defaults (browser type, database path, timeouts) once instead of constructing them by hand every time, see [Saving Results and Config Objects](configuration.md). For the full data model behind every `SearchResult` this module hands back, see [The Data Model](../concepts/data-model.md).
