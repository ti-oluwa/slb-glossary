# Changelog

## 0.1.0

First proper release, moving past the initial `0.0.1-beta` beta. Here's what it gives you:

- Search the SLB Energy Glossary live, or keep a local SQLite cache and search that instead, with lexical, semantic, and hybrid ranking modes.
- A combined query layer that checks the local cache first and falls back to a live search only when needed, caching what it finds along the way.
- A full CLI covering search, sync, local database management, config, and browser install, plus an interactive TUI mode.
- An MCP server so an LLM agent can search the glossary directly, with auth, rate limiting, and configurable read/write access.
- CSV, JSON, and XLSX import/export for the local database.
- Support for both English and Spanish glossary editions.

See the [README](README.md) for a quick tour, or the [docs site](https://ti-oluwa.github.io/slb-glossary/) for the full reference.
