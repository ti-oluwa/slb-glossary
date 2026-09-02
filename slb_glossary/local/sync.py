"""
Sync the local database from a live `Session`.

Call these functions as often (or as rarely) as fits your own use of the glossary; see the
responsible-use note on `sync_all` in particular.
"""

import dataclasses
import datetime
import logging
import time
import typing

from slb_glossary.live.api import get_results_from_urls, get_terms_on, get_terms_urls
from slb_glossary.live.api import search as live_search
from slb_glossary.live.browser import Session
from slb_glossary.local.api import count as count_terms
from slb_glossary.local.api import get_terms_urls as get_known_urls
from slb_glossary.local.api import get_topics, upsert_results_incrementally
from slb_glossary.local.types import Database, Metadata
from slb_glossary.types import SearchResult

logger = logging.getLogger(__name__)

__all__ = [
    "SyncSummary",
    "sync_all",
    "sync_letter",
    "sync_query",
    "sync_topic",
    "sync_topics",
]


async def get_known_urls_set(
    db: Database,
    *,
    query: str | None = None,
    topic: str | None = None,
    start_letter: str | None = None,
    language: str | None = None,
) -> frozenset[str]:
    """
    Collect every locally stored URL matching the given filters, as a `frozenset`.

    Used to build `exclude` sets for the live fetches below, so a sync
    doesn't pay to re-fetch a term already stored locally under the same
    filter. `frozenset` keeps membership checks against it (one per URL
    the live site returns) cheap regardless of how many URLs are excluded.
    """
    return frozenset(
        await get_known_urls(
            db, query=query, topic=topic, start_letter=start_letter, language=language
        )
    )


@dataclasses.dataclass(slots=True, frozen=True, kw_only=True)
class SyncSummary:
    """Outcome of a live-to-local synchronization call."""

    terms_written: int
    """Number of term rows inserted or updated by this sync."""

    total_terms: int
    """Total number of terms stored locally after this sync."""

    topics: dict[str, int]
    """`{topic: term_count}` across every term stored locally after this sync."""

    synced_at: str
    """ISO-8601 UTC timestamp this sync completed at."""

    interrupted: bool = False
    """
    `True` if the live fetch behind this sync raised partway through and
    this summary reflects only the partial progress saved before that
    (see each function's `persist_on_error`), rather than a complete fetch.
    """


async def record_sync(
    db: Database, *, terms_written: int, language: str, interrupted: bool = False
) -> SyncSummary:
    """Recompute the local database's totals and persist them to `metadata.json`."""
    total = await count_terms(db)
    topics = await get_topics(db, language=language)

    metadata = Metadata.load(db.metadata_path)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    metadata.last_synced_at = now
    metadata.last_sync_language = language
    metadata.term_count = total
    metadata.topics = topics
    metadata.save(db.metadata_path)

    logger.debug(
        "Recorded sync metadata: %d term(s) written, %d total, %d topic(s)%s",
        terms_written,
        total,
        len(topics),
        " (interrupted)" if interrupted else "",
    )
    return SyncSummary(
        terms_written=terms_written,
        total_terms=total,
        topics=topics,
        synced_at=now,
        interrupted=interrupted,
    )


async def drain_and_upsert(
    db: Database,
    results: typing.AsyncIterable[SearchResult],
    *,
    language: str,
    batch_size: int | None,
    persist_on_error: bool,
) -> tuple[int, bool]:
    """
    Drain a live result stream through `upsert_results_incrementally`,
    returning `(written, interrupted)`.

    :return: The total rows written, and whether `results` raised partway
        through (in which case, if `persist_on_error` was `True`, whatever
        had already been buffered at that point was still saved).
    """
    stats: dict[str, int] = {}
    interrupted = False
    try:
        async for _ in upsert_results_incrementally(
            db,
            results,
            language=language,
            batch_size=batch_size,
            persist_on_error=persist_on_error,
            stats=stats,
        ):
            pass
    except BaseException:
        interrupted = True
        # `upsert_results_incrementally` already flushed (if `persist_on_error`)
        # and logged before this propagated; still populated `stats` via
        # its `finally`, so re-raise only after recording what was saved.
        written = stats.get("written", 0)
        logger.warning(
            "Sync interrupted after saving %d term(s); re-raising the original error",
            written,
            exc_info=True,
        )
        raise
    return stats.get("written", 0), interrupted


