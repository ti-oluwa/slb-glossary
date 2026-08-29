"""
MCP (Model Context Protocol) API for the SLB Energy Glossary.

Exposes `slb_glossary.query`'s search/lookup functions as MCP tools an LLM
agent can call directly, backed by [FastMCP](https://gofastmcp.com).

The MCP application is fully configurable through `MCPConfig`.
You can configure which sources are reachable, whether
local writes are allowed, which tools are built, timeouts, auth, rate
limiting, hooks, logging, and streaming - see `slb_glossary.mcp.config`.
Auth and rate limiting are FastMCP's own middleware under the hood, not
reimplemented here - `MCPConfig` just configures them.

```python
from slb_glossary.mcp import MCPApp, MCPConfig

app = MCPApp(MCPConfig.default())

if __name__ == "__main_":
    app.run()
```

Or from the command line: `slb mcp serve` (see `slb_glossary.cli.commands.mcp`).

Requires the `mcp` extra: `pip install slb-glossary[mcp]`.
"""

from slb_glossary.mcp.api import MCPApp, resolve_icon
from slb_glossary.mcp.auth import (
    ANONYMOUS,
    Principal,
    StaticTokenVerifier,
    get_principal_from_token,
    import_provider,
)
from slb_glossary.mcp.config import (
    Auth,
    Hooks,
    LocalAccess,
    Logging,
    MCPConfig,
    RateLimit,
    RateLimitAlgorithm,
    RateLimitScope,
    ServerInfo,
    SessionAccess,
    SessionMode,
    SourcePolicy,
    Streaming,
    Timeout,
    Tool,
    resolve_tools,
)
from slb_glossary.mcp.errors import MCPConfigError, MCPError
from slb_glossary.mcp.runtime import Runtime
from slb_glossary.mcp.types import (
    AfterToolHook,
    BeforeToolHook,
    LifecycleHook,
    NamedComponent,
    ToolErrorHook,
    ToolRunContext,
)

__all__ = [
    "ANONYMOUS",
    "AfterToolHook",
    "Auth",
    "BeforeToolHook",
    "Hooks",
    "LifecycleHook",
    "LocalAccess",
    "Logging",
    "MCPApp",
    "MCPConfig",
    "MCPConfigError",
    "MCPError",
    "NamedComponent",
    "Principal",
    "RateLimit",
    "RateLimitAlgorithm",
    "RateLimitScope",
    "Runtime",
    "ServerInfo",
    "SessionAccess",
    "SessionMode",
    "SourcePolicy",
    "StaticTokenVerifier",
    "Streaming",
    "Timeout",
    "Tool",
    "ToolErrorHook",
    "ToolRunContext",
    "get_principal_from_token",
    "import_provider",
    "resolve_icon",
    "resolve_tools",
]
