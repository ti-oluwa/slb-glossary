# Building an Agent with Pydantic AI

Building an agent with [Pydantic AI](https://ai.pydantic.dev/) that can look glossary terms up itself. This page assumes you've read [Running an MCP Server](mcp-server.md); it's a worked example on top of that, not a replacement for it.

---

## Install

```bash
uv add "pydantic-ai-slim[mcp]"
```

(Or the full `pydantic-ai` package, which already includes this.) You'll also need `slb-glossary`'s `mcp` extra installed and on the same machine, since Pydantic AI will launch `slb mcp serve` itself as a subprocess: `uv add "slb-glossary[mcp]"`, or a CLI install with the `mcp` extra.

If you're working from a clone of the `slb-glossary` repository itself rather than installing it as a dependency, `uv sync --group examples --inexact` installs this same dependency for you, and [`examples/agent.py`](https://github.com/ti-oluwa/slb-glossary/blob/main/examples/agent.py) is a complete, runnable version of everything on this page (`python -m examples.agent`), both the subprocess and in-process wiring below, plus a real system prompt and a multi-question run.

## Connecting the agent to `slb mcp serve`

Pydantic AI's current MCP client is `MCPToolset`, wrapping a transport. For a stdio server like this one (launched as a subprocess, communicating over stdin/stdout), that transport is `StdioTransport`:

```python
import asyncio

from fastmcp.client.transports import StdioTransport
from pydantic_ai import Agent
from pydantic_ai.mcp import MCPToolset

toolset = MCPToolset(StdioTransport(command="slb", args=["mcp", "serve"]))
agent = Agent("anthropic:claude-sonnet-4-5", toolsets=[toolset])


async def main() -> None:
    result = await agent.run("What does porosity mean in petroleum engineering?")
    print(result.output)


asyncio.run(main())
```

Pydantic AI starts the `slb mcp serve` subprocess itself, the first time the toolset is actually used, and stops it again once the agent (or an explicit `async with toolset:` block) is done. There's no separate step where you manually start the server first, the way you would for the HTTP transport.

## Restricting what the agent can do

`slb mcp serve` with no flags is read-only and can reach both local and live sources, the same default covered in [Running an MCP Server](mcp-server.md). Pass through whichever flags fit the agent's actual job:

```python
toolset = MCPToolset(
    StdioTransport(command="slb", args=["mcp", "serve", "--no-live", "--tools", "search,get_term"])
)
```

This gives the agent a local-only, two-tool glossary. No browser, no network per call, and no ability to look up anything not already cached. Good for an agent that should be fast and predictable, at the cost of not finding terms nobody's searched for yet.

!!! warning "Don't hand an agent write access without meaning to"
    `--tools all --allow-write` exposes `glossary_sync`, which fetches from the live site and writes to your local database, an unusual thing to let an LLM decide to do on its own initiative from a natural-language prompt. Unless the agent's whole job is explicitly to manage the local cache, leave `--allow-write` off and let it stick to `read_only` (the default).

## Naming conflicts with other tools

If this agent has other toolsets registered too, and something else happens to expose a same-named tool, wrap the glossary toolset with `.prefixed(...)`:

```python
glossary = MCPToolset(StdioTransport(command="slb", args=["mcp", "serve"])).prefixed("glossary")
agent = Agent("anthropic:claude-sonnet-4-5", toolsets=[glossary, other_toolset])
```

Every tool this server exposes is already prefixed with `glossary_` on its own (`glossary_search`, `glossary_get_term`, ...; see [Running an MCP Server](mcp-server.md#choosing-which-tools-are-exposed)), so an additional `.prefixed(...)` is mainly useful if you're running more than one instance of this same server side by side, distinguished some other way (different `--config`, different `--language`).

## Running in-process, without a subprocess

If your agent and the glossary server live in the same codebase, there's a faster option than `StdioTransport`. You can hand `MCPToolset` the `fastmcp.FastMCP` server object directly, and it connects in-process, no subprocess, no network round trip:

```python
import slb_glossary.mcp as slb_mcp
from pydantic_ai import Agent
from pydantic_ai.mcp import MCPToolset

config = slb_mcp.MCPConfig(local=slb_mcp.LocalAccess(allow_write=False))
app = slb_mcp.MCPApp(config)

toolset = MCPToolset(app.server())  # in-process: same interpreter, no subprocess
agent = Agent("anthropic:claude-sonnet-4-5", toolsets=[toolset])
```

`app.server()` builds and returns the underlying `FastMCP` instance the CLI's `slb mcp serve` would otherwise run as a standalone process. This is the better choice for tests, a single-process deployment, or anywhere you're already importing `slb_glossary` directly rather than shelling out to the `slb` command, you get the exact same tool set and config surface covered in [Running an MCP Server](mcp-server.md#embedding-the-server-in-your-own-python-app), just without paying for a second process.

## Managing the connection's lifecycle explicitly

By default, the toolset connects and disconnects automatically as needed. To keep one subprocess alive across several `agent.run()` calls instead of relaunching it each time:

```python
async with agent:
    result1 = await agent.run("Define water saturation")
    result2 = await agent.run("Now compare it with porosity")
```

`async with agent` opens (and, on exit, closes) every registered toolset's connection, for `slb mcp serve`, that means starting the subprocess once and reusing it for every run inside the block, rather than a fresh `slb mcp serve` process per call.

---

## Where to go from here

For every flag `slb mcp serve` accepts, and what each tool actually does underneath, see [Running an MCP Server](mcp-server.md). For anything not covered here (resources, sampling, per-user authentication for a multi-tenant agent), see [Pydantic AI's own MCP client documentation](https://ai.pydantic.dev/mcp/client/), which this page doesn't explain in full.
