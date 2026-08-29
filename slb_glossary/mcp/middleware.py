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
    """Wires up `slb_glossary`-specific per-call hooks and call logging."""

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
        log_calls = self.config.logging.log_tool_calls

        run_context = ToolRunContext(
            tool_name=tool_name,
            principal=principal,
            arguments=arguments,
            source=get_source_from_arguments(arguments),
        )
        if context.fastmcp_context is not None:
            await context.fastmcp_context.set_state("run_context", run_context, serializable=False)

        call_started_at = time.monotonic()

        before_started_at = time.monotonic()
        for hook in self.config.hooks.before_tool:
            await hook(run_context)
        before_elapsed = time.monotonic() - before_started_at

        dispatch_started_at = time.monotonic()
        try:
            result = await call_next(context)
        except Exception as exc:
            dispatch_elapsed = time.monotonic() - dispatch_started_at
            error_started_at = time.monotonic()
            for hook in self.config.hooks.on_error:
                await hook(run_context, exc)
            error_elapsed = time.monotonic() - error_started_at
            if log_calls:
                logger.warning(
                    "[%s] MCP tool %s failed for %s after %.3fs "
                    "(before_hooks=%.3fs dispatch=%.3fs on_error_hooks=%.3fs): %s",
                    self.config.server.name,
                    tool_name,
                    principal.id,
                    time.monotonic() - call_started_at,
                    before_elapsed,
                    dispatch_elapsed,
                    error_elapsed,
                    exc,
                )
            raise
        dispatch_elapsed = time.monotonic() - dispatch_started_at

        after_started_at = time.monotonic()
        for hook in self.config.hooks.after_tool:
            await hook(run_context, result)
        after_elapsed = time.monotonic() - after_started_at

        if log_calls:
            logger.info(
                "[%s] MCP tool %s called by %s in %.3fs "
                "(before_hooks=%.3fs dispatch=%.3fs after_hooks=%.3fs)",
                self.config.server.name,
                tool_name,
                principal.id,
                time.monotonic() - call_started_at,
                before_elapsed,
                dispatch_elapsed,
                after_elapsed,
            )
        return result
