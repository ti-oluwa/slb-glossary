"""API for the local database search."""

import datetime
import json
import logging
import time
import typing
from collections.abc import Collection
from difflib import get_close_matches

import aiosqlite

from slb_glossary.constants import constants
from slb_glossary.local.hybrid import hybrid_search
from slb_glossary.local.lexical import lexical_search
from slb_glossary.local.types import Database, SearchMode
from slb_glossary.local.vectors import vector_search
from slb_glossary.types import RelatedTerm, SearchResult
from slb_glossary.utils import as_async_iterator, split_exclude

logger = logging.getLogger(__name__)

__all__ = [
    "upsert_results",
    "upsert_results_incrementally",
    "search",
    "get_terms_on",
    "get_term",
    "get_term_definitions",
    "get_random_term",
    "get_terms_urls",
    "get_topics",
    "fuzzy_match_topics",
    "count",
]


def _dump_related(related: tuple[RelatedTerm, ...] | None) -> str | None:
    """Serialize a `SearchResult.related` tuple to a compact JSON string."""
    if not related:
        return None
    return json.dumps([[link.term, link.url] for link in related])


def _load_related(raw: str | None) -> tuple[RelatedTerm, ...] | None:
    """Deserialize a `related_json` column back into a `SearchResult.related` tuple."""
    if not raw:
        return None
    return tuple(RelatedTerm(term=term, url=url) for term, url in json.loads(raw))


def _row_to_result(row: aiosqlite.Row) -> SearchResult:
    """Build a `SearchResult` from a `terms` row (or a row that at least joins in its columns)."""
    return SearchResult(
        term=row["term"],
        definition=row["definition"],
        grammatical_label=row["grammatical_label"],
        topic=row["topic"] or None,
        url=row["url"],
        image=row["image"],
        image_caption=row["image_caption"],
        related=_load_related(row["related_json"]),
        language=row["language"],
    )


UPSERT_STATEMENT = """
    INSERT INTO terms (
        url, term, definition, grammatical_label, topic, language,
        image, image_caption, related_json, source, fetched_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(url, topic) DO UPDATE SET
        term=excluded.term,
        definition=excluded.definition,
        grammatical_label=excluded.grammatical_label,
        language=excluded.language,
        image=excluded.image,
        image_caption=excluded.image_caption,
        related_json=excluded.related_json,
        source=excluded.source,
        fetched_at=excluded.fetched_at
"""


async def upsert_results(
    db: Database,
    results: typing.Iterable[SearchResult] | typing.AsyncIterable[SearchResult],
    *,
    language: str | None = None,
    source: str = "glossary",
) -> int:
    """
    Insert or replace `results` into the local database, keyed by (URL, topic).

    A result with no `url` is skipped, since `url` is half of the local
    database's primary key and there's nothing stable to upsert it
    against. A page with several definitions (one per topic it's filed
    under) upserts as that many distinct rows, all sharing the same
    `url` - keying on `url` alone would let the second definition
    silently overwrite the first.

    This writes everything in `results` in one go, only once `results` is
    fully consumed. For a live-fetched, potentially long-running stream,
    prefer `upsert_results_incrementally` instead, which writes in batches
    as results arrive rather than holding them all in memory and risking
    losing everything if the fetch is interrupted before this is called.

    :param db: The local database to write to.
    :param results: Results to store - a plain or async iterable of
        `SearchResult`, e.g. from `slb_glossary.live.search`,
        `slb_glossary.live.get_terms_on`, or `slb_glossary.local.loaders`.
    :param language: If given, force-store every result under this
        language, overriding each result's own `.language`. Left as
        `None` (the default), each result is stored under its own
        `.language` instead - the normal case, since a `SearchResult`
        already knows which glossary edition it came from.
    :param source: Provenance tag stored alongside each row: `"glossary"`
        for results fetched live from the site (the default), or a
        caller-chosen value such as `"user"` for imported data.
    :return: Number of rows written (results with no `url` don't count).
    """
    started_at = time.monotonic()
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    rows: list[tuple[typing.Any, ...]] = []
    skipped = 0

    def get_row(result: SearchResult) -> tuple[typing.Any, ...] | None:
        if not result.url:
            return None
        return (
            result.url,
            result.term,
            result.definition,
            result.grammatical_label,
            result.topic or "",
            language if language is not None else result.language,
            result.image,
            result.image_caption,
            _dump_related(result.related),
            source,
            now,
        )

    if isinstance(results, typing.AsyncIterable):
        async for result in results:
            row = get_row(result)
            if row is not None:
                rows.append(row)
            else:
                skipped += 1
    else:
        for result in results:
            row = get_row(result)
            if row is not None:
                rows.append(row)
            else:
                skipped += 1

    if skipped:
        logger.debug("Skipped %d result(s) with no url during upsert", skipped)

    if not rows:
        logger.debug(
            "upsert_results: nothing to write (0 rows in %.3fs)", time.monotonic() - started_at
        )
        return 0

    await db.connection.executemany(UPSERT_STATEMENT, rows)
    await db.connection.commit()
    elapsed = time.monotonic() - started_at
    row_count = len(rows)
    logger.info(
        "Upserted %d row(s) into the local database in %.3fs (avg %.3fs/row, source=%r)",
        row_count,
        elapsed,
        elapsed / row_count,
        source,
    )
    return row_count


