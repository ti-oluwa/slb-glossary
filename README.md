# SLB Energy Glossary

A small Python library and CLI for searching the [SLB Energy Glossary](https://glossary.slb.com/), in English and Spanish. It can search the live site directly, keep a local SQLite cache of terms you've already looked up, or do both and prefer whichever already has an answer.

This started as a hobby project to help with SPE PetroBowl prep (see [Credits](#credits)), so don't expect enterprise polish. It does what it needs to do and tries to do that reliably.

> [!IMPORTANT]
> This package is intended for research or instructional use only. See [Attribution and disclaimer](#attribution-and-disclaimer).

This README is a tutorial, not a full API reference. It introduces what each part of the library does and how the pieces fit together. A proper API reference doc isn't published yet, so for now the docstrings in the source are the most detailed source of truth on any given function's parameters and behavior.

## Table of contents

- [SLB Energy Glossary](#slb-energy-glossary)
  - [Table of contents](#table-of-contents)
  - [Installation](#installation)
    - [As a library](#as-a-library)
    - [As a CLI tool](#as-a-cli-tool)
  - [Quick start](#quick-start)
    - [Library](#library)
    - [Command line](#command-line)
  - [Core concepts](#core-concepts)
    - [`Session`: one session, many searches](#session-one-session-many-searches)
    - [Retries and backoff](#retries-and-backoff)
    - [`SearchResult`](#searchresult)
    - [Live search: `slb_glossary.live`](#live-search-slb_glossarylive)
  - [The local database: `slb_glossary.local`](#the-local-database-slb_glossarylocal)
    - [Filling the local database](#filling-the-local-database)
    - [Querying the local database](#querying-the-local-database)
    - [Fuzzy topic matching](#fuzzy-topic-matching)
    - [Importing your own data](#importing-your-own-data)
    - [Bring-your-own-embedding vector search](#bring-your-own-embedding-vector-search)
  - [Source-aware queries: `slb_glossary.query`](#source-aware-queries-slb_glossaryquery)
  - [Configuration: `slb_glossary.config`](#configuration-slb_glossaryconfig)
  - [Saving results to a file: `slb_glossary.store`](#saving-results-to-a-file-slb_glossarystore)
  - [MCP server: `slb_glossary.mcp`](#mcp-server-slb_glossarymcp)
    - [Configuring the server](#configuring-the-server)
    - [The tools it exposes](#the-tools-it-exposes)
    - [Auth, rate limiting, and hooks](#auth-rate-limiting-and-hooks)
    - [From the command line](#from-the-command-line)
    - [Serving a prebuilt app](#serving-a-prebuilt-app)
  - [Command-line interface](#command-line-interface)
    - [Command reference](#command-reference)
    - [Choosing a source: `--local` / `--live` / `--auto`](#choosing-a-source---local----live----auto)
    - [Saving and formatting output](#saving-and-formatting-output)
    - [The interactive TUI](#the-interactive-tui)
  - [Logging](#logging)
  - [Performance notes](#performance-notes)
  - [Exceptions](#exceptions)
  - [Development](#development)
  - [Contributing](#contributing)
  - [Attribution and disclaimer](#attribution-and-disclaimer)
  - [Credits](#credits)

## Installation

### As a library

This project uses [uv](https://docs.astral.sh/uv/) for dependency management.

```bash
uv add slb-glossary
```

Or with pip:

```bash
pip install slb-glossary
```

Then install the browser build patchright drives. This is a one-time step.

```bash
patchright install chromium
```

Optional extras, installed as needed:

| Extra   | Unlocks                                                              | Install                          |
| ------- | --------------------------------------------------------------------- | ----------------------------------- |
| `xlsx`  | Saving results as `.xlsx`, and importing `.xlsx`/`.xlsm` into the local database. | `uv add "slb-glossary[xlsx]"`      |
| `config`| TOML/YAML config files (`config.toml`/`.yaml`). JSON always works with no extra. | `uv add "slb-glossary[config]"`    |
| `tui`   | The interactive `--tui` mode for every CLI command.                   | `uv add "slb-glossary[tui]"`       |
| `mcp`   | The MCP server (`slb mcp serve`, `slb_glossary.mcp`). See [MCP server](#mcp-server-slb_glossarymcp). | `uv add "slb-glossary[mcp]"`       |
| `all`   | Every extra above.                                                     | `uv add "slb-glossary[all]"`       |

### As a CLI tool

`click` is a core dependency, so installing `slb-glossary` by any of the methods below gets you two equivalent commands, `slb-glossary` and the shorter `slb`, with no extra flags needed.

With [uv](https://docs.astral.sh/uv/) (recommended, since it installs into an isolated tool environment):

```bash
uv tool install "slb-glossary[all]"
```

Or try it once without installing anything, via [`uvx`](https://docs.astral.sh/uv/guides/tools/):

```bash
uvx slb-glossary search porosity
```

With [pipx](https://pipx.pypa.io/):

```bash
pipx install "slb-glossary[all]"
```

On macOS/Linux, including WSL, there's a one-line installer that picks `uv` or `pipx` for you, installing `uv` first if neither is already on your machine:

```bash
curl -fsSL https://raw.githubusercontent.com/ti-oluwa/slb-glossary/main/scripts/install.sh | sh
```

On Windows, without WSL, use uv's native installer instead, then `uv tool install`:

```powershell
powershell -c "irm https://astral.sh/uv/install.ps1 | iex; uv tool install slb-glossary"
```

Whichever method you use, finish with the one-time browser install. `slb-glossary sync` will also do this for you if you let it, see below.

```bash
slb-glossary install "chromium" # Or "firefox"/"webkit"
```

Browser builds are a large download from a single CDN, so on a slow or congested connection the install can time out partway through. If that happens, `install` takes a few flags to make it more forgiving:

```bash
slb-glossary install --timeout 120000                                        # allow 2 min per download, instead of the ~30s default
slb-glossary install --download-host https://playwright.download.prss.microsoft.com  # use a mirror if the default CDN is slow or unreachable
slb-glossary install --retries 5                                             # retry a failed download more times, with backoff
```

## Quick start

### Library

Do a quick live search on the glossary:

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
import asyncio
import slb_glossary as slb


async def main() -> None:
    async with slb.local.database() as db, slb.live.session() as session:
        # Local first. Only opens a live page if the local DB has nothing.
        # persist=True writes whatever came back live into `db`.
        async for result in slb.search("water saturation", db=db, session=session, persist=True):
            print(result.term, ":", result.definition)

        # A repeat call for the same query is now served from `db` alone.
        async for result in slb.search("water saturation", db=db):
            print("(cached)", result.term)


asyncio.run(main())
```

### Command line

```bash
slb search porosity
slb terms Geophysics --limit 20
slb define "black oil" --local
slb random --topic Drilling
slb local search viscosity --topic Petrophysic --fuzzy
```

See [Command-line interface](#command-line-interface) for the full command reference.

## Core concepts

### `Session`: one session, many searches

`slb_glossary` has no `Glossary` class. Instead, `open_session` (or the `session` context manager) launches a browser and loads the glossary's topic list once, returning a `Session`. That's a plain dataclass holding the live browser session and that metadata. Every live search function takes this session as its first argument.

```python
session = await slb.open_session(language=slb.Language.ENGLISH)
try:
    ...
finally:
    await slb.close_session(session)
```

Prefer `session` for anything but long-lived services. It guarantees the browser is closed even if your code raises.

```python
async with slb.live.session(headless=True) as session:
    ...
```

`open_session` accepts:

| Parameter          | Default              | Description                                                                                     |
| ------------------- | --------------------- | --------------------------------------------------------------------------------------------------- |
| `language`           | `Language.ENGLISH`     | Glossary edition to search, `Language.ENGLISH` or `Language.SPANISH`.                              |
| `browser_type`       | `"chromium"`           | Playwright browser family to launch: `"chromium"`, `"firefox"`, or `"webkit"`.                       |
| `headless`           | `True`                 | Run without a visible browser window.                                                               |
| `block`              | `True`                 | Resource types to drop for speed. `True` blocks images/media/fonts, `False` blocks nothing, or pass your own iterable, e.g. `{"image", "stylesheet"}`. |
| `timeout`            | `60_000`               | Milliseconds to wait for page loads and element lookups.                                            |
| `terms_per_tab`      | `12`                   | Results per page, as returned by the glossary site. Rarely needs changing.                          |
| `retry`              | `RetryPolicy()`        | Retry policy for the initial topic load, reused by search functions. See [Retries and backoff](#retries-and-backoff). |
| `settle_timeout`     | `8.0`                  | Seconds to wait for results to update after a search filter changes, since the site updates via JS rather than a full page load. |
| `poll_interval`      | `0.3`                  | Seconds between polls while waiting on `settle_timeout`.                                            |
| `executable_path`    | `None`                 | Path to a specific browser build, if not using patchright's own install.                            |
| `proxy`              | `None`                 | Playwright-style proxy settings, e.g. `{"server": "http://myproxy:3128"}`.                          |
| `viewport`           | `None`                 | Browser viewport size, e.g. `{"width": 1920, "height": 1080}`.                                      |
| `use_stealth`        | `True`                 | Apply Playwright stealth patches to the browser context.                                            |
| `initialize`         | `None`                 | Whether to load the topic list and glossary size before returning the session. `None` resolves this from `SLB_GLOSSARY_SESSION_AUTO_INITIALIZE` (off unless set). While uninitialized, most `live` search functions raise `SessionNotInitializedError` until you call `session.initialize()` yourself; `local.sync_all`/`sync_topics` initialize a session automatically if it isn't already. |

`session.topics` is a `dict` of topic name to term count, and `session.size` is the glossary's total term count, both fetched once the session is initialized. Call `slb.refresh_topics(session)` to reload them later.

You can also open a session straight from a [`Config`](#configuration-slb_glossaryconfig):

```python
async with slb.session_from_config("~/.config/slb-glossary/config.toml") as session:
    ...
```

### Retries and backoff

Page loads that briefly render before the glossary's JavaScript widget finishes populating are retried using a `RetryPolicy`:

```python
policy = slb.RetryPolicy.exponential(base_delay=0.5, attempts=5, max_delay=8.0)
async with slb.live.session(retry=policy) as session:
    ...
```

Four strategies are available, each with a constructor shortcut: `RetryPolicy.constant()`, `.linear()`, `.exponential()` (the default), and `.logarithmic()`. All accept `attempts`, `base_delay`, `factor`, `max_delay`, and `jitter` (randomizes each delay by +/-50% to avoid retry storms, on by default).

### `SearchResult`

Every result, from the live site or the local database, is a `typing.NamedTuple`:

```python
class SearchResult(typing.NamedTuple):
    term: str
    definition: str | None
    grammatical_label: str | None
    topic: str | None
    url: str | None
    image: str | None = None
    image_caption: str | None = None
    related: tuple[RelatedTerm, ...] | None = None
```

It's a plain `NamedTuple` underneath, so indexing and unpacking work as you'd expect, and if you already know a namedtuple's own methods, those work too. It also adds `result.fields` and `result.asdict()`, which is what [`slb_glossary.store`](#saving-results-to-a-file-slb_glossarystore) and the CLI's output actually use. `related` holds `RelatedTerm(term, url)` pairs parsed from a definition's "See related terms" list, when present.

### Live search: `slb_glossary.live`

`slb_glossary.live` talks only to the live site and never touches the local database. All of its functions are **async generators**. Iterate them with `async for`, and nothing more is fetched than you actually consume.

```python
# Search the whole glossary for a query
async for result in slb.live.search(session, "gas lift", limit=5):
    ...

# Search within one or more topics (comma-separated)
async for result in slb.live.search(session, "flow", topic="Well completions,Production"):
    ...

# Every term filed under a topic, one result per term
async for result in slb.live.get_terms_on(session, topic="Directional drilling"):
    ...

# Just the term detail URLs, if that's all you need
async for url in slb.live.get_terms_urls(session, query="porosity"):
    ...

# Fetch every definition on one term detail page directly
async for result in slb.live.get_results_from_url(session, url):
    ...
```

`limit` bounds the number of *terms* looked up, not the number of results yielded. A term can carry more than one definition, one per topic it's filed under, so `search(..., limit=3)` can yield more than three `SearchResult`s. Pass `limit=None` to fetch every match.

Topic names don't need to be exact. `get_topic_match` resolves whatever you pass to the closest topic in `session.topics` (case-insensitive, typo-tolerant), and every `live` function that takes a `topic` uses it internally. Call it yourself to see what a topic will resolve to before searching:

```python
slb.get_topic_match(session.topics, "drill")
# "Drilling"
```

`live.get_results_from_url`, `live.get_terms_on`, and `live.search` all support a `concurrency` argument for fetching several term detail pages in parallel, opening extra pages on the same browser context. Keep this modest. It's still one glossary site being asked for more at once.

## The local database: `slb_glossary.local`

`slb_glossary.local` is a SQLite (FTS5) cache of glossary terms, plus an optional custom embedding vector store, so repeat lookups don't have to keep re-visiting the live site.

> [!NOTE]
> The data stored locally is still SLB's, see [Attribution and disclaimer](#attribution-and-disclaimer). Enabling this module means keeping a local copy of glossary content on your own machine. You're solely responsible for that copy's lifecycle (how long you keep it, how often you refresh it, and deleting it when you're done) in compliance with SLB's terms of use.

Open a database with `database` (an `async with` context manager) or `open_db`/`close_db` directly:

```python
async with slb.local.database() as db:
    ...
```

With no path given, it opens at the OS-appropriate user data directory (see `slb_glossary.paths`, or `slb-glossary local path` on the CLI). Override it with a path, the `SLB_GLOSSARY_DATA_DIR` environment variable, or `Config.local.data_dir`.

### Filling the local database

Sync functions in `slb_glossary.local.sync` pull from a live `Session` into a `Database`, from cheapest to most expensive:

```python
await slb.local.sync_topics(db, session)  # just the topic list/counts
await slb.local.sync_query(db, session, "porosity")  # one query's results
await slb.local.sync_topic(db, session, "Drilling")  # every term under a topic
await slb.local.sync_letter(db, session, "p")  # every term starting with "p"
await slb.local.sync_all(db, session, concurrency=3)  # the entire glossary
```

Prefer `sync_query`/`sync_topic`/`sync_letter` over `sync_all` where you can. Fetching only what you actually look up keeps this package's footprint on the live site as light as possible. Each returns a `SyncSummary` (`terms_written`, `total_terms`, `topics`, `synced_at`), and updates `metadata.json` alongside the database.

### Querying the local database

`slb_glossary.local`'s query functions mirror the shapes `slb_glossary.live`'s functions return, so code written against one mostly works against the other:

```python
async for result in slb.local.search(db, "porosity", limit=10):
    ...

async for result in slb.local.get_terms_on(db, "Drilling"):
    ...

result = await slb.local.get_term(db, "porosity")  # exact name or URL
pick = await slb.local.get_random_term(db, topic="Drilling")
topics = await slb.local.get_topics(db)  # {topic: term_count}
total = await slb.local.count(db)
```

`search` ranks results best match first. SQLite FTS5 picks candidates, then each one is scored directly against your query so an actual term-name match always beats a word that just happens to show up in a definition. If you need the scores themselves, to decide if the results are good enough to trust for example, use `scored_search` instead, which returns `(result, score)` pairs, `score` from `0.0` to `1.0`:

```python
for result, score in await slb.local.scored_search(db, "porosity", limit=10):
    print(f"{score:.2f}", result.term)
```

`flush(db)` deletes every stored term, keeping sync history. `reset(db)` also forgets the sync history.

### Fuzzy topic matching

Topic filters (`search`, `get_terms_on`, `get_random_term`, `get_terms_urls`) match locally stored topic names exactly, case-insensitively, by default. The local database doesn't have access to the live site's full topic list to fuzzy-match against automatically. Pass `fuzzy=True` to tolerate minor misspellings or partial names instead, resolved against whatever topics are actually present locally:

```python
async for result in slb.local.get_terms_on(db, "Petrophysic", fuzzy=True):
    ...  # resolves to "Petrophysics" if that's what's stored locally

slb.local.fuzzy_match_topics(await slb.local.get_topics(db), "Drillng,Geolog")
# "Drilling,Geology"
```

On the CLI, this is `slb-glossary local search --topic Petrophysic --fuzzy`.

### Importing your own data

`load_file` imports a CSV, JSON, or `.xlsx`/`.xlsm` file (the last needs the `xlsx` extra) into the local database, with configurable column/field names:

```python
await slb.local.load_file(
    db,
    "my_terms.csv",
    term_field="Term",
    definition_field="Definition",
    embedding_field="Embedding",  # optional, see below
)
```

Rows need at least a term name. Every other field is optional. A row with no URL gets a stable synthetic `local://imported/<slug>` one, since `url` is the database's primary key.

### Bring-your-own-embedding vector search

`slb_glossary.local` doesn't bundle an embedding model, since that would drag in a heavy ML dependency most callers won't use. Instead, `slb_glossary.local.vectors` stores whatever embedding vector you've already computed and ranks stored vectors by cosine similarity against a query vector you supply:

```python
await slb.local.upsert_vector(db, result.url, my_embedding, model="text-embedding-3-small")

matches = await slb.local.vector_search(
    db, my_query_embedding, model="text-embedding-3-small", limit=5
)
for result, similarity in matches:
    print(f"{similarity:.3f}", result.term)
```

This is a brute-force scan. Fine for a glossary-sized dataset, but not built for million-row corpora.

## Source-aware queries: `slb_glossary.query`

`slb_glossary.local` only ever reads the local database, and `slb_glossary.live` only ever talks to the live site. `slb_glossary.query` is the layer that picks between (or combines) the two, so you don't have to hand-roll the "check local, fall back live, maybe cache what came back" dance yourself:

```python
async with slb.local.database() as db, slb.live.session() as session:
    async for result in slb.query.search("water saturation", db=db, session=session, persist=True):
        print(result.term, ":", result.definition)
```

At least one of `db` or `session` must be given to every function here, since there's nothing to query otherwise. Which is actually used, and in what order, is controlled by `source`, a `query.Source`:

| `Source`       | Behavior                                                                                     |
| --------------- | ------------------------------------------------------------------------------------------------ |
| `LOCAL`          | The local database only. Never touches the network. Requires `db`.                              |
| `LIVE`           | The live glossary only. Never touches the local database. Requires `session`.                   |
| `AUTO`    | (Default when both `db` and `session` are given.) Try `db` first. `get_term`, `get_terms_on`, `get_terms_urls`, `get_topics`, `related_terms`, `get_random_term`, and `compare` fall back to `session` if `db` came back with nothing at all. `search` is scored instead of just checked for emptiness, see below. Pass `persist=True` to cache whatever came back live. |

`search`'s `AUTO` behavior goes a step further than the rest. Each local result is scored against the query (`local.scored_search`), and if even the best of them isn't a confident match, `session` is queried too and its results are added on rather than replacing the local ones. `relevance_threshold` (`0.0` to `1.0`, default `0.55`) sets how confident is confident enough. This is what lets a search stay accurate without silently trusting a weak local match just because it happened to return something.

When only one of `db`/`session` is given, `AUTO` simply behaves like whichever of `LOCAL`/`LIVE` that one supports. The available functions mirror `slb_glossary.live`/`slb_glossary.local`'s own shapes. `search`, `get_terms_on`, `get_terms_urls`, and `get_topics` stream/return several results; `get_term`, `related_terms`, and `get_random_term` return one; `compare` looks up several terms at once. Each accepts a `fuzzy=True` flag that, for any local read, tolerates minor misspellings/partial names in `topic` (see [Fuzzy topic matching](#fuzzy-topic-matching); live reads already fuzzy-match topics unconditionally).

`get_term`, `related_terms`, and `get_random_term` return a `QueryResult(value, source, persisted)`, so callers can tell where a result actually came from and whether it was written back to `db`.

## Configuration: `slb_glossary.config`

`slb_glossary.config.Config` is a dataclass, loadable from and savable to a JSON, TOML, or YAML file (TOML/YAML need the `config` extra):

```python
config = slb.Config.load()  # default path if it exists, else built-in defaults
async with slb.session_from_config(config) as session:
    ...
```

It has three sections:

| Section    | Covers                                                                                   |
| ------------ | --------------------------------------------------------------------------------------------- |
| `session`    | Every `open_session` parameter: language, browser type, timeouts, retry policy, and more.  |
| `local`      | Whether local-database fallback is on by default, where it lives, and staleness thresholds. |
| `output`     | Default `--save` format and which result columns are shown by default.                      |

Read or write a single dotted key without touching the rest of the file:

```python
config.get("session.headless")
config.set("session.headless", False)  # accepts strings too, coerced to the field's type
config.to_file("~/.config/slb-glossary/config.toml")
```

On the CLI, `slb-glossary config` opens a guided, section-by-section wizard. `config show`/`get`/`set`/`init`/`edit`/`path` cover everything else non-interactively. The default path is the OS-appropriate user config directory (`slb-glossary config path`, or override with the `SLB_GLOSSARY_CONFIG_DIR` environment variable). Pass `--config PATH` (or `--config none` to skip it) to any other command to use a different one for that run only.

## Saving results to a file: `slb_glossary.store`

`slb_glossary.store` has no dependency on the rest of `slb_glossary`, so you can use it to save any data, glossary-related or not. `save` works with anything that satisfies `RecordLike`: a `.fields` property and an `.asdict()` method (`SearchResult` already has both, see [`SearchResult`](#searchresult)). That's it, so it happily saves `SearchResult`s, your own records, or the async generators the search functions return directly, without you collecting them first:

```python
results = slb.live.search(session, "gas lift")
await slb.store.save(results, "gas_lift.json")  # collects the generator for you
```

The file format is chosen from the destination's extension, or pass `format=` explicitly:

```python
await slb.store.save(results_list, "results.data", format="csv")
```

Built-in formats: `csv`, `json`, `jsonl`/`ndjson`, `txt`, and `xlsx` (requires the `xlsx` extra). Check what's available with `slb.store.supported_formats()`.

Add support for a new format with the `writer` decorator, no subclassing required:

```python
import pathlib
import yaml

from slb_glossary.store import RecordLike  # just a type hint, so a direct import is fine here


@slb.store.writer("yaml")
async def write_yaml(records: list[RecordLike], destination: pathlib.Path) -> None:
    with open(destination, "w") as file:
        yaml.dump([record.asdict() for record in records], file)


await slb.store.save(results_list, "results.yaml")
```

## MCP server: `slb_glossary.mcp`

`slb_glossary.mcp` exposes the same search/lookup functions as [MCP](https://modelcontextprotocol.io) tools, so an LLM agent (Claude, or anything else that speaks MCP) can search the glossary directly. It's built on [FastMCP](https://gofastmcp.com), and it's an optional extra, see [Installation](#installation), since it pulls in FastMCP as a dependency.

```python
import asyncio
from slb_glossary.mcp import MCPApp, MCPConfig

app = MCPApp(MCPConfig.default())
asyncio.run(app.run_async())  # stdio, read-only, local-and-live, unauthenticated
```

Or from the command line:

```bash
slb mcp serve
```

Both do the same thing: build an MCP server from an `MCPConfig` and serve it. `MCPConfig()` alone (or `slb mcp serve` with no flags) is already a valid server. Read-only, local database and live site both reachable, no auth, no rate limiting.

### Configuring the server

`MCPConfig` is a frozen dataclass tree. Every section is independent, so you only set what you need to change. Build one with keyword arguments, or start from `MCPConfig.default()` and override a few fields with `dataclasses.replace`:

```python
import dataclasses
from slb_glossary.mcp import MCPConfig, LocalAccess, Tool

config = MCPConfig(
    local=LocalAccess(allow_write=True),
    tools=Tool.ALL,
)
```

| Section         | Covers                                                                                      |
| ---------------- | ------------------------------------------------------------------------------------------------ |
| `server`          | Name/version/instructions advertised to MCP clients.                                          |
| `session`          | Whether/how the live `Session` is used: `SessionMode.EAGER`/`LAZY`/`PER_CALL`, idle timeout, concurrency, and every `open_session` option (via `browser`). |
| `local`            | Whether the local database is reachable, and whether **writes** are allowed (`allow_write`, off by default). |
| `source_policy`    | Which `Source` values (`LOCAL`/`LIVE`/`AUTO`) a tool call may resolve to, and the default when a caller doesn't specify one. |
| `tools`            | Which tools to build, a `Tool` flag combination, e.g. `Tool.SEARCH | Tool.GET_TERM`, or`Tool.READ_ONLY`/`Tool.ALL`. |
| `timeouts`         | A global per-call timeout, plus per-tool overrides.                                            |
| `auth`             | An `AuthBackend` for per-call caller identity, and/or a FastMCP `AuthProvider` for transport-level auth. See [Auth, rate limiting, and hooks](#auth-rate-limiting-and-hooks). |
| `rate_limit`       | Per-client/per-tool request-rate limiting.                                                     |
| `hooks`            | Callables run before/after each tool call, on error, and on startup/shutdown.                  |
| `logging`          | Where/how `slb_glossary`'s own logging is routed for the server process.                       |
| `streaming`        | Whether tools that support it report progress notifications as they work.                      |

Local writes are **off by default**. With `local.allow_write=False`, every read tool's `persist` argument is silently ignored, and the write-capable `glossary_sync` tool is never registered even if it's in `tools` (see `MCPConfig.resolved_tools`). Turning it on is a deliberate, single flag:

```python
config = MCPConfig(local=LocalAccess(allow_write=True), tools=Tool.ALL)
```

### The tools it exposes

| Tool                     | `Tool` flag       | What it does                                                              |
| -------------------------- | ------------------- | -------------------------------------------------------------------------- |
| `glossary_search`          | `SEARCH`             | Free-text search, the default choice when a term name isn't known exactly. |
| `glossary_get_term`        | `GET_TERM`           | Exact-name or URL single-term lookup, cheaper than search when you already know the name. |
| `glossary_get_terms_on`    | `GET_TERMS_ON`       | Every term filed under one or more topics.                               |
| `glossary_get_terms_urls`  | `GET_TERMS_URLS`     | URL-only listing, no full definitions.                                   |
| `glossary_get_topics`      | `GET_TOPICS`         | Topic name to term-count mapping.                                        |
| `glossary_related_terms`   | `RELATED_TERMS`      | A term's "related terms" links.                                          |
| `glossary_random_term`     | `RANDOM_TERM`        | One randomly chosen term, optionally within a topic.                     |
| `glossary_compare`         | `COMPARE`            | Several specific terms looked up side by side.                           |
| `glossary_sync`            | `SYNC`               | Fetches from the live site and writes into the local database. The only tool that writes anything, gated behind `local.allow_write=True`. |

Every tool's arguments are a frozen dataclass, mirrored straight into its MCP JSON schema, and every result is built from `SearchResult`/`RelatedTerm`'s own `asdict()`, the same shapes used everywhere else in this package. Tool descriptions are written to make the right choice as unambiguous as possible for an LLM (`glossary_get_term` for a known name, `glossary_search` otherwise, and so on). See `slb_glossary.mcp.tools.DEFAULT_INSTRUCTIONS` for the full server-level guidance shown to a connecting client.

`glossary_search` and `glossary_get_terms_on` accept a `stream=True` argument that reports live MCP progress notifications (`Context.report_progress`) as results are found. This is progress reporting, not partial results. MCP's `tools/call` always delivers one complete result at the end regardless, so `stream` only affects what a client can show while the call is still running, not the final payload's shape.

### Auth, rate limiting, and hooks

Two independent auth layers, doing different jobs.

- **`auth.provider`**: a FastMCP `AuthProvider`/`TokenVerifier`, forwarded to `fastmcp.FastMCP(auth=...)`. Protects the transport itself. A request that fails this check never reaches a tool call.
- **`auth.backend`**: an `AuthBackend` you implement (one `async def authenticate(self, request: AuthRequest) -> Principal | None` method), resolving each call into a `Principal` this server's own middleware uses for rate-limit keys, hooks, and call logging. `AuthRequest` carries the parsed bearer token, every request header, and the tool name/arguments, so a backend can key off more than a bare token if it needs to.

```python
from slb_glossary.mcp import Auth, StaticTokenAuth, Principal

auth = StaticTokenAuth({"sk-alice-...": Principal(id="alice", scopes=frozenset({"write"}))})
config = MCPConfig(auth=Auth(backend=auth, required=True))
```

Rate limiting is a `RateLimiter` protocol (one `async def hit(self, key: str) -> float` method, returning milliseconds to wait, `0` if allowed). `SlidingWindowRateLimiter` is the built-in, in-memory, single-process implementation. Swap in your own for anything distributed:

```python
from slb_glossary.mcp import RateLimit

config = MCPConfig(rate_limit=RateLimit(enabled=True, limit=60, window=60.0))
```

Hooks run around every call and around server startup/shutdown, given a `ToolRunContext` (tool name, resolved `Principal`, arguments, resolved `Source`):

```python
from slb_glossary.mcp import Hooks, ToolRunContext


async def audit(run: ToolRunContext) -> None:
    print(f"{run.principal.id} called {run.tool_name}")


config = MCPConfig(hooks=Hooks(before_tool=(audit,)))
```

### From the command line

```bash
slb mcp serve                                   # stdio, read-only, local+live
slb mcp serve --transport http --port 8000
slb mcp serve --allow-write --tools all          # enable glossary_sync too
slb mcp serve --auth-token sk-alice-...:alice    # StaticTokenAuth, one or more --auth-token
slb mcp serve --rate-limit 60                    # 60 requests/client/tool/minute
slb mcp serve --config glossary.toml             # reuse an slb_glossary Config file's session/local settings
```

Run `slb mcp serve --help` for the full set of flags, including `--source`/`--no-local`/`--no-live` (narrow which `Source`s are reachable), `--timeout` (global per-call cap), and `--auth-backend module:ClassName` (a custom, no-argument-constructor `AuthBackend`, for anything beyond static tokens).

### Serving a prebuilt app

For anything `slb mcp serve`'s flags don't cover, such as custom hooks, a hand-built `AuthBackend` with its own constructor arguments, or extra tools bolted onto a plain `fastmcp.FastMCP`, build the app yourself in Python and point the CLI at it, uvicorn-style:

```python
# app/main.py
from slb_glossary.mcp import MCPApp, MCPConfig, LocalAccess

app = MCPApp(MCPConfig(local=LocalAccess(allow_write=True)))
```

```bash
slb mcp serve app.main:app
```

`app.main:app` can be an `MCPApp`, a raw `fastmcp.FastMCP`, or a zero-argument factory function returning either. `--transport`/`--host`/`--port`/`--log-level` still apply. Every other flag is rejected if passed alongside a prebuilt app, since it would otherwise be silently ignored.

## Command-line interface

Installing `slb-glossary` (see [As a CLI tool](#as-a-cli-tool) above) gets you the `slb-glossary` command, and the shorter `slb` alias for it:

```bash
slb search porosity
slb terms Geophysics --limit 20
slb topics list
slb urls fetch "https://glossary.slb.com/en/terms/p/porosity"
```

Run `slb --help`, or `--help` after any subcommand, for the full set of options. Or pass `--tui` to fill them in interactively instead of memorizing flags. `--log-level` on the root command controls `slb_glossary`'s own log verbosity (see [Logging](#logging)).

### Command reference

| Command            | Talks to                       | What it does                                                                                     |
| -------------------- | --------------------------------- | ------------------------------------------------------------------------------------------------- |
| `search`             | Local, live, or auto               | Free-text search of the whole glossary. See [Choosing a source](#choosing-a-source---local----live----auto). |
| `terms`              | Local, live, or auto               | Every term filed under a topic.                                                                  |
| `topics list`        | Local, live, or auto               | List every topic (discipline) with term counts.                                                  |
| `topics refresh`     | Live only                          | Reload the topic list directly from the site.                                                    |
| `urls list`          | Local, live, or auto               | List term detail-page URLs matching a query/topic/letter.                                        |
| `urls fetch`         | Live only                          | Fetch every definition on one term detail-page URL.                                               |
| `define`             | Local, live, or auto               | Look up a single term's definition.                                                               |
| `related`            | Local, live, or auto               | List a term's "related terms" links.                                                             |
| `compare`            | Local, live, or auto               | Look up several terms side by side.                                                               |
| `random`             | Local, live, or auto               | Print one or more randomly chosen terms.                                                          |
| `sync`               | Live, then local                   | Check the browser engine is installed, then refresh the local database.                          |
| `update`             | Live, then local                   | Refresh the local database, assumes the browser is already installed.                            |
| `local path`         | Local only                          | Print the resolved database/metadata file paths.                                                 |
| `local stats`        | Local only                          | Term counts, topic breakdown, and last-sync info.                                                |
| `local search`       | Local only                          | Full-text search the local database (`--fuzzy` for typo-tolerant `--topic`).                     |
| `local get`          | Local only                          | Look up a single term by exact name/URL, locally.                                                |
| `local flush`        | Local only                          | Delete every stored term, keeping sync history.                                                  |
| `local reset`        | Local only                          | Flush the database and forget its sync history too.                                              |
| `config`             | n/a                                | Interactive wizard for the config file (see [Configuration](#configuration-slb_glossaryconfig)). |
| `install`            | n/a                                | Install/list/remove/update the browser engines patchright launches.                              |
| `mcp serve`          | n/a                                | Run an MCP server for LLM agents (see [MCP server](#mcp-server-slb_glossarymcp)). Requires the `mcp` extra. |

Every command in the "Local, live, or auto" rows is built on `slb_glossary.query`, so they all take the same `--local`/`--live`/`--auto` trio described below. `topics refresh` and `urls fetch` are the two holdouts that stay live-only by design: a "refresh" is explicitly asking for a fresh copy from the site, and fetching one specific URL doesn't have a meaningful local equivalent. `local search`/`local get` read the cached copy exclusively, with no live fallback at all. Reach for those when you want a hard guarantee that nothing will touch the network.

### Choosing a source: `--local` / `--live` / `--auto`

`search`, `terms`, `urls list`, `topics list`, `define`, `related`, `compare`, and `random` all accept:

```bash
slb define porosity --local           # local database only, error if disabled/missing
slb define porosity --live --cache    # live site only; --cache saves the result locally
slb define porosity --auto            # local first, live as a fallback (the default)
slb define porosity --source live     # equivalent, spelled out
```

`--db-path PATH` overrides the local database file for that run (see `local path`/`Config.local`). With `--auto` (the default), a local hit never launches a browser at all, so a search you've already cached comes back instantly, and only a genuine cache miss pays for opening a page. For `search` specifically, "hit" means a confident one. `--relevance-threshold` (default `0.55`) sets how good the local database's best match needs to be before a browser is skipped entirely. A weak match still gets served, but topped up with a live search rather than trusted on its own.

### Saving and formatting output

Every command that prints results also supports `--save PATH` (repeatable, for saving to several files/formats at once), `--format FORMAT` to override the format `PATH`'s extension implies, and `--json` to print results as a JSON array to stdout instead of a table, handy for piping into `jq` or another program:

```bash
slb search "drilling fluid" --json | jq '.[].term'
slb terms Drilling --save drilling_terms.json --quiet
```

`--quiet` suppresses console output entirely (useful with `--save`). `--show-related`/`--hide-related`, `--show-image`/`--hide-image`, and similar flags toggle individual result columns where relevant.

### The interactive TUI

Pass `--tui` to the root command, or after any subcommand, to fill in its options through a form instead of memorizing flags (requires the `tui` extra):

```bash
slb --tui                # browse and run any command
slb search --tui          # fill in `search`'s options interactively
```

## Logging

`slb_glossary` logs through the standard `logging` module under the `slb_glossary` logger and attaches a `NullHandler` by default, so it stays silent until you configure logging yourself:

```python
import logging

logging.basicConfig(level=logging.INFO)
logging.getLogger("slb_glossary").setLevel(logging.DEBUG)  # verbose, per-page detail
```

`INFO` covers session open/close, search start/end, and sync summaries. `DEBUG` covers individual page loads, retries, and parsed counts. `WARNING` covers unmatched topics and exhausted retries. The CLI's own `--log-level` flag sets this for you.

## Performance notes

A few things `slb_glossary` does on its own to keep things fast. Image, font, and media requests are blocked at the network layer by default (`block=True`). The glossary is a JavaScript app, so scripts and stylesheets still load, but nothing else needs to. Page data (topic lists, result links, definition text) is read with single `evaluate`-style JavaScript calls instead of one round-trip per DOM element. Search functions are lazy async generators, so `async for result in live.search(session, "x"): break` only does the work needed to produce that first result. And a local-database read never launches a browser.

The rest is on you, and it's mostly about not paying for a browser more than once. Open one `Session` and reuse it for every live search you need instead of opening a new one per query. Most of a session's cost is the one-time browser launch and topic fetch. A session drives a single browser page, though, so it isn't safe to share across concurrent coroutines. For parallel searches, either open one session per task, or use a function's `concurrency` argument to open extra pages on the same session (keep this modest, it's still one site being asked for more at once).

Past that, lean on the local database. `slb_glossary.query`'s `Source.AUTO` (the CLI's `--auto`, the default) tries the local database first, so a search you've already cached costs nothing beyond an SQLite read on a repeat run and only touches the network the first time. `search` specifically only trusts that local read alone if its best match is actually a good one (see `relevance_threshold` in [Source-aware queries](#source-aware-queries-slb_glossaryquery)). If it isn't, it augments with a live search instead of pretending the network step isn't needed, but the results still favor whatever's already local. If you know you'll need a topic or query a lot, `slb-glossary local sync`/`update` (or `slb_glossary.local.sync`) let you build up the cache ahead of time in one batch, so day-to-day lookups afterward stay entirely local.

## Exceptions

- `slb_glossary.NetworkError`: the glossary site could not be reached.
- `slb_glossary.BrowserError`: the browser failed to launch or crashed outside of a network issue, including an unsupported `browser_type`.
- `slb_glossary.ParsingError`: reserved for glossary pages that don't match the markup the parser expects.
- `slb_glossary.ConfigError`: a config file or dotted key (`Config.get`/`Config.set`) was invalid.
- `slb_glossary.DatabaseError`: the local database failed to open, query, or import from a file.
- `slb_glossary.QueryError`: `slb_glossary.query` can't satisfy a lookup with the source(s) it was given (e.g. `Source.LOCAL` with no `db`).
- `slb_glossary.LoggingError`: a custom `log_sink` (see [Logging](#logging)) couldn't be set up.
- `slb_glossary.store.UnsupportedFormatError`: `save` was asked for a format with no registered writer.
- `slb_glossary.store.WriterError`: the registered writer raised while writing, e.g. a permissions error or a full disk. The original exception is chained as `__cause__`.
- `slb_glossary.mcp.MCPError`: base for every `slb_glossary.mcp`-specific error (requires the `mcp` extra):
  - `MCPConfigError`: an `MCPConfig` (or a nested config within it) was invalid.
  - `AuthenticationError`: a tool call couldn't be authenticated and `auth.required` is `True`.
  - `RateLimitExceededError`: a caller exhausted their rate-limit quota, carries `.wait_ms`.

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

Anything cached locally by `slb_glossary.local` (or the default config file's local-database settings) is still SLB's content. Enabling local storage means keeping a copy on your own machine, and you're solely responsible for that copy's retention, refresh, and deletion in compliance with SLB's terms of use.

Consult the original site and its terms of use for any reuse or redistribution of glossary content: <https://www.slb.com/en/terms-of-service>. See the `NOTICE` file for the full attribution notice, and `LICENSE` for this project's own code license.

## Credits

This project was inspired by the 2023/24/25 Petrobowl Team of the Federal University of Petroleum Resources, Effurun, Delta State, Nigeria. It aided the team's preparation for the PetroQuiz and PetroBowl competitions organized by the Society of Petroleum Engineers (SPE).
