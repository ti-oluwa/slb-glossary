# Changelog

## Unreleased

- `Runtime` and `SessionPool` now live in `slb_glossary.live` (also importable from `slb_glossary`) for any app to use, not just the MCP server. Everything MCP-specific became an init argument, and `MCPApp` accepts a shared `runtime=`. `slb_glossary.mcp.Runtime` and `SessionMode` still work as re-exports.
- Fixed a stall where, with the default `max_sessions=1`, a call that found the only session full waited for the idle timeout (5 minutes by default). It now shares that session.
- Fixed a browser leak where a pool dropped by the idle reaper could still receive a session that nothing would ever close.
- Fixed the idle reaper stopping for good after one failed close, and `Runtime.close()` skipping the database and remaining pools when one pool failed to close.
- A session whose browser has crashed is now replaced instead of being handed out again.
- Switching glossary language under a full browser budget now closes the other language's idle session instead of waiting for it to time out.
- New: `Runtime.session()`, `Runtime.stats()`, `Runtime.from_config()`, `SessionPool.checkout()`, `SessionPool.stats()`, and `Runtime` as an async context manager.
- Runtime and pool errors now use library exception types (`ResourceError`, `SessionPoolError`, and subclasses) instead of `MCPError` and plain `RuntimeError`. The MCP server still reports them to clients as `MCPError`.
- Local search ignores symbols, hyphens, accents and simple plurals.

## 0.1.0

First proper release, moving past the initial `0.0.1-beta` beta. Here's what it gives you:

- Search the SLB Energy Glossary live, or keep a local SQLite cache and search that instead, with lexical, semantic, and hybrid ranking modes.
- A combined query layer that checks the local cache first and falls back to a live search only when needed, caching what it finds along the way.
- A full CLI covering search, sync, local database management, config, and browser install, plus an interactive TUI mode.
- An MCP server so an LLM agent can search the glossary directly, with auth, rate limiting, and configurable read/write access.
- CSV, JSON, and XLSX import/export for the local database.
- Support for both English and Spanish glossary editions.

See the [README](README.md) for a quick tour, or the [docs site](https://ti-oluwa.github.io/slb-glossary/) for the full reference.
