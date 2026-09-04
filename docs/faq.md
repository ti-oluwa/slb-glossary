# Troubleshooting and FAQ

## Is this affiliated with SLB?

No. `slb-glossary` is not affiliated with or endorsed by SLB. All rights to the data and content on the [SLB Energy Glossary](https://glossary.slb.com/) belong to SLB; see [SLB's terms of service](https://www.slb.com/en/terms-of-service) for the terms governing that content.

**This package is not for commercial use. It's intended for instructional and research purposes only.**

The optional local cache ([Local Search and Cache](library/local-search.md)) still holds SLB's data once you enable it, `slb-glossary` does not change who owns it. If you turn caching on, you're responsible for keeping that copy's retention, refresh, and deletion in compliance with SLB's terms linked above. `slb-glossary`'s own code is BSD-3-Clause licensed; that license covers the software, not the glossary content it fetches.

## Why is the first search slow, or the install step failing?

Two different one-time costs get mistaken for each other:

- **The browser build itself has to be downloaded once**, via `slb install chromium` (see [Installing the browser build](getting-started/installation.md#installing-the-browser-build)). If you skipped this, every command that touches the live site will fail, not just run slowly. `slb sync` (with no other flags) will tell you plainly if the browser is not installed, rather than failing partway through a search.
- **The very first search after that also launches the browser process for the first time**, which takes a few seconds longer than every search after it, since the process is already warm for the rest of that run (or the rest of that `session()` block, in library code).

If a search still hangs or times out after both of those, a slow or restrictive network is the next thing to check, raise `--timeout`/`session()`'s `timeout`, and see [Sessions and the Browser](concepts/sessions.md#retrying-a-flaky-first-load) for the retry settings that govern a flaky initial page load specifically.

## `slb config show` fails with a TOML error

```text
Error: Unable to convert an object of <class 'NoneType'> to a TOML item
```

This is a real issue in the current version: `config show`'s documented default format is TOML, but it can raise this error when a setting is unset (`None`), since TOML has no native null and the unset fields aren't stripped before serializing. `--format json` and `--format yaml` do not hit this:

```bash
slb config show --format json
```

`config init`/`config edit` aren't affected, since they write a config's actual (non-`None`) defaults rather than the full sparse effective config `show` assembles.

## Do I need the `semantic` extra?

Only for `--mode semantic`/`--mode hybrid` (CLI) or `mode="semantic"`/`"hybrid"` (library), and only on the local database, live search has no semantic mode at all. Plain lexical search (the default everywhere) needs nothing beyond the base install. See [Search Modes](concepts/search-modes.md) for what the extra actually gets you, and [`local embed`](cli/sync.md#embedding-for-semantichybrid-search)/`slb_glossary.local.embed_terms` for the one-time step semantic/hybrid search also needs beyond just installing the extra.

## Why does `search` sometimes return more results than my `--limit`?

`--limit`/`limit=` bounds how many *terms* are looked up, not how many definitions come back. The same term can carry a different definition under each topic it's filed under, so one matched term can still produce several rows. See [`search`](cli/searching.md#search) or [Live Search](library/live-search.md#searching).

## Can I use a browser other than Chromium?

Yes, `--browser-type firefox`/`webkit` (CLI) or `browser_type="firefox"`/`"webkit"` (library). Chromium is the default and the one this documentation's examples assume. Firefox/WebKit sessions will run just as fine either way. See [Sessions and the Browser](concepts/sessions.md#why-patchright-not-plain-playwright).

## Something else is wrong

Check `slb --version` and `python -c "import slb_glossary; print(slb_glossary.__version__)"` are the version you expect, then `slb install --list` to confirm the browser build is actually present. If neither explains it, `--log-level debug --log-to some-file.log` (or the matching `log_level`/`LogSink` in library code) is the fastest way to see what actually happened during a run before reporting an issue.
