# Troubleshooting and FAQ

## Is this affiliated with SLB?

No. `slb-glossary` is not affiliated with or endorsed by SLB. All rights to the data and content on the [SLB Energy Glossary](https://glossary.slb.com/) belong to SLB; see [SLB's terms of service](https://www.slb.com/en/terms-of-service) for the terms governing that content.

**This package is not for commercial use. It's intended for instructional and research purposes only.**

The optional local cache ([Local Search and Cache](library/local-search.md)) still holds SLB's data once you enable it, `slb-glossary` does not change who owns it. If you turn caching on, you are responsible for keeping that copy's retention, refresh, and deletion in compliance with SLB's terms linked above. `slb-glossary`'s own code is BSD-3-Clause licensed; that license covers the software, not the glossary content it fetches.

## Why is the first search slow, or the install step failing?

Two different one-time costs get mistaken for each other:

- **The browser build itself has to be downloaded once**, via `slb install chromium` (see [Installing the browser build](getting-started/installation.md#installing-the-browser-build)). If you skipped this, every command that touches the live site will fail, not just run slowly. `slb sync` (with no other flags) will tell you plainly if the browser is not installed, rather than failing partway through a search.
- **The very first search after that also launches the browser process for the first time**, which takes a few seconds longer than every search after it, since the process is already warm for the rest of that run (or the rest of that `session()` block, in library code).

If a search still hangs or times out after both of those, a slow or restrictive network is the next thing to check, raise `--timeout`/`session()`'s `timeout`, and see [Sessions and the Browser](concepts/sessions.md#retrying-a-flaky-first-load) for the retry settings that govern a flaky initial page load specifically.

## `slb config` says it needs the `tomlkit` package

```text
Error: Reading a .toml config requires the 'tomlkit' package. Install it with `pip install slb-glossary[config]`.
```

The global config file is TOML, and reading or writing TOML needs the `config` extra (`pip install "slb-glossary[config]"`). JSON works with no extra, so `slb config show --format json` runs on a base install. See [Saving, Output and Config Files](cli/configuration.md#the-config-command).

## Do I need the `semantic` extra?

Only for `--mode semantic`/`--mode hybrid` (CLI) or `mode="semantic"`/`"hybrid"` (library), and only on the local database. Live search has no semantic mode at all. Plain lexical search (the default everywhere) needs nothing beyond the base install. See [Search Modes](concepts/search-modes.md) for what the extra actually gets you, and [`local embed`](cli/sync.md#embedding-for-semantichybrid-search)/`slb_glossary.local.embed_terms` for the one-time step semantic/hybrid search also needs beyond just installing the extra.

## Why does `search` sometimes return more results than my `--limit`?

`--limit`/`limit=` bounds how many *terms* are looked up, not how many definitions come back. The same term can carry a different definition under each topic it's filed under, so one matched term can still produce several rows. See [`search`](cli/searching.md#search) or [Live Search](library/live-search.md#searching).

## Can I use a browser other than Chromium?

Yes, `--browser-type firefox`/`webkit` (CLI) or `browser_type="firefox"`/`"webkit"` (library). Chromium is the default and the one this documentation's examples assume. Firefox and WebKit sessions work, but the stealth patches are tuned for Chromium and haven't been checked against the site's bot detection, so Chromium is the safest choice. See [Sessions and the Browser](concepts/sessions.md#why-patchright-not-plain-playwright).

## `BrowserError: Failed to launch the glossary browser session`

This wraps whatever Playwright/patchright actually failed on; the detail is usually further down in the traceback. The two common causes:

- **The browser build isn't installed.** Playwright's own error looks like `Executable doesn't exist at .../chrome-linux/chrome`. Run `slb install chromium` (see [Installing the browser build](getting-started/installation.md#installing-the-browser-build)), then `slb install --list` to confirm it's actually there.
- **A bad `executable_path`/`launch_kwargs` override.** If you've pointed `open_session(executable_path=...)` or `--executable-path` at a specific binary, confirm that path exists and is actually executable.

## `SessionNotInitializedError`

```text
Session is not initialized and `auto_initialize=False`.
```

You passed `auto_initialize=False` to a search function, on a `Session` that hasn't loaded its topics/size yet. Sessions are lazy by default and don't load them when they open, so you have three ways out: call `await session.initialize()` first, open the session with `initialize=True`, or leave `auto_initialize` at its default (`True`) so the call initializes the session itself.

## `NetworkError: Could not reach the glossary at ...`

Raised when `session.initialize()` (or a lazy `auto_initialize=True` call) can't load the glossary's homepage at all. That means a real connectivity problem, a very slow network, or the site being down, not a bug in a specific search. Check the URL is reachable in a normal browser, then raise `timeout`/`--timeout` and see [Retrying a flaky first load](concepts/sessions.md#retrying-a-flaky-first-load).

## `EmbeddingError` when using `--mode semantic`/`hybrid`

Two different messages, two different fixes:

- ``Semantic search needs the `model2vec` package...``: install the extra with `pip install slb-glossary[semantic]`.
- **`Embedding model '...' produces N-dimensional vectors, but constants.embedding_dim is M`**: you've changed `SLB_GLOSSARY_EMBEDDING_MODEL` to a model with a different output size without also updating `SLB_GLOSSARY_EMBEDDING_DIM`. Set them consistently, or leave both at their defaults.

Either way, this is a local-database-only error. `--mode semantic`/`hybrid` doesn't exist for live search at all, so hitting this means you're already on the right path, just missing a step. See [Do I need the semantic extra?](#do-i-need-the-semantic-extra) above.

## `DatabaseError` about `sqlite-vec` or FTS5

- ``Semantic search needs the `sqlite-vec` package...``: same fix as the `model2vec` case above, `pip install slb-glossary[semantic]`.
- ``Could not load the `sqlite-vec` SQLite extension...``: the `sqlite-vec` package is installed, but your Python's `sqlite3` can't load extensions. Some builds, including some macOS ones, are compiled that way. This is a packaging issue in the Python you're running, not something `slb-glossary` can work around. You can check yours with `python -c "import sqlite3; sqlite3.connect(':memory:').enable_load_extension(True)"`, which raises an `AttributeError` when it's unsupported. Use a Python built with loadable SQLite extensions to get `--mode semantic` and `--mode hybrid`. The default `--mode lexical` needs no extension and works either way.
- **`The installed SQLite build has no FTS5 extension...`**: `slb_glossary.local`'s ordinary lexical search needs FTS5, which is on by default in nearly every modern SQLite build. If you're seeing this, you're likely on a custom-built Python; rebuilding against a stock SQLite (or using a standard python.org/Homebrew/apt build) resolves it.

## My command hangs, or fails with `PagePoolTimeoutError`

```text
PagePoolTimeoutError: Timed out after 60s waiting for a free page (6/6 pages in use: ..., about:blank, about:blank. 2 still on about:blank ...)
```

Every page in the session's pool is taken and none is coming back. This happens when more work runs at once than `max_pages` allows (high `--concurrency`, or several lookups overlapping on one session), so each holds some pages while waiting for more. Pages still on `about:blank` were opened but never used.

Lower `--concurrency`, or raise `--max-pages` (the CLI raises it for you when you pass `--concurrency`, to `concurrency + 2`). A call that asks for more workers than the pool can supply uses fewer and logs a warning. If the work is simply slow and you'd rather wait, raise `--page-acquire-timeout` (milliseconds, `0` waits forever). See [Sessions and the Browser](concepts/sessions.md#the-page-pool-how-concurrency-actually-works).

## `QueryError: needs at least one of db or session`

You called a `slb_glossary.query` function (`search`, `get_term`, etc.) with neither `db` nor `session`. At least one is required so there's something to actually query. Pass a `Database` (for `source=Source.LOCAL`/`AUTO`), a `Session` (for `source=Source.LIVE`/`AUTO`), or both.

A related one: **`source=Source.LOCAL requires db`**/**`source=Source.LIVE requires session`**. You asked for a specific source but didn't pass what it needs. `source=Source.AUTO` (the default) picks whichever of `db`/`session` you gave it, so this only comes up when you've pinned the source explicitly.

## `QueryError` about a session's language not matching

```text
Requested language 'es' does not match this session's own language 'en'.
```

A `Session` is opened for one language edition (`Language.ENGLISH` by default) and stays that way for its whole lifetime, so you can't search a different language through it mid-session. Open a second `Session` with `language="es"` instead (see [Sessions and the Browser](concepts/sessions.md)), or let a [`Runtime`](library/runtime.md) manage one per language.

## `ParsingError`

```text
... did not contain the markup a parser expected.
```

This means the glossary site's HTML structure no longer matches what `slb_glossary`'s parsers look for. Most likely the site changed something, not a one-off fluke. It's worth an issue report with the term/URL that triggered it. In the meantime, `--mode lexical`/`local search` against whatever's already cached still works fine; this only affects fetching new pages live.

## How do I share browser sessions across requests in my app?

Don't open a `Session` per request, since each one is a whole browser. Use `slb_glossary.Runtime`, which pools sessions per language and shares the local database. See [Managing sessions in your app](library/runtime.md).

## `RuntimeClosedError`, `ResourceDisabledError` or `UnknownLanguageError` from a `Runtime`

These all come from `slb_glossary.Runtime` and subclass `ResourceError` (import them from `slb_glossary.errors`). `RuntimeClosedError` means you used a runtime after `close()`. `ResourceDisabledError` means you asked for something it was configured not to provide, for example live access on a runtime built with `live_enabled=False`. `UnknownLanguageError` means the `language` isn't `en` or `es`. The MCP server reports them to clients as `MCPError`.

## The MCP server won't start: `needs the 'mcp' extra`

`slb mcp serve` (and anything under `slb_glossary.mcp`) needs `pip install slb-glossary[mcp]`. This is a separate extra from `semantic`; you don't need one to use the other.

## Am I going to get rate-limited or blocked?

Nothing in `slb_glossary` throttles your own requests for you, that's on you. (The MCP server can rate-limit the agents calling *it*, see [Connecting an AI agent](agent/mcp-server.md).) Keep concurrency modest (see [Sessions and the Browser](concepts/sessions.md) on `max_pages`), avoid tight retry loops on failure, and prefer the local cache (`--cache`, `sync`, `local import`) over repeated live lookups of the same terms. Hammering the site is the fastest way to get treated as a bot regardless of patchright's stealth patches, which reduce automation *detection*, not request *volume*.

## Something else is wrong

Check `slb --version` and `python -c "import slb_glossary; print(slb_glossary.__version__)"` are the version you expect, then `slb install --list` to confirm the browser build is actually present. If neither explains it, `--log-level debug --log-to some-file.log` (or the matching `log_level`/`LogSink` in library code) is the fastest way to see what actually happened during a run before reporting an issue.
