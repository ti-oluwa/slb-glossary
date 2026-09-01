# CLI Commands

A dense, structural reference for every `slb` command and flag. For explanations and worked examples, see [Using the CLI](../cli/index.md). Run any command with `--help` for this same information from the terminal.

Most commands share three groups of flags: **source** (where to read from), **session** (how the browser behaves, for a command that might touch the live site), and **output** (saving/printing). They're documented once here, then referenced by name in each command's own table.

---

## Shared flag groups

### Source flags

Every lookup command (`search`, `define`, `compare`, `related`, `terms`, `random`, `topics`, `urls list`) has these:

| Flag | Default | Meaning |
|---|---|---|
| `--source [local\|live\|auto]` | `auto` | Which source(s) to read from, spelled out. |
| `--local` | | Shorthand for `--source local`. |
| `--live` | | Shorthand for `--source live`. |
| `--auto` | | Shorthand for `--source auto`. |
| `--cache` / `--no-cache` | `--cache` | Save live results to the local database as they arrive. |
| `--cache-batch-size INTEGER` | `20` | Live results buffered per incremental write. |
| `--cache-on-error` / `--no-cache-on-error` | `--cache-on-error` | Keep partial progress if a live fetch fails midway. |
| `--exclude URL_OR_TERM[,...]` | | Skip specific URLs/terms. Repeatable and comma-listable. |
| `-m, --mode [lexical\|semantic\|hybrid]` | `constants.default_search_mode` | Local ranking strategy. No effect on `--live`. |
| `--fuzzy` | off | Tolerate misspellings in `--topic` against locally stored topic names. |

`search` additionally has `--relevance-threshold FLOAT` (default `0.45`) and `--annotate [auto\|always\|never]` (default `auto`), since it's the one command where `--auto` genuinely blends local and live results rather than picking one.

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
| `--max-pages INTEGER` | `6` | Browser pages the session keeps open at once. |
| `--settle-timeout FLOAT` | `8000` | Milliseconds to wait for the results list to settle. |
| `--poll-interval FLOAT` | `300` | Poll interval while waiting on `--settle-timeout`. |
| `--executable-path FILE` | | Specific browser build to launch. |
| `--proxy SERVER[,username=U][,password=P]` | | Proxy for the browser. |
| `--viewport WIDTHxHEIGHT` | full-screen | Browser viewport size. |
| `--stealth` / `--no-stealth` | auto (see [Sessions and the Browser](../concepts/sessions.md)) | Apply stealth patches. |
| `--initialize` / `--no-initialize` | auto | Load topics/size as soon as the session opens. |
| `--retry-attempts INTEGER` | `3` | Max attempts retrying a flaky initial load. |
| `--retry-base-delay FLOAT` | `0.8` | Base delay (seconds) for retry backoff. |
| `--retry-backoff [constant\|linear\|exponential\|logarithmic]` | `exponential` | Retry delay growth strategy. |
| `--retry-factor FLOAT` | `2.0` | Growth base (exponential) or log base (logarithmic). |
| `--retry-max-delay FLOAT` | `10.0` | Upper bound on any single retry delay. |
| `--retry-jitter` / `--no-retry-jitter` | `--retry-jitter` | Randomize retry delays ±50% to avoid retry storms. |
| `--concurrency INTEGER` | `1` (`compare`: from `constants.compare_concurrency`) | Concurrent term lookups. |

### Output flags

Every command that produces results shares this block:

| Flag | Default | Meaning |
|---|---|---|
| `-o, --save FILE` | | Save results to a file. Repeatable. |
| `-f, --format TEXT` | inferred from extension | Override the save format. |
| `--json` | off | Print as JSON instead of a table. Ignored with `--quiet`. |
| `-q, --quiet` | off | Don't print to the console. |
| `--tui` | off | Open this command in the interactive TUI instead. |

`search` additionally has `--url`/`--no-url`, `--show-topic`/`--hide-topic`, `--show-grammar`/`--hide-grammar`, `--show-image`/`--hide-image`, `--show-related`/`--hide-related` for column visibility.

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

Own flags: `-t/--topic`, `-a/--start-letter`, `-n/--limit` (default `3`, `0` for unlimited), plus every source, session, output, and global flag above, including the `--relevance-threshold`/`--annotate` pair that's unique to `search`.

## `define [TERM]`

`TERM`: an exact term name, or a detail-page URL. Own flags: `-t/--topic` (pick a specific stored definition for a term/URL with several). Source, session, output, global flags apply.

## `compare [TERMS]...`

Two or more terms, looked up concurrently. Terms not found by the resolved source are skipped with a note on stderr rather than failing the whole command. Own flags: `-t/--topic`, `--concurrency` (default from `constants.compare_concurrency`).

## `related [TERM]`

Lists just the related-term links, not the full definition. Own flags: `-t/--topic`. Otherwise identical flag surface to `define`.

## `terms [TOPIC]`

`TOPIC` need not be exact, the closest known topic is used. Yields at most one result per term (the one filed under `TOPIC`), unlike `search`. Own flags: `-a/--start-letter`, `-n/--limit` (default `20`, `0` for unlimited).

## `random`

Own flags: `-t/--topic`, `-n/--count` (default `1`; duplicates possible since each pick is independent).

## `topics list`

Lists the glossary's topics with term counts. With `--auto`, only lists topics actually present locally if the database is non-empty; visits live otherwise.

## `urls list` / `urls fetch <URL>`

`urls list` needs at least one of `--query`, `--topic`, `--start-letter`. `urls fetch` parses every definition on one specific detail-page URL directly. See [Searching and Defining Terms](../cli/searching.md#urls).

## `sync`

Own flags: `-t/--topic`, `-Q/--query`, `-a/--start-letter`, `--all`, `--install`, `--check-only`, `-y/--yes` (skip the `--all` confirmation). See [`sync`](../cli/sync.md#sync).

## `local <subcommand>`

`path`, `stats`, `search`, `get`, `flush`, `reset`, `export`, `import`, `embed`. Never falls back to live regardless of any source flag. See [The `local` command group](../cli/sync.md#the-local-command-group) for each subcommand's own options, `import` in particular has a large, distinct `--*-field` flag set for column mapping, and `embed` needs the `semantic` extra installed.

## `install`

Own flags: `--list`, `--update BROWSER`, `--remove BROWSER`, `--timeout` (download timeout, milliseconds), `--retries`, `--download-host`. See [`install`](../cli/sync.md#install).

## `config <subcommand>`

No subcommand: interactive wizard. `path`, `init`, `get KEY`, `set KEY VALUE`, `show`, `edit`. See [The `config` command](../cli/configuration.md#the-config-command).

## `mcp serve [APP_PATH]`

See [Running an MCP Server](../agent/mcp-server.md) for the full flag set (`--tools`, `--source`, `--no-local`, `--no-live`, `--allow-write`, `--transport`, `--auth-token`, `--rate-limit`, and more), dense enough to warrant its own page rather than a table here.
