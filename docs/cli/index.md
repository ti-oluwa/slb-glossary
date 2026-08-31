# Using the CLI

No programming knowledge is assumed anywhere in this section — just the `slb` command from a terminal. If you haven't installed it yet, see [Installation](../getting-started/installation.md).

Every command below accepts `--help` for its own full option list, and every command also accepts `--tui`, which opens an interactive form for that command instead of you having to remember its flags:

```bash
slb search --tui
```

---

## The commands, at a glance

| Command | What it does |
|---|---|
| [`search`](searching.md#search) | Search the glossary for a query, ranking by relevance. |
| [`define`](searching.md#define) | Look up one exact term. |
| [`compare`](searching.md#compare) | Look up two or more terms side by side. |
| [`related`](searching.md#related) | List the terms one definition links to. |
| [`terms`](searching.md#terms) | Fetch every term filed under one topic. |
| [`random`](searching.md#random) | Pick one or more random terms. |
| [`topics`](searching.md#topics) | List the glossary's topic list. |
| [`urls`](searching.md#urls) | List or fetch from raw glossary term URLs. |
| [`sync`](sync.md#sync) | Check the browser is installed and refresh the local cache. |
| [`local`](sync.md#the-local-command-group) | Inspect, search, import into, embed, or clear the local cache directly. |
| [`install`](sync.md#install) | Install, list, remove, or update the browser engine. |
| [`config`](configuration.md#the-config-command) | View, edit, and locate the config file. |
| `mcp` | Run this glossary as an MCP server. See [Connecting an AI agent](../agent/mcp-server.md). |

Every one of these, except `config`, `install`, and the plumbing under `local`, shares the same **source model**: read locally, live, or both. That's worth understanding once, since it explains a chunk of every other command's flags.

## The source model: `--local`, `--live`, `--auto`

Every lookup command (`search`, `define`, `compare`, `related`, `terms`, `random`) accepts `--source local|live|auto`, or the equivalent shorthand flags `--local`, `--live`, `--auto`:

- **`--local`** only reads the database on your own machine. Instant, no network, but only finds terms you've already cached there.
- **`--live`** always visits the live glossary through the background browser. Slower, but always current, and doesn't need anything cached first.
- **`--auto`** (the default) tries local first. For `search`, this means the local database's best match is scored, and used alone if it's confident enough (`--relevance-threshold`, default `0.45`); otherwise the live site is queried too, and its results are shown first, with the local ones filling in any remaining slots. For the exact-lookup commands (`define`, `compare`, `related`, `terms`, `random`), auto is simpler: use the cached copy if one exists, otherwise fetch live.

```bash
slb search porosity --auto                    # the default: local first, live as a fallback
slb search porosity --local                   # only the local cache, never the network
slb search porosity --live                    # always the live site
slb search porosity --relevance-threshold 0.8 # trust local results less readily
```

!!! tip "`--annotate` shows you which source actually answered"
    `search`'s table (and `--json` output) can show each result's origin and score as extra columns: `--annotate always`. Handy the first few times you use `--auto`, before you have a feel for when it reaches for the live site.

## Caching live results: `--cache`

Whenever a lookup command actually reaches the live glossary (via `--live`, or `--auto` falling through to it), the result is saved to the local database by default, so the same lookup is instant next time:

```bash
slb search "gas lift"          # --cache is on by default
slb search "gas lift" --no-cache   # look it up live, but don't save it locally
```

Live results are written incrementally, `--cache-batch-size` at a time (default `20`), rather than all at once at the end. That way, if a large fetch (e.g. `slb terms Drilling --limit 0`) gets interrupted partway through, whatever was already fetched is still saved, instead of the whole run being wasted. `--cache-on-error` (on by default) is what controls whether a failed fetch keeps its partial progress.

## Reading this section

<div class="grid cards" markdown>

- :material-magnify: **Searching and Defining Terms**

    ---

    `search`, `define`, `compare`, `related`, `terms`, `random`, `topics`, `urls`, and what output looks like.

    [Continue](searching.md){ .md-button }

- :material-database-sync: **Local Cache and Sync**

    ---

    `sync`, the `local` command group, and `install`, for working offline on purpose.

    [Continue](sync.md){ .md-button }

- :material-content-save-cog: **Saving, Output and Config Files**

    ---

    `--save`, output formats, `config`, and setting defaults so you don't retype flags.

    [Continue](configuration.md){ .md-button }

</div>
