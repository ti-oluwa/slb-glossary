# Using the Library

Audience: a Python developer, comfortable with `async`/`await` at least at a beginner level. If you haven't written `async def`/`await` code before, [Real Python's asyncio walkthrough](https://realpython.com/async-io-python/) is worth reading first; this section does not teach asyncio itself. If you haven't installed the library yet, see [Installation](../getting-started/installation.md).

`slb-glossary` is fully typed (it ships a `py.typed` marker), so everything below works with autocomplete and static type checking out of the box.

---

## Three layers, one package

Every capability in this library lives in one of three places, and they build on each other:

| Module | What it talks to | Speed | Needs |
|---|---|---|---|
| `slb_glossary.live` | The actual glossary website, through a browser | Slow: a real page load per call | Nothing extra; the browser build from [Installation](../getting-started/installation.md#installing-the-browser-build) |
| `slb_glossary.local` | A SQLite database on your own disk | Instant | Nothing extra for lexical search; the `semantic` extra for semantic/hybrid search |
| `slb_glossary.query` | Both of the above, combined | As fast as whichever one actually answers | Whichever of `live`/`local` the call in question needs |

You can reach for any one of these on its own. The next three pages cover them in that order, since it roughly mirrors how a real script's needs tend to grow: a plain live search first, a local cache once you're calling it more than once, then `slb_glossary.query` once you want the two combined automatically without writing that logic yourself.

## Every function returns the same two shapes

Regardless of which module you're calling, you'll only ever get one of two things back:

- **A `SearchResult`** (or `None`, if nothing was found), for a single-term lookup. `SearchResult` is a plain `NamedTuple` with fields for `term`, `definition`, `grammatical_label`, `topic`, `url`, `image`, `image_caption`, `related`, and `language`. See [The Data Model](../concepts/data-model.md#searchresult) for the full field list.
- **A stream of `SearchResult`s**, for anything that can reasonably return more than one: a search, every term under a topic, several terms compared at once.

`slb_glossary.query`'s functions wrap either shape in a `QueryResult`, adding provenance (`source`, `persisted`) alongside the value. See [Combined Search with slb_glossary.query](query.md).

## The whole surface, in one table

Beyond `slb_glossary.live.search` and `slb_glossary.local.search` themselves, `slb_glossary.query` (and the top-level `slb_glossary` package, which re-exports all of it) offers these:

| Function | What it's for |
|---|---|
| `search(query, ...)` | Ranked search, local-first with live fallback. |
| `get_term(term_or_url, ...)` | One exact term, by name or detail-page URL. |
| `compare(terms, ...)` | Several terms at once, concurrently, for side-by-side comparison. |
| `related_terms(term_or_url, ...)` | Just the related-term links from one term's definition. |
| `get_terms_on(topic, ...)` | Every term filed under one topic. |
| `get_random_term(...)` | One (or more) random terms. |
| `get_topics(...)` | The glossary's list of topics. |
| `get_terms_urls(...)` | Raw glossary URLs matching a query/topic, without fetching their content. |

Every one of these accepts the same handful of shared keyword arguments: `db`, `session`, `source` (`Source.LOCAL`/`LIVE`/`AUTO`), and `persist`. [Combined Search with slb_glossary.query](query.md) covers what each of those actually does, in depth, once — the individual functions' own docs mostly just point back to it.

## Where to go from here

<div class="grid cards" markdown>

- :material-web: **Live Search**

    ---

    `slb_glossary.live.session()` and `search()`: talking to the real glossary site.

    [Continue](live-search.md){ .md-button }

- :material-database: **Local Search and Cache**

    ---

    `slb_glossary.local.database()`, lexical/semantic/hybrid search, importing your own data.

    [Continue](local-search.md){ .md-button }

- :material-source-merge: **Combined Search**

    ---

    `slb_glossary.query`: local-first, live-fallback, and every convenience function built on it.

    [Continue](query.md){ .md-button }

</div>
