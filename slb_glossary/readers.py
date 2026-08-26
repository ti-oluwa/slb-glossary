"""
API for reading tabular records from a (file) source.

`read_rows` is the entry point most callers need; the `@reader` decorator
extends it to new file formats.
"""

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


Reader = typing.Callable[[pathlib.Path], typing.Iterator[dict[str, typing.Any]]]
"""
A callable that lazily yields one `{column: value}` row dict at a time from a file.

Register one with the `@reader(format)` decorator to teach `read_rows` a
new file format:

```python
@reader("yaml")
def read_yaml_rows(path: pathlib.Path) -> typing.Iterator[dict[str, typing.Any]]:
    import yaml

    with path.open("r", encoding="utf-8") as fh:
        yield from yaml.safe_load(fh)
```

A reader only needs to yield rows from `path`; it doesn't need to catch
or wrap I/O errors itself. Error-wrapping is domain-specific (a caller
importing rows into a database wants a different error than one just
inspecting a file), so it's left to whoever calls `read_rows`/the reader
directly.
"""


def read_csv_rows(path: pathlib.Path) -> typing.Iterator[dict[str, typing.Any]]:
    """
    Lazily read `path` as CSV, yielding one `{column: value}` row at a time.

    The file is opened lazily, on the generator's first row rather
    than at call time, and stays open only for as long as it's actually
    being iterated, so a caller can consume rows in batches as they're
    read, instead of holding the whole file's rows in memory at once.

    :param path: CSV file to read.
    :yield: One `{column: value}` dict per row.
    """
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        yield from csv.DictReader(fh)


def read_json_rows(path: pathlib.Path) -> typing.Iterator[dict[str, typing.Any]]:
    """
    Lazily yield each record from `path`'s JSON array (or an object containing one).

    JSON has no line-oriented record boundary the way CSV/XLSX do, so this
    still has to parse the whole file into memory to find the record array.

    :param path: JSON file to read.
    :yield: One record dict at a time, from the array found.
    :raises ValueError: If `path` doesn't contain a JSON array of records
        (or an object with one as one of its values).
    """
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
    yield from data


def read_xlsx_rows(path: pathlib.Path) -> typing.Iterator[dict[str, typing.Any]]:
    """
    Lazily read `path`'s first worksheet, yielding one `{header: value}` row at a time.

    Opens the workbook in `openpyxl`'s `read_only` mode, which itself
    streams rows from the underlying XML rather than loading the whole
    sheet into memory, and this generator passes that streaming straight
    through rather than collecting it into a list first. The workbook is
    closed once this generator is exhausted (or closed/garbage-collected
    early, e.g. if a caller stops consuming partway through).

    :param path: XLSX/XLSM file to read.
    :yield: One `{header: value}` dict per data row (the first row is
        treated as the header and isn't yielded itself).
    :raises ImportError: If the optional `openpyxl` dependency isn't installed.
    """
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
    :param reader_func: A callable taking a `Path` and lazily yielding
        `{column: value}` row dicts.
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


def read_rows(
    path: str | pathlib.Path, *, format: str | None = None
) -> typing.Iterator[dict[str, typing.Any]]:
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
    yield from reader_func(resolved_path)


def supported_formats() -> list[str]:
    """Return the file formats `read_rows` currently has a reader for."""
    return sorted(READERS.keys())
