# Local Cache and Sync

This page covers working with the local database deliberately, rather than letting `--cache` fill it in as a side effect of ordinary searches.

---

## `sync`

`sync` does two things in one command. It makes sure the background browser is installed, and then updates the local database.

```bash
slb sync
```

Run with no flags, this checks whether the browser family `--browser-type` would launch (Chromium, by default) is actually installed, since a missing browser is the most common reason a fresh install's first search fails. If it's missing, `sync` reports that and tells you what to run, rather than silently trying and failing partway through a fetch. Pass `--install` to have it install the browser itself instead of just telling you to:

```bash
slb sync --install
```

Once a browser is confirmed available, `sync` updates the local database. With no filters, that only refreshes the topic list and the term counts. It does not fetch any terms. To fetch terms, give it a filter. This is a standalone version of the caching that `search`/`terms`/etc. already do automatically:

```bash
slb sync --topic "Drilling Fluids"
slb sync --query porosity
slb sync --start-letter p
```

```bash
slb sync --check-only   # only report the browser state, do not touch the database at all
```

A few flags control how the fetch itself runs:

```bash
slb sync --topic Drilling --limit 50       # stop after 50 terms (the default is every matching term)
slb sync --topic Drilling --concurrency 2  # fetch 2 term pages at once. Keep this low, the site is not yours
slb sync --topic Drilling --force          # re-fetch terms you already have, to pick up changed definitions
slb sync --topic Drilling --batch-size 10  # save progress every 10 terms instead of every 20
```

Terms that are already stored are skipped unless you pass `--force`. If a fetch fails partway through, what was already fetched is saved (`--persist-on-error`, the default). Pass `--no-persist-on-error` to throw it away instead. `--with-deps` goes with `--install` and also installs the operating system packages the browser needs (Linux only).
!!! warning "`--all` updates the entire glossary"
    `sync --all` visits every single term page in the glossary, in whichever language `--language` is set to. This is the heaviest option this CLI has, and asks for confirmation before running unless you pass `-y`/`--yes`. It can't be combined with `--topic`, `--query` or `--start-letter`. Reach for a scoped `--topic`/`--query` sync instead unless you specifically want the whole thing cached.

---

## The `local` command group

Every command under `local` talks only to the database on your own machine, regardless of any `--local`/`--live`/`--auto` flag elsewhere. There's no live fallback here even if you ask for one.

### Where is it?

```bash
slb local path
```

```text
Database: /home/you/.local/share/slb-glossary/glossary.db
Metadata: /home/you/.local/share/slb-glossary/metadata.json
(WAL sidecar files, if present: /home/you/.local/share/slb-glossary/glossary.db-wal, /home/you/.local/share/slb-glossary/glossary.db-shm. Move/copy these together with the database above, and metadata separately; see `slb-glossary local path --help`.)
```

!!! note "Back it up carefully"
    The database runs in WAL mode, meaning a write can leave `-wal`/`-shm` sidecar files next to the main `.db` file that have not been folded in yet. If you copy or back up the file by hand, bring those sidecars along too, or close everything using the database first so SQLite folds them back into the main file before you copy it.

### Checking what's there

```bash
slb local stats          # add --json for machine-readable output
```

```text
Terms stored locally: 412
Last synced: 2026-08-14T09:12:03
Topics (6):
  Drilling Fluids                          128
  Well Completions                         96
  ...
```

```bash
slb local search "reservoir rock" --mode hybrid   # needs the `semantic` extra; see Search Modes
slb local get porosity --topic Petrophysics
```

`local search` and `local get` are the CLI's most direct path into what [Search Modes](../concepts/search-modes.md) covers: `local search` supports `--mode lexical|semantic|hybrid`, exactly like `search --local` does, since both go through the same underlying function. Unlike `search`, `local search` returns up to `20` results by default, and also takes `--min-similarity`, `--exclude`, `--start-letter` and `--fuzzy` (for `--topic`). `local get` offers close matches when there's no exact one, the same way `define` does (`--no-suggest` turns that off).

### Embedding for semantic/hybrid search

`--mode semantic`/`--mode hybrid` only ever search terms that already have a stored embedding. A term you just synced or imported is invisible to them until you run `local embed`:

```bash
slb local embed
```

By default this embeds every locally stored row that does not have an embedding yet, so running it again after a `sync`/`local import` only pays for what's actually new:

```bash
slb sync --topic "Drilling Fluids"
slb local embed              # embeds just the newly-synced rows
slb local search "mud weight" --mode hybrid
```

