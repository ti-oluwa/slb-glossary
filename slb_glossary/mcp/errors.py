"""Exceptions raised by `slb_glossary.mcp`."""

from slb_glossary.errors import SLBGlossaryError

__all__ = [
    "AuthenticationError",
    "MCPConfigError",
    "MCPError",
    "RateLimitExceededError",
]


class MCPError(SLBGlossaryError):
    """Base exception for every error `slb_glossary.mcp` raises."""


class MCPConfigError(MCPError):
    """Raised when an `slb_glossary.mcp.config.MCPConfig` (or a nested config) is invalid."""


class AuthenticationError(MCPError):
    """Raised when a caller couldn't be authenticated and authentication is required."""


class RateLimitExceededError(MCPError):
    """Raised when a caller has exhausted their request-rate quota for a rate-limit key."""

    def __init__(self, key: str, *, wait_ms: float) -> None:
        """
        :param key: The rate-limit key that was exceeded.
        :param wait_ms: Milliseconds the caller should wait before retrying.
        """
        super().__init__(f"Rate limit exceeded for {key!r}; retry after {wait_ms:.0f}ms.")
        self.key = key
        self.wait_ms = wait_ms
