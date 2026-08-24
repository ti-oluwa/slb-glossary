"""
Configuration for `slb_glossary.mcp`'s MCP application (`slb_glossary.mcp.api.MCPApp`).

```python
from slb_glossary.mcp.config import MCPConfig, LocalAccess

config = MCPConfig.default().update(
    local=LocalAccess(enabled=True, allow_write=True),
)
```

Every nested config is a frozen, `slots=True`, keyword-only dataclass, so
instances are hashable-by-value-where-possible, cannot be mutated out
from under a running server, and are cheap to copy with `.update(...)`.
"""

import dataclasses
import enum
import sys
from collections.abc import Iterable, Mapping

from fastmcp.server.auth import AuthProvider

from slb_glossary.config import BrowserSessionOptions, DatabaseOptions
from slb_glossary.logging import SinksSpec
from slb_glossary.mcp.auth import AuthBackend
from slb_glossary.mcp.errors import MCPConfigError
from slb_glossary.mcp.ratelimit import RateLimiter
from slb_glossary.mcp.types import AfterToolHook, BeforeToolHook, LifecycleHook, ToolErrorHook
from slb_glossary.query import Source
from slb_glossary.types import Language, Updatable

if sys.version_info >= (3, 11):
    from typing import Self
else:
    from typing_extensions import Self

__all__ = [
    "Tool",
    "SessionMode",
    "RateLimitScope",
    "SessionAccess",
    "LocalAccess",
    "SourcePolicy",
    "Timeout",
    "Auth",
    "RateLimit",
    "Hooks",
    "Logging",
    "Streaming",
    "ServerInfo",
    "MCPConfig",
    "resolve_tools",
]


class Tool(enum.Flag):
    """
    Which `slb_glossary.query` operations the MCP application/server exposes as tools.

    A flag set: combine members with `|` to build up a set of tools
    (`Tool.SEARCH | Tool.GET_TERM`), and test membership with `in`/`&`.

    Pass a `Tool` combination, or any iterable of tool-name strings (see
    `resolve_tools`), to `MCPConfig(tools=...)`.
    """

    SEARCH = enum.auto()
    """`glossary_search` - free-text search, local and/or live."""

    GET_TERM = enum.auto()
    """`glossary_get_term` - exact-name or URL single-term lookup."""

    GET_TERMS_ON = enum.auto()
    """`glossary_get_terms_on` - every term filed under a topic."""

    GET_TERMS_URLS = enum.auto()
    """`glossary_get_terms_urls` - lightweight URL-only listing."""

    GET_TOPICS = enum.auto()
    """`glossary_get_topics` - topic name to term-count mapping."""

    RELATED_TERMS = enum.auto()
    """`glossary_related_terms` - related-term links for a single term."""

    RANDOM_TERM = enum.auto()
    """`glossary_random_term` - one randomly chosen term."""

    COMPARE = enum.auto()
    """`glossary_compare` - side-by-side lookup of several terms."""

    SYNC = enum.auto()
    """
    `glossary_sync` - fetch from the live glossary and write into the local
    database. This is the only tool that writes anything, and is only ever 
    registered when both this flag *and* `LocalAccess.allow_write` are set.
    
    See `MCPConfig.resolved_tools`.
    """

    READ_ONLY = (
        SEARCH
        | GET_TERM
        | GET_TERMS_ON
        | GET_TERMS_URLS
        | GET_TOPICS
        | RELATED_TERMS
        | RANDOM_TERM
        | COMPARE
    )
    """Every tool that never writes to the local database. The default."""

    ALL = READ_ONLY | SYNC
    """Every tool this server knows how to build, including `SYNC`."""


TOOL_ALIASES: dict[str, Tool] = {
    member.name.lower(): member
    for member in Tool
    if member not in (Tool.READ_ONLY, Tool.ALL) and member.name is not None
}
TOOL_ALIASES["read_only"] = Tool.READ_ONLY
TOOL_ALIASES["all"] = Tool.ALL


