"""
A complete, real-world MCP server: read-and-write access, a bounded shared
browser session, file + stderr logging, and a longer timeout for the one
tool (`glossary_sync`) that can legitimately take a while.

Run it directly (`python -m examples.mcp_app`) to serve over streamable
HTTP, or point `slb mcp serve` at it: `slb mcp serve examples.mcp_app:app`.
"""

import slb_glossary as slb
import slb_glossary.mcp as slb_mcp

config = slb_mcp.MCPConfig(
    # Shown to a connecting client as this server's identity.
    server=slb_mcp.ServerInfo(name="example-mcp", version="0.0.1"),
    # A shared browser session, opened lazily on first use rather than at
    # startup, and capped at 3 pages in flight at once so a burst of
    # concurrent tool calls can't spin up unbounded browser tabs.
    session=slb_mcp.SessionAccess(
        enabled=True,
        max_concurrent=3,
        mode=slb_mcp.SessionMode.LAZY,
        options=slb.config.SessionOptions(
            use_stealth=False,
            log_sink=slb.log.FileSink("./example.mcp.browser.log"),
        ),
    ),
    # Lets an agent trigger `glossary_sync`, which writes to the local
    # database. Leave this `False` for a read-only deployment.
    local=slb_mcp.LocalAccess(allow_write=True),
    tools=slb_mcp.Tool.ALL,
    # Tool-call progress notifications are opt-in per call by default;
    # `allow_override=False` means a caller can't turn them on themselves.
    streaming=slb_mcp.Streaming(allow_override=False),
    # `glossary_sync` over a large topic can run well past a typical
    # request timeout, so it gets 5 minutes instead of accepting whatever
    # the transport's own default would otherwise be.
    timeouts=slb_mcp.Timeout(default=60.0, per_tool={"glossary_sync": 300.0}),
    # Every tool call, and this server's own startup/shutdown, logged to
    # both a file (for later inspection) and stderr (for `docker logs`,
    # a systemd journal, or whatever's watching this process live).
    logging=slb_mcp.Logging(
        sinks=[slb.log.FileSink("./example.mcp.log"), slb.log.StderrSink()],
        level="debug",
        log_tool_calls=True,
    ),
)
app = slb_mcp.MCPApp(config)

if __name__ == "__main__":
    app.run(transport="streamable-http")
