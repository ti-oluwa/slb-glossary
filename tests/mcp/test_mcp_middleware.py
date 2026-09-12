"""Tests for `mcp.middleware`: `get_source_from_arguments`, `MCPMiddleware`."""

import types

import pytest
from fastmcp.server.middleware import MiddlewareContext
from fastmcp.tools.base import ToolResult

from slb_glossary.mcp.config import Hooks, Logging, MCPConfig
from slb_glossary.mcp.middleware import MCPMiddleware, get_source_from_arguments
from slb_glossary.mcp.types import ToolRunContext
from slb_glossary.query import Source

pytestmark = [pytest.mark.unit, pytest.mark.anyio]


class TestGetSourceFromArguments:
    def test_no_source_argument_returns_none(self) -> None:
        """No `source` key at all resolves to `None`, not an error."""
        assert get_source_from_arguments({}) is None

    def test_a_valid_source_value(self) -> None:
        """A valid `source` string resolves to the matching `Source` member."""
        assert get_source_from_arguments({"source": "local"}) is Source.LOCAL

    def test_an_invalid_source_value_returns_none(self) -> None:
        """An unrecognized `source` value resolves to `None`, best-effort, not an error."""
        assert get_source_from_arguments({"source": "not_a_real_source"}) is None


def make_context(tool_name: str = "search", arguments: dict | None = None) -> MiddlewareContext:
    """A `MiddlewareContext` carrying just enough of a fake tool-call message for `on_call_tool`."""
    message = types.SimpleNamespace(name=tool_name, arguments=arguments or {})
    return MiddlewareContext(message=message, fastmcp_context=None)


class TestMCPMiddleware:
    async def test_calls_through_to_call_next_and_returns_its_result(self) -> None:
        """With no hooks configured, the call just passes through to `call_next` and back."""
        middleware = MCPMiddleware(MCPConfig())
        expected = ToolResult(content=[])

        async def call_next(context: MiddlewareContext) -> ToolResult:
            return expected

        result = await middleware.on_call_tool(make_context(), call_next)
        assert result is expected

    async def test_before_tool_hooks_run_in_order_before_the_call(self) -> None:
        """`before_tool` hooks all run, in order, before `call_next` is reached."""
        calls: list[str] = []

        async def first_hook(run_context: object) -> None:
            calls.append("first")

        async def second_hook(run_context: object) -> None:
            calls.append("second")

        config = MCPConfig(hooks=Hooks(before_tool=(first_hook, second_hook)))
        middleware = MCPMiddleware(config)

        async def call_next(context: MiddlewareContext) -> ToolResult:
            calls.append("call")
            return ToolResult(content=[])

        await middleware.on_call_tool(make_context(), call_next)
        assert calls == ["first", "second", "call"]

    async def test_after_tool_hooks_run_in_order_after_a_successful_call(self) -> None:
        """`after_tool` hooks all run, in order, after a successful `call_next`."""
        calls: list[str] = []

        async def first_hook(run_context: object, result: object) -> None:
            calls.append("first")

        async def second_hook(run_context: object, result: object) -> None:
            calls.append("second")

        config = MCPConfig(hooks=Hooks(after_tool=(first_hook, second_hook)))
        middleware = MCPMiddleware(config)

        async def call_next(context: MiddlewareContext) -> ToolResult:
            calls.append("call")
            return ToolResult(content=[])

        await middleware.on_call_tool(make_context(), call_next)
        assert calls == ["call", "first", "second"]

    async def test_after_tool_hooks_receive_the_actual_result(self) -> None:
        """An `after_tool` hook receives the actual `ToolResult` `call_next` returned."""
        received: list[object] = []

        async def hook(run_context: object, result: object) -> None:
            received.append(result)

        config = MCPConfig(hooks=Hooks(after_tool=(hook,)))
        middleware = MCPMiddleware(config)
        expected = ToolResult(content=[])

        async def call_next(context: MiddlewareContext) -> ToolResult:
            return expected

        await middleware.on_call_tool(make_context(), call_next)
        assert received == [expected]

    async def test_on_error_hooks_run_when_call_next_raises(self) -> None:
        """`on_error` hooks run, in order, when the underlying call raises."""
        calls: list[str] = []

        async def hook(run_context: object, exc: BaseException) -> None:
            calls.append("error_hook")

        config = MCPConfig(hooks=Hooks(on_error=(hook,)))
        middleware = MCPMiddleware(config)

        async def call_next(context: MiddlewareContext) -> ToolResult:
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            await middleware.on_call_tool(make_context(), call_next)
        assert calls == ["error_hook"]

    async def test_on_error_hooks_receive_the_actual_exception(self) -> None:
        """An `on_error` hook receives the actual exception `call_next` raised."""
        received: list[BaseException] = []

        async def hook(run_context: object, exc: BaseException) -> None:
            received.append(exc)

        config = MCPConfig(hooks=Hooks(on_error=(hook,)))
        middleware = MCPMiddleware(config)
        original = RuntimeError("boom")

        async def call_next(context: MiddlewareContext) -> ToolResult:
            raise original

        with pytest.raises(RuntimeError):
            await middleware.on_call_tool(make_context(), call_next)
        assert received == [original]

    async def test_after_tool_hooks_do_not_run_on_error(self) -> None:
        """A failed call runs `on_error` hooks, not `after_tool` hooks."""
        calls: list[str] = []

        async def after_hook(run_context: object, result: object) -> None:
            calls.append("after")

        async def error_hook(run_context: object, exc: BaseException) -> None:
            calls.append("error")

        config = MCPConfig(hooks=Hooks(after_tool=(after_hook,), on_error=(error_hook,)))
        middleware = MCPMiddleware(config)

        async def call_next(context: MiddlewareContext) -> ToolResult:
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            await middleware.on_call_tool(make_context(), call_next)
        assert calls == ["error"]

    async def test_the_exception_propagates_after_on_error_hooks_run(self) -> None:
        """The original exception still propagates after `on_error` hooks finish, not swallowed."""
        config = MCPConfig(hooks=Hooks(on_error=()))
        middleware = MCPMiddleware(config)

        async def call_next(context: MiddlewareContext) -> ToolResult:
            raise ValueError("specific failure")

        with pytest.raises(ValueError, match="specific failure"):
            await middleware.on_call_tool(make_context(), call_next)

    async def test_run_context_carries_the_tool_name_and_arguments(self) -> None:
        """The `ToolRunContext` passed to hooks carries the actual tool name and arguments."""
        seen: list[ToolRunContext] = []

        async def hook(run_context: ToolRunContext) -> None:
            seen.append(run_context)

        config = MCPConfig(hooks=Hooks(before_tool=(hook,)))
        middleware = MCPMiddleware(config)

        async def call_next(context: MiddlewareContext) -> ToolResult:
            return ToolResult(content=[])

        context = make_context(
            tool_name="define", arguments={"term": "porosity", "source": "local"}
        )
        await middleware.on_call_tool(context, call_next)

        assert len(seen) == 1
        run_context = seen[0]
        assert run_context.tool_name == "define"
        assert run_context.arguments == {"term": "porosity", "source": "local"}
        assert run_context.source is Source.LOCAL

    async def test_works_with_no_hooks_and_logging_disabled(self) -> None:
        """The whole call path works with every hook and logging disabled - no crash on the empty path."""
        config = MCPConfig(hooks=Hooks(), logging=Logging(log_tool_calls=False))
        middleware = MCPMiddleware(config)
        expected = ToolResult(content=[])

        async def call_next(context: MiddlewareContext) -> ToolResult:
            return expected

        result = await middleware.on_call_tool(make_context(), call_next)
        assert result is expected
