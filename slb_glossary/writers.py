"""
API for persisting records to a (file) store.

`save` is the only function most callers need; The `@writer` decorator
extends it to new file formats.
"""

import asyncio
import csv
import json
import pathlib
import typing
from collections.abc import Sequence

from slb_glossary.errors import UnsupportedFormatError, WriterError
from slb_glossary.types import RecordLike, materialize_records

__all__ = [
    "WRITERS",
    "Writer",
    "field_names",
    "humanize_field",
    "records_to_dicts",
    "write_csv",
    "write_json",
    "write_jsonl",
    "write_txt",
    "write_xlsx",
    "writer",
    "save",
    "supported_formats",
]


Writer = typing.Callable[[Sequence[RecordLike], pathlib.Path], typing.Awaitable[None]]
"""
An async callable that writes a list of records to a destination path.

Register one with `@writer(format)` decorator to teach `store.save`
a new file format:

```python
@writer("yaml")
async def write_yaml(records: Sequence[RecordLike], destination: pathlib.Path) -> None:
    import yaml

    with open(destination, "w") as file:
        yaml.dump(records_to_dicts(records), file)

```

A writer only needs to write the file; it does not need to create
`destination`'s parent directory (`store.save` does that first) or catch
I/O errors (`store.save` wraps those in `WriterError` with format/path
context already attached).
"""


def field_names(records: Sequence[RecordLike]) -> list[str]:
    """Return the field names of `records`, or `[]` if `records` is empty."""
    return list(records[0].fields) if records else []


ACRONYMS = frozenset({"url", "id"})
"""Field-name words rendered upper-case rather than title-cased by `humanize_field`."""


def humanize_field(field: str) -> str:
    """Turn a `snake_case` field name into a `Title Case` header."""
    words = field.split("_")
    return " ".join(word.upper() if word in ACRONYMS else word.title() for word in words)


def make_json_safe(value: typing.Any) -> typing.Any:
    """
    Recursively convert `value` into something `json.dump` can serialize.

    Handles the shapes `slb_glossary` records actually nest: `NamedTuple`
    values (e.g. `RelatedTerm`), and plain lists/tuples/dicts of those.
    Anything else is returned unchanged and left to `json.dump` itself.

    :param value: A field value from `record.asdict()`.
    :return: A JSON-serializable equivalent of `value`.
    """
    if hasattr(value, "_asdict"):
        return {key: make_json_safe(item) for key, item in value._asdict().items()}
    if isinstance(value, (list, tuple)):
        return [make_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: make_json_safe(item) for key, item in value.items()}
    return value


def render_display_text(value: typing.Any) -> str:
    """
    Render a field value as flat, human-readable text for a CSV/TXT/XLSX cell.

    `None` becomes `""`. A nested `NamedTuple` (e.g. a single `RelatedTerm`)
    renders as its most identifying field. A list/tuple of values (e.g.
    `SearchResult.related`) renders as a `"; "`-joined list of each item's
    display text.

    :param value: A field value from `record.asdict()`.
    :return: Flat text suitable for a single spreadsheet/CSV cell.
    """
    if value is None:
        return ""
    if hasattr(value, "asdict"):
        record_dict = value.asdict()
        fallback = next(iter(record_dict.values()), "")
        return str(record_dict.get("term") or record_dict.get("name") or fallback)
    if isinstance(value, (list, tuple)):
        return "; ".join(render_display_text(item) for item in value)
    return str(value)


def records_to_dicts(
    records: Sequence[RecordLike], exclude: Sequence[str] = ()
) -> list[dict[str, typing.Any]]:
    """
    Convert `records` into a list of plain, JSON-safe `dict`s, one per record.

    Field order is preserved from each record's `asdict()`, and nested
    values (e.g. a `SearchResult.related` list of `RelatedTerm`) are
    recursively converted rather than flattened to text, unlike
    `write_json`, which additionally re-keys the list by each record's
    first field for on-disk storage, this stays a flat list, which is
    usually what you want for `json.dumps`, piping to `jq`, or embedding
    in a larger JSON payload.

    :param records: The records to convert.
    :param exclude: Field names to omit from each record's dict.
    :return: One JSON-safe `dict` per record, in the same order as `records`.
    """
    excluded = frozenset(exclude)
    return [
        {
            key: make_json_safe(value)
            for key, value in record.asdict().items()
            if key not in excluded
        }
        for record in records
    ]


