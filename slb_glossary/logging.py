"""
Pluggable logging sinks for `slb_glossary`.

This module exists for callers who want more control over where
log records end up. May be a file for bug reports, `stderr`/`stdout`
explicitly, or a fully custom destination, without having to hand-roll `
logging.Handler` boilerplate themselves.

```python
from slb_glossary.logging import FileSink, configure_logging

# Route every slb_glossary log record to a file for this run.
configure_logging(sinks=FileSink("slb-glossary.log"), level="DEBUG")
```

Sinks can also be routed selectively. Pass a `{filter: sink}` mapping
instead of a single sink/list, and each record only goes to the sink(s)
whose filter matches it.

```python
from slb_glossary.logging import FileSink, StderrSink, configure_logging

# Everything from the query API (and its live/local sub-loggers) goes to
# a dedicated file; everything else still prints to stderr as usual.
configure_logging(
    sinks={
        "slb_glossary.query*": FileSink("query.log"),
        "*": StderrSink(),
    }
)
```
"""

import contextlib
import fnmatch
import importlib
import logging
import pathlib
import sys
import typing
from collections.abc import Iterable, Mapping

from rich.logging import RichHandler

from slb_glossary.constants import constants

__all__ = [
    "ConsoleSink",
    "FileSink",
    "LogSink",
    "LogSinkHandler",
    "SinkFilter",
    "SinkSpec",
    "SinksSpec",
    "StderrSink",
    "StdoutSink",
    "configure_logging",
    "import_sink",
    "resolve_sink",
    "resolve_sinks",
    "set_log_level",
]


@typing.runtime_checkable
class LogSink(typing.Protocol):
    """
    Protocol for a destination formatted log lines can be written to.

    Implement this interface to route `slb_glossary`'s logging anywhere.
    A file, a socket, a queue for an in-app log viewer, a bug-report buffer, etc.
    """

    def write(self, message: str) -> None:
        """Write one already-formatted log line to the sink."""
        ...

    def flush(self) -> None:
        """Flush any buffered output. May be a no-op."""
        ...

    def close(self) -> None:
        """Release any resources held by the sink. May be a no-op."""
        ...


class ConsoleSink:
    """A `LogSink` that writes to a given text stream (`sys.stderr` by default)."""

    __slots__ = ("_close", "_stream")

    def __init__(self, stream: typing.TextIO | None = None, close: bool = True) -> None:
        self._stream = stream if stream is not None else sys.stderr
        self._close = close

    def write(self, message: str) -> None:
        self._stream.write(message + "\n")

    def flush(self) -> None:
        with contextlib.suppress(Exception):
            self._stream.flush()

    def close(self) -> None:
        # Never close a std stream out from under the rest of the process.
        if not self._close or self._stream is sys.stderr:
            return
        self._stream.close()

    def __repr__(self) -> str:
        name = getattr(self._stream, "name", self._stream)
        return f"{type(self).__name__}({name!r})"


class StderrSink(ConsoleSink):
    """A `LogSink` that writes to `sys.stderr`. The default when no sink is configured."""

    def __init__(self) -> None:
        super().__init__(sys.stderr)


class StdoutSink(ConsoleSink):
    """A `LogSink` that writes to `sys.stdout`."""

    def __init__(self) -> None:
        super().__init__(sys.stdout)


class FileSink:
    """A `LogSink` that appends formatted log lines to a file, opened lazily on first write."""

    __slots__ = ("_encoding", "_file", "_mode", "path")

    def __init__(
        self, path: str | pathlib.Path, *, mode: str = "a", encoding: str = "utf-8"
    ) -> None:
        """
        :param path: File to write log lines to. Its parent directory is
            created on first write if it does not exist.
        :param mode: File open mode. `"a"` (the default) appends across
            runs, so a single `--log-to` file can double as a running log
            for bug reports; pass `"w"` to truncate on each run instead.
        :param encoding: Text encoding to open the file with.
        """
        self.path = pathlib.Path(path)
        self._mode = mode
        self._encoding = encoding
        self._file: typing.IO | None = None

    def _ensure_open(self) -> typing.IO:
        if self._file is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._file = open(self.path, self._mode, encoding=self._encoding)
        return self._file

    def write(self, message: str) -> None:
        self._ensure_open().write(message + "\n")

    def flush(self) -> None:
        if self._file is not None:
            with contextlib.suppress(Exception):
                self._file.flush()

    def close(self) -> None:
        if self._file is not None:
            with contextlib.suppress(Exception):
                self._file.close()
            self._file = None

    def __repr__(self) -> str:
        return f"{type(self).__name__}({str(self.path)!r})"


SinkFilter = str | typing.Callable[[logging.LogRecord], bool]
"""
A route filter for `LogSinkHandler`'s `{filter: sink(s)}` mapping form. Can be a
logger-name pattern (`fnmatch`-style, e.g. `"slb_glossary.query*"`, `"*"`
for everything) matched against each record's logger name, or a callable
taking a `logging.LogRecord` and returning whether it should go to that
route's sink(s).
"""


