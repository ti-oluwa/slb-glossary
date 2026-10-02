"""Tests for how `MCPApp` builds, shares and shuts down its `slb_glossary.live.Runtime`."""

import typing

import pytest

from slb_glossary.live import Runtime, SessionMode
from slb_glossary.mcp import SessionMode as McpSessionMode
from slb_glossary.mcp.api import MCPApp
from slb_glossary.mcp.config import LocalAccess, MCPConfig, ServerInfo, SessionAccess
from slb_glossary.mcp.runtime import Runtime as McpRuntime
from slb_glossary.mcp.runtime import build_runtime

pytestmark = [pytest.mark.unit, pytest.mark.mcp, pytest.mark.anyio]


@pytest.fixture
def anyio_backend(
    anyio_backend_asyncio_only: tuple[str, dict[str, typing.Any]],
) -> tuple[str, dict[str, typing.Any]]:
    return anyio_backend_asyncio_only


def test_runtime_and_session_mode_are_the_live_classes() -> None:
    """The MCP names are re-exports, so old imports keep working and isinstance checks agree."""
    assert McpRuntime is Runtime
    assert McpSessionMode is SessionMode


def test_runtime_from_config_translates_every_mcp_setting() -> None:
    config = MCPConfig(
        server=ServerInfo(name="my-server"),
        local=LocalAccess(enabled=False),
        session=SessionAccess(
            enabled=True,
            mode=SessionMode.EAGER,
            idle_timeout=12.5,
            max_sessions=4,
            capacity_tolerance=2,
        ),
    )
    runtime = build_runtime(config)
    assert runtime.name == "my-server"
    assert runtime.session_options is config.session.options
    assert runtime.database_options is config.local.database
    assert runtime.local_enabled is False
    assert runtime.live_enabled is True
    assert runtime.mode is SessionMode.EAGER
    assert (runtime.idle_timeout, runtime.max_sessions, runtime.capacity_tolerance) == (12.5, 4, 2)


class TestInjectedRuntime:
    async def test_app_uses_the_runtime_it_is_given_and_does_not_close_it(self) -> None:
        shared = Runtime(local_enabled=False, live_enabled=True, idle_timeout=None)
        app = MCPApp(MCPConfig.default(), runtime=shared)
        assert app.runtime is shared

        await app.start()
        assert shared.started
        await app.close()
        assert not shared.closed, "a runtime the app was handed belongs to whoever created it"
        await shared.close()

    async def test_app_closes_the_runtime_it_built_itself(self) -> None:
        app = MCPApp(
            MCPConfig(
                local=LocalAccess(enabled=False),
                session=SessionAccess(enabled=True, mode=SessionMode.LAZY, idle_timeout=None),
            )
        )
        await app.start()
        await app.close()
        assert app.runtime.closed


class TestResourceErrorTranslation:
    async def test_runtime_failures_surface_as_the_servers_own_error(self) -> None:
        """A closed runtime's `ResourceError` reaches the client as an MCP tool error."""
        from fastmcp import Client
        from fastmcp.exceptions import ToolError

        shared = Runtime(local_enabled=False, live_enabled=True, idle_timeout=None)
        app = MCPApp(
            MCPConfig(
                local=LocalAccess(enabled=False),
                session=SessionAccess(enabled=True, idle_timeout=None),
            ),
            runtime=shared,
        )

        async with Client(app.server()) as client:
            await shared.close()  # e.g. the host application shut its runtime down early
            with pytest.raises(ToolError, match="closed"):
                await client.call_tool("glossary_search", {"args": {"query": "porosity"}})