async def write_csv(records: Sequence[RecordLike], destination: pathlib.Path) -> None:
    """Write `records` to `destination` as CSV, with a humanized header row."""

    def _write() -> None:
        fields = field_names(records)
        with open(destination, "w", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            writer.writerow([humanize_field(field) for field in fields])
            for record in records:
                writer.writerow([render_display_text(value) for value in record.asdict().values()])

    await asyncio.to_thread(_write)


async def write_json(records: Sequence[RecordLike], destination: pathlib.Path) -> None:
    """
    Write `records` to `destination` as JSON, keyed by each record's first field.

    Records whose first field repeats overwrite earlier entries with the
    same key. Nested values (e.g. a `SearchResult.related` list of
    `RelatedTerm`) are preserved as native JSON arrays/objects rather than
    flattened to text.
    """

    def _write() -> None:
        data: dict[typing.Any, dict[str, typing.Any]] = {}
        for record, record_dict in zip(records, records_to_dicts(records), strict=True):
            record_dict = dict(record_dict)
            key_field = record.fields[0]
            key = record_dict.pop(key_field)
            data[key] = record_dict
        with open(destination, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=2, ensure_ascii=False)

    await asyncio.to_thread(_write)


async def write_jsonl(records: Sequence[RecordLike], destination: pathlib.Path) -> None:
    """
    Write `records` to `destination` as newline-delimited JSON (one object per line).

    Unlike `write_json`, records keep their original field order and are
    not re-keyed or deduplicated by their first field - useful for
    streaming/appending workflows, or piping straight into tools like `jq`.
    """

    def _write() -> None:
        with open(destination, "w", encoding="utf-8") as file:
            for record_dict in records_to_dicts(records):
                file.write(json.dumps(record_dict, ensure_ascii=False))
                file.write("\n")

    await asyncio.to_thread(_write)


async def write_txt(records: Sequence[RecordLike], destination: pathlib.Path) -> None:
    """Write `records` to `destination` as a numbered, human-readable list."""

    def _write() -> None:
        lines: list[str] = []
        for index, record in enumerate(records, start=1):
            record_dict = record.asdict()
            fields = record.fields
            title_field, *restfields = fields
            lines.append(f"({index}) {record_dict[title_field]}")
            for field in restfields:
                lines.append(
                    f"    {humanize_field(field)}: {render_display_text(record_dict[field])}"
                )
            lines.append("")
        with open(destination, "w", encoding="utf-8") as file:
            file.write("\n".join(lines))

    await asyncio.to_thread(_write)


async def write_xlsx(records: Sequence[RecordLike], destination: pathlib.Path) -> None:
    """
    Write `records` to `destination` as an Excel workbook.

    :raises ImportError: If `openpyxl` is not installed. Install it with
        `uv add openpyxl` or `pip install openpyxl`.
    """
    try:
        import openpyxl  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            '"openpyxl" is required to save .xlsx files. '
            "Install it with `uv add openpyxl` or `pip install openpyxl`."
        ) from exc

    def _write() -> None:
        fields = field_names(records)
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        if sheet is None:
            raise RuntimeError("`openpyxl` failed to create a workbook sheet")

        sheet.append([humanize_field(field) for field in fields])
        for record in records:
            sheet.append([render_display_text(value) for value in record.asdict().values()])
        workbook.save(destination)

    await asyncio.to_thread(_write)


WRITERS: dict[str, Writer] = {
    "csv": write_csv,
    "json": write_json,
    "jsonl": write_jsonl,
    "ndjson": write_jsonl,
    "txt": write_txt,
    "xlsx": write_xlsx,
}
"""Registry of file format to writer, mutated by `@writer(format)`."""


async def save(
    records: typing.Iterable[RecordLike] | typing.AsyncIterable[RecordLike],
    destination: str | pathlib.Path,
    *,
    format: str | None = None,
) -> None:
    """
    Save `records` to `destination`, choosing a writer by file format.

    `records` may be a plain list, any other sync iterable, or an async
    iterable - including the async generators returned by
    `slb_glossary.search.search` and `slb_glossary.search.get_terms_on`, which
    this collects before writing. `destination`'s parent directory is
    created automatically if it doesn't exist yet.

    :param records: The records to save. Each record must support
        `_asdict()` and `_fields`, as `typing.NamedTuple` instances do.
    :param destination: Path to write to. Its extension selects the writer
        to use, unless `format` is given. Missing parent directories are
        created automatically.
    :param format: File format to write, e.g. `"csv"`. Overrides the
        extension on `destination`. See `supported_formats` for the
        built-in choices.
    :raises UnsupportedFormatError: If no writer is registered for the
        resolved format.
    :raises WriterError: If the resolved writer raises while writing -
        commonly a permissions error or a full disk. The original
        exception is chained as `__cause__`.
    """
    destination = pathlib.Path(destination)
    resolved_format = (format or destination.suffix.lstrip(".") or "txt").lower()

    writer = WRITERS.get(resolved_format)
    if writer is None:
        raise UnsupportedFormatError(
            f"No writer registered for '{resolved_format}' files. "
            f"Supported formats: {', '.join(supported_formats())}. "
            "Register a custom writer with `@writer(format)`."
        )

    record_list = await materialize_records(records)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        await writer(record_list, destination)
    except Exception as exc:
        raise WriterError(
            f"Failed to write {len(record_list)} record(s) to {destination!s} "
            f"as '{resolved_format}': {exc}",
            destination=destination,
            format=resolved_format,
        ) from exc


def _register_writer(format: str, writer: Writer) -> None:
    """
    Register `writer` as the handler for `format`, adding or replacing it.

    :param format: File extension the writer handles, without a leading
        dot, e.g. `"yaml"`.
    :param writer: An async callable taking `(records, destination)` and
        writing `records` to `destination`.
    """
    WRITERS[format.lower().lstrip(".")] = writer


def writer(format: str) -> typing.Callable[[Writer], Writer]:
    """
    Decorator to register a writer function for a given file format.

    :param format: File extension the writer handles, without a leading
        dot, e.g. `"yaml"`.
    :return: A decorator that registers the decorated function as a writer.
    """

    def decorator(func: Writer) -> Writer:
        _register_writer(format, func)
        return func

    return decorator


def supported_formats() -> list[str]:
    """Return the file formats `save` currently has a writer for."""
    return sorted(WRITERS.keys())