async def upsert_results_incrementally(
    db: Database,
    results: typing.Iterable[SearchResult] | typing.AsyncIterable[SearchResult],
    *,
    language: str | None = None,
    source: str = "glossary",
    batch_size: int | None = None,
    persist_on_error: bool = True,
    stats: dict[str, int] | None = None,
) -> typing.AsyncIterator[SearchResult]:
    """
    Wrap `results`, upserting into `db` in batches as they arrive, instead of all at once.

    `upsert_results` only writes once its entire input has been consumed,
    which means the whole stream sits in memory until then, and a stream
    that dies partway through (a browser crash, a network blip, the
    process getting killed) loses everything already fetched, since
    nothing was ever written. This writes to `db` every `batch_size`
    results instead, and again with whatever's left over once `results`
    ends, including when it ends via an exception, if `persist_on_error`
    is `True`. Hence, progress is saved as it happens rather than all at once
    at the very end.

    :param db: The local database to write to.
    :param results: The result stream to wrap. A plain or async iterable.
    :param language: Passed straight through to `upsert_results`; see its
        docstring. `None` (the default) stores each result under its own
        `.language` rather than forcing one language on the whole stream.
    :param source: Provenance tag stored alongside each row. Passed
        straight through to `upsert_results`.
    :param batch_size: Number of results to buffer before writing an
        incremental batch. Smaller values save progress more often at the
        cost of more (smaller) database writes; larger values write less
        often but risk losing more unsaved results if something goes wrong
        before the next flush. `None` (the default) uses
        `constants.persist_batch_size`, resolved
        fresh on this call.
    :param persist_on_error: If `True` (the default), flush whatever's
        currently buffered when `results` raises, before letting the
        exception propagate, so an interrupted fetch still saves the
        progress it made. If `False`, an exception discards the current,
        not-yet-flushed buffer (results already flushed in earlier batches
        are unaffected either way).
    :param stats: If given, populated in place with `"written"` (total
        rows written) and `"batches"` (number of upsert calls made) once
        this generator is exhausted (normally or via error) - since an
        async generator can't hand back a return value the way a plain
        function can. Callers that only want the final count and don't
        need each result passed through (e.g. `slb_glossary.local.sync`)
        can drain this with `async for _ in ...: pass` and then read `stats`.
    :yield: Every item from `results`, unchanged.
    :raises ValueError: If `batch_size` is given and is less than 1.
    """
    resolved_batch_size = batch_size if batch_size is not None else constants.persist_batch_size
    if resolved_batch_size < 1:
        raise ValueError("`batch_size` must be at least 1")

    buffer: list[SearchResult] = []
    total_written = 0
    batches_written = 0
    error: BaseException | None = None

    async def _flush() -> None:
        nonlocal buffer, total_written, batches_written
        if not buffer:
            return

        pending, buffer = buffer, []
        written = await upsert_results(db, pending, language=language, source=source)
        total_written += written
        batches_written += 1
        logger.debug(
            "Persisted batch #%d: %d row(s) (%d total so far)",
            batches_written,
            written,
            total_written,
        )

    try:
        async for result in as_async_iterator(results):
            buffer.append(result)
            yield result
            if len(buffer) >= resolved_batch_size:
                await _flush()
    except BaseException as exc:
        error = exc
        raise
    finally:
        if buffer and (error is None or persist_on_error):
            try:
                await _flush()
            except Exception:
                logger.warning("Failed to persist the final batch of results", exc_info=True)
        elif buffer:
            logger.debug(
                "Discarding %d unpersisted result(s) after an error (persist_on_error=False)",
                len(buffer),
            )

        if total_written:
            level = logging.WARNING if error is not None else logging.INFO
            logger.log(
                level,
                "Persisted %d row(s) to the local database across %d batch(es)%s",
                total_written,
                batches_written,
                " (interrupted)" if error is not None else "",
            )
        if stats is not None:
            stats["written"] = total_written
            stats["batches"] = batches_written


