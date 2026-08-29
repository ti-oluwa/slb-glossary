"""Exceptions raised by `slb_glossary.mcp`."""

from slb_glossary.errors import SLBGlossaryError

__all__ = ["MCPConfigError", "MCPError"]


class MCPError(SLBGlossaryError):
    """Base exception for every error `slb_glossary.mcp` raises."""


class MCPConfigError(MCPError):
    """Raised when an `slb_glossary.mcp.config.MCPConfig` (or a nested config) is invalid."""
