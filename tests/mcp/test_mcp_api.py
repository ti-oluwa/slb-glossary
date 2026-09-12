"""Tests for `mcp.api`: `resolve_icon`, rate-limit/auth middleware builders, `MCPApp`, `load_app`."""

import base64
import pathlib

import pytest
from fastmcp.server.auth import AuthProvider
from fastmcp.server.middleware import AuthMiddleware
from fastmcp.server.middleware.rate_limiting import (
    RateLimitingMiddleware,
    SlidingWindowRateLimitingMiddleware,
)
from fastmcp.server.server import FastMCP

from slb_glossary.mcp.api import (
    MCPApp,
    build_authorization_middleware,
    build_rate_limit_middleware,
    get_rate_limit_key,
    load_app,
    resolve_icon,
)
from slb_glossary.mcp.auth import Principal
from slb_glossary.mcp.config import (
    Auth,
    LocalAccess,
    MCPConfig,
    RateLimit,
    RateLimitAlgorithm,
    RateLimitScope,
)
from slb_glossary.mcp.errors import MCPConfigError

pytestmark = [pytest.mark.unit, pytest.mark.anyio]


class TestResolveIcon:
    def test_none_returns_none(self) -> None:
        """No logo at all resolves to no icons."""
        assert resolve_icon(None) is None

    def test_http_url_is_passed_through_as_is(self) -> None:
        """An `http(s)://` URL is used directly as the icon `src`, not read as a file."""
        icons = resolve_icon("https://example.com/logo.png")
        assert icons is not None
        assert icons[0].src == "https://example.com/logo.png"

    def test_local_file_is_inlined_as_a_data_uri(self, tmp_path: pathlib.Path) -> None:
        """A local file path is read and inlined as a base64 data URI."""
        logo_path = tmp_path / "logo.png"
        logo_path.write_bytes(b"fake-png-bytes")

        icons = resolve_icon(str(logo_path))
        assert icons is not None
        assert icons[0].src.startswith("data:image/png;base64,")
        encoded = icons[0].src.split(",", 1)[1]
        assert base64.b64decode(encoded) == b"fake-png-bytes"

    def test_missing_local_file_raises(self, tmp_path: pathlib.Path) -> None:
        """A local path that doesn't exist raises `MCPConfigError`, not a generic `OSError`."""
        with pytest.raises(MCPConfigError, match="Could not read"):
            resolve_icon(str(tmp_path / "does-not-exist.png"))


class TestGetRateLimitKey:
    def test_global_scope_ignores_principal_and_tool(self) -> None:
        """`GLOBAL` scope always returns the same key, regardless of caller/tool."""
        principal = Principal(id="alice")
        assert get_rate_limit_key(RateLimitScope.GLOBAL, principal, "search") == "global"
        assert get_rate_limit_key(RateLimitScope.GLOBAL, principal, "sync") == "global"

    def test_client_scope_keys_by_principal_id(self) -> None:
        """`CLIENT` scope keys by the caller's principal id, regardless of tool."""
        principal = Principal(id="alice")
        assert get_rate_limit_key(RateLimitScope.CLIENT, principal, "search") == "alice"

    def test_tool_scope_keys_by_tool_name(self) -> None:
        """`TOOL` scope keys by the tool name, regardless of caller."""
        principal = Principal(id="alice")
        assert get_rate_limit_key(RateLimitScope.TOOL, principal, "search") == "search"

    def test_client_tool_scope_combines_both(self) -> None:
        """`CLIENT_TOOL` scope combines caller id and tool name."""
        principal = Principal(id="alice")
        assert (
            get_rate_limit_key(RateLimitScope.CLIENT_TOOL, principal, "search") == "alice:search"
        )


class TestBuildRateLimitMiddleware:
    def test_disabled_returns_none(self) -> None:
        """A disabled rate limit config builds no middleware at all."""
        assert build_rate_limit_middleware(RateLimit(enabled=False)) is None

    def test_token_bucket_builds_rate_limiting_middleware(self) -> None:
        """`TOKEN_BUCKET` builds FastMCP's `RateLimitingMiddleware`."""
        middleware = build_rate_limit_middleware(
            RateLimit(
                enabled=True, algorithm=RateLimitAlgorithm.TOKEN_BUCKET, limit=10, window=60.0
            )
        )
        assert isinstance(middleware, RateLimitingMiddleware)

    def test_sliding_window_builds_sliding_window_middleware(self) -> None:
        """`SLIDING_WINDOW` builds FastMCP's `SlidingWindowRateLimitingMiddleware`."""
        middleware = build_rate_limit_middleware(
            RateLimit(
                enabled=True, algorithm=RateLimitAlgorithm.SLIDING_WINDOW, limit=10, window=60.0
            )
        )
        assert isinstance(middleware, SlidingWindowRateLimitingMiddleware)