def check_filter_matches(filter: SinkFilter, record: logging.LogRecord) -> bool:
    """Whether `filter` selects `record` for its route's sink(s)."""
    if isinstance(filter, str):
        return fnmatch.fnmatch(record.name, filter)
    return bool(filter(record))


class LogSinkHandler(RichHandler):
    """
    A logging handler (`rich.logging.RichHandler`) that formats records
    and forwards them to one or more `LogSink`s.

    `sinks` can be a single `LogSink`, several, or a `{filter: sink(s)}`
    mapping. With a mapping, each record only goes to the sink(s) whose
    filter matches it, so different parts of the log stream (e.g. everything
    from the query API vs. everything else) can be routed to different places.

    ```python
    from slb_glossary.logging import FileSink, StderrSink, configure_logging

    # Everything from the query API (and its live/local sub-loggers) goes to
    # a dedicated file; everything else still prints to stderr as usual.
    configure_logging(
        sinks={
            "slb_glossary.query*": FileSink("query.log"),
            "*": StderrSink(),
        }
    )
    ```
    """

    def __init__(
        self,
        sinks: LogSink | Iterable[LogSink] | Mapping[SinkFilter, LogSink | Iterable[LogSink]],
        *,
        level: int = logging.NOTSET,
        **kwargs: typing.Any,
    ) -> None:
        super().__init__(level=level, **kwargs)
        self._routes: list[tuple[SinkFilter | None, LogSink]] = []

        if isinstance(sinks, LogSink):
            self._routes.append((None, sinks))
        elif isinstance(sinks, Mapping):
            for filter, target in sinks.items():
                assert not isinstance(filter, LogSink)
                targets = [target] if isinstance(target, LogSink) else list(target)
                self._routes.extend((filter, sink) for sink in targets)
        else:
            self._routes.extend((None, sink) for sink in sinks)

    @property
    def sinks(self) -> list[LogSink]:
        """Every distinct sink this handler writes to, across all routes."""
        seen: list[LogSink] = []
        for _, sink in self._routes:
            if sink not in seen:
                seen.append(sink)
        return seen

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
        except Exception:
            self.handleError(record)
            return

        for filter, sink in self._routes:
            if filter is not None and not check_filter_matches(filter, record):
                continue
            try:
                sink.write(message)
            except Exception:
                self.handleError(record)

    def flush(self) -> None:
        for sink in self.sinks:
            with contextlib.suppress(Exception):
                sink.flush()

    def close(self) -> None:
        for sink in self.sinks:
            with contextlib.suppress(Exception):
                sink.close()
        super().close()


def import_sink(dotted_path: str) -> typing.Any:
    """
    Import a `LogSink` class or instance given its `"module:attr"` or
    `"package.module.attr"` path.

    :param dotted_path: Either `"module:attr"` (splitting on the last
        `":"`) or `"package.module.attr"` (splitting on the last `"."`).
    :return: Whatever `attr` resolves to. Typically a `LogSink` subclass
        or an already-constructed `LogSink` instance.
    :raises ValueError: If `dotted_path` does not look like a valid import path.
    :raises ImportError: If the module can not be imported, or has no such attribute.
    """
    module_path, _, attr = dotted_path.partition(":")
    if not attr:
        module_path, _, attr = dotted_path.rpartition(".")
    if not module_path or not attr:
        raise ValueError(
            f"{dotted_path!r} is not a valid sink import path. Use 'module:ClassName' "
            f"or 'package.module.ClassName'."
        )

    module = importlib.import_module(module_path)
    try:
        return getattr(module, attr)
    except AttributeError as exc:
        raise ImportError(f"Module {module_path!r} has no attribute {attr!r}") from exc


def _looks_like_import_path(text: str) -> bool:
    if ":" in text:
        return True
    return "." in text and not pathlib.Path(text).suffix


def resolve_sink(
    spec: LogSink | type[LogSink] | str | pathlib.Path | None,
    *,
    default: LogSink | None = None,
) -> LogSink:
    """
    Resolve `--log-to`/`--log-sink`-style input (or a library-level equivalent)
    into a `LogSink`.

    :param spec: Any of:
        - `None`: returns `default`, or a `StderrSink()` if `default` is also `None`.
        - An already-constructed `LogSink`: returned as-is.
        - A `LogSink` subclass: instantiated with no arguments.
        - `"stderr"`/`"console"` or `"stdout"` (case-insensitive): the matching built-in sink.
        - A dotted import path (`"module:ClassName"` or
          `"package.module.ClassName"`): imported via `import_sink`, and
          instantiated with no arguments if it's a class rather than an
          already-built instance.
        - Anything else (a `str`/`pathlib.Path`): treated as a file path
          and wrapped in a `FileSink`.
    :param default: Fallback sink used when `spec` is `None`.
    :return: A ready-to-use `LogSink`.
    :raises ImportError: If `spec` looks like an import path but the
        module/attribute could not be found.
    """
    if spec is None:
        return default if default is not None else StderrSink()
    if isinstance(spec, LogSink):
        return spec
    if isinstance(spec, type):
        return spec()  # type: ignore[call-arg]
    if isinstance(spec, pathlib.Path):
        return FileSink(spec)

    text = str(spec).strip()
    lowered = text.lower()
    if lowered in ("stderr", "console"):
        return StderrSink()
    if lowered == "stdout":
        return StdoutSink()

    if _looks_like_import_path(text):
        target = import_sink(text)
        return typing.cast(LogSink, target() if isinstance(target, type) else target)
    return FileSink(text)


