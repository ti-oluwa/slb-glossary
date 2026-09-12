"""Tests for `mcp.auth`: `get_principal_from_token`, `StaticTokenVerifier`, `import_provider`."""

import time

import pytest
from fastmcp.server.auth import AuthProvider
from fastmcp.server.auth.auth import AccessToken

from slb_glossary.mcp.auth import (
    ANONYMOUS,
    Principal,
    StaticTokenVerifier,
    get_principal_from_token,
    import_provider,
)

pytestmark = pytest.mark.unit


class TestGetPrincipalFromToken:
    def test_none_returns_anonymous(self) -> None:
        """No `AccessToken` at all resolves to the shared `ANONYMOUS` principal."""
        assert get_principal_from_token(None) is ANONYMOUS

    def test_a_real_token_resolves_id_and_scopes(self) -> None:
        """A real `AccessToken` resolves to a `Principal` carrying its client id and scopes."""
        token = AccessToken(token="abc", client_id="alice", scopes=["read", "write"])
        principal = get_principal_from_token(token)
        assert principal == Principal(id="alice", scopes=frozenset({"read", "write"}))

    def test_a_token_with_no_scopes(self) -> None:
        """A token with no scopes at all resolves to an empty scope set, not an error."""
        token = AccessToken(token="abc", client_id="alice", scopes=[])
        principal = get_principal_from_token(token)
        assert principal.scopes == frozenset()


class TestStaticTokenVerifier:
    pytestmark = pytest.mark.anyio

    async def test_string_shorthand_becomes_the_client_id(self) -> None:
        """A bare string value is shorthand for that client id, with no scopes."""
        verifier = StaticTokenVerifier({"tok-1": "alice"})
        access_token = await verifier.verify_token("tok-1")
        assert access_token is not None
        assert access_token.client_id == "alice"
        assert access_token.scopes == []

    async def test_mapping_value_sets_client_id_and_scopes(self) -> None:
        """A mapping value sets `client_id`/`scopes` explicitly."""
        verifier = StaticTokenVerifier({"tok-1": {"client_id": "alice", "scopes": ["admin"]}})
        access_token = await verifier.verify_token("tok-1")
        assert access_token is not None
        assert access_token.client_id == "alice"
        assert access_token.scopes == ["admin"]

    async def test_mapping_value_without_client_id_defaults_to_the_token(self) -> None:
        """A mapping value with no `client_id` defaults to the token string itself."""
        verifier = StaticTokenVerifier({"tok-1": {"scopes": ["read"]}})
        access_token = await verifier.verify_token("tok-1")
        assert access_token is not None
        assert access_token.client_id == "tok-1"

    async def test_unknown_token_returns_none(self) -> None:
        """A token not in the mapping resolves to `None`, not an error."""
        verifier = StaticTokenVerifier({"tok-1": "alice"})
        assert await verifier.verify_token("not-a-real-token") is None

    async def test_expired_token_returns_none(self) -> None:
        """A token whose `expires_at` is in the past is rejected, even though it's a known token."""
        verifier = StaticTokenVerifier(
            {"tok-1": {"client_id": "alice", "expires_at": int(time.time()) - 60}}
        )
        assert await verifier.verify_token("tok-1") is None

    async def test_unexpired_token_is_accepted(self) -> None:
        """A token whose `expires_at` is in the future is accepted."""
        verifier = StaticTokenVerifier(
            {"tok-1": {"client_id": "alice", "expires_at": int(time.time()) + 3600}}
        )
        assert await verifier.verify_token("tok-1") is not None

    def test_repr_shows_the_token_count(self) -> None:
        """`repr()` reports how many tokens are configured, without leaking them."""
        verifier = StaticTokenVerifier({"tok-1": "alice", "tok-2": "bob"})
        assert "2 token" in repr(verifier)
        assert "tok-1" not in repr(verifier)


class ADummyAuthProvider(AuthProvider):
    """A minimal, no-argument-constructible `AuthProvider`, for `import_provider` tests."""


class TestImportProvider:
    def test_module_colon_class_path(self) -> None:
        """A `"module:ClassName"` path imports and instantiates the class."""
        provider = import_provider("tests.mcp.test_mcp_auth:ADummyAuthProvider")
        assert isinstance(provider, ADummyAuthProvider)

    def test_dotted_path(self) -> None:
        """A plain dotted `"package.module.ClassName"` path also works."""
        provider = import_provider("tests.mcp.test_mcp_auth.ADummyAuthProvider")
        assert isinstance(provider, ADummyAuthProvider)

    def test_missing_separator_raises_value_error(self) -> None:
        """A string with no `:` and no `.` at all is not a valid import path."""
        with pytest.raises(ValueError, match="not a valid auth-provider import path"):
            import_provider("not_a_path_at_all")

    def test_unimportable_module_raises_import_error(self) -> None:
        """A module that doesn't exist raises `ImportError`."""
        with pytest.raises(ImportError):
            import_provider("this.module.does.not.exist:SomeClass")

    def test_missing_attribute_raises_import_error(self) -> None:
        """A real module with no such attribute raises `ImportError`."""
        with pytest.raises(ImportError, match="has no attribute"):
            import_provider("tests.mcp.test_mcp_auth:NoSuchClass")

    def test_non_auth_provider_class_raises_type_error(self) -> None:
        """A resolved class that doesn't extend `AuthProvider` is rejected."""
        with pytest.raises(TypeError, match="does not extend"):
            import_provider("tests.mcp.test_mcp_auth:TestImportProvider")
