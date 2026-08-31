# Sessions and the Browser

Every part of this library that talks to the live glossary — the CLI, `slb_glossary.live`, the MCP server — ultimately does so through a `Session`: an open browser, loaded with the glossary's topic list and term count, ready to be searched. This page covers what that actually is and why it works the way it does, since a handful of design choices here explain behavior that shows up throughout [Live Search](../library/live-search.md) and the CLI.

---

## Why a browser at all?

The [SLB Energy Glossary](https://glossary.slb.com/) is a JavaScript single-page application: its content is rendered client-side, and there's no public API or static HTML to fetch and parse directly. `slb-glossary` opens a real (headless, by default) browser, navigates it to the glossary the way a person's browser would, and reads the rendered result. That's slower than an HTTP request to a JSON endpoint, and it's the reason this library reaches for a local cache ([Local Search and Cache](../library/local-search.md)) as heavily as it does: the browser round trip is the one genuinely expensive step in the whole system.

## Why patchright, not plain Playwright

The browser engine underneath is [patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python), a stealth-hardened fork of [Playwright](https://playwright.dev/), with [`playwright-stealth`](https://github.com/AtuboDad/playwright_stealth) patches layered on top when running headless. Both exist because a plain, unmodified headless browser is detectable as automation by a determined site, and getting reliably blocked defeats the whole point of a tool built to search a site regularly.

A few specifics worth knowing:

- **Stealth patches apply automatically when `headless=True`, and are skipped by default when `headless=False`.** They've been observed to make the glossary *harder* to scrape reliably in headed mode, not easier, so the library doesn't apply them there unless you explicitly ask (`use_stealth=True`).
- **The patches are tuned for Chromium specifically.** Firefox and WebKit sessions run through the same stealth initialization, but haven't been evaluated against the glossary's own bot detection the way Chromium has. Chromium is the default `browser_type` for this reason, and this documentation's examples assume it throughout.
- **`install`'s download machinery reuses Playwright's own environment variables** (`PLAYWRIGHT_DOWNLOAD_CONNECTION_TIMEOUT`, `PLAYWRIGHT_DOWNLOAD_HOST`), since patchright is a drop-in fork rather than an independent reimplementation. See [`install`](../cli/sync.md#install).

## What opening a session actually does

`session()`/`open_session()` launches the browser, opens a context (patchright's term for an isolated cookie/cache/storage sandbox, with the stealth patches applied to it), and then — unless you asked for [lazy initialization](../library/live-search.md#lazy-initialization) — loads the glossary's topic list and total term count once, storing them on the returned `Session` for the rest of its lifetime (`session.topics`, `session.size`). Everything after that reuses this one browser and context rather than launching a fresh one per search.

## The page pool: how concurrency actually works

A `Session` doesn't just hold one browser tab; it holds a small pool of them, bounded by `max_pages` (default `6`). Any operation that needs to actually load a URL — the search results page, each term's detail page — checks out a page from this pool for the duration of that one operation, then returns it. This is what makes a session safe to drive concurrently: `compare`'s `concurrency`, or `slb.live.search`'s `concurrency`, work by having several lookups in flight at once, each with its own checked-out page, rather than serializing everything through a single shared tab.

```python
async with slb.live.session(max_pages=10) as session:
    results = await slb.compare(
        ["shale", "sandstone", "limestone", "dolomite", "chalk"],
        session=session,
        concurrency=5,
    )
```

`max_pages` should comfortably cover whatever `concurrency` you actually run with, plus a little headroom for the session's own bookkeeping (the topic-list load, for instance). Raising `concurrency` without also raising `max_pages` just means concurrent operations increasingly queue for a free page rather than actually running in parallel.

## Retrying a flaky first load

Occasionally, the glossary's search widget renders with nothing in it on the very first load. `session()`'s `retry` parameter (a `RetryPolicy`) controls how that specific case is retried, how many attempts, and how the delay between them grows:

```python
from slb_glossary import RetryPolicy, BackoffType

async with slb.live.session(
    retry=RetryPolicy(attempts=5, base_delay=1000, backoff_type=BackoffType.EXPONENTIAL)
) as session:
    ...
```

This only governs that one initial-load retry, not every network call a session makes afterward; ordinary page timeouts are governed by `timeout` instead.

## RetryPolicy elsewhere in the library

`RetryPolicy` isn't specific to session startup; it's a general-purpose retry configuration used in a few other places too, and available for your own code as well:

- **`refresh_topics`** (the same facet-panel load that populates `session.topics`/`session.size`) reuses `session.retry` directly rather than taking a retry policy of its own — call it again later if the glossary's topic list may have changed mid-run, and it retries exactly like the initial load did.
- **`slb install`**'s browser download (`slb_glossary.cli.browsers`) retries a failed download per its own `RetryPolicy`, exposed as the CLI's `--retries`/`--timeout` flags rather than a policy object directly. See [`install`](../cli/sync.md#install).
- **`slb_glossary.retries.retry`** is the underlying retry loop everything above calls into, and it's public: wrap any zero-argument async callable of your own in it, independent of anything glossary-related.

```python
from slb_glossary.retries import retry, RetryPolicy


async def flaky_call() -> str: ...


result = await retry(flaky_call, policy=RetryPolicy(attempts=3, base_delay=500))
```

`retry` also accepts `until`, a callable checked against a successful result before deciding the call actually succeeded, e.g. `until=lambda r: r is not None`, for retrying a call that returns a falsy-but-not-erroring result you'd still like another attempt at.

## Where to go from here

For the functions built on top of a `Session`, see [Live Search](../library/live-search.md). For the full shape of what a search actually returns, see [The Data Model](data-model.md).
