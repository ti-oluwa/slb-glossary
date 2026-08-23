"""Import user-provided CSV/JSON/XLSX data into the local database (and, optionally, its vector store)."""

import csv
import json
import logging
import pathlib
import typing

from slb_glossary.constants import constants
from slb_glossary.errors import DatabaseError
from slb_glossary.local.api import upsert_results
from slb_glossary.local.types import Database
from slb_glossary.local.vectors import upsert_vector
from slb_glossary.types import SearchResult

logger = logging.getLogger(__name__)

__all__ = ["load_file"]


def read_csv_rows(path: pathlib.Path) -> typing.Iterator[dict[str, typing.Any]]:
    """
    Lazily read `path` as CSV, yielding one `{column: value}` row at a time.

    The file is opened lazily, on the generator's first row rather
    than at call time, and stays open only for as long as it's actually
    being iterated, so `load_file` can consume (and upsert) rows in
    batches as they're read, instead of holding the whole file's rows in
    memory at once.

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
    :raises DatabaseError: If `path` doesn't contain a JSON array of
        records (or an object with one as one of its values).
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        for value in data.values():
            if isinstance(value, list):
                data = value
                break

    if not isinstance(data, list):
        raise DatabaseError(
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
    :raises DatabaseError: If the optional `openpyxl` dependency isn't installed.
    """
    try:
        import openpyxl  # noqa: F401
    except ImportError as exc:
        raise DatabaseError(
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


READERS: dict[str, typing.Callable[[pathlib.Path], typing.Iterator[dict[str, typing.Any]]]] = {
    "csv": read_csv_rows,
    "json": read_json_rows,
    "xlsx": read_xlsx_rows,
    "xlsm": read_xlsx_rows,
}


def _get_field(row: typing.Mapping[str, typing.Any], name: str | None) -> typing.Any:
    """Return `row[name]` matched case-insensitively, or `None` if absent/empty/unset."""
    if not name:
        return None
    for key, value in row.items():
        if str(key).strip().lower() == name.lower() and value not in (None, ""):
            return value
    return None


def _record_to_result(
    row: typing.Mapping[str, typing.Any],
    *,
    term_field: str,
    definition_field: str | None,
    topic_field: str | None,
    url_field: str | None,
    grammatical_label_field: str | None,
    language_field: str | None,
    default_language: str,
) -> SearchResult | None:
    """Build a `SearchResult` from one imported row, or `None` if it has no term."""
    term = _get_field(row, term_field)
    if not term:
        return None

    url = _get_field(row, url_field)
    if not url:
        # url is the local database's primary key; synthesize a stable one
        # from the term itself so rows without a URL column still
        # round-trip through upsert_results/get_term. Such rows just can't
        # be matched against a live glossary URL later.
        slug = "-".join(str(term).strip().lower().split())
        url = f"local://imported/{slug}"

    definition = _get_field(row, definition_field)
    grammatical_label = _get_field(row, grammatical_label_field)
    topic = _get_field(row, topic_field)
    language = _get_field(row, language_field)

    return SearchResult(
        term=str(term),
        definition=str(definition) if definition is not None else None,
        grammatical_label=str(grammatical_label) if grammatical_label is not None else None,
        topic=str(topic) if topic is not None else None,
        url=str(url),
        language=str(language) if language is not None else default_language,
    )


def _parse_embedding(raw: typing.Any) -> list[float] | None:
    """Parse an embedding cell/value into a list of floats, or `None` if empty/unparsable."""
    if raw is None or raw == "":
        return None
    if isinstance(raw, list):
        try:
            return [float(x) for x in raw]
        except (TypeError, ValueError):
            return None

    text = str(raw).strip()
    if not text:
        return None
    if text.startswith("["):
        try:
            return [float(x) for x in json.loads(text)]
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

    for delimiter in (",", ";"):
        if delimiter in text:
            parts = text.split(delimiter)
            break
    else:
        parts = text.split()
    try:
        return [float(part.strip()) for part in parts if part.strip()]
    except ValueError:
        return None


async def load_file(
    db: Database,
    path: str | pathlib.Path,
    *,
    format: str | None = None,
    term_field: str = "term",
    definition_field: str | None = "definition",
    topic_field: str | None = "topic",
    url_field: str | None = "url",
    grammatical_label_field: str | None = "grammatical_label",
    language_field: str | None = "language",
    default_language: str = "en",
    embedding_field: str | None = None,
    embedding_model: str = "custom",
    source: str = "user",
    batch_size: int | None = None,
) -> int:
    """
    Import term data from a CSV, JSON, or XLSX file into the local database.

    Each row/record needs at least `term_field`; every other field is
    optional and can be set to `None` to skip it entirely.

    Rows are read lazily from `path` and upserted into  `db` in batches
    of `batch_size` rows.

    :param db: The local database to write to.
    :param path: Path to the source file.
    :param format: One of `"csv"`, `"json"`, `"xlsx"`. Inferred from
        `path`'s extension if not given.
    :param term_field: Column/key holding each row's term name.
    :param definition_field: Column/key holding each row's definition
        text, or `None` to leave every imported row's definition unset.
    :param topic_field: Column/key holding each row's topic, or `None` to
        leave every imported row's topic unset.
    :param url_field: Column/key holding each row's source URL, or `None`
        to always synthesize a `local://imported/<slugified-term>` URL -
        needed since `url` is the local database's primary key.
    :param grammatical_label_field: Column/key holding each row's
        grammatical label (e.g. "Noun"), or `None` to leave it unset.
    :param language_field: Column/key holding each row's language edition
        (e.g. `"en"`/`"es"`), or `None` to always use `default_language`
        instead. A row with this column present but empty still falls
        back to `default_language`.
    :param default_language: Language stored for a row with no usable
        `language_field` value.
    :param embedding_field: Column/key holding a precomputed embedding
        vector for each row - either a JSON array, or a delimiter-separated
        (comma, semicolon, or whitespace) string of numbers. If given, a
        vector is stored for every row that has one (see
        `slb_glossary.local.vectors.upsert_vector`).
    :param embedding_model: Model label to store `embedding_field` vectors
        under. Only meaningful when `embedding_field` is given.
    :param source: Provenance tag stored on every imported row (see
        `slb_glossary.local.api.upsert_results`). Defaults to `"user"`
        so imported data can be told apart from live `"glossary"` rows.
    :param batch_size: Number of rows to buffer before writing an
        incremental upsert batch to `db`. Smaller values save progress
        more often at the cost of more (smaller) database writes; larger
        values write less often but risk losing more unwritten rows if
        something interrupts the import before the next flush. `None`
        (the default) uses `constants.import_batch_size`,
        resolved fresh on this call.
    :return: Number of rows imported.
    :raises DatabaseError: If `format` (or `path`'s extension) is
        unsupported, `path` isn't a well-formed file of that format, or
        `.xlsx` support isn't installed.
    :raises ValueError: If `batch_size` is given and is less than 1.
    """
    resolved_batch_size = batch_size if batch_size is not None else constants.import_batch_size
    if resolved_batch_size < 1:
        raise ValueError("`batch_size` must be at least 1")

    resolved_path = pathlib.Path(path)
    resolved_format = (format or resolved_path.suffix.lstrip(".")).lower()
    reader = READERS.get(resolved_format)
    if reader is None:
        raise DatabaseError(
            f"Unsupported import format {resolved_format!r} for {resolved_path!s}. "
            f"Supported formats: {', '.join(sorted(set(READERS)))}."
        )

    total_written = 0
    batches_written = 0
    buffer: list[SearchResult] = []

    async def _flush() -> None:
        nonlocal buffer, total_written, batches_written
        if not buffer:
            return
        pending, buffer = buffer, []
        # `language=None`: store each result's own `.language` field (set
        # per row in `_record_to_result`) rather than forcing one
        # language on the whole batch.
        written = await upsert_results(db, pending, language=None, source=source)
        total_written += written
        batches_written += 1
        logger.debug(
            "Imported batch #%d: %d row(s) (%d total so far) from %s",
            batches_written,
            written,
            total_written,
            resolved_path,
        )

    row_iter = reader(resolved_path)
    try:
        while True:
            # Isolate errors actually raised while *reading* the next row
            # (a malformed CSV line, a JSON decode error, a missing
            # `openpyxl`) from errors raised while *processing* one
            # already read (e.g. a database error from `upsert_vector`),
            # so only the former gets rewrapped as a `DatabaseError` about
            # the source file.
            try:
                row = next(row_iter)
            except StopIteration:
                break
            except DatabaseError:
                raise
            except Exception as exc:
                raise DatabaseError(
                    f"Could not read {resolved_path!s} as {resolved_format}: {exc}"
                ) from exc

            result = _record_to_result(
                row,
                term_field=term_field,
                definition_field=definition_field,
                topic_field=topic_field,
                url_field=url_field,
                grammatical_label_field=grammatical_label_field,
                language_field=language_field,
                default_language=default_language,
            )
            if result is None or not result.url:
                continue
            buffer.append(result)

            if embedding_field:
                parsed_embedding = _parse_embedding(_get_field(row, embedding_field))
                if parsed_embedding:
                    await upsert_vector(db, result.url, parsed_embedding, model=embedding_model)

            if len(buffer) >= resolved_batch_size:
                await _flush()
    finally:
        await _flush()

    logger.info(
        "Imported %d row(s) from %s across %d batch(es)",
        total_written,
        resolved_path,
        batches_written,
    )
    return total_written