def _apply_exclude(
    sql: str,
    params: list[typing.Any],
    exclude: Collection[str] | None,
    *,
    url_column: str = "url",
    term_column: str = "term",
) -> str:
    """
    Append `AND <url_column> NOT IN (...)`/`AND LOWER(TRIM(<term_column>)) NOT IN (...)` for `exclude`.

    `exclude` can hold URLs, term names, or a mix of both (see
    `slb_glossary.utils.split_exclude`); this appends whichever clauses
    are actually needed and extends `params` in place with their values.

    :param sql: The SQL built so far, ending right after its last `WHERE`/
        `AND` condition (no trailing whitespace required).
    :param params: The parameter list built so far. Extended in place.
    :param exclude: URLs/term names to exclude, or `None`.
    :param url_column: The (optionally table-qualified) column holding a
        row's URL, e.g. `"url"` or `"terms.url"`.
    :param term_column: The (optionally table-qualified) column holding a
        row's term name, e.g. `"term"` or `"terms.term"`.
    :return: `sql`, with any exclude clauses appended.
    """
    excluded_urls, excluded_names = split_exclude(exclude)
    if excluded_urls:
        placeholders = ", ".join("?" for _ in excluded_urls)
        sql += f" AND {url_column} NOT IN ({placeholders})"
        params.extend(excluded_urls)
    if excluded_names:
        placeholders = ", ".join("?" for _ in excluded_names)
        sql += f" AND LOWER(TRIM({term_column})) NOT IN ({placeholders})"
        params.extend(excluded_names)
    return sql


def fuzzy_match_topics(
    topics: typing.Mapping[str, typing.Any] | typing.Iterable[str],
    topic: str,
    *,
    cutoff: float = 0.6,
) -> str:
    """
    Resolve a user-supplied topic name to its closest match(es) among locally stored topics.

    Same difflib-based approach as `slb_glossary.utils.get_topic_match`
    uses for the live glossary's topic list, applied to whatever's actually
    been synced/imported into the local database instead.

    :param topics: Known local topic names, e.g. `get_topics(db)`'s
        return value (or any iterable of topic name strings).
    :param topic: One topic name, or several comma-separated, e.g.
        `"Geophysic,Drillng"`. Matching is case-insensitive and tolerant of
        minor misspellings.
    :param cutoff: Minimum similarity ratio (0-1, per `difflib.get_close_matches`)
        for a candidate to count as a match. Lower values match more loosely.
    :return: The resolved topic name(s), comma-separated, in their
        originally stored casing. A part of `topic` with no close match is
        dropped silently. Returns `""` if `topic` is empty, `topics` is
        empty, or nothing in `topic` matched.
    """
    if not topic:
        return ""

    lowered_to_original = {name.lower(): name for name in topics}
    if not lowered_to_original:
        return ""
    available = list(lowered_to_original)

    resolved: list[str] = []
    for raw_part in topic.split(","):
        candidate = raw_part.strip().lower()
        if not candidate:
            continue
        if candidate in lowered_to_original:
            resolved.append(lowered_to_original[candidate])
            continue
        matches = get_close_matches(candidate, available, n=1, cutoff=cutoff)
        if matches:
            resolved.append(lowered_to_original[matches[0]])

    result = ",".join(dict.fromkeys(resolved))
    if result != topic:
        logger.debug("Fuzzy-matched topic %r -> %r", topic, result)
    return result


