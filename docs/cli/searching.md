# Searching and Defining Terms

Audience: same as the rest of this section, anyone using `slb` from a terminal. This page covers every lookup command. All of them share the [source model](index.md#the-source-model-local-live-auto) (`--local`/`--live`/`--auto`) and [caching behavior](index.md#caching-live-results-cache) (`--cache`) described on the previous page, so this page focuses on what makes each command different from the others.

---

## `search`

Ranked, fuzzy-ish search: give it a term, a partial phrase, or even a plain-English question, and it finds the closest matching definitions.

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

A matched term can carry more than one definition, one per topic it's filed under, so `search` can return more rows than `--limit` implies: `--limit` bounds how many *terms* are looked up, not how many definitions come back for them.

```bash
slb search "drilling fluid" --topic Drilling --limit 10
```

### Choosing what columns show

`search`'s table always shows the term and its definition. Everything else is a toggle:

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

For when you already know the exact term name, or have its URL, and don't need `search`'s ranking:

```bash
slb define "water saturation"
slb define "https://glossary.slb.com/en/terms/p/porosity"
```

`define` reads locally first by default and only reaches the live site if the term isn't cached yet, same as every other lookup command's `--auto`.

---

## `compare`

Looks up two or more terms and prints their definitions side by side, fetched concurrently rather than one at a time:

```bash
slb compare "water flooding" "gas flooding"
slb compare porosity permeability --local
```

A term `compare` can't find under the resolved source is skipped, with a note printed to stderr rather than the whole command failing. Raise `--concurrency` if you're comparing a long list and want them fetched in parallel:

```bash
slb compare shale sandstone limestone dolomite --concurrency 4
```

---

## `related`

Lists the terms one definition's own "related terms" section links to, without printing the full definition text itself:

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

`TOPIC` doesn't need to be an exact match. The closest topic(s) the glossary actually has are used, so `slb terms drilling` still finds "Drilling Fluids" and any other topic containing that word. Unlike `search`, `terms` returns at most one result per term: the one definition filed under the topic you asked for, not every definition that term happens to have across other topics too.

```bash
slb terms "Well Completions" --limit 20
slb terms Drilling --start-letter p
```

!!! warning "`--limit 0` fetches the whole topic"
    Some topics carry hundreds of terms. `--limit 0` (unlimited) on a large topic means a correspondingly large number of live page visits if nothing is cached yet. Reach for [`sync`](sync.md#sync) instead if what you actually want is to build up the local cache for a topic over time; it has the same filters, plus a confirmation prompt before anything heavy.

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

Every command on this page can save what it finds instead of, or in addition to, printing it: see [Saving, Output and Config Files](configuration.md). For working offline on purpose, building up the local cache ahead of time, or managing that cache directly, see [Local Cache and Sync](sync.md). For the full flag list of any command shown here, see [CLI Commands](../api/cli.md), or just run it with `--help`.
