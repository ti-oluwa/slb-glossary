"""
`LogSink` implementations, `LogSinkHandler` routing, `resolve_sink(s)`, and `configure_logging`.
"""

import io
import logging
import pathlib
import sys
import typing

import pytest

from slb_glossary.logging import (
    ConsoleSink,
    FileSink,
    LogSink,
    LogSinkHandler,
    SinkFilter,
    StderrSink,
    StdoutSink,
    check_filter_matches,
    configure_logging,
    import_sink,
    resolve_sink,
    resolve_sinks,
    set_log_level,
)

pytestmark = pytest.mark.unit


class RecordingSink:
    """A minimal `LogSink` that records every write for assertions."""

    def __init__(self) -> None:
        self.messages: list[str] = []
        self.flushed = False
        self.closed = False

    def write(self, message: str) -> None:
        self.messages.append(message)

    def flush(self) -> None:
        self.flushed = True

    def close(self) -> None:
        self.closed = True


def make_log_record(
    logger_name: str = "slb_glossary.test", message: str = "hi"
) -> logging.LogRecord:
    return logging.LogRecord(
        name=logger_name,
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )


class TestConsoleSink:
    def test_write_appends_newline_to_stream(self):
        """`write` appends the message plus a newline to the given stream."""
        stream = io.StringIO()
        sink = ConsoleSink(stream)
        sink.write("hello")
        assert stream.getvalue() == "hello\n"

    def test_defaults_to_stderr(self):
        """With no stream given, `ConsoleSink` writes to `sys.stderr`."""
        assert ConsoleSink()._stream is sys.stderr

    def test_close_does_not_close_the_underlying_stream(self):
        """`close()` is a no-op: it never closes a shared std stream."""
        stream = io.StringIO()
        sink = ConsoleSink(stream, close=False)
        sink.close()
        assert not stream.closed


class TestStderrStdoutSink:
    def test_stderr_sink_targets_sys_stderr(self):
        """`StderrSink()` writes to `sys.stderr`."""
        assert StderrSink()._stream is sys.stderr

    def test_stdout_sink_targets_sys_stdout(self):
        """`StdoutSink()` writes to `sys.stdout`."""
        assert StdoutSink()._stream is sys.stdout


class TestFileSink:
    def test_write_creates_parent_directory_and_appends_newline(self, tmp_path: pathlib.Path):
        """First `write` creates the parent dir lazily and appends a trailing newline."""
        path = tmp_path / "nested" / "log.txt"
        sink = FileSink(path)
        sink.write("hello")
        sink.close()
        assert path.read_text(encoding="utf-8") == "hello\n"

    def test_mode_a_appends_across_instances(self, tmp_path: pathlib.Path):
        """`mode='a'` (the default) appends rather than truncating on a new sink instance."""
        path = tmp_path / "log.txt"
        first_sink = FileSink(path)
        first_sink.write("first")
        first_sink.close()

        second_sink = FileSink(path)
        second_sink.write("second")
        second_sink.close()
        assert path.read_text(encoding="utf-8") == "first\nsecond\n"

    def test_mode_w_truncates(self, tmp_path: pathlib.Path):
        """`mode='w'` truncates the file on open rather than appending."""
        path = tmp_path / "log.txt"
        path.write_text("old content\n", encoding="utf-8")
        sink = FileSink(path, mode="w")
        sink.write("new")
        sink.close()
        assert path.read_text(encoding="utf-8") == "new\n"

    def test_file_opened_lazily_not_at_construction(self, tmp_path: pathlib.Path):
        """The file isn't created until the first `write()` call."""
        path = tmp_path / "log.txt"
        FileSink(path)
        assert not path.exists()


class TestCheckFilterMatches:
    def test_string_filter_uses_fnmatch_against_logger_name(self):
        """A string filter is matched via `fnmatch` against `record.name`."""
        record = make_log_record(logger_name="slb_glossary.query.search")
        assert check_filter_matches("slb_glossary.query*", record) is True
        assert check_filter_matches("slb_glossary.local*", record) is False

    def test_callable_filter_receives_the_record(self):
        """A callable filter is called with the record and its truthy result used."""
        record = make_log_record()
        assert check_filter_matches(lambda r: r.name == record.name, record) is True
        assert check_filter_matches(lambda r: False, record) is False


