"""Datastrutures and types shared across `slb_glossary` MCP API"""

import dataclasses
import time
import typing
from collections.abc import Awaitable, Callable, Mapping

from slb_glossary.mcp.auth import Principal
from slb_glossary.query import Source

__all__ = [
    "AfterToolHook",
    "BeforeToolHook",
    "LifecycleHook",
    "NamedComponent",
    "ToolErrorHook",
    "ToolRunContext",
]


class NamedComponent:
    """
    Mixin giving a component a human-readable `name` for use in logs, task names, and `repr`.
    """

    def __init__(self, name: str) -> None:
        """:param name: Human-readable name for this component."""
        self.name = name

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.name!r})"


@dataclasses.dataclass(slots=True, kw_only=True)
class ToolRunContext:
    """
    Everything a tool call's hooks (see `slb_glossary.mcp.config.Hooks`) get to see.

    Built fresh per call by `slb_glossary.mcp.middleware.MCPMiddleware`
    and passed to `before_tool`/`after_tool`/`on_error` hooks, and also
    stashed in the FastMCP `Context` state (see
    `MCPMiddleware.on_call_tool`) so tool bodies can read it too.

    Read-only in practice. Hooks are meant to observe/veto (by raising),
    not mutate call state.
    """

    tool_name: str
    """The MCP tool name being called, e.g. `\"glossary_search\"`."""

    principal: Principal
    """
    The resolved caller identity. Defaults to `slb_glossary.mcp.auth.ANONYMOUS` if
    no auth backend is configured or the call carried no token.
    """

    arguments: Mapping[str, typing.Any]
    """The tool call's raw arguments, as a plain mapping."""

    source: Source | None
    """The `Source` this call resolved to, if applicable to this tool."""

    started_at: float = dataclasses.field(default_factory=time.monotonic)
    """`time.monotonic()` reading taken when the call began."""


BeforeToolHook = Callable[[ToolRunContext], Awaitable[None]]
"""
`async def hook(run: ToolRunContext) -> None`.

Called just before a tool's body executes. Raise to abort the 
call (surfaced to the caller as a tool error)
."""

AfterToolHook = Callable[[ToolRunContext, typing.Any], Awaitable[None]]
"""
`async def hook(run: ToolRunContext, result: Any) -> None`. 
Called after a tool's body returns successfully, with its result. 

`result` is typed `Any` because it's whatever JSON-serializable value the tool produced.
"""

ToolErrorHook = Callable[[ToolRunContext, BaseException], Awaitable[None]]
"""
`async def hook(run: ToolRunContext, error: BaseException) -> None`.
Called when a tool's body raises. The error still propagates afterward.
"""

LifecycleHook = Callable[[], Awaitable[None]]
"""`async def hook() -> None`. Called once on server startup/shutdown."""
