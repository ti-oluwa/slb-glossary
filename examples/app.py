"""
A complete MCP server with read-and-write access, a bounded shared
browser session, and file + stderr logging.

Run it directly (`python -m examples.app`) to serve over streamable
HTTP, or point `slb mcp serve` at it like so; `slb mcp serve examples.app:app`.
"""

import slb_glossary as slb
import slb_glossary.mcp as slb_mcp

config = slb_mcp.MCPConfig(
    # Shown to a connecting client as this server's identity.
    server=slb_mcp.ServerInfo(name="example-mcp", version="0.0.1"),
    # A pooled browser session per language, opened lazily on first use
    # rather than at startup. `max_pages` caps concurrent operations
    # (page tabs) within one session; `max_sessions` caps how many
    # browser instances may be open at once, across every language
    # combined - the pool opens an extra one for a language only once its
    # existing session(s) are already full.
    session=slb_mcp.SessionAccess(
        enabled=True,
        max_sessions=2,
        mode=slb_mcp.SessionMode.LAZY,
        options=slb.config.SessionOptions(
            max_pages=3,
            use_stealth=False,
            log_sink=slb.log.FileSink("./example.mcp.browser.log"),
        ),
    ),
    # Lets an agent trigger `glossary_sync`, which writes to the local
    # database. Leave this `False` for a read-only deployment.
    local=slb_mcp.LocalAccess(allow_write=True),
    tools=slb_mcp.Tool.ALL,
    # Tool-call progress notifications are opt-in per call by default;
    # `allow_override=False` means a caller can not turn them on themselves.
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
