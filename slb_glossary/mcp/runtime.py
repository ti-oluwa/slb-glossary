"""Builds the `slb_glossary.live.Runtime` the MCP server runs on from an `MCPConfig`."""

from slb_glossary.live.runtime import Runtime
from slb_glossary.mcp.config import MCPConfig

__all__ = ["Runtime", "build_runtime"]


def build_runtime(config: MCPConfig) -> Runtime:
    """
    Build the `Runtime` an `MCPApp` configured by `config` runs on.

    :param config: The MCP server's configuration.
    :return: A new, not yet started `Runtime`.
    """
    return Runtime(
        name=config.server.name,
        session_options=config.session.options,
        database_options=config.local.database,
        mode=config.session.mode,
        local_enabled=config.local.enabled,
        live_enabled=config.session.enabled,
        idle_timeout=config.session.idle_timeout,
        max_sessions=config.session.max_sessions,
        capacity_tolerance=config.session.capacity_tolerance,
    )
