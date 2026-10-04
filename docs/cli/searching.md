# Searching and Defining Terms

This page covers every lookup command. All of them share the [source model](index.md#the-source-model-local-live-auto) (`--local`/`--live`/`--auto`) and [caching behavior](index.md#caching-live-results-cache) (`--cache`) described on the previous page, so this page focuses on what makes each command different from the others.

---

## `search`

This command does a ranked search that forgives typos and stray punctuation. Give it a term, a partial phrase, or even a plain-English question, and it finds the closest matching definitions.

```bash
slb search "what is porosity"
```

`search` runs your query through the same text-cleanup step whether asked as a question ("what is porosity", "define porosity", "tell me about porosity") or as a bare term, so all four of these return the same thing:

```bash
slb search porosity
slb search "what is porosity"
slb search "define porosity"
slb search "tell me about porosity"
```

Matching ignores case, accents and punctuation too. `capillary-pressure`, `"Capillary pressure?"` and `capillary_pressure` all find "Capillary pressure", and a stray symbol on the edge of a query (`capillary-`, `:rig`) is simply dropped. A query with no letters or digits in it (`???`) finds nothing, and never opens a browser.

A matched term can carry more than one definition, one per topic it's filed under, so `search` can return more rows than `--limit` implies. `--limit` (default `3`) bounds how many *terms* are looked up, not how many definitions come back for them.

```bash
slb search "drilling fluid" --topic Drilling --limit 10
```

### Choosing how matching works

These flags apply to the local half of a search:

```bash
slb search "fluid moving through rock" --mode hybrid   # lexical + semantic, needs the semantic extra
slb search porosity --topic drilng --fuzzy             # tolerate a misspelled --topic
slb search porosity --exclude "Porosity log"           # leave a term (or a URL) out of the results
```

`--mode` is `lexical` (the default, works out of the box), `semantic`, or `hybrid`. The last two need the `semantic` extra and terms that have been embedded with [`local embed`](sync.md#the-local-command-group). See [Search modes](../concepts/search-modes.md). `--exclude` can be repeated or comma separated. `--concurrency` raises how many live lookups run at once.

### Choosing what columns show

`search`'s table always shows the term and its definition. Everything else is a toggle (`--show-image`, `--show-related` and the others are off or on depending on the command, so check `--help`):

```bash
slb search porosity --show-image --show-related   # add the image URL and related-terms columns
slb search porosity --hide-topic --no-url          # drop the topic and URL columns
```

### JSON output

```bash
slb search porosity --json
```

Prints the same result set as a JSON array instead of a table, useful for piping into `jq` or another script without going through a `--save` file at all.

---

## `define`

For when you already know the exact term name, or have its URL, and do not need `search`'s ranking:

```bash
slb define "water saturation"
slb define "https://glossary.slb.com/en/terms/p/porosity"
```

`define` reads locally first by default and only reaches the live site if there is no exact local match for the term. Similar terms that happen to be cached do not count as a match. Pass `--local` to stay offline, or `--live` to skip the local copy.

When there's no exact match, `define` offers a few similarly named terms instead of just saying nothing was found. In an interactive terminal you can pick one by number to see its definition.

```bash
slb define drilling                      # no exact "drilling"? offers close matches
slb define drilling --no-suggest         # just report that nothing was found
slb define drilling --max-similar 5      # offer up to 5 alternatives
```

If a term is filed under several topics, `--topic` picks which stored definition to show. It only affects a local read.

---

## `compare`

Looks up two or more terms and prints their definitions side by side, fetched concurrently rather than one at a time:

```bash
slb compare "water flooding" "gas flooding"
slb compare porosity permeability --local
```

A term `compare` can not find under the resolved source is skipped, with a note printed to stderr rather than the whole command failing. Raise `--concurrency` if you are comparing a long list and want them fetched in parallel:

```bash
slb compare shale sandstone limestone dolomite --concurrency 4
```

---

## `related`

Lists the terms related to the term you give it, as a table of names and URLs. The related terms are the ones that appear as links in the definition's text, so this is a way to explore the glossary's internal network of definitions:

```bash
slb related "water saturation"
```

This is the CLI path into the `related` field on `SearchResult`. See [The Data Model](../concepts/data-model.md#relatedterm) for what that field actually contains.

---

## `terms`

Fetches every term filed under one topic, rather than searching for a specific word:

```bash
slb terms Geophysics
```

`TOPIC` does not need to be an exact match for a live read. The closest topic(s) the glossary actually has are used, so `slb terms drill fluids` still finds "Drilling Fluids" and any other topic containing that word. For a local read, add `--fuzzy` to get the same forgiveness; otherwise the topic has to match a stored one (ignoring case). Unlike `search`, `terms` returns at most one result per term, the one definition filed under the topic you asked for, not every definition that term happens to have across other topics too.

```bash
slb terms "Well Completions" --limit 20
slb terms Drilling --start-letter p
```

!!! warning "`terms` fetches the whole topic unless you set `--limit`"
    `--limit` defaults to `0`, which means every term under the topic. Some topics carry hundreds of terms, and a live read visits one page per term, so a large topic that isn't cached yet is a lot of page loads. Pass `--limit` to cap it. If what you actually want is to build up the local cache for a topic over time, use [`sync --topic`](sync.md#sync) instead. It has the same filters, plus a confirmation prompt before anything heavy.

    With `--auto`, a topic the local database already holds *any* terms for is served from there alone, so a partly cached topic comes back partly. Use `--live` for the full list.

---

## `random`

For "term of the day"-style browsing:

```bash
slb random
slb random --topic Drilling --count 5
```

With `--live` (or `--auto` falling back to it), `random` samples a random detail page, since the live site itself has no dedicated random-term endpoint to call.

---

## `topics`

```bash
slb topics list
```

Lists every topic the glossary is organized under, with a term count for each. With `--auto` (the default), a database that already has cached terms lists only the topics actually present there; the live site is only visited if the local database is empty.

---

## `urls`

The two commands under `urls` are the lowest-level lookups this CLI offers, for when you want the raw glossary URLs themselves rather than parsed definitions.

```bash
slb urls list --topic Geophysics --limit 5
```

`urls list` needs at least one of `--query`, `--topic`, or `--start-letter` to know what to list.

```bash
slb urls fetch "https://glossary.slb.com/en/terms/p/porosity"
```

`urls fetch` parses every definition found on one specific detail page URL directly, skipping search or topic matching entirely. This is what `define` uses internally when you pass it a URL instead of a term name.

---

## Where to go from here

Every command on this page can save what it finds instead of, or in addition to, printing it. See [Saving, Output and Config Files](configuration.md). For working offline on purpose, building up the local cache ahead of time, or managing that cache directly, see [Local Cache and Sync](sync.md). For the full flag list of any command shown here, see [CLI Commands](../api/cli.md), or just run it with `--help`.