async def sync_topics(db: Database, session: Session) -> SyncSummary:
    """
    Refresh the local database's recorded topic list from `session`, without fetching terms.

    Cheap relative to the other sync functions as it only records
    `session.topics`, the counts already captured when the session opened
    (or last refreshed via `slb_glossary.topics.refresh_topics`) and does no
    additional requests to the glossary site.

    :param db: The local database to update.
    :param session: An open `Session` to read topic counts from.
    :return: A summary of the sync (`terms_written` is always `0`).
    """
    if not session.initialized:
        logger.debug("Session not yet initialized; loading topics before recording them")
        await session.initialize()

    logger.debug("Syncing topic list only (%d topic(s) known to session)", len(session.topics))
    return await record_sync(db, terms_written=0, language=session.language.value)


async def sync_query(
    db: Database,
    session: Session,
    query: str,
    *,
    topic: str | None = None,
    start_letter: str | None = None,
    limit: int | None = None,
    concurrency: int = 1,
    batch_size: int | None = None,
    persist_on_error: bool = True,
    skip_existing: bool = True,
) -> SyncSummary:
    """
    Fetch `query`'s results from the live glossary and store them locally.

    A lightweight way to keep the local database warm for terms you
    actually look up, without pulling the whole glossary.

    :param db: The local database to write to.
    :param session: An open `Session` to fetch from.
    :param query: Free-text query, as for `slb_glossary.live.search`.
    :param topic: Restrict the fetch to this topic, or several
        comma-separated topics.
    :param start_letter: Restrict the fetch to terms starting with this letter.
    :param limit: Maximum number of terms to fetch. `None` for unlimited.
        Counts only terms actually fetched; a term already stored locally
        and skipped (see `skip_existing`) doesn't use up this budget.
    :param concurrency: Concurrent term-page fetches. Keep this low; see
        `slb_glossary.live.get_results_from_urls`'s own note on server load.
    :param batch_size: Number of results to buffer before each incremental
        write to `db`. See `slb_glossary.local.upsert_results_incrementally`.
    :param persist_on_error: If `True` (the default), save whatever's
        buffered so far if the fetch raises partway through, instead of
        losing it (the resulting `SyncSummary.interrupted` is then `True`,
        and the original exception is still re-raised after saving).
    :param skip_existing: If `True` (the default), don't re-fetch a term
        already stored locally under this same `query`/`topic`/`start_letter`
        filter; the live site is only asked for terms not already known.
        Pass `False` to force a full re-fetch, e.g. to refresh
        already-stored definitions that may have changed live.
    :return: A summary of the sync.
    """
    started_at = time.monotonic()
    logger.info("Syncing query %r to the local database", query)
    exclude = (
        await get_known_urls_set(
            db,
            query=query,
            topic=topic,
            start_letter=start_letter,
            language=session.language.value,
        )
        if skip_existing
        else None
    )
    results = live_search(
        session,
        query,
        topic=topic,
        start_letter=start_letter,
        limit=limit,
        concurrency=concurrency,
        exclude=exclude,
    )
    written, interrupted = await drain_and_upsert(
        db,
        results,
        language=session.language.value,
        batch_size=batch_size,
        persist_on_error=persist_on_error,
    )
    summary = await record_sync(
        db,
        terms_written=written,
        language=session.language.value,
        interrupted=interrupted,
    )
    logger.info(
        "Synced query %r: %d term(s) written in %.3fs",
        query,
        written,
        time.monotonic() - started_at,
    )
    return summary


