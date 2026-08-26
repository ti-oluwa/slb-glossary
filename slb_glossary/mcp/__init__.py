"""
MCP (Model Context Protocol) API for the SLB Energy Glossary.

Exposes `slb_glossary.query`'s search/lookup functions as MCP tools an LLM
agent can call directly, backed by [FastMCP](https://gofastmcp.com).

The MCP application is fully configurable through `MCPConfig`.
You can configure which sources are reachable, whether
local writes are allowed, which tools are built, timeouts, auth, rate
limiting, hooks, logging, and streaming - see `slb_glossary.mcp.config`.

```python
from slb_glossary.mcp import MCPApp, MCPConfig

app = MCPApp(MCPConfig.default())

if __name__ == "__main_":
    app.run()
```

Or from the command line: `slb mcp serve` (see `slb_glossary.cli.commands.mcp`).

Requires the `mcp` extra: `pip install slb-glossary[mcp]`.
"""

from slb_glossary.mcp.api import MCPApp
from slb_glossary.mcp.auth import (
    ANONYMOUS,
    AuthBackend,
    AuthRequest,
    NullAuth,
    Principal,
    StaticTokenAuth,
    import_backend,
)
from slb_glossary.mcp.config import (
    Auth,
    Hooks,
    LocalAccess,
    Logging,
    MCPConfig,
    RateLimit,
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
from slb_glossary.mcp.errors import (
    AuthenticationError,
    MCPConfigError,
    MCPError,
    RateLimitExceededError,
)
from slb_glossary.mcp.ratelimit import RateLimiter, SlidingWindowRateLimiter
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
    "AuthBackend",
    "AuthRequest",
    "AuthenticationError",
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
    "NullAuth",
    "Principal",
    "RateLimit",
    "RateLimitExceededError",
    "RateLimitScope",
    "RateLimiter",
    "Runtime",
    "ServerInfo",
    "SessionAccess",
    "SessionMode",
    "SlidingWindowRateLimiter",
    "SourcePolicy",
    "StaticTokenAuth",
    "Streaming",
    "Timeout",
    "Tool",
    "ToolErrorHook",
    "ToolRunContext",
    "import_backend",
    "resolve_tools",
]