```bash
slb local embed --urls "https://glossary.slb.com/en/terms/p/porosity,https://glossary.slb.com/en/terms/m/mud-weight"   # only these rows
slb local embed --reembed    # recompute every embedding in scope, e.g. after switching SLB_GLOSSARY_EMBEDDING_MODEL
slb local embed --batch-size 200
```

`--topic` limits it to one topic (or several, comma separated), and `--fuzzy` forgives a misspelled one. To drop embeddings without touching the terms themselves, use `local delete-embeddings`. Terms stay searchable with `--mode lexical`, and only `semantic` and `hybrid` lose them until the next `local embed`:

```bash
slb local delete-embeddings --yes
slb local delete-embeddings --urls "https://glossary.slb.com/en/terms/p/porosity"
```

Needs the `semantic` extra installed (`uv add "slb-glossary[semantic]"`). The embedding model itself downloads once from Hugging Face and is cached locally after that. See [Search Modes](../concepts/search-modes.md#semantic-matching-meaning) for what it costs to run and why the first `local embed` on a large database takes noticeably longer than later ones.

### Clearing it out

```bash
slb local flush --yes    # delete every stored term, keep sync history
slb local reset --yes    # delete stored terms AND forget sync history
```

`flush` is what you want if you just want a clean slate of terms without losing the record of what's been synced before; `reset` is a harder wipe, useful mainly for troubleshooting a database that's gotten into a confusing state.

### Exporting what's there

```bash
slb local export --save all_terms.json                          # the whole database
slb local export --topic "Well Completions" --save completions.csv
slb local export --query "flooding" --save flooding_terms.xlsx  # ranked by relevance
```

### Importing your own data

`local import` fills the database from a file you already have, rather than from the live glossary, useful for seeding it with an internal wordlist or a dataset from somewhere else entirely:

```bash
slb local import terms.csv
```

By default this expects a `term` column and a `definition` column, matched case-insensitively; every other field (`topic`, `url`, `grammatical_label`, `image`, `related`, ...) is optional. If your file uses different column names, tell `import` where to look instead of renaming your file:

```bash
slb local import terms.xlsx --topic-field Category --url-field ""
```

`--url-field ""` tells it to synthesize a `local://imported/<term-slug>` URL for each row instead of expecting one in the file. This matters because a row's URL and topic together are the local database's primary key, so `import` needs *something* there to know whether a given row is new or an update to an existing one.

```bash
slb local import terms.json --source-tag internal-wordlist
```

`--source-tag` marks imported rows with where they came from, so you can later tell an imported row apart from one fetched from the live glossary (tagged `glossary`). Imported rows are tagged `user` unless you say otherwise.

---

## `install`

Manages the background browser build itself, what `sync --install` calls under the hood, and the same command [Installation](../getting-started/installation.md#installing-the-browser-build) walks through the first time.

```bash
slb install                    # install patchright's default browser set
slb install firefox webkit     # install specific browsers
slb install --list             # what's installed right now
slb install --update chromium  # reinstall/refresh chromium specifically
slb install --remove firefox   # remove a browser you no longer need
```

`--list`, `--update` and `--remove` are plain flags, and `BROWSERS` (the positional `chromium`/`firefox`/`webkit` names above) is optional. Omitted, `install` installs/updates patchright's default browser set, or lists/removes every installed browser, depending on which flag you paired it with. `--force` reinstalls a browser even if it's already there. `--with-deps` also installs the OS-level packages a browser needs to actually run (Linux only), and `--only-shell` installs Chromium's smaller headless-shell build instead of the full browser, if you never run headed.

The engine underneath is [patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python), a stealth-hardened fork of Playwright, not vanilla Playwright itself; see [Sessions and the Browser](../concepts/sessions.md) for why that distinction matters for a scraping tool like this one. `install`'s download-related flags reuse Playwright's own environment variables under the hood (`PLAYWRIGHT_DOWNLOAD_CONNECTION_TIMEOUT`, `PLAYWRIGHT_DOWNLOAD_HOST`), since patchright is a drop-in fork:

```bash
slb install --timeout 120000 --retries 5                              # slow connection
slb install --download-host https://playwright.download.prss.microsoft.com  # a mirror
```

---

## Where to go from here

For the config file that lets you set defaults like `--db-path` or `--browser-type` once instead of retyping them on every command, see [Saving, Output and Config Files](configuration.md). For the same local-cache model from Python instead of the terminal, see [Local Search and Cache](../library/local-search.md).
