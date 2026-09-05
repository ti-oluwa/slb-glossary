"""
Caller identity authentication for `slb_glossary.mcp`, derived from FastMCP's own auth layer.

```python
from slb_glossary.mcp.auth import StaticTokenVerifier

provider = StaticTokenVerifier(
    {
        "sk-alice-...": {"client_id": "alice", "scopes": ["read", "write"]},
        "sk-bot-...": {"client_id": "readonly-bot"},
    }
)
```
"""

import importlib
import time
import typing

from fastmcp.server.auth import AuthProvider
from fastmcp.server.auth.auth import AccessToken, TokenVerifier

__all__ = [
    "ANONYMOUS",
    "Principal",
    "StaticTokenVerifier",
    "get_principal_from_token",
    "import_provider",
]


class Principal(typing.NamedTuple):
    """An authenticated (or anonymous) caller identity."""

    id: str
    """Stable identifier for this caller. A FastMCP `AccessToken.client_id`, or `"anonymous"`."""

    scopes: frozenset[str] = frozenset()
    """
    This caller's OAuth scopes, straight from their `AccessToken`. Read
    them from `slb_glossary.mcp.types.ToolRunContext.principal` in a hook
    if you need scope-gated behavior beyond what
    `slb_glossary.mcp.config.Auth.required_scopes` already enforces.
    """


ANONYMOUS = Principal(id="anonymous")
"""The `Principal` used when there's no `AccessToken` for the current call (no `Auth.provider` configured, or an unauthenticated transport like stdio)."""


def get_principal_from_token(token: AccessToken | None) -> Principal:
    """
    Resolve FastMCP's current-call `AccessToken` into a `Principal`.

    :param token: The result of `fastmcp.server.dependencies.get_access_token()`.
    :return: `ANONYMOUS` if `token` is `None`, otherwise a `Principal`
        built from its `client_id`/`scopes`.
    """
    if token is None:
        return ANONYMOUS
    return Principal(id=token.client_id, scopes=frozenset(token.scopes))


class StaticTokenVerifier(TokenVerifier):
    """
    A FastMCP `AuthProvider` backed by a fixed, in-process mapping of bearer token to identity.

    Unlike a hand-rolled lookup done inside a tool or middleware, this is
    a real `TokenVerifier`. Pass it to `Auth.provider` and it secures the
    transport itself, the same as any OAuth-backed provider.
    An unrecognized token never reaches a tool call.

    Meant for simple, fixed API-key setups. For anything backed by a
    database, external identity provider, or tokens that expire/rotate,
    implement your own `TokenVerifier` instead.
    """

    def __init__(self, tokens: typing.Mapping[str, str | typing.Mapping[str, typing.Any]]) -> None:
        """
        Initialize the verifier.

        :param tokens: Mapping of raw bearer token to either a bare `str`
            (shorthand for `{"client_id": that_string}`), or a mapping of
            `AccessToken` constructor kwargs (`client_id`, `scopes`,
            `expires_at`, `claims`, ...). `client_id` defaults to the
            token string itself if omitted.
        """
        super().__init__()
        resolved: dict[str, AccessToken] = {}
        for token, value in tokens.items():
            if isinstance(value, str):
                resolved[token] = AccessToken(token=token, client_id=value, scopes=[])
                continue

            kwargs = dict(value)
            kwargs.setdefault("client_id", token)
            kwargs.setdefault("scopes", [])
            resolved[token] = AccessToken(token=token, **kwargs)
        self._tokens = resolved

    async def verify_token(self, token: str) -> AccessToken | None:
        access_token = self._tokens.get(token)
        if access_token is None:
            return None
        if access_token.expires_at is not None and access_token.expires_at < time.time():
            return None
        return access_token

    def __repr__(self) -> str:
        return f"{type(self).__name__}({len(self._tokens)} token(s))"


def import_provider(dotted_path: str) -> AuthProvider:
    """
    Import and instantiate an `AuthProvider` from a dotted path, with no constructor arguments.

    :param dotted_path: `"module:ClassName"` or `"package.module.ClassName"`.
    :return: An instance of the imported class.
    :raises ValueError: If `dotted_path` does not look like a valid import path.
    :raises ImportError: If the module can not be imported, or has no such attribute.
    :raises TypeError: If the resolved attribute is not a no-argument-constructible `AuthProvider`.
    """
    module_path, _, attr = dotted_path.partition(":")
    if not attr:
        module_path, _, attr = dotted_path.rpartition(".")
    if not module_path or not attr:
        raise ValueError(
            f"{dotted_path!r} is not a valid auth-provider import path. Use "
            f"'module:ClassName' or 'package.module.ClassName'."
        )

    module = importlib.import_module(module_path)
    try:
        target = getattr(module, attr)
    except AttributeError as exc:
        raise ImportError(f"Module {module_path!r} has no attribute {attr!r}") from exc

    provider = target() if isinstance(target, type) else target
    if not isinstance(provider, AuthProvider):
        raise TypeError(
            f"{dotted_path!r} resolved to {provider!r}, which does not extend "
            f"`fastmcp.server.auth.AuthProvider`."
        )
    return provider