async def resolve_topic(
    db: Database, topic: str | None, fuzzy: bool, *, language: str | None = None
) -> str | None:
    """
    Resolve a caller-supplied topic filter, optionally fuzzily, against the local database.

    :param db: The local database to read stored topic names from, only
        queried when `fuzzy` is `True`.
    :param topic: Raw topic filter as given by the caller (comma-separated
        for several topics), or `None`/empty for no filter.
    :param fuzzy: If `True`, resolve `topic` against `get_topics(db)` via
        `fuzzy_match_topics` instead of using it as-is.
    :return: The topic filter to apply, or `None`/`""` if there's nothing
        to filter by, including when `fuzzy` is `True` and no locally
        stored topic came close enough to match.
    """
    if not topic:
        return None
    if not fuzzy:
        return topic

    stored_topics = await get_topics(db, language=language)
    return fuzzy_match_topics(stored_topics, topic) or None


@typing.overload
async def search(
    db: Database,
    query: str,
    *,
    topic: str | None = None,
    start_letter: str | None = None,
    language: str | None = None,
    limit: int | None = 20,
    fuzzy: bool = False,
    mode: SearchMode | str | None = None,
    scored: typing.Literal[False] = False,
    exclude: Collection[str] | None = None,
) -> list[SearchResult]: ...
@typing.overload
async def search(
    db: Database,
    query: str,
    *,
    topic: str | None = None,
    start_letter: str | None = None,
    language: str | None = None,
    limit: int | None = 20,
    fuzzy: bool = False,
    mode: SearchMode | str | None = None,
    scored: typing.Literal[True],
    exclude: Collection[str] | None = None,
) -> list[tuple[SearchResult, float]]: ...


async def search(
    db: Database,
    query: str,
    *,
    topic: str | None = None,
    start_letter: str | None = None,
    language: str | None = None,
    limit: int | None = 20,
    fuzzy: bool = False,
    mode: SearchMode | str | None = None,
    scored: bool = False,
    exclude: Collection[str] | None = None,
) -> list[SearchResult] | list[tuple[SearchResult, float]]:
    """
    Search the local database for `query`, best match first.

    The entrypoint for all three ranking strategies, chosen via `mode`:

    - `"lexical"` (the default): `slb_glossary.local.lexical_search`,
      bm25 full-text ranking. Needs nothing beyond the base install.
    - `"semantic"`: `slb_glossary.local.vector_search`, embedding
      similarity ranking. Needs the `semantic` extra installed, and
      terms already embedded via `slb_glossary.local.embed_terms`.
    - `"hybrid"`: `slb_glossary.local.hybrid_search`, both, fused. Same
      extra/embedding requirement as `"semantic"`.

    `constants.default_search_mode` stays `"lexical"` rather than
    `"hybrid"` out of the box, so that plain `search(db, query)` keeps
    working on a database that's never had `embed_terms` run on it, and
    without forcing the `semantic` extra on every install. Set that
    constant (or pass `mode="hybrid"` per call) once you've embedded your
    terms; it generally ranks better than `"lexical"` alone.

    Pass `scored=True` to get each result's `[0.0, 1.0]`-ish relevance
    score alongside it, as `(result, score)` pairs, instead of calling
    the mode's underlying function separately.

    Unlike `slb_glossary.live.search`, this never touches the live
    glossary site; results are only as fresh as the last sync, import,
    or (for `"semantic"`/`"hybrid"`) `embed_terms` call.

    :param db: The local database to search.
    :param query: Free-text query, matched against term, definition, and topic.
    :param topic: Restrict results to this topic, or several
        comma-separated topics (case-insensitive exact match by default).
    :param start_letter: Restrict results to terms starting with this letter.
    :param language: Restrict results to this glossary language edition
        (e.g. `"en"`/`"es"`). `None` (the default) doesn't filter by language.
    :param limit: Maximum number of results. `None` for unlimited.
    :param fuzzy: If `True`, tolerate minor misspellings/partial names in
        `topic` by resolving it against locally stored topic names first.
        Has no effect if `topic` is falsy.
    :param mode: Which ranking strategy to use: `"lexical"`, `"semantic"`,
        or `"hybrid"`, or the matching `slb_glossary.local.types.SearchMode`
        member. `None` (the default) uses `constants.default_search_mode`,
        resolved fresh on this call.
    :param scored: If `True`, yield `(result, score)` pairs instead of
        bare results.
    :param exclude: URLs and/or term names to leave out of the results
        entirely. See `slb_glossary.utils.split_exclude` for how an entry
        is told apart as a URL vs. a term name.
    :return: Matching `SearchResult`s, or `(SearchResult, float)` pairs if
        `scored=True`, best match first either way.
    :raises DatabaseError: With `mode="semantic"`/`"hybrid"`, if
        `sqlite-vec` isn't installed, or its extension can't be loaded.
    :raises EmbeddingError: With `mode="semantic"`/`"hybrid"`, if
        `model2vec` isn't installed, or the embedding model's output size
        doesn't match `constants.embedding_dim`.
    """
    search_mode = SearchMode(mode if mode is not None else constants.default_search_mode)
    if search_mode is SearchMode.LEXICAL:
        results = await lexical_search(
            db,
            query,
            topic=topic,
            start_letter=start_letter,
            language=language,
            limit=limit,
            fuzzy=fuzzy,
            exclude=exclude,
        )
    elif search_mode is SearchMode.SEMANTIC:
        results = await vector_search(
            db,
            query,
            topic=topic,
            start_letter=start_letter,
            language=language,
            limit=limit,
            fuzzy=fuzzy,
            exclude=exclude,
        )
    else:
        results = await hybrid_search(
            db,
            query,
            topic=topic,
            start_letter=start_letter,
            language=language,
            limit=limit,
            fuzzy=fuzzy,
            exclude=exclude,
        )
    return results if scored else [result for result, _ in results]