async def sync_topic(
    db: Database,
    session: Session,
    topic: str,
    *,
    limit: int | None = None,
    concurrency: int = 1,
    batch_size: int | None = None,
    persist_on_error: bool = True,
    skip_existing: bool = True,
) -> SyncSummary:
    """
    Fetch every term filed under `topic` from the live glossary and store them locally.

    :param db: The local database to write to.
    :param session: An open `Session` to fetch from.
    :param topic: Topic name, or several comma-separated topic names.
    :param limit: Maximum number of terms to fetch. `None` for unlimited.
        Counts only terms actually fetched; a term already stored locally
        and skipped (see `skip_existing`) doesn't use up this budget.
    :param concurrency: Concurrent term-page fetches.
    :param batch_size: Number of results to buffer before each incremental
        write to `db`. See `slb_glossary.local.upsert_results_incrementally`.
    :param persist_on_error: If `True` (the default), save whatever's
        buffered so far if the fetch raises partway through, instead of
        losing it (the resulting `SyncSummary.interrupted` is then `True`,
        and the original exception is still re-raised after saving).
    :param skip_existing: If `True` (the default), don't re-fetch a term
        already stored locally under `topic`; the live site is only asked
        for terms not already known. Pass `False` to force a full
        re-fetch, e.g. to refresh already-stored definitions that may
        have changed live.
    :return: A summary of the sync.
    """
    started_at = time.monotonic()
    logger.info("Syncing topic %r to the local database", topic)
    exclude = (
        await get_known_urls_set(
            db,
            topic=topic,
            language=session.language.value,
        )
        if skip_existing
        else None
    )
    results = get_terms_on(session, topic, limit=limit, concurrency=concurrency, exclude=exclude)
    written, interrupted = await drain_and_upsert(
        db,
        results,
        language=session.language.value,
        batch_size=batch_size,
        persist_on_error=persist_on_error,
    )
    summary = await record_sync(
        db,
        terms_written=written,
        language=session.language.value,
        interrupted=interrupted,
    )
    logger.info(
        "Synced topic %r: %d term(s) written in %.3fs",
        topic,
        written,
        time.monotonic() - started_at,
    )
    return summary


async def sync_letter(
    db: Database,
    session: Session,
    start_letter: str,
    *,
    topic: str | None = None,
    limit: int | None = None,
    concurrency: int = 1,
    batch_size: int | None = None,
    persist_on_error: bool = True,
    skip_existing: bool = True,
) -> SyncSummary:
    """
    Fetch every term starting with `start_letter` from the live glossary and
    store them locally.

    Useful for incremental updates keyed by the alphabet instead of by
    topic, e.g. syncing by start-letter "a" through "z" over several
    separate, spaced-out runs.

    :param db: The local database to write to.
    :param session: An open `Session` to fetch from.
    :param start_letter: The starting letter to restrict the fetch to.
    :param topic: Also restrict the fetch to this topic, or several
        comma-separated topics.
    :param limit: Maximum number of terms to fetch. `None` for unlimited.
        Counts only terms actually fetched; a term already stored locally
        and skipped (see `skip_existing`) doesn't use up this budget.
    :param concurrency: Concurrent term-page fetches.
    :param batch_size: Number of results to buffer before each incremental
        write to `db`. See `slb_glossary.local.upsert_results_incrementally`.
    :param persist_on_error: If `True` (the default), save whatever's
        buffered so far if the fetch raises partway through, instead of
        losing it (the resulting `SyncSummary.interrupted` is then `True`,
        and the original exception is still re-raised after saving).
    :param skip_existing: If `True` (the default), don't re-fetch a term
        already stored locally under this `start_letter`/`topic` filter;
        the live site is only asked for terms not already known. Pass
        `False` to force a full re-fetch, e.g. to refresh already-stored
        definitions that may have changed live.
    :return: A summary of the sync.
    """
    started_at = time.monotonic()
    logger.info("Syncing letter %r (topic=%r) to the local database", start_letter, topic)
    exclude = (
        await get_known_urls_set(
            db,
            topic=topic,
            start_letter=start_letter,
            language=session.language.value,
        )
        if skip_existing
        else None
    )
    urls = get_terms_urls(
        session, topic=topic, start_letter=start_letter, limit=limit, exclude=exclude
    )
    results = get_results_from_urls(
        session,
        urls,
        topic=topic,
        concurrency=concurrency,
        first_only=True,
        exclude=exclude,
    )
    written, interrupted = await drain_and_upsert(
        db,
        results,
        language=session.language.value,
        batch_size=batch_size,
        persist_on_error=persist_on_error,
    )
    summary = await record_sync(
        db,
        terms_written=written,
        language=session.language.value,
        interrupted=interrupted,
    )
    logger.info(
        "Synced letter %r: %d term(s) written in %.3fs",
        start_letter,
        written,
        time.monotonic() - started_at,
    )
    return summary


