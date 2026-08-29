import slb_glossary as slb
import slb_glossary.mcp as slb_mcp

config = slb_mcp.MCPConfig(
    server=slb_mcp.ServerInfo(name="example-mcp", version="0.0.1"),
    session=slb_mcp.SessionAccess(
        enabled=True,
        max_concurrent=3,
        mode=slb_mcp.SessionMode.LAZY,
        options=slb.config.SessionOptions(
            use_stealth=False,
            log_sink=slb.log.FileSink("./example.mcp.browser.log"),
        ),
    ),
    local=slb_mcp.LocalAccess(allow_write=True),
    tools=slb_mcp.Tool.ALL,
    streaming=slb_mcp.Streaming(allow_override=False),
    timeouts=slb_mcp.Timeout(default=300),
    logging=slb_mcp.Logging(
        sinks=[slb.log.FileSink("./example.mcp.log"), slb.log.StderrSink()], level="debug"
    ),
)
app = slb_mcp.MCPApp(config)

if __name__ == "__main__":
    app.run(transport="streamable-http")