def resolve_tools(value: Tool | str | Iterable[str] | None) -> Tool:
    """
    Normalize `value` into a `Tool` flag combination.

    :param value: A `Tool` (returned as-is), a single tool-name string
        (e.g. `"search"`, `"read_only"`), an iterable of such strings, or
        `None` for `Tool.READ_ONLY`.
    :return: The resolved `Tool` combination.
    :raises MCPConfigError: If any name in `value` isn't a known tool name.
    """
    if value is None:
        return Tool.READ_ONLY
    if isinstance(value, Tool):
        return value
    names = [value] if isinstance(value, str) else list(value)
    resolved = Tool(0)
    for name in names:
        key = name.strip().lower()
        member = TOOL_ALIASES.get(key)
        if member is None:
            choices = ", ".join(sorted(TOOL_ALIASES))
            raise MCPConfigError(f"Unknown MCP tool name {name!r}. Expected one of: {choices}.")
        resolved |= member
    return resolved


class SessionMode(enum.Enum):
    """Defines when the MCP application's live `Session` is opened and how long it lives."""

    EAGER = "eager"
    """
    Open one shared session at server startup, before any tool call. Lowest
    per-call latency, at the cost of always paying for a browser launch even
    if no live lookup is ever made.
    """

    LAZY = "lazy"
    """
    Open one shared session on the first tool call that needs it, then
    reuse it. Nothing is launched if every call is served locally. The
    default.
    """

    PER_CALL = "per_call"
    """
    Open a fresh session for every tool call that needs one, and close it
    immediately after. Slowest and heaviest, but gives every call full
    isolation. Handy under multi-tenant auth where sessions shouldn't be
    shared across callers.
    """


