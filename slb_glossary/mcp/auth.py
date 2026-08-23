"""
Pluggable, request-aware authentication/authorization for `slb_glossary.mcp`.

This is independent of `FastMCP`'s own OAuth-flavored
`fastmcp.server.auth.AuthProvider` machinery, which secures the
*transport* (e.g. rejecting an unauthenticated HTTP request before it ever
reaches a tool). `AuthBackend` here resolves each tool call into a
`Principal` that the server's middleware, rate limiter, and hooks key off
of as a different, but complementary layer.

See `slb_glossary.mcp.config.Auth` for how the two combine, and
`slb_glossary.mcp.api.MCPApp` for where each is wired in.

```python
from slb_glossary.mcp.auth import Principal, StaticTokenAuth

auth = StaticTokenAuth(
    {
        "sk-alice-...": Principal(id="alice", scopes=frozenset({"read", "write"})),
        "sk-bot-...": "readonly-bot",  # bare string is shorthand for Principal(id=...)
    }
)
```
"""

import importlib
import types
import typing
from collections.abc import Mapping

__all__ = [
    "Principal",
    "AuthRequest",
    "AuthBackend",
    "StaticTokenAuth",
    "NullAuth",
    "ANONYMOUS",
    "import_backend",
]


class Principal(typing.NamedTuple):
    """An authenticated (or anonymous) caller identity."""

    id: str
    """Stable identifier for this caller"""

    scopes: frozenset[str] = frozenset()
    """
    Free-form authorization scopes this caller holds. You can read them from
    `slb_glossary.mcp.types.ToolRunContext.principal` in a hook or a
    custom `AuthBackend` if you need scope-gated behavior.
    """

    metadata: Mapping[str, typing.Any] = types.MappingProxyType({})
    """
    Arbitrary extra free-form data an `AuthBackend` wants to carry alongside the
    principal (e.g. a display name, a plan tier).
    """


ANONYMOUS = Principal(id="anonymous")
"""The `Principal` used for calls with no token, when `Auth.required` is `False`."""


class AuthRequest(typing.NamedTuple):
    """
    Holds everything an `AuthBackend` gets to resolve a caller's identity from.
    """

    token: str | None
    """
    The bearer token parsed from the `Authorization: Bearer <token>`
    header, with the scheme prefix stripped, or `None` if there wasn't
    one (including on transports with no per-request headers, like stdio).
    """

    headers: Mapping[str, str]
    """
    Every HTTP header on the incoming request, lower-cased keys. Empty
    on transports with no per-request headers (e.g. stdio).
    """

    tool_name: str
    """The MCP tool name about to be called."""

    arguments: Mapping[str, typing.Any]
    """The tool call's raw arguments, as a plain mapping. """


@typing.runtime_checkable
class AuthBackend(typing.Protocol):
    """
    Protocol for resolving an `AuthRequest` into a `Principal`.

    Pass an instance to `slb_glossary.mcp.config.Auth.backend`.
    """

    async def authenticate(self, request: AuthRequest) -> Principal | None:
        """
        Resolves `request` into a `Principal`.

        :param request: The token, headers, and call metadata to authenticate from.
        :return: The resolved `Principal`, or `None` if `request` doesn't
            resolve to anyone. A `None` return is treated as
            "unauthenticated" and rejected if `Auth.required` is `True`,
            otherwise falls back to `ANONYMOUS`.
        """
        ...


class StaticTokenAuth:
    """An `AuthBackend` backed by a fixed, in-process mapping of token to `Principal`."""

    __slots__ = ("_tokens",)

    def __init__(self, tokens: Mapping[str, Principal | str]) -> None:
        """
        Initialize the backend with a mapping of bearer token to
        `Principal` (or bare string shorthand).

        :param tokens: Mapping of raw bearer token to either a `Principal`
            directly, or a bare `str` used as shorthand for
            `Principal(id=that_string)`.
        """
        resolved: dict[str, Principal] = {}
        for token, value in tokens.items():
            resolved[token] = value if isinstance(value, Principal) else Principal(id=value)
        self._tokens = resolved

    async def authenticate(self, request: AuthRequest) -> Principal | None:
        if request.token is None:
            return None
        return self._tokens.get(request.token)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({len(self._tokens)} token(s))"


class NullAuth:
    """An `AuthBackend` that authenticates nobody. Equivalent to leaving `backend=None`."""

    __slots__ = ()

    async def authenticate(self, request: AuthRequest) -> Principal | None:
        return None


def import_backend(dotted_path: str) -> AuthBackend:
    """
    Import and instantiate an `AuthBackend` from a dotted path, with no constructor arguments.

    :param dotted_path: `"module:ClassName"` or `"package.module.ClassName"`.
    :return: An instance of the imported class.
    :raises ValueError: If `dotted_path` doesn't look like a valid import path.
    :raises ImportError: If the module can't be imported, or has no such attribute.
    :raises TypeError: If the resolved attribute isn't a no-argument-constructible `AuthBackend`.
    """
    module_path, _, attr = dotted_path.partition(":")
    if not attr:
        module_path, _, attr = dotted_path.rpartition(".")
    if not module_path or not attr:
        raise ValueError(
            f"{dotted_path!r} is not a valid auth-backend import path. Use "
            f"'module:ClassName' or 'package.module.ClassName'."
        )

    module = importlib.import_module(module_path)
    try:
        target = getattr(module, attr)
    except AttributeError as exc:
        raise ImportError(f"Module {module_path!r} has no attribute {attr!r}") from exc

    backend = target() if isinstance(target, type) else target
    if not isinstance(backend, AuthBackend):
        raise TypeError(
            f"{dotted_path!r} resolved to {backend!r}, which doesn't implement "
            f"`AuthBackend` (an `authenticate(self, request)` coroutine method)."
        )
    return backend
