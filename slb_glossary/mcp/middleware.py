"""`FastMCP` middleware for `slb_glossary` MCP API."""

import logging
import time
from collections.abc import Awaitable, Callable

from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.tools.base import ToolResult

from slb_glossary.mcp.auth import get_principal_from_token
from slb_glossary.mcp.config import MCPConfig
from slb_glossary.mcp.types import ToolRunContext
from slb_glossary.query import Source

logger = logging.getLogger(__name__)

__all__ = ["MCPMiddleware"]


def get_source_from_arguments(arguments: dict) -> Source | None:
    """Best-effort parse of a `source` MCP argument into a `Source`, for `ToolRunContext`."""
    raw = arguments.get("source")
    if raw is None:
        return None
    try:
        return Source(raw)
    except ValueError:
        return None


class MCPMiddleware(Middleware):
    """
    Wires up `slb_glossary`-specific per-call hooks and call logging.

    One instance is added per `slb_glossary.mcp.api.MCPApp`, ahead of any
    `FastMCP`-native middleware (auth, rate limiting) `MCPApp.server` adds
    alongside it. So that `before_tool`/`after_tool`/`on_error` hooks and
    `ToolRunContext.principal` see whatever identity `FastMCP`'s own auth
    layer already resolved for this call.
    """

    def __init__(self, config: MCPConfig) -> None:
        self.config = config

    async def on_call_tool(
        self,
        context: MiddlewareContext,
        call_next: Callable[[MiddlewareContext], Awaitable[ToolResult]],
    ) -> ToolResult:
        tool_name: str = getattr(context.message, "name", "<unknown>")
        arguments = dict(getattr(context.message, "arguments", None) or {})
        principal = get_principal_from_token(get_access_token())

        run_context = ToolRunContext(
            tool_name=tool_name,
            principal=principal,
            arguments=arguments,
            source=get_source_from_arguments(arguments),
        )
        if context.fastmcp_context is not None:
            await context.fastmcp_context.set_state("run_context", run_context, serializable=False)

        for hook in self.config.hooks.before_tool:
            await hook(run_context)

        started_at = time.monotonic()
        try:
            result = await call_next(context)
        except Exception as exc:
            for hook in self.config.hooks.on_error:
                await hook(run_context, exc)
            if self.config.logging.log_tool_calls:
                logger.warning(
                    "MCP tool %s failed for %s after %.3fs: %s",
                    tool_name,
                    principal.id,
                    time.monotonic() - started_at,
                    exc,
                )
            raise

        for hook in self.config.hooks.after_tool:
            await hook(run_context, result)

        if self.config.logging.log_tool_calls:
            logger.info(
                "MCP tool %s called by %s in %.3fs",
                tool_name,
                principal.id,
                time.monotonic() - started_at,
            )
        return result
