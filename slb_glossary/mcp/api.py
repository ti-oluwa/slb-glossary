"""
The main entry point of `slb_glossary` MCP API.

Holds `MCPApp`, which turns an `MCPConfig` into a ready-to-serve `fastmcp.FastMCP`
server for the SLB Energy Glossary.

```python
from slb_glossary.mcp import MCPApp, MCPConfig

app = MCPApp(MCPConfig.default())
app.run(...)  # stdio by default
```

Or reach for the underlying `fastmcp.FastMCP` server directly (e.g. to
mount it inside a larger ASGI app, or drive it from `FastMCP`'s own CLI):

```python
server = app.server(...)
```
"""

import asyncio
import contextlib
import dataclasses
import importlib
import inspect
import logging
import typing

from fastmcp.server.context import Context
from fastmcp.server.server import FastMCP

from slb_glossary.constants import constants
from slb_glossary.logging import configure_logging
from slb_glossary.mcp.config import MCPConfig
from slb_glossary.mcp.middleware import MCPMiddleware
from slb_glossary.mcp.ratelimit import SlidingWindowRateLimiter
from slb_glossary.mcp.runtime import Runtime
from slb_glossary.mcp.tools import DEFAULT_INSTRUCTIONS, ToolSpec, build_tool_specs
from slb_glossary.mcp.types import NamedComponent

logger = logging.getLogger(__name__)

__all__ = ["MCPApp", "load_app"]


class MCPApp(NamedComponent):
    """
    A configured, buildable MCP server for the SLB Energy Glossary.

    Construction (`MCPApp(config)`) is cheap and does no I/O; the
    underlying `FastMCP` server and its tools are assembled lazily on first
    `server()`/`run()`/`run_async()` call.

    Resource startup happens in `run_async`/`run`, or explicitly via `start()`
    for callers embedding the server in their own event loop / lifespan management.
    """

    def __init__(self, config: MCPConfig | None = None) -> None:
        """
        Initialize the MCP application.

        :param config: The server's `MCPConfig`. Defaults to `MCPConfig.default()`.
        """
        self.config = config if config is not None else MCPConfig.default()
        super().__init__(self.config.server.name)
        self.runtime = Runtime(self.config)
        self._server: FastMCP | None = None

    def server(self, **server_kwargs: typing.Any) -> FastMCP:
        """
        Build (if not already built) and return the underlying `fastmcp.FastMCP` server.

        Idempotent: repeated calls return the same instance. Building
        registers every tool `self.config.resolve_tools()` selects and
        attaches `slb_glossary.mcp.middleware.MCPMiddleware`, but does
        not open any resources yet (database/session). That happens in `start()`.
        """
        if self._server is not None:
            return self._server

        self._ensure_rate_limiter()

        from slb_glossary import __version__

        kwargs = {
            "name": self.config.server.name,
            "version": self.config.server.version or __version__,
            "instructions": self.config.server.instructions or DEFAULT_INSTRUCTIONS,
            "auth": self.config.auth.provider,
            "middleware": [MCPMiddleware(self.config)],
            **server_kwargs,
        }
        server = FastMCP(**kwargs)

        for spec in build_tool_specs(self.config):
            self._add_tool(server, spec)

        self._server = server
        return server

    def _ensure_rate_limiter(self) -> None:
        """Fill in `RateLimit.limiter` with a default in-memory limiter if left unset."""
        rate_limit = self.config.rate_limit
        if rate_limit.enabled and rate_limit.limiter is None:
            limiter = SlidingWindowRateLimiter(limit=rate_limit.limit, window=rate_limit.window)
            self.config = dataclasses.replace(
                self.config, rate_limit=dataclasses.replace(rate_limit, limiter=limiter)
            )
            self.runtime.config = self.config

    def _add_tool(self, server: FastMCP, spec: ToolSpec) -> None:
        """Wrap `spec.handler` into a `FastMCP` tool function and register it on `server`."""
        args_type = spec.args_type
        timeout = self.config.timeouts.for_tool(spec.name)
        annotations = {"readOnlyHint": not spec.writes, "destructiveHint": spec.writes}

        async def _tool(args: args_type, ctx: Context) -> dict[str, typing.Any]:  # type: ignore[valid-type]
            async def report_progress(count: int, total: int | None) -> None:
                await ctx.report_progress(progress=count, total=total)

            return await spec.handler(
                args, self.runtime, self.config, report_progress=report_progress
            )

        _tool.__name__ = spec.name
        _tool.__doc__ = spec.description
        server.tool(
            _tool,
            name=spec.name,
            description=spec.description,
            tags=set(spec.tags),
            timeout=timeout,
            annotations=annotations,
        )

    async def start(self) -> None:
        """
        Perform startup-time resource work (open the local DB, eagerly open a
        live session if configured) and run `Hooks.on_startup` hooks.

        Idempotent: safe to call before `run_async`, which also calls this.
        """
        self._configure_logging()
        await self.runtime.start()
        for hook in self.config.hooks.on_startup:
            await hook()
        logger.info("[%s] MCP application started", self.name)

    async def aclose(self) -> None:
        """Tear down every resource opened by `start()` and run `Hooks.on_shutdown` hooks."""
        await self.runtime.aclose()
        for hook in self.config.hooks.on_shutdown:
            await hook()
        logger.info("[%s] MCP application closed", self.name)

    def _configure_logging(self) -> None:
        """
        Apply `MCPConfig.logging` via `slb_glossary.logging.configure_logging`.
        """
        logging_config = self.config.logging
        if logging_config.sinks is None and logging_config.level is None:
            return

        configure_logging(
            sinks=logging_config.sinks,
            level=logging_config.level,
            logger_name=logging_config.logger_name,
            fmt=logging_config.fmt or constants.log_format,
            propagate=logging_config.propagate,
        )

    async def run_async(self, **transport_kwargs: typing.Any) -> None:
        """
        Start resources, serve until the transport stops, then always clean up.

        :param transport_kwargs: Forwarded to `fastmcp.FastMCP.run_async`,
            e.g. `transport="http", host="0.0.0.0", port=8000`. Defaults
            to FastMCP's own default (stdio) when omitted.
        """
        server = self.server()
        await self.start()
        async with contextlib.aclosing(self):
            await server.run_async(**transport_kwargs)

    def run(self, **transport_kwargs: typing.Any) -> None:
        """
        Synchronous convenience wrapper around `MCPApp.run_async`, for simple entry points.

        :param transport_kwargs: Forwarded to `fastmcp.FastMCP.run_async` - see `run_async`.
        """
        asyncio.run(self.run_async(**transport_kwargs))