async def sync_all(
    db: Database,
    session: Session,
    *,
    concurrency: int = 1,
    batch_size: int | None = None,
    persist_on_error: bool = True,
    skip_existing: bool = True,
) -> SyncSummary:
    """
    Fetch the entire glossary from the live site and store it locally.

    This walks every topic `session` knows about and fetches every term
    filed under it. This is the heaviest sync this module offers, and the one
    most likely to draw attention from the glossary site.

    Use it sparingly, and mind the local-data disclaimer in
    `slb_glossary.local`'s package docstring; `sync_query`/`sync_topic`
    are lighter alternatives for keeping specific terms fresh instead of
    mirroring the whole site.

    Each topic is upserted incrementally as its terms are fetched (see
    `batch_size`), and if a topic's fetch fails partway through, whatever
    was already fetched for it, and for every topic completed before it
    is kept: only the failing topic's own in-progress batch is affected by
    `persist_on_error`, and the exception still propagates once that's
    handled, ending the sync at that point rather than skipping ahead to
    the next topic.

    :param db: The local database to write to.
    :param session: An open `Session` to fetch from.
    :param concurrency: Concurrent term-page fetches, per topic.
    :param batch_size: Number of results to buffer before each incremental
        write to `db`. See `slb_glossary.local.upsert_results_incrementally`.
    :param persist_on_error: If `True` (the default), save whatever's
        buffered for the topic currently being fetched if it raises
        partway through, instead of losing it (the resulting
        `SyncSummary.interrupted` is then `True`, and the original
        exception is still re-raised after saving).
    :param skip_existing: If `True` (the default), don't re-fetch a term
        already stored locally under the topic currently being synced;
        each topic only asks the live site for terms it doesn't already
        have. Pass `False` to force a full re-fetch of every topic, e.g.
        to refresh already-stored definitions that may have changed live.
    :return: A summary of the sync.
    """
    started_at = time.monotonic()
    if not session.initialized:
        logger.debug("Session not yet initialized; loading topics before syncing")
        await session.initialize()

    topic_names = sorted(session.topics)
    topics_count = len(topic_names)
    if not topic_names:
        logger.warning(
            "Session reports no topics; nothing to sync. This usually means "
            "the glossary's topic list failed to load. Try again, or pass "
            "a higher --retry-attempts."
        )
    logger.info("Syncing entire glossary (%d topics) to local database", topics_count)
    total_written = 0
    interrupted = False
    try:
        for index, topic_name in enumerate(topic_names, start=1):
            topic_started_at = time.monotonic()
            exclude = (
                await get_known_urls_set(
                    db,
                    topic=topic_name,
                    language=session.language.value,
                )
                if skip_existing
                else None
            )
            results = get_terms_on(session, topic_name, concurrency=concurrency, exclude=exclude)
            written, _ = await drain_and_upsert(
                db,
                results,
                language=session.language.value,
                batch_size=batch_size,
                persist_on_error=persist_on_error,
            )
            total_written += written
            elapsed = time.monotonic() - started_at
            logger.debug(
                "Synced topic %d/%d (%r): %d term(s) in %.3fs (%d total so far, %.3fs elapsed, avg %.3fs/topic)",
                index,
                topics_count,
                topic_name,
                written,
                time.monotonic() - topic_started_at,
                total_written,
                elapsed,
                elapsed / index,
            )
    except BaseException:
        interrupted = True
        raise
    finally:
        summary = await record_sync(
            db,
            terms_written=total_written,
            language=session.language.value,
            interrupted=interrupted,
        )

    elapsed = time.monotonic() - started_at
    logger.info(
        "Synced entire glossary: %d term(s) written across %d topic(s) in %.3fs (avg %.3fs/topic)",
        total_written,
        topics_count,
        elapsed,
        elapsed / topics_count if topic_names else 0.0,
    )
    return summary