class TestSinkHandler:
    def test_single_sink_receives_every_record(self):
        """A single `LogSink` (no mapping) receives every emitted record."""
        sink = RecordingSink()
        handler = LogSinkHandler(sink)
        handler.setFormatter(logging.Formatter("%(message)s"))
        handler.emit(make_log_record(message="hello"))
        assert sink.messages == ["hello"]

    def test_iterable_of_sinks_all_receive_every_record(self):
        """An iterable of sinks (no mapping) all receive every emitted record."""
        sink_a, sink_b = RecordingSink(), RecordingSink()
        handler = LogSinkHandler([sink_a, sink_b])
        handler.setFormatter(logging.Formatter("%(message)s"))
        handler.emit(make_log_record(message="hello"))
        assert sink_a.messages == ["hello"]
        assert sink_b.messages == ["hello"]

    def test_mapping_routes_records_by_filter(self):
        """With a `{filter: sink}` mapping, only matching records reach each sink."""
        query_sink, other_sink = RecordingSink(), RecordingSink()
        handler = LogSinkHandler({"slb_glossary.query*": query_sink, "*": other_sink})
        handler.setFormatter(logging.Formatter("%(message)s"))
        handler.emit(make_log_record(logger_name="slb_glossary.query.search", message="q"))
        handler.emit(make_log_record(logger_name="slb_glossary.local.sync", message="l"))
        assert query_sink.messages == ["q"]
        assert other_sink.messages == ["q", "l"]

    def test_mapping_value_can_be_an_iterable_of_sinks(self):
        """A mapping value may be a list of sinks, all receiving matching records."""
        sink_a, sink_b = RecordingSink(), RecordingSink()
        handler = LogSinkHandler({"*": [sink_a, sink_b]})
        handler.setFormatter(logging.Formatter("%(message)s"))
        handler.emit(make_log_record(message="hello"))
        assert sink_a.messages == ["hello"]
        assert sink_b.messages == ["hello"]

    def test_sinks_property_deduplicates_across_routes(self):
        """`.sinks` lists each distinct sink once, even if used in multiple routes."""
        shared_sink = RecordingSink()
        handler = LogSinkHandler({"a*": shared_sink, "b*": shared_sink})
        assert handler.sinks == [shared_sink]

    def test_flush_flushes_every_sink(self):
        """`flush()` flushes every distinct sink."""
        sink = RecordingSink()
        handler = LogSinkHandler(sink)
        handler.flush()
        assert sink.flushed is True

    def test_close_closes_every_sink(self):
        """`close()` closes every distinct sink."""
        sink = RecordingSink()
        handler = LogSinkHandler(sink)
        handler.close()
        assert sink.closed is True

    def test_sink_write_error_is_handled_not_raised(self):
        """A sink whose `write` raises doesn't propagate the exception out of `emit`."""

        class BrokenSink:
            def write(self, message: str) -> None:
                raise OSError("broken")

            def flush(self) -> None:
                pass

            def close(self) -> None:
                pass

        handler = LogSinkHandler(BrokenSink())
        handler.setFormatter(logging.Formatter("%(message)s"))
        # Should not raise.
        handler.emit(make_log_record())


class TestImportSink:
    def test_imports_using_colon_separator(self):
        """`"module:attr"` form resolves `attr` from `module`."""
        assert import_sink("slb_glossary.logging:StderrSink") is StderrSink

    def test_imports_using_dotted_path(self):
        """`"package.module.attr"` form (no colon) resolves via `rpartition(".")`."""
        assert import_sink("slb_glossary.logging.StderrSink") is StderrSink

    def test_raises_value_error_for_unparsable_path(self):
        """A string with neither a colon nor a dot raises `ValueError`."""
        with pytest.raises(ValueError, match="not a valid sink import path"):
            import_sink("not-a-path")

    def test_raises_import_error_for_missing_attribute(self):
        """A valid module but nonexistent attribute raises `ImportError`."""
        with pytest.raises(ImportError):
            import_sink("slb_glossary.logging:NotARealSink")


class TestResolveSink:
    def test_none_returns_default_or_stderr_sink(self):
        """`None` returns `default` if given, else a fresh `StderrSink()`."""
        assert isinstance(resolve_sink(None), StderrSink)
        custom_default = RecordingSink()
        assert resolve_sink(None, default=custom_default) is custom_default

    def test_instance_is_returned_as_is(self):
        """An already-constructed `LogSink` instance passes through unchanged."""
        sink = RecordingSink()
        assert resolve_sink(sink) is sink

    def test_class_is_returned_unchanged_not_instantiated(self):
        """A `LogSink` subclass is returned as-is, *not* instantiated: `isinstance(cls, LogSink)`
        is `True` for a runtime-checkable `Protocol` (it only checks attribute
        presence, and a class object has its own methods as attributes), so
        `resolve_sink`'s `isinstance(spec, LogSink)` branch catches classes
        before the dedicated `isinstance(spec, type)` branch ever runs.
        Verified directly against the running code, not assumed from the
        docstring, which describes the (apparently unreachable) intended
        behavior of instantiating the class."""
        result = resolve_sink(StderrSink)
        assert result is StderrSink

    @pytest.mark.parametrize("spec", ["stderr", "console", "STDERR"])
    def test_stderr_console_strings_resolve_to_stderr_sink(self, spec: str):
        """`"stderr"`/`"console"` (case-insensitive) resolve to `StderrSink`."""
        assert isinstance(resolve_sink(spec), StderrSink)

    def test_stdout_string_resolves_to_stdout_sink(self):
        """`"stdout"` resolves to `StdoutSink`."""
        assert isinstance(resolve_sink("stdout"), StdoutSink)

    def test_import_path_string_resolves_via_import_sink(self):
        """A dotted import path string resolves via `import_sink`, instantiating a class."""
        result = resolve_sink("slb_glossary.logging:StderrSink")
        assert isinstance(result, StderrSink)

    def test_plain_path_object_is_wrapped_in_file_sink(self, tmp_path: pathlib.Path):
        """A `pathlib.Path` is always wrapped in a `FileSink`, never treated as an import path."""
        path = tmp_path / "log.txt"
        result = resolve_sink(path)
        assert isinstance(result, FileSink)
        assert result.path == path

    def test_plain_string_path_is_wrapped_in_file_sink(self, tmp_path: pathlib.Path):
        """A plain filesystem-looking string (no colon/dots) is wrapped in a `FileSink`."""
        path = tmp_path / "log.txt"
        result = resolve_sink(str(path))
        assert isinstance(result, FileSink)