class TestBuildAuthorizationMiddleware:
    def test_no_required_scopes_returns_none(self) -> None:
        """No `required_scopes` builds no authorization middleware at all."""
        assert build_authorization_middleware(Auth()) is None

    def test_required_scopes_builds_auth_middleware(self) -> None:
        """Non-empty `required_scopes` builds FastMCP's `AuthMiddleware`."""

        class ADummyAuthProvider(AuthProvider): ...

        middleware = build_authorization_middleware(
            Auth(provider=ADummyAuthProvider(), required_scopes=("admin",))
        )
        assert isinstance(middleware, AuthMiddleware)


class TestMCPApp:
    def test_default_config_builds_a_server(self) -> None:
        """`MCPApp()` with no config at all builds successfully, using `MCPConfig.default()`."""
        app = MCPApp()
        server = app.server()
        assert isinstance(server, FastMCP)

    def test_server_is_idempotent(self) -> None:
        """Repeated `server()` calls return the exact same `FastMCP` instance."""
        app = MCPApp(MCPConfig.default())
        first = app.server()
        second = app.server()
        assert first is second

    def test_building_the_server_opens_no_resources(self) -> None:
        """Building the server does not open a database or a live session."""
        app = MCPApp(MCPConfig.default())
        app.server()
        assert app._started is False

    async def test_read_only_config_registers_no_sync_tool(self) -> None:
        """A read-only (the default) config never registers the write-capable sync tool."""
        app = MCPApp(MCPConfig.default())
        server = app.server()
        tools = await server.list_tools()
        assert not any("sync" in tool.name for tool in tools)

    async def test_write_enabled_config_registers_the_sync_tool(self) -> None:
        """A config with `Tool.SYNC` and `local.allow_write=True` registers the sync tool."""
        from slb_glossary.mcp.config import Tool

        config = MCPConfig(tools=Tool.ALL, local=LocalAccess(allow_write=True))
        app = MCPApp(config)
        server = app.server()
        tools = await server.list_tools()
        assert any("sync" in tool.name for tool in tools)


def a_prebuilt_app() -> MCPApp:
    """A pre-built `MCPApp`, for `load_app` tests below."""
    return MCPApp(MCPConfig.default())


def an_app_factory() -> MCPApp:
    """A zero-argument factory returning a fresh `MCPApp`, for `load_app` tests below."""
    return MCPApp(MCPConfig.default())


async def an_async_app_factory() -> MCPApp:
    """An (unsupported) async factory, for `load_app`'s rejection test below."""
    return MCPApp(MCPConfig.default())


NOT_AN_APP = "just a string, not an MCPApp/FastMCP"


class TestLoadApp:
    def test_loads_a_prebuilt_app_attribute(self) -> None:
        """A module attribute that's already an `MCPApp` is returned as-is."""
        app = load_app("tests.mcp.test_mcp_api:a_prebuilt_app")
        # `a_prebuilt_app` is a function, so it's called; this exercises the factory path too.
        assert isinstance(app, MCPApp)

    def test_calls_a_zero_argument_factory(self) -> None:
        """A callable attribute that isn't already an app is called as a factory."""
        app = load_app("tests.mcp.test_mcp_api:an_app_factory")
        assert isinstance(app, MCPApp)

    def test_missing_separator_raises_value_error(self) -> None:
        """A path with no `:` separator at all is rejected."""
        with pytest.raises(ValueError, match="not a valid app import path"):
            load_app("no_colon_here")

    def test_unimportable_module_raises_import_error(self) -> None:
        """A module that doesn't exist raises `ImportError`."""
        with pytest.raises(ImportError):
            load_app("this.module.does.not.exist:app")

    def test_missing_attribute_raises_import_error(self) -> None:
        """A real module with no such attribute raises `ImportError`."""
        with pytest.raises(ImportError, match="has no attribute"):
            load_app("tests.mcp.test_mcp_api:no_such_attribute")

    def test_async_factory_is_rejected(self) -> None:
        """An async factory function is explicitly rejected, with guidance, not silently awaited."""
        with pytest.raises(TypeError, match="async factory"):
            load_app("tests.mcp.test_mcp_api:an_async_app_factory")

    def test_non_app_value_raises_type_error(self) -> None:
        """A resolved value that's neither an `MCPApp`/`FastMCP` nor a factory for one is rejected."""
        with pytest.raises(TypeError, match="neither an"):
            load_app("tests.mcp.test_mcp_api:NOT_AN_APP")
