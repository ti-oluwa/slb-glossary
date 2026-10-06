# CLI Commands

This page contains a dense, structural reference for every `slb` command and flag. For explanations and worked examples, see [Using the CLI](../cli/index.md). Run any command with `--help` for this same information from the terminal.

Most commands share three groups of flags: **source** (where to read from), **session** (how the browser behaves, for a command that might touch the live site), and **output** (saving/printing). They're documented once here, then each command's section lists which parts of them it has. Not every command has every flag, so check the section for the command you're using.

---

## Shared flag groups

### Source flags

Every lookup command (`search`, `define`, `compare`, `related`, `terms`, `random`, `topics list`, `urls list`) has the first group, from `--source` to `--cache-on-error`. The last three rows only exist where noted in the command's section.

| Flag | Default | Meaning |
|---|---|---|
| `--source [local\|live\|auto]` | `auto` | Which source(s) to read from, spelled out. |
| `--local` | | Shorthand for `--source local`. |
| `--live` | | Shorthand for `--source live`. |
| `--auto` | | Shorthand for `--source auto`. |
| `--cache` / `--no-cache` | `--cache` | Save live results to the local database as they arrive. |
| `--cache-batch-size INTEGER` | `20` | Live results buffered per incremental write. |
| `--cache-on-error` / `--no-cache-on-error` | `--cache-on-error` | Keep partial progress if a live fetch fails midway. |
| `--exclude URL_OR_TERM[,...]` | | Skip specific URLs/terms. Repeatable and comma-listable. Only on `search`, `terms` and `urls list`. |
| `-m, --mode [lexical\|semantic\|hybrid]` | `constants.default_search_mode` | Local ranking strategy. No effect on `--live`. Only on `search`. |
| `--fuzzy` | off | Tolerate misspellings in `--topic` against locally stored topic names. Only on `search`, `terms` and `urls list`. |

`search` additionally has `--relevance-threshold FLOAT` (default `0.45`), since it's the one command where `--auto` genuinely blends local and live results rather than picking one. `--annotate [auto\|always\|never]` (default `auto`) shows which source answered and is on `search`, `compare` and `random`.

The lookup commands also take `--metadata-path FILE`, the path to the database's `metadata.json` (it defaults to the one next to `--db-path`).

### Session flags

Every command that can reach the live glossary shares this block (session/browser behavior):

| Flag | Default | Meaning |
|---|---|---|
| `-L, --language [en\|es]` | `en` | Glossary language edition. |
| `-b, --browser-type [chromium\|firefox\|webkit]` | `chromium` | Browser family to launch. |
| `--headless` / `--headed` | `--headless` | Run with or without a visible window. |
| `--block` / `--no-block` | `--block` | Block images/media/fonts/stylesheets for speed. |
| `--block-resource [...]` | | Specific resource type to block. Repeatable; overrides `--block`. |
| `--timeout FLOAT` | `60000.0` | Milliseconds for page loads/element lookups. |
| `--terms-per-tab INTEGER` | `12` | Results the glossary returns per results page. |
| `--max-pages INTEGER` | `6` | Browser pages the session keeps open at once. Passing `--concurrency` raises it to `concurrency + 2` if it is lower (unless you pass `--max-pages`). |
| `--page-acquire-timeout FLOAT` | `60000` | Milliseconds to wait for a free page before failing with `PagePoolTimeoutError`. `0` waits forever. |
| `--settle-timeout FLOAT` | `3000` | Milliseconds to wait for the results list to settle. |
| `--poll-interval FLOAT` | `300` | Poll interval while waiting on `--settle-timeout`. |
| `--executable-path FILE` | | Specific browser build to launch. |
| `--proxy SERVER[,username=U][,password=P]` | | Proxy for the browser. |
| `--viewport WIDTHxHEIGHT` | full-screen | Browser viewport size. |
| `--stealth` / `--no-stealth` | auto (see [Sessions and the Browser](../concepts/sessions.md)) | Apply stealth patches. |
| `--initialize` / `--no-initialize` | lazy | Load topics/size as soon as the session opens. Off by default, the first call that needs them loads them. |
| `--retry-attempts INTEGER` | `3` | Max attempts retrying a flaky initial load. |
| `--retry-base-delay FLOAT` | `0.8` | Base delay (seconds, not milliseconds) for retry backoff. |
| `--retry-backoff [constant\|linear\|exponential\|logarithmic]` | `exponential` | Retry delay growth strategy. |
| `--retry-factor FLOAT` | `2.0` | Growth base (exponential) or log base (logarithmic). |
| `--retry-max-delay FLOAT` | `10.0` | Upper bound on any single retry delay. |
| `--retry-jitter` / `--no-retry-jitter` | `--retry-jitter` | Randomize retry delays ±50% to avoid retry storms. |
| `--concurrency INTEGER` | `1` (`compare`: `3`) | Concurrent term lookups. Only on `search`, `terms` and `compare`. |

### Output flags

Every command that produces results has the first four rows (`--tui` is on most commands, but not on `local` or `mcp`):

| Flag | Default | Meaning |
|---|---|---|
| `-o, --save FILE` | | Save results to a file. Repeatable. |
| `-f, --format TEXT` | inferred from extension | Override the save format. |
| `--json` | off | Print as JSON instead of a table. Ignored with `--quiet`. |
| `-q, --quiet` | off | Don't print to the console. |
| `--tui` | off | Open this command in the interactive TUI instead. |

