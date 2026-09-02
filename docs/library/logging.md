# Logging

Every part of this library logs through one shared logger, `slb_glossary`'s own package root logger, so a single call routes everything to wherever you want it. That can be a file for later inspection, `stderr` for whatever's watching the process live, or several destinations split by which part of the library produced each record.

---

## The quick way: `configure_logging`

```python
import slb_glossary as slb

slb.logging.configure_logging(sinks="app.log", level="debug")
```

A bare string is treated as a file path. Calling `configure_logging` again later (for instance, because a long-running process wants to change where it logs mid-run) cleanly tears down the handler it previously attached before setting up the new one, so repeated calls never pile up duplicate handlers or duplicate log lines.

```python
slb.logging.configure_logging(sinks="stderr")  # explicit stderr
slb.logging.configure_logging(sinks=[slb.log.StderrSink(), "app.log"])  # both at once
```

## Sinks

A **sink** is anywhere a (formatted) log line can go. `slb_glossary.logging` (aliased `slb.log`) ships four defautl sinks, and you can write your own as long as it satisfies the `LogSink` protocol (a `write(message)` method; `flush()`/`close()` optional):

| Sink | Writes to |
|---|---|
| `StderrSink()` | stderr, the default if you don't specify one. |
| `StdoutSink()` | stdout. |
| `FileSink(path, mode="a", encoding="utf-8")` | A file, opened lazily on first write. `mode="w"` truncates each run instead of appending. |
| a `"module:ClassName"` string | Your own class, loaded via `slb.logging.import_sink`, as long as it satisfies the `LogSink` protocol (a `write(message)` method; `flush()`/`close()` optional). |

```python
slb.logging.configure_logging(sinks=slb.log.FileSink("./logs/glossary.log", mode="w"))
```

## Routing different parts of the library to different sinks

`sinks` also accepts a `{filter: sink(s)}` mapping, so you're not limited to sending everything to the same place:

```python
slb.logging.configure_logging(
    sinks={
        "slb_glossary.live*": slb.log.FileSink("./browser.log"),  # every live-session log line
        "slb_glossary.query*": slb.log.FileSink(
            "./queries.log"
        ),  # every query.search/compare/... call
        "*": slb.log.StderrSink(),  # everything else
    },
)
```

Each key is either an `fnmatch`-style pattern matched against the record's logger name (`"slb_glossary.live*"` catches `slb_glossary.live.browser`, `slb_glossary.live.api`, and so on), or a callable taking a `logging.LogRecord` and returning whether it belongs to that route. A record matching more than one pattern goes to every sink whose pattern matched, not just the first.

## Setting just the level

```python
slb.logging.set_log_level("debug")
```

For changing verbosity without touching where output goes at all, `configure_logging(level=...)` does the same thing, but also lets you set `sinks` in the same call if you want both.

## Where a session's own browser logs go

`session()`'s `log_sink` parameter is independent of the rest of this page. It routes only that one session's own browser-automation log lines (page navigation, retries, timeouts), leaving everything else on whatever `configure_logging` set up:

```python
async with slb.live.session(log_sink=slb.log.FileSink("./this-session.log")) as session:
    ...
```

## The CLI's version of this page

The CLI's `--log-level`, `--log-to`, and `--log-sink` flags (covered in [Saving, Output and Config Files](../cli/configuration.md#logging)) are a thin wrapper over exactly `configure_logging`. The MCP server has its own, separate `Logging` config section, covering the server process as a whole rather than one session, see [Running an MCP Server](../agent/mcp-server.md#logging).