def load_app(dotted_path: str) -> MCPApp | FastMCP:
    """
    Import `dotted_path` and return the `MCPApp`/`FastMCP` instance it points to.

    Can be used to load a pre-built MCP server from a dotted import path, uvicorn-style.

    :param dotted_path: `"module:attr"` or `"package.module:attr"` - the
        part after `:` is looked up with `getattr` on the imported module.
        If that attribute is callable and not already an `MCPApp`/`FastMCP`,
        it's called with no arguments and its return value is used instead
        (a factory function, e.g. `def create_app() -> MCPApp: ...`).
    :return: The resolved `MCPApp` or `FastMCP` instance.
    :raises ValueError: If `dotted_path` doesn't contain a `:` separator.
    :raises ImportError: If the module can't be imported, or has no such attribute.
    :raises TypeError: If, after resolving/calling it, the result still
        isn't an `MCPApp` or `FastMCP`.
    """
    module_path, sep, attr = dotted_path.partition(":")
    if not sep or not module_path or not attr:
        raise ValueError(
            f"{dotted_path!r} is not a valid app import path. Use "
            f"'module:attr' or 'package.module:attr', e.g. 'app.main:app'."
        )

    module = importlib.import_module(module_path)
    try:
        target = getattr(module, attr)
    except AttributeError as exc:
        raise ImportError(f"Module {module_path!r} has no attribute {attr!r}") from exc

    app = target
    if callable(app) and not isinstance(app, (MCPApp, FastMCP)):
        app = app()
        if inspect.isawaitable(app):
            raise TypeError(
                f"{dotted_path!r} resolved to an async factory ({target!r}); "
                f"only synchronous zero-argument factories are supported. Build the "
                f"`MCPApp`/`FastMCP` instance at import time instead (e.g. module-level "
                f"`app = MCPApp(...)`), or call your async setup yourself and expose "
                f"the already-built instance as the target attribute."
            )

    if not isinstance(app, (MCPApp, FastMCP)):
        raise TypeError(
            f"{dotted_path!r} resolved to {app!r}, which is neither an `MCPApp`, a "
            f"`FastMCP`, nor a zero-argument factory returning one."
        )
    return app
