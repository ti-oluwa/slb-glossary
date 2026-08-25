"""Import externally provided CSV/JSON/XLSX data into the local database."""

import logging
import pathlib
import typing

from slb_glossary.constants import constants
from slb_glossary.errors import DatabaseError
from slb_glossary.local.api import upsert_results
from slb_glossary.local.types import Database
from slb_glossary.readers import READERS, read_rows
from slb_glossary.types import SearchResult

logger = logging.getLogger(__name__)

__all__ = ["load_file"]


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
    :param format: One of `"csv"`, `"json"`, `"xlsx"`, or any other format
        registered with `slb_glossary.readers`'s `@reader` decorator.
        Inferred from `path`'s extension if not given.
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
    :param source: Provenance tag stored on every imported row (see
        `slb_glossary.local.api.upsert_results`). Defaults to `"user"`
        so imported data can be told apart from live `"glossary"` rows.
    :param batch_size: Number of rows to buffer before writing an
        incremental upsert batch to `db`. Smaller values save progress
        more often at the cost of more (smaller) database writes; larger
        values write less often but risk losing more unwritten rows if
        something interrupts the import before the next flush. `None`
        (the default) uses `constants.import_batch_size`, resolved fresh
        on this call.
    :return: Number of rows imported. Run `slb_glossary.local.embed_terms`
        afterward to make imported terms searchable via
        `slb_glossary.local.vector_search`/`hybrid_search`.
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
    if resolved_format not in READERS:
        raise DatabaseError(
            f"Unsupported import format {resolved_format!r} for {resolved_path!s}. "
            f"Supported formats: {', '.join(READERS)}."
        )

    total_written = 0
    batches_written = 0
    buffer: list[SearchResult] = []

    async def _flush() -> None:
        nonlocal buffer, total_written, batches_written
        if not buffer:
            return
        pending, buffer = buffer, []
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

    row_iter = read_rows(resolved_path, format=resolved_format)
    try:
        while True:
            # Isolate errors actually raised while *reading* the next row
            # from errors raised while *processing* one already read, so
            # only the error that occurred while processing gets rewrapped as a
            # `DatabaseError` about the source file.
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
