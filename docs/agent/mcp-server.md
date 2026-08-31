# Running an MCP Server

Audience: anyone connecting an AI agent (Claude Desktop, Claude Code, a custom agent) to this glossary, whether or not you're writing Python. Needs the `mcp` extra: `uv add "slb-glossary[mcp]"` for library use, or the CLI already has it if you installed with `[all]`.

[MCP](https://modelcontextprotocol.io/) (Model Context Protocol) is the open standard this server speaks: an agent connects to it, sees a list of tools, and calls them the same way it would call any other tool. Everything here is a thin layer over exactly the [`slb_glossary.query`](../library/query.md) functions already covered elsewhere in this documentation — the server doesn't reimplement any lookup logic of its own.

---

## The fastest path: the CLI

```bash
slb mcp serve
```

This starts a server over `stdio` (the default transport — reading/writing MCP messages over standard input/output, the way an agent's own process expects when it launches this as a subprocess), with a sensible default tool set and no local-database writes allowed. That's enough to point an MCP client at right away.

### Connecting Claude Desktop or Claude Code

Add this to your MCP client's config (Claude Desktop's `claude_desktop_config.json`, or Claude Code's `.mcp.json`):

```json
{
  "mcpServers": {
    "slb-glossary": {
      "command": "slb",
      "args": ["mcp", "serve"]
    }
  }
}
```

The client launches `slb mcp serve` itself as a subprocess and talks to it over stdio; there's no separate "start the server first" step for this transport.

### Serving over HTTP instead

For a server other machines (or a remote agent) connect to over the network rather than one launched as a local subprocess:

```bash
slb mcp serve --transport http --host 0.0.0.0 --port 8000
```

---

## Choosing which tools are exposed

```bash
slb mcp serve --tools search,get_term          # only these two
slb mcp serve --tools read_only                # the default: every non-writing tool
slb mcp serve --tools all                       # read_only, plus glossary_sync
```

Every tool corresponds directly to a `slb_glossary.query` function, prefixed `glossary_`: `glossary_search`, `glossary_get_term`, `glossary_get_terms_on`, `glossary_get_terms_urls`, `glossary_get_topics`, `glossary_related_terms`, `glossary_random_term`, `glossary_compare`, and `glossary_sync`. `read_only` is every one of these except `glossary_sync`, which is the only tool that ever writes to the local database.

!!! warning "`glossary_sync` needs `--allow-write` too, even under `--tools all`"
    `--allow-write` is a separate switch from `--tools`, off by default. With it off, `glossary_sync` is never registered regardless of `--tools`, and every read tool's `persist` argument is silently ignored rather than actually caching anything. This is a deliberate two-key lock: an agent that can only read the glossary can't accidentally (or be prompted to) write to your local database, even if it's given the full tool list.

    ```bash
    slb mcp serve --tools all --allow-write
    ```

## Restricting which source an agent can reach

```bash
slb mcp serve --no-live                # local-only server: no browser, no network per call
slb mcp serve --no-local                # live-only: never reads the cache
slb mcp serve --source local --source live   # both allowed; can still be requested per call
```

`--no-local`/`--no-live` are the blunt instrument. For finer control — letting an agent choose `source` per call, but only from a narrower set than the server could technically support — see `SourcePolicy` in [Embedding the server yourself](#embedding-the-server-in-your-own-python-app).

## Locking it down for anything beyond local, trusted use

```bash
slb mcp serve --transport http --auth-token "a-long-random-token"
slb mcp serve --transport http --auth-token "token123:my-client-id"
slb mcp serve --rate-limit 30 --rate-limit-window 60
```

`--auth-token` accepts a bare token, or a `token:client_id` pair, checked by FastMCP's own auth layer; give it more than once for multiple valid callers. `--rate-limit` caps requests per client per tool per window, with `--rate-limit-algorithm` choosing between `sliding_window` (no burst allowed above the limit) and `token_bucket` (short bursts tolerated). Both matter far more once you're serving over `http`/`sse` to something other than a single trusted local agent than they do over `stdio`.

---

## Embedding the server in your own Python app

For anything the CLI's flags don't cover — a custom `AuthProvider` that needs constructor arguments, wiring the server into an existing FastAPI app's lifespan, or just preferring config-as-code — build the `MCPApp` directly:

```python
from slb_glossary.mcp.config import MCPConfig, LocalAccess, SessionAccess, Tool

config = MCPConfig(
    tools=Tool.ALL,
    local=LocalAccess(allow_write=True),
    session=SessionAccess(enabled=True),
)

from slb_glossary.mcp.api import MCPApp

app = MCPApp(config)
app.run()   # or app.run_async() inside an existing event loop
```

`MCPApp(config)` is cheap and does no I/O; the underlying `fastmcp.FastMCP` server and its tools are only assembled on the first `server()`/`run()`/`run_async()` call. `MCPConfig()` alone (no arguments) is a fully valid default: read-only, local and live both enabled, unauthenticated, unlimited rate — exactly what `slb mcp serve` with no flags gives you.

### Loading an app this way from the CLI

```bash
slb mcp serve app.main:app
```

`app.main:app` is a uvicorn-style import path: `app/main.py` containing a module-level `app = MCPApp(...)` (or a zero-argument factory function returning one). When `APP_PATH` is given this way, every flag except `--transport`/`--host`/`--port`/`--log-level` is ignored, since the app is already fully configured in code; passing one of the ignored flags alongside `APP_PATH` is an error, specifically so you can't accidentally think a flag did something it didn't.

---

## Where to go from here

For a worked example connecting this server to an actual agent framework, see [Building an Agent with Pydantic AI](pydantic-ai.md). For the full config surface (`SessionAccess`, `LocalAccess`, `SourcePolicy`, `ServerInfo`), see [`slb_glossary.mcp.config`](../api/library.md#slb_glossarymcp).
