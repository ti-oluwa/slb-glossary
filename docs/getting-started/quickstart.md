# Quickstart

A five-minute tour before the longer tutorials. By the end of this page you'll have looked up a term, cached it locally, and seen where the rest of this documentation branches off to.

This page assumes you've finished [Installation](installation.md), including the browser build step.

---

## Look something up

```bash
slb search "water saturation"
```

You'll see a table with the term, its grammatical label, the topic it's filed under, and its definition. If more than one row comes back, that's not a bug: the same term can carry a different definition under each topic it's filed under, and `search` shows all of them.

## Look up one exact term

If you already know the exact name, `define` skips the search-and-rank step and goes straight to the term:

```bash
slb define "black oil"
```

## Compare two terms side by side

```bash
slb compare "water flooding" "gas flooding"
```

## Cache what you looked up

Every command above talked to the live glossary. Add `--cache` (the default, so you don't even need to type it) and the result is also written to a local SQLite database:

```bash
slb search porosity   # cached automatically
```

Run it again, and you'll notice it comes back faster: `search` reads the local copy first by default, and only reaches the live site if nothing local is confident enough to answer.

```bash
slb local stats
```

```text
Terms stored locally: 1
Last synced: 2026-08-30T12:03:41
Topics (1):
  Petrophysics                             1
```

## See what else is there

That's the shape of the whole tool: search, define, compare, and a local cache underneath all of them so repeat lookups are fast and offline-friendly. From here:

<div class="grid cards" markdown>

- :material-console: **Using the CLI**

    ---

    Every lookup command, the full source/cache model, and how to browse a whole topic at once.

    [Using the CLI](../cli/index.md){ .md-button }

- :material-language-python: **Using the library**

    ---

    The same capabilities from your own async Python code, one layer at a time.

    [Using the library](../library/index.md){ .md-button }

- :material-robot: **Connecting an AI agent**

    ---

    Run this as an MCP server so an agent can look terms up on its own.

    [Connecting an AI agent](../agent/mcp-server.md){ .md-button }

</div>
