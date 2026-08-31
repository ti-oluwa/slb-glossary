# Live Search

This page covers `slb_glossary.live`: the module that actually talks to the glossary website.

---

## Opening a session

Everything in `slb_glossary.live` needs an open `Session`, obtained through `session()`:

```python
import asyncio

import slb_glossary as slb


async def main() -> None:
    async with slb.live.session() as session:
        async for result in slb.live.search(session, "porosity"):
            print(result.term, ":", result.definition)


asyncio.run(main())
```

`async with` guarantees the session (and the browser page it holds open) is closed even if `search` raises partway through, so you never leave a browser process running past the end of your script.

### Choosing what the session does

`session()` takes a long list of keyword-only arguments; the ones worth knowing about early:

```python
async with slb.live.session(
    language="es",              # search the Spanish edition instead of English
    browser_type="firefox",     # "chromium" (default), "firefox", or "webkit"
    headless=False,             # show the browser window, for debugging
    timeout=90_000,             # milliseconds to wait for page loads/elements
) as session:
    ...
```

A few more that matter once you're running this somewhere other than your own laptop:

```python
async with slb.live.session(
    proxy={"server": "http://myproxy:3128"},
    executable_path="/opt/chrome/chrome",   # use a specific browser build
    viewport={"width": 1920, "height": 1080},
) as session:
    ...
```

!!! info "Why headless matters for `use_stealth`"
    `session()` applies stealth patches (via `playwright-stealth`, on top of the [patchright](../concepts/sessions.md) engine underneath) automatically when `headless=True`, and skips them when `headless=False`. This isn't arbitrary: stealth patches have been observed to make the glossary *harder* to scrape reliably in headed mode, not easier. You can override this either way with `use_stealth=True`/`False`, but the default is deliberately conditional on `headless` rather than always-on.

### Lazy initialization

Opening a session doesn't, by itself, load anything from the glossary; the first call that actually needs the topic list (like `search`) triggers that automatically. If you'd rather control exactly when that first network round trip happens — say, to measure it separately, or to fail fast before doing anything else — open the session without initializing it, and call it explicitly:

```python
async with slb.live.session(initialize=False) as session:
    await session.initialize()   # do this now, explicitly, on your own terms
    async for result in slb.live.search(session, "porosity"):
        ...
```

Passing `auto_initialize=False` to `search` (or any other `slb_glossary.live` function) instead raises `SessionNotInitializedError` if the session hasn't been initialized yet, rather than silently initializing it on your behalf. Reach for this where an unexpected network call at that point in your code would be surprising.

---

## Searching

```python
async for result in slb.live.search(session, "porosity"):
    print(result.term)
```

A few defaults worth knowing, since they're easy to trip over:

- **`limit` defaults to `3`.** `slb.live.search` only looks up the first 3 matching *terms* by default, not the whole result set. Pass `limit=None` for everything that matches, or a higher number for more than 3:

    ```python
    async for result in slb.live.search(session, "flooding", limit=10):
        ...
    ```

- **A matched term can yield more than one result.** The same term can be filed under more than one topic, with a different definition each time, so the number of `SearchResult`s you get back isn't capped at `limit`; `limit` bounds how many terms are looked up, not how many definitions come back for them.

- **Results arrive in relevance order only if `concurrency=1` (the default).** Raise `concurrency` to fetch multiple term pages in parallel and finish faster, at the cost of results no longer necessarily arriving best-match-first:

    ```python
    async for result in slb.live.search(session, "flooding", limit=10, concurrency=4):
        ...  # faster, but not guaranteed most-relevant-first anymore
    ```

```python
async for result in slb.live.search(
    session, "drilling fluid", topic="Drilling Fluids", start_letter="d"
):
    ...
```

---

## Reading a result

Every function in `slb_glossary.live` (and everywhere else in this library) hands you a `SearchResult`: a plain `NamedTuple`, so you can unpack it positionally or read fields by name.

```python
async for result in slb.live.search(session, "porosity"):
    print(result.term)              # by name
    term, definition, *_ = result   # or positionally, ignoring the rest
```

See [The Data Model](../concepts/data-model.md#searchresult) for the full field list, including `related` (a tuple of `RelatedTerm`s) and the two-language support (`language`).

---

## Where to go from here

Every live search here re-visits the site: nothing is remembered between runs. For a local cache that makes repeat lookups instant and offline-capable, see [Local Search and Cache](local-search.md). For a function that reads the cache first and only falls back to exactly what's on this page when needed, see [Combined Search with slb_glossary.query](query.md).
