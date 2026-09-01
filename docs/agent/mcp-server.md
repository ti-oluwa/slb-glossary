# Running an MCP Server

Connecting an AI agent (Claude Desktop, Claude Code, a custom agent) to this glossary, whether or not you're writing Python. Needs the `mcp` extra: `uv add "slb-glossary[mcp]"` for library use, or the CLI already has it if you installed with `[all]`.

[MCP](https://modelcontextprotocol.io/) (Model Context Protocol) is the open standard this server speaks: an agent connects to it, sees a list of tools, and calls them the same way it would call any other tool. Everything here is a thin layer over exactly the [`slb_glossary.query`](../library/query.md) functions already covered elsewhere in this documentation, the server doesn't reimplement any lookup logic of its own.

---

## The fastest path: the CLI

```bash
slb mcp serve
```

This starts a server over `stdio` (the default transport, reading/writing MCP messages over standard input/output, the way an agent's own process expects when it launches this as a subprocess), with a sensible default tool set and no local-database writes allowed. That's enough to point an MCP client at right away.

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

`--no-local`/`--no-live` are the blunt instrument, each toggling `session.enabled`/`local.enabled` on the underlying `MCPConfig`. For finer control (letting an agent choose `source` per call, but only from a narrower set than the server could technically support, or hiding the choice from the tool schema entirely), build `SourcePolicy` directly:

```python
import slb_glossary.mcp as slb_mcp
from slb_glossary import Source

config = slb_mcp.MCPConfig(
    source_policy=slb_mcp.SourcePolicy(
        allowed=frozenset({Source.LOCAL, Source.AUTO}),  # never let a call force a live fetch
        default=Source.AUTO,
        expose_choice=True,  # False hides the `source` argument from every tool's schema
    ),
)
```

Leaving `allowed` unset computes it automatically from `session.enabled`/`local.enabled`: both enabled allows all three (`LOCAL`/`LIVE`/`AUTO`), either alone restricts to just that one plus `AUTO`.

## Locking it down for anything beyond local, trusted use

```bash
slb mcp serve --transport http --auth-token "a-long-random-token"
slb mcp serve --transport http --auth-token "token123:my-client-id"
slb mcp serve --rate-limit 30 --rate-limit-window 60
```

`--auth-token` accepts a bare token, or a `token:client_id` pair, checked by FastMCP's own auth layer; give it more than once for multiple valid callers. `--rate-limit` caps requests per client per tool per window, with `--rate-limit-algorithm` choosing between `sliding_window` (no burst allowed above the limit) and `token_bucket` (short bursts tolerated). Both matter far more once you're serving over `http`/`sse` to something other than a single trusted local agent than they do over `stdio`.

---

## Embedding the server in your own Python app

The CLI's flags cover the common cases. `MCPConfig` itself is considerably deeper (per-tool timeouts, lifecycle/per-call hooks, structured logging sinks, progress streaming, and a real `AuthProvider` rather than a bare token), and building it directly in code is how you reach the rest of it:

```python
import slb_glossary as slb
import slb_glossary.mcp as slb_mcp

config = slb_mcp.MCPConfig(
    server=slb_mcp.ServerInfo(name="my-glossary-mcp", version="1.0.0"),
    session=slb_mcp.SessionAccess(
        enabled=True,
        mode=slb_mcp.SessionMode.LAZY,  # open the shared browser on first use, not at startup
        max_concurrent=3,
        options=slb.config.SessionOptions(use_stealth=False),
    ),
    local=slb_mcp.LocalAccess(allow_write=True),
    tools=slb_mcp.Tool.ALL,
    timeouts=slb_mcp.Timeout(default=60.0, per_tool={"glossary_sync": 300.0}),
    rate_limit=slb_mcp.RateLimit(enabled=True, limit=30, window=60.0),
    streaming=slb_mcp.Streaming(default=True),
    logging=slb_mcp.Logging(
        sinks=[slb.log.FileSink("./mcp.log"), slb.log.StderrSink()],
        level="info",
    ),
)
app = slb_mcp.MCPApp(config)

if __name__ == "__main__":
    app.run(transport="streamable-http")
```

`MCPApp(config)` is cheap and does no I/O; the underlying `fastmcp.FastMCP` server and its tools are only assembled on the first `server()`/`run()`/`run_async()` call. `MCPConfig()` alone (no arguments) is a fully valid default: read-only, local and live both enabled, unauthenticated, unlimited rate, `SessionMode.LAZY` - exactly what `slb mcp serve` with no flags gives you. Every section is independently optional; the CLI's own flags (`--tools`, `--allow-write`, `--rate-limit`, ...) each set one narrow slice of this same config for you.

A few fields worth knowing about that the CLI has no flag for at all:

- **`session.mode`** (`SessionMode.EAGER`/`LAZY`/`PER_CALL`): when the shared browser session is opened. `LAZY` (the default) opens nothing until the first call that needs it; `PER_CALL` opens and closes a fresh session for every call needing one, for full isolation under multi-tenant auth.
- **`timeouts.per_tool`**: a per-tool override map, since a `glossary_sync` call over a large topic legitimately needs longer than a `glossary_get_term` call.
- **`hooks`** (`Hooks(before_tool=..., after_tool=..., on_error=..., on_startup=..., on_shutdown=...)`): run your own code around every tool call or around server startup/shutdown, without subclassing anything.
- **`logging`**: routes `slb_glossary`'s own logging (the same sinks/levels covered in [Saving, Output and Config Files](../cli/configuration.md)) for this server process specifically, separate from whatever logging your surrounding app already has configured.

See [`slb_glossary.mcp`](../api/library.md#slb_glossarymcp) for the full field list of every one of these.

### A real `AuthProvider`, not just a bare token

The CLI's `--auth-token` is a convenience for `slb_mcp.StaticTokenVerifier`. For anything past a handful of fixed keys, build a real `AuthProvider` (FastMCP's own auth abstraction) and pass it as `auth.provider`:

```python
config = slb_mcp.MCPConfig(
    auth=slb_mcp.Auth(
        provider=slb_mcp.StaticTokenVerifier(
            {"a-long-token": "client-a", "another-token": "client-b"}
        ),
        required_scopes=(),
    ),
)
```

`slb_mcp.import_provider("myapp.auth:build_provider")` loads one from a dotted path instead, if you'd rather keep the provider construction elsewhere in your codebase.

### Loading an app this way from the CLI

```bash
slb mcp serve app.main:app
```

`app.main:app` is a uvicorn-style import path: `app/main.py` containing a module-level `app = MCPApp(...)` (or a zero-argument factory function returning one). When `APP_PATH` is given this way, every flag except `--transport`/`--host`/`--port`/`--log-level` is ignored, since the app is already fully configured in code; passing one of the ignored flags alongside `APP_PATH` is an error, specifically so you can't accidentally think a flag did something it didn't.

## Logging

`MCPConfig.logging` (a `slb_mcp.Logging`) controls this server process's own logging, separately from any logging your surrounding application already has configured:

```python
config = slb_mcp.MCPConfig(
    logging=slb_mcp.Logging(
        sinks=[slb.log.FileSink("./mcp.log"), slb.log.StderrSink()],
        level="info",
        log_tool_calls=True,  # the default: log every call's name, caller, duration, outcome
    ),
)
```

This mirrors `slb_glossary.logging.configure_logging` closely enough that anything covered in [Logging](../library/logging.md) (routing different loggers to different sinks, a custom `LogSink` class, changing the format string) applies here too, just scoped to the `logging=` field instead of a direct function call. Leave it unset (the default) to inherit whatever logging setup, if any, is already in place when the server starts.

## Getting the underlying FastMCP instance

`MCPApp` doesn't hide the `fastmcp.FastMCP` server it builds. `app.server()` returns it directly, built (once, lazily) from your `MCPConfig`. From there, it's a regular FastMCP app you can extend with anything FastMCP itself supports, beyond what `MCPConfig` has a dedicated field for:

```python
app = slb_mcp.MCPApp(config)
mcp = app.server()  # the actual fastmcp.FastMCP instance


@mcp.tool()
def internal_note(text: str) -> str:
    """A tool that has nothing to do with the glossary at all."""
    return f"noted: {text}"


@mcp.resource("glossary://about")
def about() -> str:
    return "An MCP server for the SLB Energy Glossary."


if __name__ == "__main__":
    mcp.run(transport="stdio")  # run the FastMCP instance directly, or app.run(), works the same
```

This is the escape hatch for anything `MCPConfig` doesn't model directly: extra tools/resources/prompts unrelated to the glossary, FastMCP middleware, or mounting this server inside a larger ASGI app's own routing. `app.server()` is idempotent: calling it again returns the same instance rather than rebuilding it, so mixing this with `app.run()`/`app.run_async()` afterward is safe.

---

## Where to go from here

For a worked example connecting this server to an actual agent framework, see [Building an Agent with Pydantic AI](pydantic-ai.md). For the full config surface, see [`slb_glossary.mcp`](../api/library.md#slb_glossarymcp). For a complete, runnable server built with several of these fields together, see [`examples/app.py`](https://github.com/ti-oluwa/slb-glossary/blob/main/examples/app.py) in the repository (`python -m examples.app`, or `slb mcp serve examples.app:app`).
