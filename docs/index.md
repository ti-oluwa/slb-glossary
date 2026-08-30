# slb-glossary

<div class="hero" markdown>

**slb-glossary** searches the [SLB Energy Glossary](https://glossary.slb.com/) from a terminal, from your own Python code, or from an AI agent, with an optional local cache so repeat lookups don't need the network at all.

[Installation](getting-started/installation.md){ .md-button .md-button--primary }
[Using the CLI](cli/index.md){ .md-button }

</div>

---

## What is this?

The [SLB Energy Glossary](https://glossary.slb.com/) is a large, well maintained reference of oilfield and energy terms, but it's a JavaScript web application, not a page you can just download and read. There's no public API for it either. `slb-glossary` is a small, focused tool built to search it anyway: it drives a real browser in the background to load the site the way a person would, then parses the definitions out of the page.

It is not affiliated with or endorsed by SLB. The glossary content it searches belongs to SLB; `slb-glossary` only searches and displays it. See the [FAQ](faq.md#is-this-affiliated-with-slb) for the full attribution.

## Three ways in

Everyone ends up at [Installation](getting-started/installation.md) first. After that, pick whichever of these matches what you're actually trying to do:

<div class="grid cards" markdown>

- :material-console: **Just the terminal**

    ---

    You want to type a term and see its definition. No code. Covered in [Using the CLI](cli/index.md).

- :material-language-python: **Your own Python code**

    ---

    A script, a notebook, a data pipeline. Covered in [Using the library](library/index.md).

- :material-robot: **An AI agent**

    ---

    You want Claude, or an agent you're building, to be able to look terms up itself. Covered in [Connecting an AI agent](agent/mcp-server.md).

</div>

## A quick look

Here's the CLI path, since it needs no code at all. After [installing](getting-started/installation.md):

```bash
slb search "water saturation"
```

This opens a background browser, searches the live glossary, and prints a table of matching definitions to your terminal. The first search after install is slower than the rest, since the background browser only has to start once. See [why](faq.md#why-is-the-first-search-slow) if that catches you off guard.

Here's the same lookup from Python, since it's about the same number of lines:

```python
import asyncio

import slb_glossary as slb


async def main() -> None:
    async with slb.live.session() as session:  # (1)!
        async for result in slb.live.search(session, "water saturation"):  # (2)!
            print(result.term, ":", result.definition)


asyncio.run(main())
```

1. Opens the background browser for the duration of this block, and closes it automatically on exit, even if `search` raises.
2. `slb.live.search` is an async generator. A search term can have more than one definition (one per topic it's filed under), so more than one `SearchResult` can come back for a single query.

Both do the same underlying work. The CLI is a thin wrapper over exactly this library.

## How it works

`slb-glossary` is built around three things, layered so each one is optional on its own:

- **`slb_glossary.live`** talks to the actual glossary website, through a Playwright-driven browser. This is the only way to reach terms `slb-glossary` doesn't already know about, and it's the slowest of the three, since it's a real network round trip through a real page load.
- **`slb_glossary.local`** keeps a SQLite database on your own disk. Once a term has been looked up once and saved there, reading it back is instant and needs no network or browser at all. It also supports [semantic search](concepts/search-modes.md) over whatever you've stored, matching a paraphrase instead of an exact word.
- **`slb_glossary.query`** sits on top of both. Its `search` function reads from the local database first, and only reaches for a live fetch if the local result isn't confident enough, optionally saving that live result back to the database as it goes (`persist=True`) so the next call for the same term doesn't need the network either.

You can use any one of these on its own, or all three together. The [library tutorial](library/index.md) walks through them in that order, since that's roughly the order a real script's needs tend to grow in: start with a plain live search, add a local cache once you're calling it more than once, then reach for `slb_glossary.query` once you want the two combined automatically.

!!! info "The CLI, and the MCP server, are both built on this same library"
    `slb search`, `slb define`, and the rest of the CLI commands are a thin layer over exactly the functions described above and covered in [Using the library](library/index.md). The MCP server described in [Connecting an AI agent](agent/mcp-server.md) is another thin layer over the same functions, exposed as tools an agent can call. Nothing in this project has a separate implementation per interface.

## Scope and limitations

`slb-glossary` is deliberately narrow: it searches one glossary, not a general web-scraping or knowledge-base tool. That scope comes with a few limits worth knowing up front:

- **It depends on the glossary's own page structure.** If SLB changes how the site is laid out, parsing can break until the library is updated to match.
- **A live search opens a real browser.** That's slower than an HTTP request to a JSON API, which is why the local cache and `persist=True` exist: to make the slow path something you only pay for once per term.
- **It reads two languages of the glossary**, English and Spanish, because that's what the site itself publishes. See `slb_glossary.Language` in the [API reference](api/library.md).

## Ready to go?

<div class="grid cards" markdown>

- :material-download: **Installation**

    ---

    Install the CLI, the library, or both, and the background browser they both depend on.

    [Installation](getting-started/installation.md){ .md-button }

- :material-console: **Using the CLI**

    ---

    Your first search, defining an exact term, browsing a topic, and working offline from a local cache.

    [Using the CLI](cli/index.md){ .md-button }

- :material-language-python: **Using the library**

    ---

    Live search, the local cache, and `slb_glossary.query` for the two combined.

    [Using the library](library/index.md){ .md-button }

</div>