class RateLimitScope(enum.Enum):
    """What key a `RateLimit.limiter` is consulted under."""

    GLOBAL = "global"
    """One shared bucket for the whole server, across every client and tool."""

    CLIENT = "client"
    """One bucket per authenticated client (or `"anonymous"`), across all tools."""

    TOOL = "tool"
    """One bucket per tool name, shared by every client."""

    CLIENT_TOOL = "client_tool"
    """One bucket per `(client, tool)` pair. The default: the most granular."""


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class SessionAccess(Updatable):
    """Controls if/how/when the MCP application may open a live `Session`."""

    enabled: bool = True
    """
    Whether `Source.LIVE` (and `Source.AUTO` falling back to it) is
    available at all. `False` makes this a local-only server regardless of
    `SourcePolicy`.
    """

    mode: SessionMode = SessionMode.LAZY
    """When the shared session is opened. See `SessionMode`."""

    idle_timeout: float | None = 300.0
    """
    Seconds an `EAGER`/`LAZY` session may sit unused before a background
    reaper closes it (a later call re-opens one). `None` disables the
    reaper, so a session lives until server shutdown. Ignored for `PER_CALL`.
    """

    max_concurrent: int = 1
    """
    Maximum number of live sessions (browser instances) open at once.
    Bounded with a semaphore; relevant mainly to `PER_CALL` mode, where
    concurrent tool calls can each want their own session.
    """

    browser: BrowserSessionOptions = dataclasses.field(default_factory=BrowserSessionOptions)
    """
    Options forwarded to `slb_glossary.live.browser.open_session` -
    language, browser type, headless, resource blocking, retry policy, and so on.
    See `slb_glossary.config.BrowserSessionOptions`.
    """


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class LocalAccess(Updatable):
    """Controls if/how the MCP application may read and write the local database."""

    enabled: bool = True
    """
    Whether `Source.LOCAL` (and `Source.AUTO` preferring it) is available
    at all. `False` makes this a live-only server regardless of `SourcePolicy`.
    """

    allow_write: bool = False
    """
    Whether any tool call may write to the local database. **Off by default**:
    with this as `False`, every read tool's `persist` argument is silently
    ignored (never actually persists), and `Tool.SYNC` is never registered
    even if requested in `MCPConfig.tools` - see `MCPConfig.resolved_tools`.

    Set this to `True` to let the server cache/persists live lookups, or
    run explicit syncs.
    """

    database: DatabaseOptions = dataclasses.field(default_factory=DatabaseOptions)
    """
    Options for the local database itself: storage location, filename,
    staleness threshold. See `slb_glossary.config.DatabaseOptions`.
    """


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class SourcePolicy(Updatable):
    """Controls which `slb_glossary.query.Source` values callers may request."""

    allowed: frozenset[Source] | None = None
    """
    The set of `Source` values a tool call's `source` argument may
    resolve to. `None` (the default) is computed automatically by
    `MCPConfig` post initialization from `SessionAccess.enabled`/
    `LocalAccess.enabled`. 
    
    This defines every source this server actually has access to. 
    Set explicitly to narrow further (e.g. to `{Source.LOCAL}`
    on a server with both local and live access, to still pin every tool
    to one source).
    """

    default: Source = Source.AUTO
    """`Source` used when a tool call doesn't specify one."""

    expose_choice: bool = True
    """
    Whether tool schemas even include a `source` argument. `False` hides
    it entirely from callers/LLMs; every call then uses `default` (still
    narrowed by `allowed`, `SessionAccess.enabled`, and
    `LocalAccess.enabled`).
    """


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class Timeout(Updatable):
    """Per-call execution time caps, enforced by FastMCP's own tool `timeout=`."""

    default: float | None = 60.0
    """
    The default number of seconds a foreground tool call may run before being
    cancelled. `None` means tools run with no cap unless overridden per-tool
    in `per_tool`.
    """

    per_tool: Mapping[str, float | None] = dataclasses.field(default_factory=dict)
    """
    Tool name (e.g. `"glossary_search"`) to timeout override in
    seconds, taking precedence over `default` for that tool. A value of
    `None` here explicitly disables the timeout for that tool.
    """

    def for_tool(self, name: str) -> float | None:
        """Resolve the effective timeout, in seconds, for tool `name`."""
        if name in self.per_tool:
            return self.per_tool[name]
        return self.default


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class Auth(Updatable):
    """Controls authentication/authorization (*mainly authorization*) for tool calls."""

    backend: AuthBackend | None = None
    """
    The `slb_glossary.mcp.auth.AuthBackend` used to resolve a caller
    into a `Principal`, for this application's use (rate-limit keys, hooks,
    call logging). 
    
    `None` disables this layer and every caller is treated as
    the anonymous `Principal`.
    """

    required: bool = False
    """
    If `True` and `backend` is set, calls with no token, or a token that
    doesn't resolve to a `Principal`, are rejected. If `False`, an
    unresolved token just falls back to the anonymous principal (still
    subject to rate limiting/hooks under whatever key that resolves to).
    """

    provider: AuthProvider | None = None
    """
    A FastMCP `fastmcp.server.auth.AuthProvider` (e.g. a `TokenVerifier`
    subclass), forwarded straight to `fastmcp.FastMCP(auth=...)`. This
    protects the transport itself. Hence, an HTTP request that fails this check
    never reaches a tool call, let alone `backend` above. The two layers
    can share one underlying identity store: have your `TokenVerifier`
    and your `AuthBackend` both read from the same store/database, or wrap
    one in a small adapter.
    """


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class RateLimit(Updatable):
    """Controls optional per-tool/per-client request-rate limiting."""

    enabled: bool = False
    """Whether rate limiting is enforced at all. Off by default."""

    limiter: RateLimiter | None = None
    """
    The `slb_glossary.mcp.ratelimit.RateLimiter` to consult. 
    
    `None` while `enabled=True` builds a default in-memory
    from `limit`/`window`.
    """

    limit: int = 60
    """
    Requests allowed per `window` per rate-limit key, used only
    when `limiter` is left `None` for the default limiter to be built from.
    """

    window: float = 60.0
    """Sliding window size, in seconds, used only when `limiter` is left `None`."""

    scope: RateLimitScope = RateLimitScope.CLIENT_TOOL
    """What key `limiter` is consulted under. See `RateLimitScope`."""


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class Hooks(Updatable):
    """Caller-supplied hooks run around every tool call and around the server's lifecycle."""

    before_tool: tuple[BeforeToolHook, ...] = ()
    """
    Run in order, just before each tool call's body. See
    `slb_glossary.mcp.types.BeforeToolHook`.
    """

    after_tool: tuple[AfterToolHook, ...] = ()
    """
    Run in order, after each successful tool call. See
    `slb_glossary.mcp.types.AfterToolHook`.
    """

    on_error: tuple[ToolErrorHook, ...] = ()
    """
    Run in order, when a tool call's body raises. See
    `slb_glossary.mcp.types.ToolErrorHook`.
    """

    on_startup: tuple[LifecycleHook, ...] = ()
    """Run in order, once, before the server starts accepting calls."""

    on_shutdown: tuple[LifecycleHook, ...] = ()
    """
    Run in order, once, while the server is shutting down (after every
    resource `slb_glossary.mcp.runtime.Runtime` opened has been closed).
    """


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class Logging(Updatable):
    """
    Controls where/how `slb_glossary`'s logging is routed for this server process.

    Mirrors `slb_glossary.logging.configure_logging`'s own parameters
    closely, so anything that module supports is available here too.
    """

    sinks: SinksSpec = None
    """
    Where to route logging. Any single `slb_glossary.logging.resolve_sink`
    spec (a `LogSink` instance/class, `"stderr"`/`"stdout"`, a file path, or
    a `"module:ClassName"` import path), several, or a `{filter: sink(s)}`
    mapping to send only matching log records to each sink, e.g. everything
    from a live session/the query API to one file, everything else to
    another. See `slb_glossary.logging.SinkHandler`.

    `None` leaves whatever logging setup is already in place untouched.
    """

    level: int | str | None = None
    """
    Logging level for `logger_name`'s logger. `None` leaves the current
    level untouched.
    """

    logger_name: str = "slb_glossary"
    """
    Name of the logger `sinks`/`level` are applied to. Defaults to
    `slb_glossary`'s package root logger, so every module's own logger
    propagates up to it.
    """

    fmt: str | None = None
    """
    `logging.Formatter` format string used for every sink. `None` uses
    `slb_glossary.constants.constants.log_format`.
    """

    propagate: bool = False
    """
    Whether `logger_name`'s logger should still propagate records to its
    own ancestor loggers after also sending them to `sinks`.
    """

    log_tool_calls: bool = True
    """
    Whether the server's own middleware logs each tool call's name,
    caller, duration, and outcome at `INFO`/`WARNING` level.
    """


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class Streaming(Updatable):
    """Controls the optional `stream` argument tools that can stream expose."""

    default: bool = False
    """
    Default value of the `stream` argument on tools that support it
    (`glossary_search`, `glossary_get_terms_on`). When enabled, the tool
    reports incremental MCP progress notifications (via `Context.report_progress`)
    as results are found, in addition to still returning the full result at
    the end.
    
    MCP tool results are not itself partial/incremental, so this
    is progress reporting, not a change in what's ultimately returned.
    """

    allow_override: bool = True
    """
    Whether a tool call may override `default` with its own `stream`
    argument. `False` hides the argument and always uses `default`.
    """


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class ServerInfo(Updatable):
    """Identity metadata for the MCP application/server itself."""

    name: str = "slb-glossary"
    """Server name advertised to MCP clients, and used as for logging."""

    version: str | None = None
    """Server version advertised to MCP clients. `None` uses `slb_glossary.__version__`."""

    instructions: str | None = None
    """
    Free-text instructions shown to connecting clients/LLMs about how to
    use this server as a whole. `None` uses a sensible built-in default.

    See `slb_glossary.mcp.tools.DEFAULT_INSTRUCTIONS`.
    """


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class MCPConfig(Updatable):
    """
    Top-level configuration for `slb_glossary.mcp.api.MCPApp`.

    ```python
    from slb_glossary.mcp.config import MCPConfig, LocalAccess, Tool

    config = MCPConfig(
        local=LocalAccess(allow_write=True),
        tools=Tool.ALL,
    )
    ```

    Every field has a default, so `MCPConfig()` alone is a valid,
    read-only, local-and-live, unauthenticated, unlimited-rate server
    configuration. Validated at construction time.

    Build with `.update(...)` to change one field without re-specifying the rest.
    """

    server: ServerInfo = dataclasses.field(default_factory=ServerInfo)
    """Server identity metadata."""

    session: SessionAccess = dataclasses.field(default_factory=SessionAccess)
    """Live `Session` access."""

    local: LocalAccess = dataclasses.field(default_factory=LocalAccess)
    """Local database access."""

    source_policy: SourcePolicy = dataclasses.field(default_factory=SourcePolicy)
    """Which `Source` values tool calls may resolve to."""

    tools: Tool = Tool.READ_ONLY
    """
    Which tools to build. See `Tool`, `resolve_tools`, and
    `resolved_tools` for how this interacts with `local.allow_write`.
    """

    timeouts: Timeout = dataclasses.field(default_factory=Timeout)
    """Per-call execution time caps."""

    auth: Auth = dataclasses.field(default_factory=Auth)
    """Authentication/authorization."""

    rate_limit: RateLimit = dataclasses.field(default_factory=RateLimit)
    """Per-tool/per-client request-rate limiting."""

    hooks: Hooks = dataclasses.field(default_factory=Hooks)
    """Caller-supplied lifecycle and per-call hooks."""

    logging: Logging = dataclasses.field(default_factory=Logging)
    """Logging routing for this server process."""

    streaming: Streaming = dataclasses.field(default_factory=Streaming)
    """Progress-reporting behavior for tools that support it."""

    def __post_init__(self) -> None:
        if not self.session.enabled and not self.local.enabled:
            raise MCPConfigError(
                f"{type(self).__name__}: at least one of `session.enabled`/`local.enabled` "
                f"must be True. A server with neither can't read anything."
            )

        allowed = self.source_policy.allowed
        if allowed is None:
            computed = {Source.AUTO}
            if self.local.enabled:
                computed.add(Source.LOCAL)
            if self.session.enabled:
                computed.add(Source.LIVE)
            object.__setattr__(
                self,
                "source_policy",
                dataclasses.replace(self.source_policy, allowed=frozenset(computed)),
            )
            allowed = self.source_policy.allowed
            assert allowed is not None  # mypy can't see that object.__setattr__ changed it
        else:
            if not allowed:
                raise MCPConfigError(
                    f"{type(self).__name__}: `source_policy.allowed` must not be empty."
                )
            if Source.LOCAL in allowed and not self.local.enabled:
                raise MCPConfigError(
                    f"{type(self).__name__}: `source_policy.allowed` includes `Source.LOCAL` "
                    f"but `local.enabled` is False."
                )
            if Source.LIVE in allowed and not self.session.enabled:
                raise MCPConfigError(
                    f"{type(self).__name__}: `source_policy.allowed` includes `Source.LIVE` "
                    f"but `session.enabled` is False."
                )

        if self.source_policy.default not in allowed:
            raise MCPConfigError(
                f"{type(self).__name__}: `source_policy.default` ({self.source_policy.default}) "
                f"must be a member of `source_policy.allowed` ({allowed})."
            )
        if self.rate_limit.enabled and self.rate_limit.limit <= 0:
            raise MCPConfigError(f"{type(self).__name__}: `rate_limit.limit` must be positive.")
        if self.session.max_concurrent < 1:
            raise MCPConfigError(
                f"{type(self).__name__}: `session.max_concurrent` must be at least 1."
            )

    def resolve_tools(self) -> Tool:
        """
        Return the actual set of tools to build, after gating `Tool.SYNC` on write access.

        `Tool.SYNC` is stripped out unless *both* `Tool.SYNC` is in `tools`
        *and* `local.allow_write` is `True`. So flipping on a
        write-capable tool always requires an explicit, deliberate
        `local.allow_write=True` in addition to requesting the tool itself.

        :return: `self.tools`, with `Tool.SYNC` cleared if write access isn't granted.
        """
        tools = self.tools
        if Tool.SYNC in tools and not self.local.allow_write:
            tools &= ~Tool.SYNC
        return tools

    @classmethod
    def default(cls, *, language: str | Language | None = None) -> Self:
        """
        Return a fresh, all-defaults `MCPConfig`.

        Equivalent to `MCPConfig()` when `language` is omitted. `language`
        exists as a shortcut for the one setting that's awkward to reach
        even through `.update(...)` alone, since it's nested three levels
        down (`session.browser.language`):

        ```python
        config = MCPConfig.default(language="es")

        # instead of:
        config = MCPConfig.default().update(
            session=SessionAccess().update(
                browser=BrowserSessionOptions().update(language="es"),
            ),
        )
        ```

        :param language: Glossary language edition the default session
            should search - `"en"`/`"es"`, or a `Language` member. `None`
            (the default) leaves `SessionAccess.browser.language` at its
            own default (`"en"`).
        :return: A fresh `MCPConfig`, defaults throughout except for
            `session.browser.language` if `language` was given.
        :raises MCPConfigError: If `language` is a string that isn't a
            valid `Language` value.
        """
        config = cls()
        if language is None:
            return config

        if isinstance(language, Language):
            language_value = language.value
        else:
            try:
                language_value = Language(language).value
            except ValueError as exc:
                choices = ", ".join(lang.value for lang in Language)
                raise MCPConfigError(
                    f"Unknown language {language!r}. Expected one of: {choices}."
                ) from exc

        return config.update(
            session=config.session.update(
                browser=config.session.browser.update(language=language_value),
            ),
        )