`search`, `define`, `compare`, `terms`, `random` and `urls fetch` also have `--url`/`--no-url`, `--show-topic`/`--hide-topic`, `--show-grammar`/`--hide-grammar`, `--show-image`/`--hide-image` and `--show-related`/`--hide-related` for column visibility. The defaults are the same everywhere except `define`, which shows related terms by default. `related` and `topics list` have none of them.

### Global flags

| Flag | Meaning |
|---|---|
| `--db-path FILE` | Path to the local database file. |
| `--config default\|none\|PATH` | Config file to load defaults from. |
| `--log-level [debug\|info\|warning\|error\|critical]` | Logging verbosity for this run. |
| `--log-to PATH\|stderr\|stdout` | Where to route logging output. |
| `--log-sink module:ClassName` | Custom `LogSink` class/instance. Takes priority over `--log-to`. |

---

## `search [QUERY]`

Own flags: `-t/--topic`, `-a/--start-letter`, `-n/--limit` (default `3`, `0` for unlimited), `--relevance-threshold`, `-m/--mode`, `--fuzzy`, `--exclude`, `--annotate`, `--concurrency`, plus the source, session, output, column and global flags above.

## `define [TERM]`

`TERM`: an exact term name, or a detail-page URL. Own flags: `-t/--topic` (pick a specific stored definition for a term/URL with several), `--suggest`/`--no-suggest` (default `--suggest`: offer close matches when there's no exact one), `--similar-pool-size N` and `--max-similar N` (both default from `constants`, minimum `1`). With `--auto` it only skips the live site when the local copy has an **exact** match. It has no `--mode`, `--fuzzy` or `--exclude`.

## `compare [TERMS]...`

Two or more terms, looked up concurrently. Terms not found by the resolved source are skipped with a note on stderr rather than failing the whole command. Own flags: `-t/--topic`, `--concurrency` (default `3`), `--annotate`. It has no `--mode`, `--fuzzy` or `--exclude`.

## `related [TERM]`

Lists just the related-term links, not the full definition. Own flags: `-t/--topic`. It has the source and session flags but no column, `--suggest`, `--mode`, `--fuzzy` or `--exclude` flags.

## `terms [TOPIC]`

`TOPIC` need not be exact, the closest known topic is used. Yields at most one result per term (the one filed under `TOPIC`), unlike `search`. Own flags: `-a/--start-letter`, `-n/--limit` (default: every term under the topic, same as `0`), `--concurrency`, `--fuzzy`, `--exclude`. With `--auto`, a topic the local database has any terms for is served from there alone.

## `random`

Own flags: `-t/--topic`, `-n/--count` (default `1`; duplicates possible since each pick is independent), `--annotate`.

## `topics list`

Lists the glossary's topics with term counts. With `--auto`, only lists topics actually present locally if the database is non-empty; visits live otherwise.

## `urls list` / `urls fetch <URL>`

`urls list` needs at least one of `-Q/--query`, `-t/--topic`, `-a/--start-letter`, and also takes `-n/--limit` (default: every match), `--fuzzy` and `--exclude`. `urls fetch` takes `-t/--topic` and the column flags, but no source flags. It parses every definition on one specific detail-page URL directly. See [Searching and Defining Terms](../cli/searching.md#urls).

## `sync`

Own flags: `-t/--topic`, `-Q/--query`, `-a/--start-letter`, `--all` (can't be combined with the filters), `-n/--limit`, `--concurrency`, `--force`/`--no-force` (re-fetch terms already stored), `--batch-size`, `--persist-on-error`/`--no-persist-on-error`, `--install`, `--with-deps`, `--check-only`, `-y/--yes` (skip the confirmation a heavy update asks for, which `--all` always triggers). With no filters it only refreshes the topic list. See [`sync`](../cli/sync.md#sync).

## `local <subcommand>`

`path`, `stats`, `search`, `get`, `flush`, `reset`, `export`, `import`, `embed`, `delete-embeddings`. Never falls back to live regardless of any source flag. See [The `local` command group](../cli/sync.md#the-local-command-group) for each subcommand's own options, `import` in particular has a large, distinct `--*-field` flag set for column mapping, and `embed` needs the `semantic` extra installed.

## `install`

Takes optional `BROWSERS...` names. Own flags: `--list`, `--update`, `--remove` (all plain flags that apply to the browsers named, or to every installed one), `--force`, `--with-deps`, `--only-shell`, `--timeout` (download timeout, milliseconds), `--retries` (default `3`), `--download-host`. See [`install`](../cli/sync.md#install).

## `config <subcommand>`

No subcommand: interactive wizard (also `wizard`). `path`, `init`, `get KEY`, `set KEY VALUE`, `show` (`--format json|toml|yaml`, default toml), `edit`. These take `--path FILE` to work on a file other than the global config. See [The `config` command](../cli/configuration.md#the-config-command).

## `mcp serve [APP_PATH]`

See [Running an MCP Server](../agent/mcp-server.md) for the full flag set (`--tools`, `--source`, `--no-local`, `--no-live`, `--session-mode`, `--allow-write`, `--transport`, `--auth-token`, `--auth-provider`, `--require-scope`, `--rate-limit`, and more), dense enough to warrant its own page rather than a table here.
