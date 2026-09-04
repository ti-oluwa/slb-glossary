"""API for reading tabular records from a (file) source."""

import asyncio
import csv
import json
import pathlib
import typing

from slb_glossary.errors import UnsupportedFormatError

__all__ = [
    "READERS",
    "Reader",
    "read_csv_rows",
    "read_json_rows",
    "read_rows",
    "read_xlsx_rows",
    "reader",
    "supported_formats",
]


Reader = typing.Callable[[pathlib.Path], typing.AsyncIterator[dict[str, typing.Any]]]
"""
An async callable that lazily yields one `{column: value}` row dict at a time from a file.

Register one with the `@reader(format)` decorator to teach `read_rows` a
new file format:

```python
import yaml

@reader("yaml")
async def read_yaml_rows(path: pathlib.Path) -> typing.AsyncIterator[dict[str, typing.Any]]:

    def load() -> list[dict[str, typing.Any]]:
        with path.open("r", encoding="utf-8") as fh:
            return yaml.safe_load(fh)

    for row in await asyncio.to_thread(load):
        yield row
```

A reader only needs to yield rows from `path`; it does not need to catch
or wrap I/O errors itself. Error-wrapping is domain-specific (a caller
importing rows into a database wants a different error than one just
inspecting a file), so it's left to whoever calls `read_rows`/the reader
directly.

A reader is async so that reading a large file does not block the event
loop while `slb_glossary`'s other async work (a live search, a database
write) is in flight. The built-in readers below offload their actual
blocking file I/O to a worker thread via `asyncio.to_thread` internally;
a custom reader that only does quick, in-memory work can skip that and
still just be an `async def` generator, no threading needed.
"""


_EXHAUSTED = object()


async def iter_in_thread(
    sync_iter: typing.Iterator[dict[str, typing.Any]],
) -> typing.AsyncIterator[dict[str, typing.Any]]:
    """
    Wrap a lazy, blocking `sync_iter` as an async iterator.

    Each `next()` call (including the file I/O it may trigger) runs in a
    worker thread via `asyncio.to_thread`, so pulling from `sync_iter`
    never blocks the event loop, while still only reading as much of the
    underlying file as has actually been consumed so far.

    :param sync_iter: A synchronous, lazy iterator, e.g. a generator
        that opens and reads a file incrementally.
    :yield: Whatever `sync_iter` yields, one item at a time.
    """
    while True:
        item = await asyncio.to_thread(next, sync_iter, _EXHAUSTED)
        if item is _EXHAUSTED:
            return
        yield typing.cast(dict[str, typing.Any], item)