SinkSpec = LogSink | type[LogSink] | str | pathlib.Path | None
"""Anything `resolve_sink` accepts for a single sink."""

SinksSpec = SinkSpec | Iterable[SinkSpec] | Mapping[SinkFilter, SinkSpec | Iterable[SinkSpec]]
"""
Anything `configure_logging`/`resolve_sinks` accept for `sinks`. Can be a single
sink (spec), several, or a `{filter: sink(s)}` mapping that routes only
matching log records to each sink. See `LogSinkHandler`.
"""


def _is_single_sink_spec(value: typing.Any) -> bool:
    """Whether `value` is one sink spec, rather than a collection of them."""
    return value is None or isinstance(value, (LogSink, type, str, pathlib.Path))


def resolve_sinks(
    spec: SinksSpec, *, default: LogSink | None = None
) -> LogSink | list[LogSink] | dict[SinkFilter, list[LogSink]]:
    """
    Resolve `sinks`-style input into ready `LogSink`(s), in the same shape it came in.

    :param spec: A single sink spec, an iterable of them, or a
        `{filter: spec(s)}` mapping (see `SinksSpec`).
    :param default: Fallback sink for a `None`/empty single spec (see `resolve_sink`).
    :return: A single `LogSink` for a single spec, a `list[LogSink]` for
        an iterable, or a `{filter: list[LogSink]}` mapping for a mapping.
    """
    if isinstance(spec, Mapping):
        return {  # type: ignore[return-value]
            filter: (
                [resolve_sink(target, default=default)]  # type: ignore
                if _is_single_sink_spec(target)
                else [resolve_sink(item, default=default) for item in target]  # type: ignore
            )
            for filter, target in spec.items()
        }

    if _is_single_sink_spec(spec):
        return resolve_sink(spec, default=default)  # type: ignore
    return [resolve_sink(item, default=default) for item in spec]  # type: ignore


def set_log_level(level: int | str, *, logger_name: str = "slb_glossary") -> None:
    """
    Set `logger_name`'s logger to `level`, without touching its handlers/sinks.

    :param level: Logging level, by name (`"DEBUG"`, case-insensitive) or
        as a numeric `logging` level.
    :param logger_name: Name of the logger to set the level on. Defaults
        to `"slb_glossary"`, the package's root logger.
    """
    logger = logging.getLogger(logger_name)
    logger.setLevel(level.upper() if isinstance(level, str) else level)


def configure_logging(
    *,
    sinks: SinksSpec = None,
    level: int | str | None = None,
    logger_name: str = "slb_glossary",
    fmt: str | None = None,
    propagate: bool = False,
) -> LogSinkHandler:
    """
    Route every `logger_name` (and descendant) log record to `sinks`.

    :param sinks: One sink (spec), several, a `{filter: sink(s)}` mapping
        to route only matching log records to each sink (see
        `LogSinkHandler`), or `None` for a single `StderrSink()`.
    :param level: Logging level (name or numeric) to set on `logger_name`'s
        logger. `None` leaves the logger's current level untouched, so this
        can be called purely to redirect output without also changing verbosity.
    :param logger_name: Name of the logger to attach the handler to.
        Defaults to `"slb_glossary"`, the package's root logger, so every
        module's `logging.getLogger(__name__)` call propagates up to it.
    :param fmt: `logging.Formatter` format string used for every sink.
        `None` (the default) uses `constants.log_format`,
        resolved fresh on this call (so `SLB_GLOSSARY_LOG_FORMAT` set after
        import still takes effect).
    :param propagate: Whether `logger_name`'s logger should still propagate
        records to its own ancestor loggers (e.g. the root logger) after
        also sending them to `sinks`. Defaults to `False` to avoid
        duplicate output alongside the root handler `logging.basicConfig`
        sets up on package initialization.
    :return: The `LogSinkHandler` now attached to `logger_name`'s logger.
    """
    resolved_fmt = fmt if fmt is not None else constants.log_format
    resolved_sinks = resolve_sinks(sinks, default=StderrSink())

    logger = logging.getLogger(logger_name)
    if level is not None:
        logger.setLevel(level.upper() if isinstance(level, str) else level)
    logger.propagate = propagate

    for existing in list(logger.handlers):
        if isinstance(existing, LogSinkHandler):
            logger.removeHandler(existing)
            existing.close()

    handler = LogSinkHandler(resolved_sinks, level=logger.level)
    handler.setFormatter(logging.Formatter(resolved_fmt))
    logger.addHandler(handler)
    logger.debug(
        "Routed %r logging to: %s", logger_name, ", ".join(repr(sink) for sink in handler.sinks)
    )
    return handler
