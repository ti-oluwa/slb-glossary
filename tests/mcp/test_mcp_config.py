"""Tests for `mcp.config`: `Tool`/`resolve_tools`, `MCPConfig` validation and defaults."""

import pytest

from slb_glossary.mcp.config import (
    LocalAccess,
    MCPConfig,
    RateLimit,
    SessionAccess,
    SourcePolicy,
    Tool,
    resolve_tools,
)
from slb_glossary.mcp.errors import MCPConfigError
from slb_glossary.query import Source
from slb_glossary.types import Language

pytestmark = pytest.mark.unit


class TestResolveTools:
    def test_none_returns_read_only(self) -> None:
        """`None` resolves to `Tool.READ_ONLY`, the default."""
        assert resolve_tools(None) == Tool.READ_ONLY

    def test_a_tool_instance_is_returned_as_is(self) -> None:
        """An already-resolved `Tool` combination passes through unchanged."""
        assert resolve_tools(Tool.SEARCH | Tool.SYNC) == Tool.SEARCH | Tool.SYNC

    def test_a_single_name_string(self) -> None:
        """A single tool-name string resolves to that one member."""
        assert resolve_tools("search") == Tool.SEARCH

    def test_case_and_whitespace_insensitive(self) -> None:
        """A tool name is matched case- and whitespace-insensitively."""
        assert resolve_tools(" Search \n") == Tool.SEARCH

    def test_an_iterable_of_names_combines_them(self) -> None:
        """Several tool names combine into one flag set."""
        assert resolve_tools(["search", "get_term"]) == Tool.SEARCH | Tool.GET_TERM

    def test_read_only_and_all_aliases(self) -> None:
        """`"read_only"`/`"all"` resolve to their matching combination aliases."""
        assert resolve_tools("read_only") == Tool.READ_ONLY
        assert resolve_tools("all") == Tool.ALL

    def test_unknown_name_raises(self) -> None:
        """An unrecognized tool name raises `MCPConfigError`, not a silent no-op."""
        with pytest.raises(MCPConfigError, match=r"Unknown MCP tool name"):
            resolve_tools("not_a_real_tool")


class TestMCPConfigValidation:
    def test_defaults_alone_are_valid(self) -> None:
        """`MCPConfig()` with no arguments at all is a valid configuration."""
        MCPConfig()  # must not raise

    def test_neither_session_nor_local_enabled_raises(self) -> None:
        """A config that can read nothing at all (neither source enabled) is rejected."""
        with pytest.raises(MCPConfigError, match=r"local.enabled"):
            MCPConfig(
                session=SessionAccess(enabled=False),
                local=LocalAccess(enabled=False),
            )

    def test_source_policy_allowed_defaults_from_enabled_sources(self) -> None:
        """With `source_policy.allowed` unset, it's computed from which sources are enabled."""
        config = MCPConfig(session=SessionAccess(enabled=False), local=LocalAccess(enabled=True))
        assert config.source_policy.allowed == frozenset({Source.AUTO, Source.LOCAL})

    def test_empty_allowed_set_raises(self) -> None:
        """An explicitly empty `source_policy.allowed` is rejected."""
        with pytest.raises(MCPConfigError, match=r"must not be empty"):
            MCPConfig(source_policy=SourcePolicy(allowed=frozenset()))

    def test_allowed_local_without_local_enabled_raises(self) -> None:
        """`Source.LOCAL` in `allowed` while `local.enabled=False` is rejected as inconsistent."""
        with pytest.raises(MCPConfigError, match=r"local.enabled"):
            MCPConfig(
                local=LocalAccess(enabled=False),
                source_policy=SourcePolicy(allowed=frozenset({Source.LOCAL})),
            )

    def test_allowed_live_without_session_enabled_raises(self) -> None:
        """`Source.LIVE` in `allowed` while `session.enabled=False` is rejected as inconsistent."""
        with pytest.raises(MCPConfigError, match=r"session.enabled"):
            MCPConfig(
                session=SessionAccess(enabled=False),
                source_policy=SourcePolicy(allowed=frozenset({Source.LIVE})),
            )

    def test_default_source_not_in_allowed_raises(self) -> None:
        """`source_policy.default` has to actually be one of `source_policy.allowed`."""
        with pytest.raises(MCPConfigError, match=r"source_policy.default"):
            MCPConfig(
                source_policy=SourcePolicy(default=Source.LIVE, allowed=frozenset({Source.LOCAL}))
            )

    def test_rate_limit_enabled_with_non_positive_limit_raises(self) -> None:
        """A rate limit that's enabled but has a non-positive limit is rejected."""
        with pytest.raises(MCPConfigError, match=r"rate_limit.limit"):
            MCPConfig(rate_limit=RateLimit(enabled=True, limit=0))

    def test_max_sessions_below_one_raises(self) -> None:
        """`session.max_sessions` below 1 is rejected."""
        with pytest.raises(MCPConfigError, match=r"max_sessions"):
            MCPConfig(session=SessionAccess(max_sessions=0))

    def test_negative_capacity_tolerance_raises(self) -> None:
        """A negative `session.capacity_tolerance` is rejected."""
        with pytest.raises(MCPConfigError, match=r"capacity_tolerance"):
            MCPConfig(session=SessionAccess(capacity_tolerance=-1))


class TestMCPConfigResolveTools:
    def test_sync_stripped_without_allow_write(self) -> None:
        """`Tool.SYNC` is dropped from the resolved set unless `local.allow_write` is also True."""
        config = MCPConfig(tools=Tool.ALL, local=LocalAccess(allow_write=False))
        assert Tool.SYNC not in config.resolve_tools()

    def test_sync_kept_with_allow_write(self) -> None:
        """`Tool.SYNC` is kept when both requested and `local.allow_write=True`."""
        config = MCPConfig(tools=Tool.ALL, local=LocalAccess(allow_write=True))
        assert Tool.SYNC in config.resolve_tools()

    def test_allow_write_alone_does_not_add_sync(self) -> None:
        """`local.allow_write=True` alone does not add `Tool.SYNC` if it wasn't requested."""
        config = MCPConfig(tools=Tool.READ_ONLY, local=LocalAccess(allow_write=True))
        assert Tool.SYNC not in config.resolve_tools()


class TestMCPConfigDefault:
    def test_default_with_no_language_is_plain_defaults(self) -> None:
        """`MCPConfig.default()` with no `language` is equivalent to `MCPConfig()`."""
        assert MCPConfig.default() == MCPConfig()

    def test_default_with_a_language_string(self) -> None:
        """`language=` sets `session.options.language` via the shortcut."""
        config = MCPConfig.default(language="es")
        assert config.session.options.language == Language.SPANISH.value

    def test_default_with_a_language_member(self) -> None:
        """A `Language` member is accepted directly, not just its string value."""
        config = MCPConfig.default(language=Language.SPANISH)
        assert config.session.options.language == Language.SPANISH.value

    def test_default_with_an_invalid_language_raises(self) -> None:
        """An unrecognized language string raises `MCPConfigError`."""
        with pytest.raises(MCPConfigError, match=r"Unknown language"):
            MCPConfig.default(language="not-a-real-language")