async def get_terms_on(
    db: Database,
    topic: str,
    *,
    start_letter: str | None = None,
    language: str | None = None,
    limit: int | None = None,
    fuzzy: bool = False,
    exclude: Collection[str] | None = None,
) -> list[SearchResult]:
    """
    Return every locally stored term filed under `topic`.

    By default, `topic` must match a topic name already stored in the
    local database exactly (case-insensitively). Pass `fuzzy=True` to
    tolerate minor misspellings/partial names instead, resolved against
    whatever topics are actually present locally (there's no access to the
    live site's full topic list here, unlike `slb_glossary.live.get_terms_on`).

    :param db: The local database to read from.
    :param topic: Topic name, or several comma-separated topic names.
        Topic names themselves are language-specific (the glossary's
        Spanish edition doesn't use the same topic names as its English
        one), so this should already be in whatever language you mean;
        see `language` to also restrict which stored terms match.
    :param start_letter: Restrict results to terms starting with this letter.
    :param language: Restrict results to this glossary language edition
        (e.g. `"en"`/`"es"`). `None` (the default) doesn't filter by language.
    :param limit: Maximum number of results. `None` for unlimited.
    :param fuzzy: If `True`, resolve `topic` against locally stored topic
        names first, instead of requiring an exact (case-insensitive) match.
    :param exclude: URLs and/or term names to leave out of the results
        entirely, filtered in SQL before `limit` is applied. See
        `slb_glossary.utils.split_exclude` for how an entry is told apart
        as a URL vs. a term name.
    :return: `SearchResult`s filed under `topic`, ordered by term name.
    """
    logger.debug(
        "Local `get_terms_on`: topic=%r start_letter=%r language=%r limit=%r fuzzy=%r "
        "exclude=%d entr(ies)",
        topic,
        start_letter,
        language,
        limit,
        fuzzy,
        len(exclude) if exclude else 0,
    )
    started_at = time.monotonic()
    resolved_topic = await resolve_topic(db, topic, fuzzy, language=language)
    if not resolved_topic:
        logger.debug("No local topic resolved for %r, yielding nothing", topic)
        return []

    topics = [name.strip() for name in resolved_topic.split(",") if name.strip()]
    if not topics:
        return []

    placeholders = ", ".join("?" for _ in topics)
    sql = f"SELECT * FROM terms WHERE topic COLLATE NOCASE IN ({placeholders})"
    params: list[typing.Any] = list(topics)
    if start_letter:
        sql += " AND term COLLATE NOCASE LIKE ?"
        params.append(f"{start_letter}%")
    if language:
        sql += " AND language = ?"
        params.append(language)
    sql = _apply_exclude(sql, params, exclude)

    sql += " ORDER BY term"
    if limit:
        sql += " LIMIT ?"
        params.append(limit)

    async with db.connection.execute(sql, params) as cursor:
        rows = await cursor.fetchall()

    results = [_row_to_result(row) for row in rows]
    elapsed = time.monotonic() - started_at
    logger.debug(
        "Local `get_terms_on(%r)` returned %d term(s) in %.3fs", topic, len(results), elapsed
    )
    return results


