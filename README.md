# SLB Glossary

A Python library and CLI for searching the [SLB Energy Glossary](https://glossary.slb.com/), in English and Spanish. It can search the live site directly, keep a local SQLite cache of terms you've already looked up (with lexical, semantic, and hybrid ranking), or do both and intelligently use whichever (local cache or live site) to return results. It also ships an [MCP server](#mcp-server) so an LLM agent can search the glossary directly.

This began as a hobby project to help with SPE PetroBowl prep (see [Credits](#credits)), so do not expect production polish. It does what it needs to do and tries to do that reliably.

> [!IMPORTANT]
> This package is intended for research or instructional use only. See [Attribution and disclaimer](#attribution-and-disclaimer).

This README is a quick tour, not a reference. For the full documentation, a complete CLI reference, the Python API walked through page by page, and the concepts behind search modes/sessions/the data model - see **[ti-oluwa.github.io/slb-glossary](https://ti-oluwa.github.io/slb-glossary/)**.

## Table of contents

- [SLB Glossary](#slb-glossary)
  - [Table of contents](#table-of-contents)
  - [Installation](#installation)
  - [Quick start](#quick-start)
    - [Library](#library)
    - [Command line](#command-line)
  - [How it fits together](#how-it-fits-together)
  - [The local database](#the-local-database)
  - [MCP server](#mcp-server)
  - [Command-line interface](#command-line-interface)
  - [Performance notes](#performance-notes)
  - [Examples](#examples)
  - [Development](#development)
  - [Contributing](#contributing)
  - [Attribution and disclaimer](#attribution-and-disclaimer)
  - [Credits](#credits)

## Installation

```bash
uv add slb-glossary          # or: pip install slb-glossary
patchright install chromium  # one-time browser install
```

Optional extras, installed as needed: `xlsx` (`.xlsx` import/export), `config` (TOML/YAML config files), `tui` (`--tui` interactive mode), `mcp` (the MCP server), `semantic` (semantic/hybrid local search). `uv add "slb-glossary[all]"` gets everything. See [Installation](https://ti-oluwa.github.io/slb-glossary/getting-started/installation/) in the docs for the full extras table and CLI-tool install methods (`uv tool install`, `uvx`, `pipx`, the one-line installer script).

To use it as a CLI tool without adding it to a project:

```bash
uv tool install "slb-glossary[all]"   # or: uvx slb-glossary search porosity
slb-glossary install chromium         # one-time browser install
```

`slb-glossary` and the shorter `slb` do the same thing.

## Quick start

### Library

```python
import asyncio
import slb_glossary as slb


async def main() -> None:
    async with slb.live.session() as session:
        async for result in slb.live.search(session, "porosity"):
            print(result.term, ":", result.definition)


asyncio.run(main())
```

Caching what you look up locally, then reading it back without a browser, is a few lines more:

```python
async def main() -> None:
    async with slb.local.database() as db, slb.live.session() as session:
        # Local first. Only opens a live page if the local DB has nothing.
        async for result in slb.search("water saturation", db=db, session=session, persist=True):
            print(result.term, ":", result.definition)

        # A repeat call for the same query is now served from `db` alone.
        async for result in slb.search("water saturation", db=db):
            print("(cached)", result.term)
```

### Command line

```bash
slb search porosity
slb terms Geophysics --limit 20
slb define "black oil" --local
slb random --topic Drilling
slb local search viscosity --topic Petrophysic --fuzzy --mode hybrid
```

See [Command-line interface](#command-line-interface) below, or [the CLI docs](https://ti-oluwa.github.io/slb-glossary/cli/) for the full reference.

## How it fits together

- **`slb_glossary.live`** talks only to the live site, via a Playwright/patchright-driven `Session` (`slb.live.session()`). Every function is an async generator, and nothing is fetched until you iterate it.
- **`slb_glossary.local`** is a SQLite (FTS5 + optional vector) cache of terms you've already looked up, so repeat lookups don't have to revisit the live site. See [The local database](#the-local-database).
- **`slb_glossary.query`** is the layer that picks between (or combines) the two, so you don't have to hand-roll "check local, fall back live, maybe cache what came back" yourself:

  ```python
  async with slb.local.database() as db, slb.live.session() as session:
      async for result in slb.query.search("water saturation", db=db, session=session, persist=True):
          print(result.term, ":", result.definition)
  ```

  A `Source` (`LOCAL`/`LIVE`/`AUTO`) controls which is used. `AUTO` (the default when both `db` and `session` are given) tries local first; for `search` specifically, it also checks *how good* the local match is (`relevance_threshold`) before deciding whether a live search is worth doing too. Every result comes back as a `QueryResult(value, source, persisted, score)`, so you can tell where it actually came from. See [Combined search with `slb_glossary.query`](https://ti-oluwa.github.io/slb-glossary/library/query/) for the full behavior, and [Sessions and the browser](https://ti-oluwa.github.io/slb-glossary/concepts/sessions/) for `open_session`'s full parameter list, retry policies, and lifecycle.
- **`SearchResult`** is the `typing.NamedTuple` every result comes back as, from any of the three layers above - same shape everywhere, so code written against one mostly works against another. See [The data model](https://ti-oluwa.github.io/slb-glossary/concepts/data-model/).

## The local database

`slb_glossary.local` opens with `database()` (an `async with` context manager), at the OS-appropriate user data directory by default, overridable with a path, `SLB_GLOSSARY_DATA_DIR`, or `Config.local.data_dir`:

```python
async with slb.local.database() as db:
    ...
```

**Filling it**, from a live `Session`, from cheapest to most expensive: `sync_topics` (just the topic list), `sync_query`/`sync_topic`/`sync_letter`, `sync_all` (the entire glossary). Pass `skip_existing=False` to force a refresh of terms already stored, e.g. to pick up a definition that changed live since the last sync (`slb-glossary sync --force` on the CLI).

**Querying it** mirrors `slb_glossary.live`'s own shapes (`search`, `get_terms_on`, `get_term`, `get_random_term`, `get_topics`, ...). `search` ranks lexically (bm25 full-text: SQLite FTS5 picks candidates, then each is scored so an actual term-name match always beats a word that just happens to appear in a definition) by default. Pass `mode="semantic"` or `"hybrid"` to rank by embedding similarity instead, or both fused by reciprocal rank fusion, but these need the `semantic` extra and `embed_terms(db)` run first:

```python
await slb.local.embed_terms(db)  # embeds new or changed terms; skips what's already up to date
matches = await slb.local.search(db, "rock that lets fluid through", mode="hybrid", scored=True)
```

`embed_terms`'s default (`only_missing=True`) tracks each embedding against a hash of what it was actually computed from, so it re-embeds a term whose content changed since it was last embedded. It does not just re-embed terms with no embedding at all. Also, a changed `embedding_model` invalidates everything at once. `delete_embeddings(db)` (`slb-glossary local delete-embeddings` on the CLI) clears stored embeddings without touching the terms themselves.

`load_file` imports a CSV/JSON/`.xlsx` file (`slb-glossary local import`); `flush`/`reset` clear stored terms and embeddings together, atomically (`reset` also forgets sync history). Topic filters accept `fuzzy=True` to tolerate misspellings against whatever topics are actually stored locally.

See [Local search and cache](https://ti-oluwa.github.io/slb-glossary/library/local-search/) and [Search modes](https://ti-oluwa.github.io/slb-glossary/concepts/search-modes/) for the full picture, including importing your own data, fuzzy topic matching in depth, and how lexical/semantic/hybrid ranking each actually work.

## MCP server

`slb_glossary.mcp` exposes the same search/lookup functions as [MCP](https://modelcontextprotocol.io) tools (built on [FastMCP](https://gofastmcp.com)), so an LLM agent can search the glossary directly. Requires the `mcp` extra.

```bash
slb mcp serve                            # stdio, read-only, local+live, no auth

slb mcp serve --transport http --port 8000 --allow-write --tools all
```

```python
from slb_glossary.mcp import MCPApp, MCPConfig

app = MCPApp(MCPConfig.default())

if __name__ == "__main__":
    app.run(transport="http", port=8000)
```

Local writes (the `glossary_sync` tool) are off by default. Use `local.allow_write=True` (or `--allow-write`) to turn them on. `MCPConfig` also covers auth (a FastMCP `AuthProvider`/`TokenVerifier`, or ready-made static API keys), rate limiting, and hooks around each call. See [Running an MCP server](https://ti-oluwa.github.io/slb-glossary/agent/mcp-server/) for configuring all of that, and [Building an agent with Pydantic AI](https://ti-oluwa.github.io/slb-glossary/agent/pydantic-ai/) for a worked example.

## Command-line interface

```bash
slb search porosity
slb terms Geophysics --limit 20
slb topics list
slb urls fetch "https://glossary.slb.com/en/terms/p/porosity"
```

Run `slb --help`, or `--help` after any subcommand, for the full set of options, or pass `--tui` to fill them in interactively instead (needs the `tui` extra).

| Command                    | Talks to             | What it does                                                                  |
| --------------------------- | --------------------- | ------------------------------------------------------------------------------ |
| `search`                    | Local, live, or auto  | Free-text search of the whole glossary. `--mode` for lexical/semantic/hybrid.  |
| `terms`                     | Local, live, or auto  | Every term filed under a topic.                                                |
| `topics list`               | Local, live, or auto  | List every topic with term counts.                                             |
| `urls list`                 | Local, live, or auto  | List term detail-page URLs matching a query/topic/letter.                      |
| `urls fetch`                | Live only              | Fetch every definition on one term detail-page URL.                            |
| `define`                    | Local, live, or auto  | Look up a single term's definition.                                            |
| `related`                   | Local, live, or auto  | List a term's "related terms" links.                                           |
| `compare`                   | Local, live, or auto  | Look up several terms side by side.                                            |
| `random`                    | Local, live, or auto  | Print one or more randomly chosen terms.                                       |
| `sync`                      | Live, then local       | Refresh the local database. `--force` to also re-fetch terms already stored.  |
| `local path`/`stats`        | Local only              | Resolved file paths / term counts, topic breakdown, last-sync info.           |
| `local search`              | Local only              | Full-text search the local database. `--fuzzy`, `--mode`, `--min-similarity`. |
| `local get`                 | Local only              | Look up a single term by exact name/URL, locally.                             |
| `local embed`               | Local only              | Compute/store embeddings for locally stored terms, for `--mode semantic`/`hybrid`. |
| `local delete-embeddings`   | Local only              | Clear stored embeddings without touching the terms themselves.                |
| `local import`/`export`     | Local only              | Import a CSV/JSON/XLSX file, or write local terms back out to one.            |
| `local flush`/`reset`       | Local only              | Delete every stored term and embedding; `reset` also forgets sync history.    |
| `config`                     | n/a                    | Interactive wizard for the config file.                                       |
| `install`                    | n/a                    | Install/list/remove/update the browser engines patchright launches.           |
| `mcp serve`                  | n/a                    | Run an MCP server for LLM agents. Requires the `mcp` extra.                   |

Every "Local, live, or auto" type command takes `--local`/`--live`/`--auto` (`--auto` is the default; local first, live as a fallback). Most also take `--annotate` (show each result's origin and score which is handy when `--auto` might be pulling from either source) and `--show-*`/`--hide-*` flags to toggle result columns. `--save PATH`/`--format`/`--json`/`--quiet` control output. See [Searching and defining terms](https://ti-oluwa.github.io/slb-glossary/cli/searching/), [Local cache and sync](https://ti-oluwa.github.io/slb-glossary/cli/sync/), and [Saving, output, and config files](https://ti-oluwa.github.io/slb-glossary/cli/configuration/) for the full picture, or [the CLI API reference](https://ti-oluwa.github.io/slb-glossary/api/cli/) for every flag on every command.

## Performance notes

Image/font/media requests are blocked by default, page data is read in single `evaluate`-style JS calls instead of one round-trip per element, search functions are lazy (`break`-ing out of `async for` stops the work), and a local-database read never launches a browser. Open one `Session` and reuse it for every live search instead of opening a new one per query as most of the cost is the one-time browser launch. Lean on the local database (`Source.AUTO`, the CLI's default `--auto`) so repeat lookups cost an SQLite read instead of a page load; `slb-glossary sync` lets you build the cache up ahead of time in one batch. See [Sessions and the browser](https://ti-oluwa.github.io/slb-glossary/concepts/sessions/) for concurrency/session-sharing details.

## Examples

The [`examples/`](examples/) directory has complete, runnable scripts: [`query.py`](examples/query.py) (local-first search with a live fallback, saving results, semantic search), [`app.py`](examples/app.py) (a complete MCP server), and [`agent.py`](examples/agent.py) (a Pydantic AI agent using the MCP server, both as a subprocess and in-process).

```bash
git clone https://github.com/ti-oluwa/slb-glossary.git && cd slb-glossary
uv sync --group examples --inexact
uv run python -m examples.query
```

## Development

This project uses [ruff](https://docs.astral.sh/ruff/) for linting and formatting:

```bash
uv run ruff check . --fix
uv run ruff format .
```

## Contributing

Contributions are welcome. Please fork the repository and submit a pull request.

## Attribution and disclaimer

All rights to the data and content on the SLB Energy Glossary website are owned by SLB. This project is not affiliated with or endorsed by SLB, and does not claim ownership of glossary entries or their text.

**Not for commercial use. This package is intended for educational, instructional, and research purposes only.**

Anything cached locally by `slb_glossary.local` (or the default config file's local-database settings) is still SLB's content. Enabling local storage means keeping a copy on your own machine, and you are solely responsible for that copy's retention, refresh, and deletion in compliance with SLB's terms of use.

Consult the original site and its terms of use for any reuse or redistribution of glossary content: <https://www.slb.com/en/terms-of-service>. See the `NOTICE` file for the full attribution notice, and `LICENSE` for this project's own code license.

## Credits

This project was inspired by the 2023/24/25 Petrobowl Team of the Federal University of Petroleum Resources, Effurun, Delta State, Nigeria. It aided the team's preparation for the PetroQuiz and PetroBowl competitions organized by the Society of Petroleum Engineers (SPE).
