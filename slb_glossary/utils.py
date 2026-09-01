"""Utilities shared across the package."""

import builtins
import enum
import logging
import os
import sys
import time
import typing
from difflib import get_close_matches

from rich import box
from rich.console import Console
from rich.live import Live
from rich.table import Table

from slb_glossary.errors import EnvironmentVariableError
from slb_glossary.types import RecordLike, SearchResult

__all__ = [
    "Lookup",
    "env",
    "log_timed_yields",
    "parse_int",
    "print_async_records",
    "print_records",
    "split_exclude",
]

logger = logging.getLogger(__name__)

T = typing.TypeVar("T")


_ENV_CASTERS: dict[type, typing.Callable[[str], typing.Any]] = {
    bool: lambda raw: raw.strip().lower() in ("1", "true", "yes", "on"),
    int: int,
    float: float,
    str: str,
}


def env(
    name: str,
    default: T,
    *,
    type: type[T] | typing.Callable[[str], T] | None = None,
    validator: typing.Callable[[T], bool] | None = None,
) -> T:
    """
    Read environment variable `name` at call time, cast it, and validate it.

    Example:

    ```python
    DEFAULT_RELEVANCE_THRESHOLD = env(
        "SLB_GLOSSARY_RELEVANCE_THRESHOLD", default=0.55, validator=lambda v: 0.0 <= v <= 1.0
    )
    ```

    :param name: Environment variable name to read.
    :param default: Value used when `name` isn't set in the environment.
        Also determines the expected type when `type` isn't given explicitly.
    :param type: Expected type to cast the raw string value to, or the callable
        to cast the string to the correct type. Defaults to `type(default)`.
        Supports `bool`, `int`, `float`, `str`, and any `enum.Enum` subclass
        (matched by value).
    :param validator: Optional extra check run on the cast value. A
        `False` return is treated the same as a cast failure.
    :return: The cast, validated value, or `default` if `name` isn't set.
    :raises EnvironmentVariableError: If `name` is set but its value can't be cast to
        the expected type, or fails `validator`.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default

    expected = type if type is not None else builtins.type(default)
    try:
        if isinstance(expected, builtins.type) and issubclass(expected, enum.Enum):
            value = expected(raw)  # type: ignore[call-arg]
        elif not isinstance(expected, builtins.type) and callable(expected):
            value = expected(raw)  # type: ignore[arg-type,assignment]
        else:
            caster = _ENV_CASTERS.get(expected, expected)
            value = caster(raw)  # type: ignore[union-attr]
    except (ValueError, TypeError) as exc:
        raise EnvironmentVariableError(
            f"Environment variable {name}={raw!r} is not a valid {expected.__name__}."
        ) from exc

    if validator is not None and not validator(value):  # type: ignore[arg-type]
        raise EnvironmentVariableError(
            f"Environment variable {name}={raw!r} is not a valid value."
        )
    return typing.cast(T, value)


def parse_int(text: str) -> int:
    """
    Parse an integer out of glossary page text such as `"1,204"` or `" 42 "`.

    :param text: The text to parse.
    :return: The parsed integer.
    :raises ValueError: If `text` does not contain a valid integer once
        commas and surrounding whitespace are stripped.
    """
    return int(text.replace(",", "").replace(" ", ""))


def get_topic_match(topics: typing.Mapping[str, int], topic: str) -> str:
    """
    Resolve a user-supplied topic name to its closest match in `topics`.

    :param topics: Known glossary topics, as returned by `fetch_topics` or
        held on `Session.topics`.
    :param topic: One topic name, or several separated by commas, e.g.
        `"Geophysics,Geology"`. Matching is case-insensitive and tolerant of
        minor misspellings.
    :return: The resolved topic(s), comma-separated and title-cased, ready
        to pass to `slb_glossary.urls.build_search_url`. Returns `""` if
        `topic` is empty or any of its parts has no close match in `topics`.
    """
    if not topic:
        return topic

    available = [name.lower() for name in topics]
    resolved: list[str] = []
    for raw_part in topic.split(","):
        candidate = raw_part.strip().lower()
        if candidate in available:
            resolved.append(candidate)
            continue

        matches = get_close_matches(candidate, available, n=1, cutoff=0.5)
        if not matches:
            logger.warning("No topic match found for %r", candidate)
            return ""
        resolved.append(matches[0])

    return ",".join(resolved).title()


_ACRONYMS = frozenset({"url", "id"})
"""Field-name words rendered upper-case rather than title-cased by `humanize_field`."""


def humanize_field(field: str) -> str:
    """Turn a `snake_case` field name into a `Title Case` column header."""
    words = field.split("_")
    return " ".join(word.upper() if word in _ACRONYMS else word.title() for word in words)


async def log_timed_yields(
    iterable: typing.AsyncIterable[T],
    *,
    logger: logging.Logger,
    label: str,
    level: int = logging.DEBUG,
) -> typing.AsyncIterator[T]:
    """
    Wrap an async iterator, logging per-yield and running-average timing metrics.

    Every item logs one `level` line with the time since the previous
    yield (or since iteration started, for the first item), the running
    average time per yield so far, and the total elapsed time so far,
    e.g. for spotting a slow tail end of an otherwise-fast fetch. Nothing
    is logged if the wrapped iterator never yields anything.

    This only instruments timing; it doesn't buffer or otherwise change
    what's yielded. `async for item in log_timed_yields(inner, ...)` is
    equivalent to `async for item in inner` except for the added logging.

    :param iterable: The async iterator to wrap.
    :param logger: Logger to emit timing records to.
    :param label: Short description of what's being iterated, included in
        every log line, e.g. `"search(%r)" % query`.
    :param level: Logging level for the per-yield lines.
    :yield: Each item from `iterable`, unchanged.
    """
    start = time.monotonic()
    previous = start
    count = 0
    async for item in iterable:
        now = time.monotonic()
        count += 1
        elapsed = now - start
        logger.log(
            level,
            "`%s`: yield #%d took %.3fs (avg %.3fs/yield so far, %.3fs elapsed total)",
            label,
            count,
            now - previous,
            elapsed / count,
            elapsed,
        )
        previous = now
        yield item


def split_exclude(
    exclude: typing.Iterable[str] | None,
) -> tuple[frozenset[str], frozenset[str]]:
    """
    Split a mixed `exclude` collection into `(excluded_urls, excluded_term_names)`.

    Both the local database API and the live glossary API accept a single
    `exclude` collection that can hold URLs, term names, or a mix of both.

    An entry is treated as a URL if it looks like one (starts with
    `"http://"`/`"https://"`); everything else is treated as a
    term name, matched case-insensitively/whitespace-insensitively via
    `normalize_text`-style folding, so `"Porosity"` and `" porosity "` both
    exclude the same term.

    :param exclude: URLs and/or term names to exclude, or `None`.
    :return: `(excluded_urls, excluded_term_names)`, each a `frozenset`.
        `excluded_term_names` entries are lowercased and whitespace-
        collapsed, ready to compare against a similarly normalized term
        name. Both sets are empty if `exclude` is `None`/empty.
    """
    if not exclude:
        return frozenset(), frozenset()

    urls: set[str] = set()
    names: set[str] = set()
    for item in exclude:
        if not item:
            continue
        if item.startswith(("http://", "https://")):
            urls.add(item)
        else:
            names.add(" ".join(item.strip().lower().split()))
    return frozenset(urls), frozenset(names)


def normalize_text(text: str) -> str:
    """Lowercase `text` and collapse its whitespace."""
    return " ".join(text.strip().lower().split())


def as_async_iterator(
    results: typing.Iterable[T] | typing.AsyncIterable[T],
) -> typing.AsyncIterator[T]:
    """Normalize a sync or async iterable of items `T` into an async iterator."""

    async def _wrap_sync(
        sync_results: typing.Iterable[T],
    ) -> typing.AsyncIterator[T]:
        for result in sync_results:
            yield result

    if isinstance(results, typing.AsyncIterable):
        return results.__aiter__()
    return _wrap_sync(results)


def _format_cell(value: typing.Any, *, max_related_shown: int = 6) -> str:
    """
    Render an arbitrary record field value as printable table cell text.

    Handles the shapes records actually carry: `None`, a
    single `RelatedTerm`-like `NamedTuple`, and lists/tuples of those (e.g.
    `SearchResult.related`). Trims long lists rather than flooding the
    cell, since this is for a terminal table, not a file export.

    :param value: A field value from `record.asdict()`.
    :param max_related_shown: Maximum number of items to list from a
        list/tuple value before summarizing the rest as `"+N more"`.
    :return: Cell text. Never `None`; empty/missing values render as `"-"`.
    """
    if value is None:
        return "-"

    if isinstance(value, (list, tuple)):
        if not value:
            return "-"

        names = [_format_cell(item) for item in value]
        if len(names) > max_related_shown:
            shown = ", ".join(names[:max_related_shown])
            return f"{shown}, +{len(names) - max_related_shown} more"
        return ", ".join(names)

    if isinstance(value, RecordLike) or hasattr(value, "asdict"):
        record_dict = value.asdict()
        fallback = next(iter(record_dict.values()), "-")
        return str(record_dict.get("term") or record_dict.get("name") or fallback)

    text = str(value).strip()
    return text or "-"


DEFAULT_RESULT_TABLE_TITLE = "Search Results"
"""Default table title used by `print_records`/`print_async_records` for `SearchResult`s."""

DEFAULT_GENERIC_TABLE_TITLE = "Results"
"""Default table title used by `print_records`/`print_async_records` for non-`SearchResult` records."""


def _make_result_table(
    *,
    title: str | None = None,
    show_url: bool = True,
    show_topic: bool = True,
    show_grammar: bool = True,
    show_image: bool = False,
    show_related: bool = False,
    show_origin: bool = False,
    show_score: bool = False,
) -> Table:
    """Build the specialized table used to display `SearchResult`s."""
    table = Table(
        title=title or DEFAULT_RESULT_TABLE_TITLE,
        title_style="bold bright_blue",
        box=box.HEAVY,
        expand=True,
        show_lines=True,
    )
    table.add_column("Term", style="bold magenta", no_wrap=True)
    if show_grammar:
        table.add_column("Grammar", style="cyan", no_wrap=True)
    if show_topic:
        table.add_column("Topic", style="green", no_wrap=True)
    table.add_column("Definition", style="white")
    if show_related:
        table.add_column("Related", style="yellow")
    if show_image:
        table.add_column("Image", style="blue", overflow="fold")
    if show_url:
        table.add_column("Source", style="blue", overflow="fold")
    if show_origin:
        table.add_column("Origin", style="bright_black", no_wrap=True)
    if show_score:
        table.add_column("Score", style="bright_black", no_wrap=True)
    return table


def _format_result_row(
    result: SearchResult,
    *,
    show_url: bool = True,
    show_topic: bool = True,
    show_grammar: bool = True,
    show_image: bool = False,
    show_related: bool = False,
    show_origin: bool = False,
    show_score: bool = False,
    origin: str | None = None,
    score: float | None = None,
) -> list[str]:
    """
    Build one table row for a `SearchResult`, matching `_make_result_table`'s columns.

    :param show_origin: Whether to append an "Origin" cell. Must match
        whatever `_make_result_table` was built with.
    :param show_score: Whether to append a "Score" cell. Must match
        whatever `_make_result_table` was built with.
    :param origin: Text for the "Origin" cell, e.g. `"local"`/`"live"`.
        Rendered as `"-"` if `None`. Ignored unless `show_origin=True`.
    :param score: Value for the "Score" cell, e.g. a `QueryResult.score`.
        Rendered as `"-"` if `None` (expected for a result with no
        comparable score, e.g. most live ones). Ignored unless `show_score=True`.
    """
    definition = result.definition.strip() if result.definition else "(no definition parsed)"
    row: list[str] = [result.term]
    if show_grammar:
        row.append(result.grammatical_label or "-")
    if show_topic:
        row.append(result.topic or "-")
    row.append(definition)
    if show_related:
        row.append(_format_cell(result.related))
    if show_image:
        if not result.image_caption:
            row.append(result.image or "-")
        else:
            row.append(f"{result.image_caption} - {result.image}")
    if show_url:
        row.append(result.url or "-")
    if show_origin:
        row.append(origin or "-")
    if show_score:
        row.append(f"{score:.2f}" if score is not None else "-")
    return row


def _make_generic_table(
    fields: typing.Sequence[str],
    *,
    title: str | None = None,
    show_origin: bool = False,
    show_score: bool = False,
) -> Table:
    """Build a plain table with one humanized column per field, for non-`SearchResult` records."""
    table = Table(
        title=title or DEFAULT_GENERIC_TABLE_TITLE,
        title_style="bold bright_blue",
        box=box.HEAVY,
        expand=True,
        show_lines=True,
    )
    for field in fields:
        table.add_column(humanize_field(field), style="white", overflow="fold")
    if show_origin:
        table.add_column("Origin", style="bright_black", no_wrap=True)
    if show_score:
        table.add_column("Score", style="bright_black", no_wrap=True)
    return table


def _format_generic_row(
    record: typing.Any,
    fields: typing.Sequence[str],
    *,
    show_origin: bool = False,
    show_score: bool = False,
    origin: str | None = None,
    score: float | None = None,
) -> list[str]:
    """Build one table row for an arbitrary `RecordLike`, matching `_make_generic_table`'s columns."""
    record_dict = record.asdict()
    row = [_format_cell(record_dict.get(field)) for field in fields]
    if show_origin:
        row.append(origin or "-")
    if show_score:
        row.append(f"{score:.2f}" if score is not None else "-")
    return row