class TestResolveSinks:
    def test_single_spec_returns_a_single_sink(self):
        """A single spec (not a collection) resolves to one `LogSink`, not a list."""
        result = resolve_sinks("stderr")
        assert isinstance(result, StderrSink)

    def test_iterable_spec_returns_a_list_of_sinks(self):
        """An iterable of specs resolves to a `list[LogSink]`."""
        result = resolve_sinks(["stderr", "stdout"])
        assert isinstance(result, list)
        assert isinstance(result[0], StderrSink)
        assert isinstance(result[1], StdoutSink)

    def test_mapping_spec_returns_a_mapping_of_lists(self):
        """A `{filter: spec(s)}` mapping resolves to `{filter: list[LogSink]}`."""
        result = resolve_sinks({"*": "stderr", "slb_glossary.query*": ["stdout"]})
        result = typing.cast(dict[SinkFilter, list[LogSink]], result)
        assert result["*"] == [resolve_sink("stderr")] or isinstance(result["*"][0], StderrSink)
        assert isinstance(result["slb_glossary.query*"][0], StdoutSink)


class TestSetLogLevel:
    def test_sets_level_by_name(self):
        """A level name string (case-insensitive) sets the logger's level."""
        set_log_level("debug", logger_name="slb_glossary.test.loglevel")
        assert logging.getLogger("slb_glossary.test.loglevel").level == logging.DEBUG

    def test_sets_level_by_numeric_value(self):
        """A numeric level sets the logger's level directly."""
        set_log_level(logging.WARNING, logger_name="slb_glossary.test.loglevel2")
        assert logging.getLogger("slb_glossary.test.loglevel2").level == logging.WARNING


class TestConfigureLogging:
    def test_attaches_a_sinkhandler_to_the_named_logger(self):
        """`configure_logging` attaches a `LogSinkHandler` to `logger_name`'s logger."""
        logger_name = "slb_glossary.test.configure1"
        handler = configure_logging(sinks=RecordingSink(), logger_name=logger_name)
        try:
            assert handler in logging.getLogger(logger_name).handlers
        finally:
            logging.getLogger(logger_name).removeHandler(handler)

    def test_repeat_calls_remove_previous_sinkhandler(self):
        """Calling `configure_logging` again replaces, rather than stacks, the `LogSinkHandler`."""
        logger_name = "slb_glossary.test.configure2"
        first_handler = configure_logging(sinks=RecordingSink(), logger_name=logger_name)
        second_handler = configure_logging(sinks=RecordingSink(), logger_name=logger_name)
        try:
            handlers = logging.getLogger(logger_name).handlers
            assert first_handler not in handlers
            assert second_handler in handlers
            assert sum(isinstance(h, LogSinkHandler) for h in handlers) == 1
        finally:
            logging.getLogger(logger_name).removeHandler(second_handler)

    def test_level_none_leaves_existing_level_untouched(self):
        """`level=None` (the default) doesn't change the logger's current level."""
        logger_name = "slb_glossary.test.configure3"
        logger = logging.getLogger(logger_name)
        logger.setLevel(logging.WARNING)
        handler = configure_logging(sinks=RecordingSink(), logger_name=logger_name, level=None)
        try:
            assert logger.level == logging.WARNING
        finally:
            logger.removeHandler(handler)

    def test_propagate_defaults_to_false(self):
        """`propagate` defaults to `False`, to avoid duplicate output via ancestor handlers."""
        logger_name = "slb_glossary.test.configure4"
        handler = configure_logging(sinks=RecordingSink(), logger_name=logger_name)
        try:
            assert logging.getLogger(logger_name).propagate is False
        finally:
            logging.getLogger(logger_name).removeHandler(handler)

    def test_returned_handler_writes_through_to_the_sink(self):
        """A record logged through the configured logger reaches the sink's `write`."""
        logger_name = "slb_glossary.test.configure5"
        sink = RecordingSink()
        handler = configure_logging(
            sinks=sink, logger_name=logger_name, level="INFO", fmt="%(message)s"
        )
        try:
            logging.getLogger(logger_name).info("hello from test")
            assert sink.messages == ["hello from test"]
        finally:
            logging.getLogger(logger_name).removeHandler(handler)