@typing.overload
async def get_term(
    db: Database,
    term_or_url: str,
    *,
    language: str | None = None,
    topic: str | None = None,
    with_similar: typing.Literal[False] = False,
    similar_pool_size: int | None = None,
    max_similar_terms: int | None = None,
) -> SearchResult | None: ...
@typing.overload
async def get_term(
    db: Database,
    term_or_url: str,
    *,
    language: str | None = None,
    topic: str | None = None,
    with_similar: typing.Literal[True],
    similar_pool_size: int | None = None,
    max_similar_terms: int | None = None,
) -> tuple[SearchResult | None, list[tuple[SearchResult, float]]]: ...


async def get_term(
    db: Database,
    term_or_url: str,
    *,
    language: str | None = None,
    topic: str | None = None,
    with_similar: bool = False,
    similar_pool_size: int | None = None,
    max_similar_terms: int | None = None,
) -> SearchResult | None | tuple[SearchResult | None, list[tuple[SearchResult, float]]]:
    """
    Look up a single locally stored term by exact URL or exact term name.

    A glossary page can carry several definitions of the same term, one
    per topic it's filed under (see
    `slb_glossary.live.parsers.TERM_SECTION_SELECTOR`), all sharing the
    same URL. Pass `topic` to pick a specific one; without it, and more
    than one is stored, which one comes back is only deterministic (by
    topic name, alphabetically), not meaningful - use
    `get_term_definitions` instead if you want all of them, or need to
    pick by some other criterion.

    :param db: The local database to read from.
    :param term_or_url: A glossary term detail-page URL, or an exact
        (case-insensitive) term name.
    :param language: Restrict the lookup (and, with `with_similar=True`,
        the alternatives search) to this glossary language edition (e.g.
        `"en"`/`"es"`). `None` (the default) doesn't filter by language.
    :param topic: Restrict the lookup to this exact (case-insensitive)
        topic, disambiguating a term/URL with several stored definitions.
        `None` (the default) doesn't filter by topic; see above for what
        that means when more than one definition is stored.
    :param with_similar: If `True`, also search for up to `max_similar_terms`
        other locally stored results, via `lexical_search` on `term_or_url`
        itself, best match first, the exact match (if any) excluded. Each
        paired with its own relevance score, the same shape `lexical_search`
        itself returns. Handy for a "did you mean" prompt when the exact
        match turns out to be `None`, or just to see what else is nearby.
    :param similar_pool_size: Candidates `lexical_search` pulls before
        alternatives are drawn from them. Only used when `with_similar=True`.
        `None` (the default) uses
        `constants.similar_terms_pool_size`,
        resolved fresh on this call.
    :param max_similar_terms: Max alternatives returned. Only used when
        `with_similar=True`. `None` (the default) uses
        `constants.max_similar_terms`, resolved
        fresh on this call.
    :return: The stored `SearchResult`, or `None` if not found locally.
        With `with_similar=True`, a `(result, similar)` pair instead,
        `similar` being `(alternative, score)` pairs.
    """
    logger.debug("Local get_term: %r (language=%r topic=%r)", term_or_url, language, topic)
    sql = "SELECT * FROM terms WHERE (url = ? OR term = ? COLLATE NOCASE)"
    params: list[typing.Any] = [term_or_url, term_or_url]
    if language:
        sql += " AND language = ?"
        params.append(language)
    if topic:
        sql += " AND topic = ? COLLATE NOCASE"
        params.append(topic)
    sql += " ORDER BY topic LIMIT 1"

    async with db.connection.execute(sql, params) as cursor:
        row = await cursor.fetchone()

    result = _row_to_result(row) if row is not None else None
    if result is None:
        logger.debug("No local term found for %r", term_or_url)

    if not with_similar:
        return result

    resolved_pool_size = (
        similar_pool_size if similar_pool_size is not None else constants.similar_terms_pool_size
    )
    resolved_max_similar = (
        max_similar_terms if max_similar_terms is not None else constants.max_similar_terms
    )
    # Imported here, not at module level: see the comment in `_search`.
    from slb_glossary.local.lexical import lexical_search

    scored = await lexical_search(db, term_or_url, language=language, limit=resolved_pool_size)
    similar = [
        (candidate, score)
        for candidate, score in scored
        if result is None or candidate.url != result.url
    ][:resolved_max_similar]
    return result, similar