def _read_csv_rows(path: pathlib.Path) -> typing.Iterator[dict[str, typing.Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        yield from csv.DictReader(fh)


async def read_csv_rows(path: pathlib.Path) -> typing.AsyncIterator[dict[str, typing.Any]]:
    """
    Lazily read `path` as CSV, yielding one `{column: value}` row at a time.

    The file is opened lazily, on the first row actually pulled rather
    than at call time, and stays open only for as long as it's actually
    being iterated, so a caller can consume rows in batches as they're
    read, instead of holding the whole file's rows in memory at once.
    Reading happens in a worker thread, so it never blocks the event loop.

    :param path: CSV file to read.
    :yield: One `{column: value}` dict per row.
    """
    async for row in iter_in_thread(_read_csv_rows(path)):
        yield row


async def read_json_rows(path: pathlib.Path) -> typing.AsyncIterator[dict[str, typing.Any]]:
    """
    Lazily yield each record from `path`'s JSON array (or an object containing one).

    JSON has no line-oriented record boundary the way CSV/XLSX do, so this
    still has to parse the whole file into memory to find the record array;
    that parse runs in a worker thread so it does not block the event loop,
    but nothing is actually streamed record-by-record from disk the way
    `read_csv_rows`/`read_xlsx_rows` can.

    :param path: JSON file to read.
    :yield: One record dict at a time, from the array found.
    :raises ValueError: If `path` does not contain a JSON array of records
        (or an object with one as one of its values).
    """

    def load() -> list[typing.Any]:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            for value in data.values():
                if isinstance(value, list):
                    data = value
                    break

        if not isinstance(data, list):
            raise ValueError(
                f"{path}: expected a JSON array of records (or an object containing one)."
            )
        return data

    for row in await asyncio.to_thread(load):
        yield row


def _read_xlsx_rows(path: pathlib.Path) -> typing.Iterator[dict[str, typing.Any]]:
    try:
        import openpyxl
    except ImportError as exc:
        raise ImportError(
            "Reading a .xlsx file requires the 'openpyxl' package. "
            "Install it with `pip install slb-glossary[xlsx]`."
        ) from exc

    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = workbook.active
        rows_iter = sheet.iter_rows(values_only=True)  # type: ignore[union-attr]
        try:
            header = [str(cell) if cell is not None else "" for cell in next(rows_iter)]
        except StopIteration:
            return
        for row in rows_iter:
            yield {header[i]: value for i, value in enumerate(row) if i < len(header)}
    finally:
        workbook.close()


async def read_xlsx_rows(path: pathlib.Path) -> typing.AsyncIterator[dict[str, typing.Any]]:
    """
    Lazily read `path`'s first worksheet, yielding one `{header: value}`
    row at a time.

    Opens the workbook in `openpyxl`'s `read_only` mode, which itself
    streams rows from the underlying XML rather than loading the whole
    sheet into memory; this generator passes that streaming straight
    through, in a worker thread, rather than blocking the event loop or
    collecting the sheet into a list first. The workbook is closed once
    this generator is exhausted (or closed/garbage-collected early, e.g.
    if a caller stops consuming partway through).

    :param path: XLSX/XLSM file to read.
    :yield: One `{header: value}` dict per data row (the first row is
        treated as the header and is not yielded itself).
    :raises ImportError: If the optional `openpyxl` dependency is not installed.
    """
    async for row in iter_in_thread(_read_xlsx_rows(path)):
        yield row


READERS: dict[str, Reader] = {
    "csv": read_csv_rows,
    "json": read_json_rows,
    "xlsx": read_xlsx_rows,
    "xlsm": read_xlsx_rows,
}
"""Registry of file format to reader, mutated by `@reader(format)`."""


def _register_reader(format: str, reader_func: Reader) -> None:
    """
    Register `reader_func` as the handler for `format`, adding or replacing it.

    :param format: File extension the reader handles, without a leading
        dot, e.g. `"yaml"`.
    :param reader_func: An async callable taking a `Path` and lazily
        yielding `{column: value}` row dicts.
    """
    READERS[format.lower().lstrip(".")] = reader_func


def reader(format: str) -> typing.Callable[[Reader], Reader]:
    """
    Decorator to register a reader function for a given file format.

    :param format: File extension the reader handles, without a leading
        dot, e.g. `"yaml"`.
    :return: A decorator that registers the decorated function as a reader.
    """

    def decorator(func: Reader) -> Reader:
        _register_reader(format, func)
        return func

    return decorator


async def read_rows(
    path: str | pathlib.Path, *, format: str | None = None
) -> typing.AsyncIterator[dict[str, typing.Any]]:
    """
    Lazily read `path` as tabular records, choosing a reader by file format.

    :param path: Path to the source file.
    :param format: File format to read as, e.g. `"csv"`. Overrides `path`'s
        extension. See `supported_formats` for the built-in choices.
    :yield: One `{column: value}` dict per record, in the source file's own order.
    :raises UnsupportedFormatError: If no reader is registered for the resolved format.
    """
    resolved_path = pathlib.Path(path)
    resolved_format = (format or resolved_path.suffix.lstrip(".")).lower()

    reader_func = READERS.get(resolved_format)
    if reader_func is None:
        raise UnsupportedFormatError(
            f"No reader registered for {resolved_format!r} files. "
            f"Supported formats: {', '.join(supported_formats())}. "
            "Register a custom reader with `@reader(format)`."
        )
    async for row in reader_func(resolved_path):
        yield row


def supported_formats() -> list[str]:
    """Return the file formats `read_rows` currently has a reader for."""
    return sorted(READERS.keys())
