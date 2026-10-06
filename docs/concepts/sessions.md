# Sessions and the Browser

Every part of this library that talks to the live glossary (the CLI, `slb_glossary.live`, the MCP server) ultimately does so through a `Session`. It is an open browser, loaded with the glossary's topic list and term count, ready to be searched. This page covers what that actually is and why it works the way it does, since a handful of design choices here explain behavior that shows up throughout [Live Search](../library/live-search.md) and the CLI.

---

## Why a browser at all?

The [SLB Energy Glossary](https://glossary.slb.com/) is a JavaScript single-page application: its content is rendered client-side, and there's no public API or static HTML to fetch and parse directly. `slb-glossary` opens a real (headless, by default) browser, navigates it to the glossary the way a person's browser would, and reads the rendered result. That's slower than an HTTP request to a JSON endpoint, and it's the reason this library reaches for a local cache ([Local Search and Cache](../library/local-search.md)) as heavily as it does. The browser round trip is the one genuinely expensive step in the whole system.

## Why patchright, not plain Playwright

The browser engine underneath is [patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python), a stealth-hardened fork of [Playwright](https://playwright.dev/), with [`playwright-stealth`](https://github.com/AtuboDad/playwright_stealth) patches layered on top when running headless. Both exist because a plain, unmodified headless browser is detectable as automation by a determined site, and getting reliably blocked defeats the whole point of a tool built to search a site regularly.

A few specifics worth knowing:

- **Stealth patches apply automatically when `headless=True`, and are skipped by default when `headless=False`.** They've been observed to make the glossary *harder* to scrape reliably in headed mode, not easier, so the library does not apply them there unless you explicitly ask (`use_stealth=True`).
- **The patches are tuned for Chromium specifically.** Firefox and WebKit sessions run through the same stealth initialization, but have not been evaluated against the glossary's own bot detection the way Chromium has. Chromium is the default `browser_type` for this reason, and this documentation's examples assume it throughout.
- **`install`'s download machinery reuses Playwright's own environment variables** (`PLAYWRIGHT_DOWNLOAD_CONNECTION_TIMEOUT`, `PLAYWRIGHT_DOWNLOAD_HOST`), since patchright is a drop-in fork rather than an independent reimplementation. See [`install`](../cli/sync.md#install).

## What opening a session actually does

`session()`/`open_session()` launches the browser and opens a context (patchright's term for an isolated cookie/cache/storage sandbox, with the stealth patches applied to it). By default it stops there. This is [lazy initialization](../library/live-search.md#lazy-initialization): the first call that needs the glossary's topic list and total term count loads them, once, and stores them on the `Session` for the rest of its lifetime (`session.topics`, `session.size`). Pass `initialize=True` (or set `SLB_GLOSSARY_SESSION_AUTO_INITIALIZE=true`) to load them up front instead. Either way, everything after that reuses this one browser and context rather than launching a fresh one per search.

## The page pool: how concurrency actually works

A `Session` does not just hold one browser tab; it holds a small pool of them, bounded by `max_pages` (default `6`). Any operation that needs to actually load a URL (the search results page, each term's detail page) checks out a page from this pool for the duration of that one operation, then returns it. This is what makes a session safe to drive concurrently: `compare`'s `concurrency`, or `slb.live.search`'s `concurrency`, work by having several lookups in flight at once, each with its own checked-out page, rather than serializing everything through a single shared tab.

```python
async with slb.live.session(max_pages=10) as session:
    results = await slb.compare(
        ["shale", "sandstone", "limestone", "dolomite", "chalk"],
        session=session,
        concurrency=5,
    )
```

`max_pages` should comfortably cover whatever `concurrency` you actually run with, plus a page for the search that feeds the workers and one for the session's own base page (kept open after the topic-list load). Raising `concurrency` without also raising `max_pages` does not buy more parallelism. When a call asks for more workers than the pool can supply, it uses fewer and logs a warning saying so.

Workers open their page only when they have a URL to fetch, and give it back when they are done. That matters because a task that holds a page it isn't using while it waits for another can deadlock a full pool: several tasks each end up holding part of what they need.

### When the pool has no free page

A call that can't get a page waits for one to close. It won't wait forever: after `page_acquire_timeout` (milliseconds, default `60000`, `0` to wait forever) it raises `PagePoolTimeoutError`. The message says how many pages are in use, what each one is showing, and how many are still on `about:blank`. Blank pages were opened but never used, which is the sign of something holding pages while it waits for more.

```python
async with slb.live.session(max_pages=10, page_acquire_timeout=30_000) as session:
    ...
```

From the CLI it is `--page-acquire-timeout`, and in the config file `session.page_acquire_timeout`. If you hit the error, lower `concurrency`, raise `max_pages`, or, if the work is just slow, raise the timeout.

## Retrying a flaky first load

Occasionally, the glossary's search widget renders with nothing in it on the very first load. `session()`'s `retry` parameter (a `RetryPolicy`) controls how that specific case is retried, how many attempts, and how the delay between them grows:

```python
from slb_glossary import RetryPolicy, BackoffType

async with slb.live.session(
    retry=RetryPolicy(attempts=5, base_delay=1000, backoff_type=BackoffType.EXPONENTIAL)
) as session:
    ...
```

`RetryPolicy` delays are in **milliseconds** in Python, so `base_delay=1000` is one second. The CLI flags (`--retry-base-delay`) and the config file (`session.retry.base_delay`) take **seconds** instead.

## `RetryPolicy` elsewhere in the library

`RetryPolicy` is not specific to session startup; it's a general-purpose retry configuration used in a few other places too, and available for your own code as well:

- **`refresh_topics`** (the same facet-panel load that populates `session.topics`/`session.size`) reuses `session.retry` directly rather than taking a retry policy of its own, call it again later if the glossary's topic list may have changed mid-run, and it retries exactly like the initial load did.
- **`slb install`**'s browser download (`slb_glossary.cli.browsers`) retries a failed download per its own `RetryPolicy`, exposed as the CLI's `--retries`/`--timeout` flags rather than a policy object directly. See [`install`](../cli/sync.md#install).
- **`slb_glossary.retries.retry`** is the underlying retry loop everything above calls into, and it's public: wrap any zero-argument async callable of your own in it, independent of anything glossary-related.

```python
from slb_glossary.retries import retry, RetryPolicy


async def flaky_call() -> str: ...


result = await retry(flaky_call, policy=RetryPolicy(attempts=3, base_delay=500))
```

`retry` also accepts `until`, a callable checked against a successful result before deciding the call actually succeeded, e.g. `until=lambda r: r is not None`, for retrying a call that returns a falsy, but not erroring result you'd still like another attempt at.

## Sharing sessions across requests

A `Session` is a whole browser, so a service that handles many requests shouldn't open one per request. `slb_glossary.Runtime` manages that for you: it keeps one pool of sessions per language, shares them across concurrent calls, opens another browser only when the existing ones are busy (up to `max_sessions`), closes idle ones, and replaces any whose browser has crashed. It also owns the shared local database.

```python
async with slb.Runtime(max_sessions=2) as runtime:
    async with runtime.session("en") as session:
        ...
```

See [Managing sessions in your app](../library/runtime.md).

---

## Where to go from here

For the functions built on top of a `Session`, see [Live Search](../library/live-search.md). For the full shape of what a search actually returns, see [The Data Model](data-model.md).