async def get_term_definitions(
    db: Database,
    term_or_url: str,
    *,
    language: str | None = None,
) -> list[SearchResult]:
    """
    Return every locally stored definition of a term, one per topic it's filed under.

    A glossary page can carry several definitions of the same term, one
    per topic (see `slb_glossary.live.parsers.TERM_SECTION_SELECTOR`), all
    sharing the same URL - `get_term` only ever returns one of them,
    picked by `topic` if given. Use this instead when you want all of
    them, e.g. to show every sense of a term across disciplines.

    :param db: The local database to read from.
    :param term_or_url: A glossary term detail-page URL, or an exact
        (case-insensitive) term name.
    :param language: Restrict to this glossary language edition (e.g.
        `"en"`/`"es"`). `None` (the default) doesn't filter by language.
    :return: Every stored definition for `term_or_url`, ordered by topic.
        Empty if nothing locally stored matches.
    """
    sql = "SELECT * FROM terms WHERE (url = ? OR term = ? COLLATE NOCASE)"
    params: list[typing.Any] = [term_or_url, term_or_url]
    if language:
        sql += " AND language = ?"
        params.append(language)
    sql += " ORDER BY topic"

    async with db.connection.execute(sql, params) as cursor:
        rows = await cursor.fetchall()

    results = [_row_to_result(row) for row in rows]
    logger.debug("Local `get_term_definitions(%r)` returned %d row(s)", term_or_url, len(results))
    return results


async def get_random_term(
    db: Database,
    *,
    topic: str | None = None,
    language: str | None = None,
    fuzzy: bool = False,
    exclude: Collection[str] | None = None,
) -> SearchResult | None:
    """
    Return one randomly chosen locally stored term, optionally restricted to a topic.

    :param db: The local database to read from.
    :param topic: Restrict the pick to this topic, or several
        comma-separated topics. `None` picks from every locally stored term.
    :param language: Restrict the pick to this glossary language edition
        (e.g. `"en"`/`"es"`). `None` (the default) doesn't filter by language.
    :param fuzzy: If `True`, tolerate minor misspellings/partial names in
        `topic` by resolving it against locally stored topic names first.
        Has no effect if `topic` is falsy.
    :param exclude: URLs and/or term names to leave out of the pick
        entirely, e.g. terms already seen this run. See
        `slb_glossary.utils.split_exclude` for how an entry is told apart
        as a URL vs. a term name.
    :return: A random `SearchResult`, or `None` if the local database (or
        the given topic/language within it, once `exclude` is taken into
        account) has no terms left to pick from.
    """
    logger.debug(
        "Local `get_random_term`: topic=%r language=%r fuzzy=%r exclude=%d entr(ies)",
        topic,
        language,
        fuzzy,
        len(exclude) if exclude else 0,
    )
    sql = "SELECT * FROM terms"
    params: list[typing.Any] = []
    conditions: list[str] = []

    resolved_topic = await resolve_topic(db, topic, fuzzy, language=language)
    if resolved_topic:
        topics = [name.strip() for name in resolved_topic.split(",") if name.strip()]
        if topics:
            placeholders = ", ".join("?" for _ in topics)
            conditions.append(f"topic COLLATE NOCASE IN ({placeholders})")
            params.extend(topics)
    if language:
        conditions.append("language = ?")
        params.append(language)

    excluded_urls, excluded_names = split_exclude(exclude)
    if excluded_urls:
        placeholders = ", ".join("?" for _ in excluded_urls)
        conditions.append(f"url NOT IN ({placeholders})")
        params.extend(excluded_urls)
    if excluded_names:
        placeholders = ", ".join("?" for _ in excluded_names)
        conditions.append(f"LOWER(TRIM(term)) NOT IN ({placeholders})")
        params.extend(excluded_names)

    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY RANDOM() LIMIT 1"

    async with db.connection.execute(sql, params) as cursor:
        row = await cursor.fetchone()

    if row is None:
        logger.debug("No local term available for random pick (topic=%r)", topic)
        return None
    return _row_to_result(row)


