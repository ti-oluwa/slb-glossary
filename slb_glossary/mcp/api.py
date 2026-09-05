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
import base64
import contextlib
import importlib
import inspect
import logging
import math
import mimetypes
import pathlib
import time
import typing
from urllib.parse import urlsplit

import mcp.types
from fastmcp.server.auth import require_scopes
from fastmcp.server.context import Context
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware import AuthMiddleware, Middleware, MiddlewareContext
from fastmcp.server.middleware.rate_limiting import (
    RateLimitingMiddleware,
    SlidingWindowRateLimitingMiddleware,
)
from fastmcp.server.server import FastMCP

from slb_glossary.constants import constants
from slb_glossary.logging import configure_logging
from slb_glossary.mcp.auth import Principal, get_principal_from_token
from slb_glossary.mcp.config import Auth, MCPConfig, RateLimit, RateLimitAlgorithm, RateLimitScope
from slb_glossary.mcp.errors import MCPConfigError
from slb_glossary.mcp.middleware import MCPMiddleware
from slb_glossary.mcp.runtime import Runtime
from slb_glossary.mcp.tools import DEFAULT_INSTRUCTIONS, ToolSpec, build_tool_specs
from slb_glossary.mcp.types import NamedComponent

logger = logging.getLogger(__name__)

__all__ = ["MCPApp", "load_app", "resolve_icon"]


def resolve_icon(logo: str | None) -> list[mcp.types.Icon] | None:
    """
    Resolve `slb_glossary.mcp.config.ServerInfo.logo` into an `icons` list for `fastmcp.FastMCP`.

    An `http(s)://` URL is passed straight through as the icon's `src`.
    Anything else is treated as a local file path and inlined as a
    base64 data URI, so the icon does not depend on that file still being
    reachable by whatever eventually connects, only on it existing
    right now, at server-build time.

    :param logo: `ServerInfo.logo`.
    :return: A single-item icon list, or `None` if `logo` is `None`.
    :raises MCPConfigError: If `logo` looks like a local path but does not
        exist or can not be read.
    """
    if logo is None:
        return None

    if urlsplit(logo).scheme in ("http", "https"):
        mime_type, _ = mimetypes.guess_type(logo)
        return [mcp.types.Icon(src=logo, mimeType=mime_type)]

    path = pathlib.Path(logo)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise MCPConfigError(f"Could not read `server.logo` at {path}: {exc}") from exc

    mime_type, _ = mimetypes.guess_type(path.name)
    mime_type = mime_type or "application/octet-stream"
    encoded = base64.b64encode(data).decode("ascii")
    logger.debug("Inlined server.logo (%s, %d byte(s)) from %s", mime_type, len(data), path)
    return [mcp.types.Icon(src=f"data:{mime_type};base64,{encoded}", mimeType=mime_type)]


def get_rate_limit_key(scope: RateLimitScope, principal: Principal, tool_name: str) -> str:
    if scope is RateLimitScope.GLOBAL:
        return "global"
    if scope is RateLimitScope.CLIENT:
        return principal.id
    if scope is RateLimitScope.TOOL:
        return tool_name
    assert scope is RateLimitScope.CLIENT_TOOL, f"Unexpected RateLimitScope member {scope!r}."
    return f"{principal.id}:{tool_name}"


def _build_rate_limit_middleware(config: RateLimit) -> Middleware | None:
    """Build the FastMCP rate-limiting middleware `config` describes, or `None` if disabled."""
    if not config.enabled:
        return None

    def get_client_id(context: MiddlewareContext) -> str:
        tool_name = getattr(context.message, "name", "<unknown>")
        principal = get_principal_from_token(get_access_token())
        return get_rate_limit_key(config.scope, principal, tool_name)

    if config.algorithm is RateLimitAlgorithm.TOKEN_BUCKET:
        return RateLimitingMiddleware(
            max_requests_per_second=config.limit / config.window,
            burst_capacity=config.limit,
            get_client_id=get_client_id,
        )

    window_minutes = max(1, math.ceil(config.window / 60))
    return SlidingWindowRateLimitingMiddleware(
        max_requests=config.limit,
        window_minutes=window_minutes,
        get_client_id=get_client_id,
    )