RecordT = typing.TypeVar("RecordT", bound=RecordLike)


@typing.runtime_checkable
class _Lookup(typing.Protocol[RecordT]):
    """
    Structural shape `annotate=True` table/JSON output needs from each
    item: satisfied by `slb_glossary.query.QueryResult`, without
    importing that class directly (`slb_glossary.query` itself depends
    on this module, so importing it back here would be circular).
    """

    value: RecordT
    source: typing.Any
    """A `slb_glossary.query.Source` member; only `.value` (its string name) is used."""
    score: float | None


Lookup = _Lookup
"""
Public name for `_Lookup`, for other modules that need to type-annotate an `annotate=True` caller's input without a
circular import on `slb_glossary.query.QueryResult` itself.
"""


def _make_table_and_formatter(
    sample: typing.Any,
    *,
    title: str | None,
    show_url: bool,
    show_topic: bool,
    show_grammar: bool,
    show_image: bool,
    show_related: bool,
    annotate: bool = False,
) -> tuple[Table, typing.Callable[[typing.Any], list[str]]]:
    """
    Choose a table layout and row formatter suited to `sample`'s record type.

    `slb_glossary`'s CLI prints several record shapes through this same
    function (search results, but also plain URL/topic listings), so this
    dispatches on the *first* item seen rather than assuming every caller
    is printing `SearchResult`s.

    :param sample: The first record to be printed, used only to pick a
        layout. With `annotate=True`, this is a `_Lookup`-shaped item
        (e.g. a `slb_glossary.query.QueryResult`), not the bare record itself.
    :param title: Table/section title to use instead of the type-based
        default (`"Search Results"` for `SearchResult`s, `"Results"`
        otherwise). `None` keeps that default.
    :param annotate: If `True`, add "Origin"/"Score" columns, and expect
        every record passed to the returned formatter to be `_Lookup`-shaped
        (`.value`/`.source`/`.score`) rather than a bare record.
    :return: A `(table, formatter)` pair; call `formatter(record)` for
        every record (including `sample`) to get its row cells.
    """
    record_sample = sample.value if annotate else sample

    if isinstance(record_sample, SearchResult):
        table = _make_result_table(
            title=title,
            show_url=show_url,
            show_topic=show_topic,
            show_grammar=show_grammar,
            show_image=show_image,
            show_related=show_related,
            show_origin=annotate,
            show_score=annotate,
        )

        def _result_formatter(record: typing.Any) -> list[str]:
            result, origin, score = _unwrap(record, annotate=annotate)
            return _format_result_row(
                result,
                show_url=show_url,
                show_topic=show_topic,
                show_grammar=show_grammar,
                show_image=show_image,
                show_related=show_related,
                show_origin=annotate,
                show_score=annotate,
                origin=origin,
                score=score,
            )

        return table, _result_formatter

    fields = list(getattr(record_sample, "fields", None) or record_sample.asdict().keys())
    table = _make_generic_table(fields, title=title, show_origin=annotate, show_score=annotate)

    def _generic_formatter(record: typing.Any) -> list[str]:
        rec, origin, score = _unwrap(record, annotate=annotate)
        return _format_generic_row(
            rec, fields, show_origin=annotate, show_score=annotate, origin=origin, score=score
        )

    return table, _generic_formatter