async def get_terms_urls(
    db: Database,
    *,
    query: str | None = None,
    topic: str | None = None,
    start_letter: str | None = None,
    language: str | None = None,
    limit: int | None = None,
    fuzzy: bool = False,
    exclude: Collection[str] | None = None,
) -> list[str]:
    """
    Return locally stored term URLs matching the given filters.

    :param db: The local database to read from.
    :param query: If given, restrict to (and rank by) an FTS5 match on
        this free-text query. See `search`.
    :param topic: Restrict to this topic, or several comma-separated topics.
    :param start_letter: Restrict to terms starting with this letter.
    :param language: Restrict to this glossary language edition (e.g.
        `"en"`/`"es"`). `None` (the default) doesn't filter by language.
    :param limit: Maximum number of URLs. `None` for unlimited.
    :param fuzzy: If `True`, tolerate minor misspellings/partial names in
        `topic` by resolving it against locally stored topic names first.
        Has no effect if `topic` is falsy.
    :param exclude: URLs and/or term names to leave out of the results
        entirely, filtered before `limit` is applied. See
        `slb_glossary.utils.split_exclude` for how an entry is told apart
        as a URL vs. a term name.
    :return: Matching term URLs.
    """
    logger.debug(
        "Local `get_terms_urls`: query=%r topic=%r start_letter=%r language=%r limit=%r "
        "exclude=%d entr(ies)",
        query,
        topic,
        start_letter,
        language,
        limit,
        len(exclude) if exclude else 0,
    )
    started_at = time.monotonic()

    if query:
        results = [
            result.url
            for result in await search(
                db,
                query,
                topic=topic,
                start_letter=start_letter,
                language=language,
                limit=limit,
                fuzzy=fuzzy,
                exclude=exclude,
            )
            if result.url
        ]
        logger.debug(
            "Local `get_terms_urls(query=%r)` returned %d url(s) in %.3fs",
            query,
            len(results),
            time.monotonic() - started_at,
        )
        return results

    resolved_topic = await resolve_topic(db, topic, fuzzy, language=language)
    sql = "SELECT url FROM terms WHERE 1=1"
    params: list[typing.Any] = []
    if resolved_topic:
        topics = [name.strip() for name in resolved_topic.split(",") if name.strip()]
        if topics:
            placeholders = ", ".join("?" for _ in topics)
            sql += f" AND topic COLLATE NOCASE IN ({placeholders})"
            params.extend(topics)

    if start_letter:
        sql += " AND term COLLATE NOCASE LIKE ?"
        params.append(f"{start_letter}%")

    if language:
        sql += " AND language = ?"
        params.append(language)

    sql = _apply_exclude(sql, params, exclude)

    sql += " ORDER BY term"
    if limit:
        sql += " LIMIT ?"
        params.append(limit)

    async with db.connection.execute(sql, params) as cursor:
        rows = await cursor.fetchall()

    results = [row["url"] for row in rows if row["url"]]
    logger.debug(
        "Local `get_terms_urls` returned %d url(s) in %.3fs",
        len(results),
        time.monotonic() - started_at,
    )
    return results


async def get_topics(db: Database, *, language: str | None = None) -> dict[str, int]:
    """
    Return `{topic: term_count}` for every topic represented in the local database.

    :param db: The local database to read from.
    :param language: Restrict to this glossary language edition (e.g.
        `"en"`/`"es"`). Topic names are language-specific (the glossary's
        Spanish edition uses different topic names than its English one),
        so counting across both without filtering can double-count the
        "same" topic under its two different names. `None` (the default)
        doesn't filter, and counts every stored term regardless of language.
    :return: Topic name to term count, for topics that have at least one
        locally stored term (matching `language`, if given).
    """
    sql = """
        SELECT topic, COUNT(*) AS term_count FROM terms
        WHERE topic IS NOT NULL AND topic != ''
    """
    params: list[typing.Any] = []
    if language:
        sql += " AND language = ?"
        params.append(language)
    sql += " GROUP BY topic COLLATE NOCASE ORDER BY topic COLLATE NOCASE"

    counts: dict[str, int] = {}
    async with db.connection.execute(sql, params) as cursor:
        async for row in cursor:
            counts[row["topic"]] = row["term_count"]

    logger.debug("Local database has %d topic(s) stored (language=%r)", len(counts), language)
    return counts


async def count(db: Database) -> int:
    """
    Return the total number of terms stored locally.

    :param db: The local database to read from.
    :return: The row count of the `terms` table.
    """
    async with db.connection.execute("SELECT COUNT(*) AS n FROM terms") as cursor:
        row = await cursor.fetchone()

    total = row["n"] if row is not None else 0
    logger.debug("Local database has %d term(s) stored", total)
    return total