def _build_authorization_middleware(config: Auth) -> Middleware | None:
    """Build FastMCP's scope-based authorization middleware for `config.required_scopes`, if any."""
    if not config.required_scopes:
        return None
    return AuthMiddleware(auth=require_scopes(*config.required_scopes))


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

        Idempotent. Repeated calls return the same instance.

        Building registers every tool `self.config.resolve_tools()` selects and
        attaches `slb_glossary.mcp.middleware.MCPMiddleware` (hooks and
        call logging) plus, when configured, FastMCP's own scope-based
        authorization (`self.config.auth.required_scopes`) and
        rate-limiting (`self.config.rate_limit`) middleware. Also
        resolves `self.config.server.logo` into an icon (see `resolve_icon`) if set.

        This does not open any resources yet (database/session). That happens in `start()`.
        """
        if self._server is not None:
            return self._server

        from slb_glossary import __version__

        middleware: list[Middleware] = [MCPMiddleware(self.config)]
        authorization_middleware = _build_authorization_middleware(self.config.auth)
        if authorization_middleware is not None:
            middleware.append(authorization_middleware)
            logger.info(
                "[%s] Authorization enabled: required scopes = %s",
                self.name,
                sorted(self.config.auth.required_scopes),
            )

        rate_limit_middleware = _build_rate_limit_middleware(self.config.rate_limit)
        if rate_limit_middleware is not None:
            middleware.append(rate_limit_middleware)
            logger.info(
                "[%s] Rate limiting enabled: %d req / %.0fs, algorithm=%s, scope=%s",
                self.name,
                self.config.rate_limit.limit,
                self.config.rate_limit.window,
                self.config.rate_limit.algorithm.value,
                self.config.rate_limit.scope.value,
            )

        kwargs = {
            "name": self.config.server.name,
            "version": self.config.server.version or __version__,
            "instructions": self.config.server.instructions or DEFAULT_INSTRUCTIONS,
            "auth": self.config.auth.provider,
            "icons": resolve_icon(self.config.server.logo),
            "middleware": middleware,
            **server_kwargs,
        }
        server = FastMCP(**kwargs)

        tool_count = 0
        for spec in build_tool_specs(self.config):
            self.add_tool(server, spec)
            tool_count += 1

        self._server = server
        logger.info("[%s] Server built: %d tool(s) registered", self.name, tool_count)
        return server

    def add_tool(self, server: FastMCP, spec: ToolSpec) -> None:
        """Wrap `spec.handler` into a `FastMCP` tool function and register it on `server`."""
        args_type = spec.args_type
        timeout = self.config.timeouts.for_tool(spec.name)
        annotations = {"readOnlyHint": not spec.writes, "destructiveHint": spec.writes}
        log_calls = self.config.logging.log_tool_calls

        async def tool(args: args_type, ctx: Context) -> dict[str, typing.Any]:  # type: ignore[valid-type]
            async def report_progress(count: int, total: int | None) -> None:
                await ctx.report_progress(progress=count, total=total)

            started_at = time.monotonic()
            try:
                result = await spec.handler(
                    args, self.runtime, self.config, report_progress=report_progress
                )
            except Exception:
                if log_calls:
                    logger.debug(
                        "[%s] %s handler raised after %.3fs",
                        self.name,
                        spec.name,
                        time.monotonic() - started_at,
                    )
                raise
            if log_calls:
                logger.debug(
                    "[%s] %s handler completed in %.3fs",
                    self.name,
                    spec.name,
                    time.monotonic() - started_at,
                )
            return result

        tool.__name__ = spec.name
        tool.__doc__ = spec.description
        server.tool(
            tool,
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
        started_at = time.monotonic()
        self.configure_logging()
        await self.runtime.start()
        for hook in self.config.hooks.on_startup:
            await hook()
        logger.info(
            "[%s] MCP application started in %.3fs", self.name, time.monotonic() - started_at
        )

    async def aclose(self) -> None:
        """Tear down every resource opened by `start()` and run `Hooks.on_shutdown` hooks."""
        started_at = time.monotonic()
        await self.runtime.aclose()
        for hook in self.config.hooks.on_shutdown:
            await hook()
        logger.info(
            "[%s] MCP application closed in %.3fs", self.name, time.monotonic() - started_at
        )

    def configure_logging(self) -> None:
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
    :raises ValueError: If `dotted_path` does not contain a `:` separator.
    :raises ImportError: If the module can not be imported, or has no such attribute.
    :raises TypeError: If, after resolving/calling it, the result still
        is not an `MCPApp` or `FastMCP`.
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