def _unwrap(record: typing.Any, *, annotate: bool) -> tuple[typing.Any, str | None, float | None]:
    """Split a possibly `_Lookup`-wrapped `record` into `(record, origin, score)`."""
    if not annotate:
        return record, None, None
    lookup = typing.cast(_Lookup, record)
    return lookup.value, lookup.source.value, lookup.score


def print_records(
    results: typing.Iterable[typing.Any],
    *,
    title: str | None = None,
    out: typing.TextIO | None = None,
    limit: int | None = None,
    show_url: bool = True,
    show_topic: bool = True,
    show_grammar: bool = True,
    show_image: bool = False,
    show_related: bool = False,
    annotate: bool = False,
) -> int:
    """
    Pretty-print a sequence of records to `out` as a table.

    Works with any `slb_glossary.types.RecordLike` (`SearchResult`s get a
    specialized layout with `show_*` column toggles; any other record type
    gets a generic table with one column per field), and with any iterable,
    including lazily produced generators.

    :param results: The records to print. May be empty. With
        `annotate=True`, each item is a `slb_glossary.query.QueryResult`
        (or anything else `_Lookup`-shaped) wrapping the record, rather
        than the bare record itself.
    :param title: Title shown above the table, e.g. `"Terms under Drilling"`.
        Defaults to `"Search Results"` for `SearchResult`s, or `"Results"`
        for any other record type.
    :param out: Stream to print to. Defaults to `sys.stdout`.
    :param limit: Maximum number of records to print. Prints every record
        if `None`.
    :param show_url: For `SearchResult`s, whether to show the source URL column.
    :param show_topic: For `SearchResult`s, whether to show the topic column.
    :param show_grammar: For `SearchResult`s, whether to show the grammatical label column.
    :param show_image: For `SearchResult`s, whether to show the image URL column.
    :param show_related: For `SearchResult`s, whether to show the related-terms column.
    :param annotate: If `True`, add "Origin"/"Score" columns, reading them
        off each `_Lookup`-shaped item, instead of printing bare records.
    :return: The number of records printed.
    """
    if out is None:
        out = sys.stdout
    console = Console(file=out)

    iterator = iter(results)
    try:
        first = next(iterator)
    except StopIteration:
        console.print(_make_generic_table([], title=title))
        return 0

    table, format_row = _make_table_and_formatter(
        first,
        title=title,
        show_url=show_url,
        show_topic=show_topic,
        show_grammar=show_grammar,
        show_image=show_image,
        show_related=show_related,
        annotate=annotate,
    )

    def records() -> typing.Iterator[RecordLike]:
        yield first
        yield from iterator

    count = 0
    if console.is_terminal:
        with Live(table, console=console, refresh_per_second=8, transient=False) as live:
            for record in records():
                if limit is not None and count >= limit:
                    break
                table.add_row(*format_row(record))
                live.update(table)
                count += 1
        return count

    for record in records():
        if limit is not None and count >= limit:
            break
        table.add_row(*format_row(record))
        count += 1

    console.print(table)
    return count


async def print_async_records(
    results: typing.AsyncIterable[typing.Any],
    *,
    title: str | None = None,
    out: typing.TextIO | None = None,
    limit: int | None = None,
    show_url: bool = True,
    show_topic: bool = True,
    show_grammar: bool = True,
    show_image: bool = False,
    show_related: bool = False,
    annotate: bool = False,
) -> int:
    """Pretty-print an async stream of records as they are yielded. See `print_records`."""
    if out is None:
        out = sys.stdout
    console = Console(file=out)

    iterator = results.__aiter__()
    try:
        first = await iterator.__anext__()
    except StopAsyncIteration:
        console.print(_make_generic_table([], title=title))
        return 0

    table, format_row = _make_table_and_formatter(
        first,
        title=title,
        show_url=show_url,
        show_topic=show_topic,
        show_grammar=show_grammar,
        show_image=show_image,
        show_related=show_related,
        annotate=annotate,
    )

    async def records() -> typing.AsyncIterator[RecordLike]:
        yield first
        async for record in iterator:
            yield record

    count = 0
    if console.is_terminal:
        with Live(table, console=console, refresh_per_second=8, transient=False) as live:
            async for record in records():
                if limit is not None and count >= limit:
                    break
                table.add_row(*format_row(record))
                live.update(table)
                count += 1
        return count

    async for record in records():
        if limit is not None and count >= limit:
            break
        table.add_row(*format_row(record))
        count += 1

    console.print(table)
    return count
