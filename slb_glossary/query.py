"""
Source-aware query API containing a set of functions that can read the local
database, the live glossary, or both, without the caller having to specify what
source to use, and what order to use them every time.

```python
import slb_glossary as slb

async with slb.local.database() as db, slb.live.session() as session:
    # Local first; only opens a live page if the local DB has nothing.
    # Whatever came back live is written to `db` so the next call is local-only.
    async for lookup in slb.query.search(
        "water saturation", db=db, session=session, persist=True
    ):
        print(lookup.source.value, lookup.score, lookup.value.term)
```

At least one of `db` or `session` must be given to every function here.
Else, there's nothing to query. Which of the two is actually used (and
in what order) is controlled by `source`:

* `Source.LOCAL` - the local database only. Never touches the network.
  Requires `db`.
* `Source.LIVE` - the live glossary only. Never touches the local database
  (not even to read it). Requires `session`.
* `Source.AUTO` (the default when both `db` and `session` are given).
  Try `db` first; only fall back to `session` if the local database has
  nothing for the query. Pass `persist=True` to write whatever came back
  live into `db`, so a repeat lookup is served locally next time.

When only one of `db`/`session` is given, `Source.AUTO` simply
behaves like whichever of `Source.LOCAL`/`Source.LIVE` that one supports.

`search` narrows `Source.AUTO` further than the other functions here: a
local hit doesn't automatically end the search. Each local result is
scored against the query (see `slb_glossary.local.scored_search`), and if
even the best of them isn't a confident match, the live glossary is
queried too and its results are added on. The local results aren't
thrown away, just no longer treated as the whole answer.
`relevance_threshold` controls how confident is confident enough. See
`search`'s own docstring.

`search`/`get_terms_on` write live results to `db` incrementally, in
batches, rather than buffering the whole stream and writing it in one shot
at the end. See `slb_glossary.local.upsert_results_incrementally`'s
docstring for why, and `persist_batch_size`/`persist_on_error` for how to
tune it.

Every function here returns or yields `LookupResult`s, not bare values,
so a caller can always knows which source actually answered a query (and,
where relevant, how confident the match was) without threading that
information through separately. Unwrap with `.value`.
"""

import asyncio
import dataclasses
import enum
import logging
import random
import string
import time
import typing
from collections.abc import Collection

from slb_glossary import live
from slb_glossary.constants import constants
from slb_glossary.errors import QueryError
from slb_glossary.live.browser import Session
from slb_glossary.local import api as local_api
from slb_glossary.local.types import Database
from slb_glossary.natural_language import clean_query
from slb_glossary.relevance import EXACT_MATCH_SCORE, score_result
from slb_glossary.types import RelatedTerm, SearchResult

logger = logging.getLogger(__name__)

__all__ = [
    "Source",
    "LookupResult",
    "SimilarResult",
    "search",
    "get_terms_on",
    "get_terms_urls",
    "get_topics",
    "get_term",
    "related_terms",
    "get_random_term",
    "compare",
]


class Source(enum.Enum):
    """Where a `slb_glossary.query` function is allowed to read/write results from."""

    LOCAL = "local"
    """The local database only. Never touches the network. Requires `db`."""

    LIVE = "live"
    """The live glossary only. Never touches the local database. Requires `session`."""

    AUTO = "auto"
    """
    Local first, live as a fallback when the local database has nothing.
    See the module docstring for the full behavior.
    """


T = typing.TypeVar("T")


@dataclasses.dataclass(slots=True, kw_only=True, frozen=True)
class LookupResult(typing.Generic[T]):
    """The outcome of one value returned or yielded by a `slb_glossary.query` function, with its provenance."""

    value: T
    """The looked-up value itself (e.g. a `SearchResult`, or `None` if nothing was found)."""

    source: Source
    """Which source actually served this lookup: `Source.LOCAL` or `Source.LIVE`."""

    persisted: bool
    """
    Whether this result was written to `db` as part of the call that
    produced it (only ever `True` for a live result fetched with
    `persist=True`). For a streamed lookup (`search`, `get_terms_on`),
    this reflects whether persistence was requested for the call as a
    whole, not whether this specific item's own database write has been
    confirmed yet. 
    
    Live results are written incrementally in batches so an individual 
    item's write may still be pending in its batch when it's yielded.
    """

    score: float | None = None
    """
    A relevance score in `[0.0, 1.0]` for this value against the query it
    was found for, where that's meaningful. 
    
    `None` where scoring doesn't apply (a topic listing, a related-terms lookup, 
    and so on).
    """


@dataclasses.dataclass(slots=True, kw_only=True, frozen=True)
class SimilarResult:
    """
    The outcome of a `with_similar=True` term lookup: an exact match, plus alternatives.

    Returned by `get_term` (wrapped in a `LookupResult` itself) in place
    of a bare `SearchResult | None` when the caller opted into also
    seeing similarly-named results.
    """

    exact: LookupResult[SearchResult] | None
    """
    A `LookupResult` wrapping the exact (case-insensitive) term-name
    match, or `None` if there wasn't one. Its own `.score` is
    `EXACT_MATCH_SCORE`, since it's exact by definition.
    """

    similar: tuple[LookupResult[SearchResult], ...] = ()
    """
    Other results the live search turned up for the same query, each
    scored against it (see `slb_glossary.relevance.score_result`), best
    match first, `exact` itself excluded.

    Always empty for a local-only lookup, since only a live search has
    anything to compare against.
    """

    def __bool__(self) -> bool:
        return self.exact is not None or bool(self.similar)


def validate_source(db: Database | None, session: Session | None, source: Source) -> None:
    """Validate that `db`/`session` actually support the requested `source`."""
    if db is None and session is None:
        raise QueryError(
            "`slb_glossary.query` needs at least one of `db` or `session` to query anything."
        )
    if source is Source.LOCAL and db is None:
        raise QueryError(
            "`source=Source.LOCAL` requires `db` (a `database()`/`open_db()` Database)."
        )
    if source is Source.LIVE and session is None:
        raise QueryError("`source=Source.LIVE` requires `session` (an open `Session`).")


def resolve_source(db: Database | None, session: Session | None, source: Source) -> Source:
    """Attempt to narrow `Source.AUTO` to a starting concrete source given what's available."""
    validate_source(db, session, source)
    if source is not Source.AUTO or (db is not None and session is not None):
        return source
    return Source.LOCAL if db is not None else Source.LIVE


def validate_language(session: Session | None, language: str | None) -> None:
    """
    Validate that `language`, if given, matches `session`'s own language.

    A `Session` is bound to one glossary language edition for its whole
    lifetime, set when it was opened. So a live fetch can't honor a
    different `language` on a per-call basis the way a local read can.
    To search a different language live, open a second `Session` with
    that language instead.

    :param session: The live session about to be used, or `None` if this
        call has none (e.g. a local-only read).
    :param language: The language requested for this call, if any.
    :raises slb_glossary.QueryError: If `session` and `language` are both
        given and don't match.
    """
    if session is None or language is None:
        return
    if language != session.language.value:
        raise QueryError(
            f"Requested language {language!r} does not match this session's own "
            f"language {session.language.value!r}. Open a new `Session` with "
            f"`language={language!r}` to search that edition live instead."
        )


async def _maybe_persist(
    db: Database | None, results: typing.Sequence[SearchResult], *, persist: bool, language: str
) -> bool:
    """Upsert `results` into `db` in one shot."""
    if not persist or db is None or not results:
        return False
    started_at = time.monotonic()
    written = await local_api.upsert_results(db, results, language=language)
    if written:
        logger.info(
            "Cached %d result(s) fetched live into the local database in %.3fs",
            written,
            time.monotonic() - started_at,
        )
    return written > 0


def persist_incrementally(
    db: Database | None,
    results: typing.AsyncIterator[SearchResult],
    *,
    persist: bool,
    language: str,
    batch_size: int | None = None,
    persist_on_error: bool = True,
) -> typing.AsyncIterator[SearchResult]:
    """
    Wrap a live result stream, upserting into `db` in batches as results arrive.

    A thin wrapper around `slb_glossary.local.upsert_results_incrementally`,
    which does batching/flush-on-error work.

    :param db: The local database to write to. `results` is passed through
        unchanged (no persistence attempted) if this is `None`.
    :param results: The live result stream to wrap.
    :param persist: If `False`, disables writing entirely; `results` still
        passes through unchanged. Checked once up front so this is a cheap
        no-op wrapper when persistence wasn't requested.
    :param language: Glossary language edition these results were fetched
        in. Passed straight through to `slb_glossary.local.upsert_results_incrementally`.
    :param batch_size: Number of results to buffer before writing an
        incremental batch. Smaller values save progress more often at the
        cost of more (smaller) database writes; larger values write less
        often but risk losing more unsaved results if something goes wrong
        before the next flush. `None` (the default) passes through
        unchanged to `slb_glossary.local.upsert_results_incrementally`,
        which resolves it from `constants.persist_batch_size`.
    :param persist_on_error: If `True` (the default), flush whatever's
        currently buffered when `results` raises, before letting the
        exception propagate, so an interrupted fetch still saves the
        progress it made. If `False`, an exception discards the current,
        not-yet-flushed buffer (results already flushed in earlier batches
        are unaffected either way).
    :yield: Every item from `results`, unchanged.
    :raises ValueError: If `batch_size` is given and is less than 1.
    """
    if not persist or db is None:
        return results

    return local_api.upsert_results_incrementally(
        db,
        results,
        language=language,
        batch_size=batch_size,
        persist_on_error=persist_on_error,
    )


async def search(
    query: str,
    *,
    db: Database | None = None,
    session: Session | None = None,
    source: Source = Source.AUTO,
    topic: str | None = None,
    start_letter: str | None = None,
    language: str | None = None,
    limit: int | None = 3,
    concurrency: int = 1,
    persist: bool = False,
    persist_batch_size: int | None = None,
    persist_on_error: bool = True,
    fuzzy: bool = False,
    relevance_threshold: float | None = None,
    exclude: Collection[str] | None = None,
    auto_initialize: bool = True,
) -> typing.AsyncIterator[LookupResult[SearchResult]]:
    """
    Search for `query`, reading from `db`/`session` according to `source`.

    `query` is first passed through `slb_glossary.natural_language.clean_query`,
    so a plain-English question like "what is water saturation" is searched
    as "water saturation" against both `db` and `session`.

    With `source=Source.AUTO` (the default when both `db` and `session`
    are given), the local database is searched first and scored.
    If its best result meets `relevance_threshold`, those local results
    are served alone. Otherwise the live glossary is queried too - and
    queried *first*, ahead of the (unconfident) local results, on the
    theory that a live result is generally more trustworthy than a local
    match that wasn't confident enough to stand alone. Local results
    aren't thrown away, they still fill out any of `limit` that live
    couldn't, just no longer shown ahead of the live ones once local
    itself wasn't confident.

    :param query: Free-text query.
    :param db: An open local `Database`. Required for `Source.LOCAL`, and
        used as the primary source (and/or cache target) for `Source.AUTO`.
    :param session: An open live `Session`. Required for `Source.LIVE`,
        and used as the fallback (and/or live source) for `Source.AUTO`.
    :param source: Which source(s) to read from.
    :param topic: Restrict results to this topic, or several comma-separated topics.
    :param start_letter: Restrict results to terms starting with this letter.
    :param language: Restrict results to this glossary language edition
        (e.g. `"en"`/`"es"`). For a local read, this filters stored
        results by their `.language`; `None` (the default) doesn't
        filter. For a live read, `session` is already bound to one
        language for its whole lifetime, so `language` here is only
        validated against it, not applied as a filter. See
        `validate_language`.
    :param limit: Maximum number of terms to look up. `None` for unlimited.
    :param concurrency: Concurrent term-page fetches, only relevant when a
        live fetch happens. See `slb_glossary.live.search`.
    :param persist: If `True`, and a live fetch happens, write its results
        into `db` (if given) so the next matching call can be served
        locally. Written incrementally as results arrive (see
        `slb_glossary.local.upsert_results_incrementally`), and not all at
        once at the end.
    :param persist_batch_size: Number of live results to buffer before each
        incremental write to `db`. Only relevant when `persist=True` and a
        live fetch actually happens. `None` (the default) passes through
        unchanged to `slb_glossary.local.upsert_results_incrementally`,
        which resolves it from `constants.persist_batch_size`.
    :param persist_on_error: If `True` (the default), and `persist=True`,
        save whatever's buffered so far if the live fetch raises partway
        through, instead of losing it.
    :param fuzzy: If `True`, any local-database read (a `Source.LOCAL` read,
        or `Source.AUTO`'s local-first attempt) tolerates minor
        misspellings/partial names in `topic`. Live reads already
        fuzzy-match topics unconditionally, so this has no effect on them.
    :param relevance_threshold: Only used by `Source.AUTO`. The local
        database's best-scoring result must meet this (`0.0`-`1.0`, see
        `slb_glossary.local.scored_search`) for its results to be served
        without also querying the live glossary. Lower it to trust local
        results more readily (fewer live fetches); raise it to augment
        with live results more often. `None` (the default) uses
        `constants.relevance_threshold`, resolved
        fresh on this call.
    :param exclude: URLs and/or term names to leave out of the results
        entirely, e.g. ones already handled elsewhere in the same run.
        Passed straight through to whichever of `slb_glossary.local.search`/
        `slb_glossary.live.search` actually runs (and both, for
        `Source.AUTO`'s live-then-local merge). See
        `slb_glossary.utils.split_exclude` for how an entry is told apart
        as a URL vs. a term name. `None` (the default) excludes nothing.
    :yield: `LookupResult[SearchResult]`s, best match first.
    :param auto_initialize: If a live fetch happens and `session` isn't
        initialized yet, initialize it automatically (the default) or
        raise. See `slb_glossary.live.ensure_initialized`. Only ever
        matters when a live fetch actually happens - for `Source.AUTO`,
        that's never guaranteed, so passing `auto_initialize=False` with
        an uninitialized `session` doesn't raise unless local results
        turn out to be needed.
    :raises slb_glossary.QueryError: If neither `db` nor `session` is given,
        the requested `source` needs one that wasn't given, or `language`
        doesn't match `session`'s own language.
    :raises SessionNotInitializedError: If a live fetch happens, `session`
        isn't initialized, and `auto_initialize` is `False`.
    """
    validate_language(session, language)
    normalized_query = clean_query(query)
    resolved_relevance_threshold = (
        relevance_threshold if relevance_threshold is not None else constants.relevance_threshold
    )
    started_at = time.monotonic()
    resolved = resolve_source(db, session, source)
    count = 0
    if resolved is Source.LOCAL:
        assert db is not None
        async for result, score in local_api.search(
            db,
            normalized_query,
            topic=topic,
            start_letter=start_letter,
            language=language,
            limit=limit,
            fuzzy=fuzzy,
            scored=True,
            exclude=exclude,
        ):
            count += 1
            yield LookupResult(value=result, source=Source.LOCAL, persisted=False, score=score)

        logger.debug(
            "`query.search(%r, source=LOCAL)` yielded %d result(s) in %.3fs",
            normalized_query,
            count,
            time.monotonic() - started_at,
        )
        return

    if resolved is Source.LIVE or source is not Source.AUTO:
        assert session is not None
        live_stream = live.search(
            session,
            normalized_query,
            topic=topic,
            start_letter=start_letter,
            limit=limit,
            concurrency=concurrency,
            exclude=exclude,
            auto_initialize=auto_initialize,
        )
        async for result in persist_incrementally(
            db,
            live_stream,
            persist=persist,
            language=session.language.value,
            batch_size=persist_batch_size,
            persist_on_error=persist_on_error,
        ):
            count += 1
            score = score_result(normalized_query, result)
            yield LookupResult(value=result, source=Source.LIVE, persisted=persist, score=score)

        logger.debug(
            "`query.search(%r, source=LIVE)` yielded %d result(s) in %.3fs",
            normalized_query,
            count,
            time.monotonic() - started_at,
        )
        return

    # source is `Source.AUTO`, resolved started as `LOCAL`: score it, then decide.
    assert db is not None
    scored = await local_api.scored_search(
        db,
        normalized_query,
        topic=topic,
        start_letter=start_letter,
        language=language,
        limit=limit,
        fuzzy=fuzzy,
        exclude=exclude,
    )
    results = [result for result, _ in scored]
    best_score = scored[0][1] if scored else 0.0

    if results and best_score >= resolved_relevance_threshold:
        logger.debug(
            "Serving `search(%r)` from the local database alone "
            "(%d result(s), best score %.3f >= threshold %.3f, in %.3fs)",
            normalized_query,
            len(results),
            best_score,
            resolved_relevance_threshold,
            time.monotonic() - started_at,
        )
        for result, score in scored:
            yield LookupResult(value=result, source=Source.LOCAL, persisted=False, score=score)
        return

    if session is None:
        logger.debug(
            "`search(%r)`: local database's best score %.3f is below threshold %.3f "
            "(or empty), but no session to augment with; serving local results as-is",
            normalized_query,
            best_score,
            resolved_relevance_threshold,
        )
        for result, score in scored:
            yield LookupResult(value=result, source=Source.LOCAL, persisted=False, score=score)
        return

    logger.debug(
        "`search(%r)`: local database's best score %.3f is below threshold %.3f "
        "(or empty); trying the live glossary first, filling out with local results",
        normalized_query,
        best_score,
        resolved_relevance_threshold,
    )
    seen_urls: set[str] = set()
    seen_terms: set[str] = set()
    live_count = 0
    live_stream = live.search(
        session,
        normalized_query,
        topic=topic,
        start_letter=start_letter,
        limit=limit,
        concurrency=concurrency,
        exclude=exclude,
        auto_initialize=auto_initialize,
    )
    async for result in persist_incrementally(
        db,
        live_stream,
        persist=persist,
        language=session.language.value,
        batch_size=persist_batch_size,
        persist_on_error=persist_on_error,
    ):
        seen_urls.add(result.url or "")
        seen_terms.add((result.term or "").strip().lower())
        live_count += 1
        count += 1
        score = score_result(normalized_query, result)
        yield LookupResult(value=result, source=Source.LIVE, persisted=persist, score=score)

    remaining = None if limit is None else max(limit - live_count, 0)
    if remaining == 0:
        logger.debug(
            "`query.search(%r, source=AUTO)` yielded %d result(s) in %.3fs total "
            "(live quota already filled; not falling back to local)",
            normalized_query,
            count,
            time.monotonic() - started_at,
        )
        return

    local_count = 0
    for result, score in scored:
        if remaining is not None and local_count >= remaining:
            break
        url = result.url
        term_key = (result.term or "").strip().lower()
        if (url and url in seen_urls) or term_key in seen_terms:
            # Already covered by a live result; don't yield it twice.
            continue

        seen_urls.add(url or "")
        seen_terms.add(term_key)
        local_count += 1
        count += 1
        yield LookupResult(value=result, source=Source.LOCAL, persisted=False, score=score)

    logger.debug(
        "`query.search(%r, source=AUTO->LIVE+LOCAL)` yielded %d result(s) "
        "(%d live, %d local) in %.3fs total",
        normalized_query,
        count,
        live_count,
        local_count,
        time.monotonic() - started_at,
    )


async def get_terms_on(
    topic: str,
    *,
    db: Database | None = None,
    session: Session | None = None,
    source: Source = Source.AUTO,
    start_letter: str | None = None,
    language: str | None = None,
    limit: int | None = None,
    concurrency: int = 1,
    persist: bool = False,
    persist_batch_size: int | None = None,
    persist_on_error: bool = True,
    fuzzy: bool = False,
    exclude: Collection[str] | None = None,
    auto_initialize: bool = True,
) -> typing.AsyncIterator[SearchResult]:
    """
    Yield every term filed under `topic`, reading from `db`/`session` according to `source`.

    Same local-first, live-fallback behavior as `search` for `Source.AUTO`.

    :param topic: Topic name, or several comma-separated topic names.
        Topic names themselves are language-specific (the glossary's
        Spanish edition doesn't use the same topic names as its English
        one), so this should already be in whatever language you mean;
        see `language` to also restrict which stored terms match on a
        local read.
    :param db: An open local `Database`.
    :param session: An open live `Session`.
    :param source: Which source(s) to read from.
    :param start_letter: Restrict results to terms starting with this letter.
    :param language: Restrict results to this glossary language edition
        (e.g. `"en"`/`"es"`). For a local read, this filters by each
        stored result's `.language`; `None` (the default) doesn't filter.
        For a live read, `session` is already bound to one language for
        its whole lifetime, so `language` here is only validated against
        it, not applied as a filter. See `validate_language`.
    :param limit: Maximum number of terms to yield. `None` for unlimited.
    :param concurrency: Concurrent term-page fetches, only relevant when a
        live fetch happens.
    :param persist: If `True`, and a live fetch happens, cache its results
        into `db`, incrementally as they arrive - see
        `slb_glossary.local.upsert_results_incrementally`.
    :param persist_batch_size: Number of live results to buffer before each
        incremental write to `db`. Only relevant when `persist=True` and a
        live fetch actually happens. `None` (the default) passes through
        unchanged to `slb_glossary.local.upsert_results_incrementally`,
        which resolves it from `constants.persist_batch_size`.
    :param persist_on_error: If `True` (the default), and `persist=True`,
        save whatever's buffered so far if the live fetch raises partway
        through, instead of losing it.
    :param fuzzy: If `True`, any local-database read tolerates minor
        misspellings/partial names in `topic`. Live reads already
        fuzzy-match topics unconditionally, so this has no effect on them.
    :param exclude: URLs and/or term names to leave out of the results
        entirely. Passed straight through to whichever of
        `slb_glossary.local.get_terms_on`/`slb_glossary.live.get_terms_on` actually
        runs. See `slb_glossary.utils.split_exclude` for how an entry is
        told apart as a URL vs. a term name. `None` (the default)
        excludes nothing.
    :yield: `SearchResult`s filed under `topic`.
    :param auto_initialize: If a live fetch happens and `session` isn't
        initialized yet, initialize it automatically (the default) or
        raise. See `slb_glossary.live.ensure_initialized`.
    :raises slb_glossary.QueryError: If neither `db` nor `session` is given,
        the requested `source` needs one that wasn't given, or `language`
        doesn't match `session`'s own language.
    :raises SessionNotInitializedError: If a live fetch happens, `session`
        isn't initialized, and `auto_initialize` is `False`.
    """
    validate_language(session, language)
    started_at = time.monotonic()
    resolved = resolve_source(db, session, source)
    if resolved is Source.LOCAL:
        assert db is not None
        count = 0
        async for result in local_api.get_terms_on(
            db,
            topic,
            start_letter=start_letter,
            language=language,
            limit=limit,
            fuzzy=fuzzy,
            exclude=exclude,
        ):
            count += 1
            yield result

        logger.debug(
            "`query.get_terms_on(%r, source=LOCAL)` yielded %d result(s) in %.3fs",
            topic,
            count,
            time.monotonic() - started_at,
        )
        return

    if resolved is Source.LIVE or source is not Source.AUTO:
        assert session is not None
        live_stream = live.get_terms_on(
            session,
            topic,
            start_letter=start_letter,
            limit=limit,
            concurrency=concurrency,
            exclude=exclude,
            auto_initialize=auto_initialize,
        )
        count = 0
        async for result in persist_incrementally(
            db,
            live_stream,
            persist=persist,
            language=session.language.value,
            batch_size=persist_batch_size,
            persist_on_error=persist_on_error,
        ):
            count += 1
            yield result

        logger.debug(
            "`query.get_terms_on(%r, source=LIVE)` yielded %d result(s) in %.3fs",
            topic,
            count,
            time.monotonic() - started_at,
        )
        return

    assert db is not None
    results = [
        result
        async for result in local_api.get_terms_on(
            db,
            topic,
            start_letter=start_letter,
            language=language,
            limit=limit,
            fuzzy=fuzzy,
            exclude=exclude,
        )
    ]
    if results:
        logger.debug(
            "Serving `get_terms_on(%r)` from the local database (%d result(s) in %.3fs)",
            topic,
            len(results),
            time.monotonic() - started_at,
        )
        for result in results:
            yield result
        return

    if session is None:
        return

    logger.debug(
        "Local database had nothing for topic %r; falling back to the live glossary", topic
    )
    live_stream = live.get_terms_on(
        session,
        topic,
        start_letter=start_letter,
        limit=limit,
        concurrency=concurrency,
        exclude=exclude,
        auto_initialize=auto_initialize,
    )
    live_count = 0
    async for result in persist_incrementally(
        db,
        live_stream,
        persist=persist,
        language=session.language.value,
        batch_size=persist_batch_size,
        persist_on_error=persist_on_error,
    ):
        live_count += 1
        yield result
    logger.debug(
        "`query.get_terms_on(%r, source=AUTO->LIVE)` yielded %d result(s) in %.3fs total",
        topic,
        live_count,
        time.monotonic() - started_at,
    )


async def get_terms_urls(
    *,
    db: Database | None = None,
    session: Session | None = None,
    source: Source = Source.AUTO,
    query: str | None = None,
    topic: str | None = None,
    start_letter: str | None = None,
    language: str | None = None,
    limit: int | None = None,
    fuzzy: bool = False,
    exclude: Collection[str] | None = None,
    auto_initialize: bool = True,
) -> typing.AsyncIterator[str]:
    """
    Yield term detail-page URLs matching the given filters, reading from
    `db`/`session` according to `source`.

    Lighter-weight than `search`/`get_terms_on`: only the URLs themselves
    are returned, no definitions are fetched or parsed so there's
    nothing here to persist. Same local-first, live-fallback behavior as
    `search` for `Source.AUTO`.

    :param db: An open local `Database`.
    :param session: An open live `Session`.
    :param source: Which source(s) to read from.
    :param query: Restrict to a free-text query match.
    :param topic: Restrict to this topic, or several comma-separated
        topics. Topic names themselves are language-specific; see `language`.
    :param start_letter: Restrict to terms starting with this letter.
    :param language: Restrict results to this glossary language edition
        (e.g. `"en"`/`"es"`). For a local read, this filters by each
        stored result's `.language`; `None` (the default) doesn't filter.
        For a live read, `session` is already bound to one language for
        its whole lifetime, so `language` here is only validated against
        it, not applied as a filter. See `validate_language`.
    :param limit: Maximum number of URLs to yield. `None` for unlimited.
    :param fuzzy: If `True`, any local-database read tolerates minor
        misspellings/partial names in `topic`. Live reads already
        fuzzy-match topics unconditionally, so this has no effect on them.
    :param exclude: URLs and/or term names to skip over instead of
        yielding, e.g. ones already stored locally. Honored by both a
        local and a live read (see `slb_glossary.utils.split_exclude` for
        how an entry is told apart as a URL vs. a term name); a local
        read's own filters already narrow to what's stored, so excluding
        from that same set besides is rarely useful, but it's still
        honored there too for consistency. `None` (the default) excludes nothing.
    :yield: Matching term detail-page URLs.
    :param auto_initialize: If a live fetch happens and `session` isn't
        initialized yet, initialize it automatically (the default) or
        raise. See `slb_glossary.live.ensure_initialized`.
    :raises slb_glossary.QueryError: If neither `db` nor `session` is given,
        the requested `source` needs one that wasn't given, or `language`
        doesn't match `session`'s own language.
    :raises SessionNotInitializedError: If a live fetch happens, `session`
        isn't initialized, and `auto_initialize` is `False`.
    """
    validate_language(session, language)
    resolved = resolve_source(db, session, source)
    if resolved is Source.LOCAL:
        assert db is not None
        async for url in local_api.get_terms_urls(
            db,
            query=query,
            topic=topic,
            start_letter=start_letter,
            language=language,
            limit=limit,
            fuzzy=fuzzy,
            exclude=exclude,
        ):
            yield url
        return

    if resolved is Source.LIVE or source is not Source.AUTO:
        assert session is not None
        async for url in live.get_terms_urls(
            session,
            query=query,
            topic=topic,
            start_letter=start_letter,
            limit=limit,
            exclude=exclude,
            auto_initialize=auto_initialize,
        ):
            yield url
        return

    # source is Source.AUTO, resolved started as LOCAL: try it, then fall back.
    assert db is not None
    urls = [
        url
        async for url in local_api.get_terms_urls(
            db,
            query=query,
            topic=topic,
            start_letter=start_letter,
            language=language,
            limit=limit,
            fuzzy=fuzzy,
            exclude=exclude,
        )
    ]
    if urls:
        logger.debug("Serving `get_terms_urls(...)` from the local database")
        for url in urls:
            yield url
        return

    if session is None:
        logger.debug(
            "Local database had nothing for `get_terms_urls(...)`; no session to fall back to"
        )
        return

    logger.debug(
        "Local database had nothing for `get_terms_urls(...)`; falling back to the live glossary"
    )
    async for url in live.get_terms_urls(
        session,
        query=query,
        topic=topic,
        start_letter=start_letter,
        limit=limit,
        exclude=exclude,
        auto_initialize=auto_initialize,
    ):
        yield url


async def get_topics(
    *,
    db: Database | None = None,
    session: Session | None = None,
    source: Source = Source.AUTO,
    language: str | None = None,
    auto_initialize: bool = True,
) -> dict[str, int]:
    """
    Return `{topic: term_count}`, reading from `db`/`session` according to `source`.

    Unlike `search`/`get_terms_on`, a live read here never touches the
    network by itself once `session` is initialized: `session.topics` is
    already loaded at that point, so this just returns it directly.

    :param db: An open local `Database`. Its topic counts only reflect
        terms that have actually been cached locally, which may be a
        subset of the live glossary's full topic list.
    :param session: An open live `Session`.
    :param source: Which source(s) to read from. `Source.AUTO`
        prefers the local database when it has at least one topic, falling
        back to `session.topics` otherwise.
    :param language: Restrict to this glossary language edition (e.g.
        `"en"`/`"es"`). Only meaningful for a local read: topic names are
        language-specific (the glossary's Spanish edition uses different
        topic names than its English one), so counting across both
        without filtering can double-count the "same" topic under its two
        different names. For a live read, `session.topics` is already
        specific to `session`'s own language; `language` is only
        validated against it, not applied as a filter.
    :param auto_initialize: If a live read happens and `session` isn't
        initialized yet, initialize it automatically (the default) or
        raise. See `slb_glossary.live.ensure_initialized`.
    :return: Topic name to term count.
    :raises slb_glossary.QueryError: If neither `db` nor `session` is given,
        the requested `source` needs one that wasn't given, or `language`
        doesn't match `session`'s own language.
    :raises SessionNotInitializedError: If a live read happens, `session`
        isn't initialized, and `auto_initialize` is `False`.
    """
    validate_language(session, language)
    resolved = resolve_source(db, session, source)
    if resolved is Source.LOCAL:
        assert db is not None
        return await local_api.get_topics(db, language=language)

    if resolved is Source.LIVE or source is not Source.AUTO:
        assert session is not None
        await live.ensure_initialized(session, auto_initialize)
        return dict(session.topics)

    assert db is not None
    topics = await local_api.get_topics(db, language=language)
    if topics:
        return topics

    if session is None:
        return {}
    await live.ensure_initialized(session, auto_initialize)
    return dict(session.topics)


@typing.overload
async def get_term(
    term_or_url: str,
    *,
    db: Database | None = None,
    session: Session | None = None,
    source: Source = Source.AUTO,
    persist: bool = False,
    language: str | None = None,
    with_similar: typing.Literal[False] = False,
    similar_pool_size: int | None = None,
    max_similar_terms: int | None = None,
    auto_initialize: bool = True,
) -> LookupResult[SearchResult | None]: ...


@typing.overload
async def get_term(
    term_or_url: str,
    *,
    db: Database | None = None,
    session: Session | None = None,
    source: Source = Source.AUTO,
    persist: bool = False,
    language: str | None = None,
    with_similar: typing.Literal[True],
    similar_pool_size: int | None = None,
    max_similar_terms: int | None = None,
    auto_initialize: bool = True,
) -> LookupResult[SimilarResult]: ...


async def get_term(
    term_or_url: str,
    *,
    db: Database | None = None,
    session: Session | None = None,
    source: Source = Source.AUTO,
    persist: bool = False,
    language: str | None = None,
    with_similar: bool = False,
    similar_pool_size: int | None = None,
    max_similar_terms: int | None = None,
    auto_initialize: bool = True,
) -> LookupResult:
    """
    Look up a single term by exact name or detail-page URL.

    :param term_or_url: An exact (case-insensitive) term name, or a
        glossary term detail-page URL.
    :param db: An open local `Database`.
    :param session: An open live `Session`.
    :param source: Which source(s) to read from.
    :param persist: If `True`, and a live fetch happens, cache its result(s)
        into `db`. This is a single-value lookup, so there's normally only
        one result to write. Batching doesn't apply here the way it does
        for `search`/`get_terms_on`. With `with_similar=True`, every
        alternative gathered alongside the exact match is cached too.
    :param language: Restrict the lookup to this glossary language edition
        (e.g. `"en"`/`"es"`). For a local read, this filters by each
        stored result's `.language`; `None` (the default) doesn't filter.
        For a live read, `session` is already bound to one language for
        its whole lifetime, so `language` here is only validated against
        it, not applied as a filter. See `validate_language`.
    :param with_similar: If `True`, resolve to a `LookupResult[SimilarResult]`
        instead: `SimilarResult.exact` holds what a plain call would have
        returned, and `SimilarResult.similar` holds up to `max_similar_terms`
        other results found for `term_or_url` along the way, best match
        first, whether that's a local `scored_search` pass or a live one.
        Handy for a "did you mean" prompt when the exact match turns out
        to be `None`.
    :param similar_pool_size: Candidates pulled while looking for the
        exact match, and, with `with_similar=True`, to draw alternatives
        from. `None` (the default) uses
        `constants.similar_terms_pool_size`,
        resolved fresh on this call.
    :param max_similar_terms: Max alternatives returned in
        `SimilarResult.similar`. Ignored unless `with_similar=True`.
        `None` (the default) uses
        `constants.max_similar_terms`, resolved
        fresh on this call.
    :return: A `LookupResult` wrapping the found `SearchResult` (or `None` if
        not found by the resolved source(s)), scored `EXACT_MATCH_SCORE` if
        found. Or, with `with_similar=True`, a `SimilarResult`, where each
        of `.exact`/`.similar` carries its own score.
    :param auto_initialize: If a live fetch happens and `session` isn't
        initialized yet, initialize it automatically (the default) or
        raise. See `slb_glossary.live.ensure_initialized`.
    :raises slb_glossary.QueryError: If neither `db` nor `session` is given,
        the requested `source` needs one that wasn't given, or `language`
        doesn't match `session`'s own language.
    :raises SessionNotInitializedError: If a live fetch happens, `session`
        isn't initialized, and `auto_initialize` is `False`.
    """
    validate_language(session, language)
    resolved = resolve_source(db, session, source)
    if resolved is Source.LOCAL:
        assert db is not None
        return await _lookup_local_term(
            db,
            term_or_url,
            language=language,
            with_similar=with_similar,
            similar_pool_size=similar_pool_size,
            max_similar_terms=max_similar_terms,
        )

    if resolved is Source.LIVE or source is not Source.AUTO:
        assert session is not None
        fetched = await _lookup_live_term(
            session,
            term_or_url,
            with_similar=with_similar,
            similar_pool_size=similar_pool_size,
            max_similar_terms=max_similar_terms,
            auto_initialize=auto_initialize,
        )
        return await _finalize_live_term_lookup(
            db, session, fetched, with_similar=with_similar, persist=persist
        )

    assert db is not None
    local_lookup = await _lookup_local_term(
        db,
        term_or_url,
        language=language,
        with_similar=with_similar,
        similar_pool_size=similar_pool_size,
        max_similar_terms=max_similar_terms,
    )
    if with_similar:
        assert isinstance(local_lookup.value, SimilarResult)
        found = local_lookup.value.exact is not None
    else:
        found = local_lookup.value is not None
    if found:
        return local_lookup

    if session is None:
        return local_lookup

    fetched = await _lookup_live_term(
        session,
        term_or_url,
        with_similar=with_similar,
        similar_pool_size=similar_pool_size,
        max_similar_terms=max_similar_terms,
        auto_initialize=auto_initialize,
    )
    return await _finalize_live_term_lookup(
        db, session, fetched, with_similar=with_similar, persist=persist
    )


async def _lookup_local_term(
    db: Database,
    term_or_url: str,
    *,
    language: str | None,
    with_similar: bool,
    similar_pool_size: int | None,
    max_similar_terms: int | None,
) -> LookupResult:
    """
    Look up `term_or_url` in `db` and build `get_term`'s return value from it.

    A local lookup's exact match is always a name match by construction,
    so a hit always scores `EXACT_MATCH_SCORE`. With `with_similar=True`,
    alternatives come from `slb_glossary.local.get_term`'s own
    `with_similar` support (backed by `scored_search`), scored the same
    way `search` itself scores local results.
    """
    if not with_similar:
        result = await local_api.get_term(db, term_or_url, language=language)
        score = EXACT_MATCH_SCORE if result is not None else None
        return LookupResult(value=result, source=Source.LOCAL, persisted=False, score=score)

    result, similar_pairs = await local_api.get_term(
        db,
        term_or_url,
        language=language,
        with_similar=True,
        similar_pool_size=similar_pool_size,
        max_similar_terms=max_similar_terms,
    )
    exact = (
        LookupResult(value=result, source=Source.LOCAL, persisted=False, score=EXACT_MATCH_SCORE)
        if result is not None
        else None
    )
    similar = tuple(
        LookupResult(value=candidate, source=Source.LOCAL, persisted=False, score=score)
        for candidate, score in similar_pairs
    )
    return LookupResult(
        value=SimilarResult(exact=exact, similar=similar), source=Source.LOCAL, persisted=False
    )


async def _finalize_live_term_lookup(
    db: Database | None,
    session: Session,
    fetched: LookupResult[SearchResult] | None | SimilarResult,
    *,
    with_similar: bool,
    persist: bool,
) -> LookupResult:
    """
    Persist `_lookup_live_term`'s result if requested, then build `get_term`'s return value from it.

    Shared by `get_term`'s `Source.LIVE` branch and its `Source.AUTO`
    live-fallback branch, since both do the same persist-then-wrap work
    once they have a `fetched` value from `_lookup_live_term`.
    """
    persisted = await _maybe_persist(
        db,
        results=_flatten_results(fetched, with_similar=with_similar),
        persist=persist,
        language=session.language.value,
    )
    if with_similar:
        assert isinstance(fetched, SimilarResult)
        return LookupResult(value=fetched, source=Source.LIVE, persisted=persisted)

    assert not isinstance(fetched, SimilarResult)
    if fetched is None:
        return LookupResult(value=None, source=Source.LIVE, persisted=persisted)
    return LookupResult(
        value=fetched.value, source=Source.LIVE, persisted=persisted, score=fetched.score
    )


def _flatten_results(
    fetched: LookupResult[SearchResult] | None | SimilarResult, *, with_similar: bool
) -> list[SearchResult]:
    """Flatten a `_lookup_live_term` result into the `SearchResult`(s) `_maybe_persist` should cache."""
    if not with_similar:
        assert not isinstance(fetched, SimilarResult)
        return [fetched.value] if fetched is not None else []

    assert isinstance(fetched, SimilarResult)
    exact = [fetched.exact.value] if fetched.exact is not None else []
    return exact + [lookup.value for lookup in fetched.similar]


@typing.overload
async def _lookup_live_term(
    session: Session,
    term_or_url: str,
    *,
    with_similar: typing.Literal[False] = False,
    similar_pool_size: int | None = None,
    max_similar_terms: int | None = None,
    auto_initialize: bool = True,
) -> LookupResult[SearchResult] | None: ...


@typing.overload
async def _lookup_live_term(
    session: Session,
    term_or_url: str,
    *,
    with_similar: typing.Literal[True],
    similar_pool_size: int | None = None,
    max_similar_terms: int | None = None,
    auto_initialize: bool = True,
) -> SimilarResult: ...


async def _lookup_live_term(
    session: Session,
    term_or_url: str,
    *,
    with_similar: bool = False,
    similar_pool_size: int | None = None,
    max_similar_terms: int | None = None,
    auto_initialize: bool = True,
) -> LookupResult[SearchResult] | None | SimilarResult:
    """
    Resolve `term_or_url` against the live glossary: a URL fetches directly, else it's searched.

    :param session: An open live `Session`.
    :param term_or_url: An exact (case-insensitive) term name, or a
        glossary term detail-page URL.
    :param with_similar: If `True`, return a `SimilarResult` gathering up to
        `max_similar_terms` other results turned up while searching for
        the exact match, instead of just the exact match itself. A direct
        URL fetch has nothing to search, so `SimilarResult.similar` is
        always empty in that case.
    :param similar_pool_size: Live results to pull while looking for the
        exact match, and, with `with_similar=True`, to draw
        `SimilarResult.similar` alternatives from. `None` (the default)
        uses `constants.similar_terms_pool_size`, resolved fresh on this call.
    :param max_similar_terms: Max alternatives returned in
        `SimilarResult.similar`. Ignored unless `with_similar=True`.
        `None` (the default) uses `constants.max_similar_terms`, resolved
        fresh on this call.
    :return: A `LookupResult` wrapping the exact `SearchResult` match (or
        `None`), or with `with_similar=True`, a `SimilarResult` wrapping
        the exact match (if any) and its alternatives, each already
        wrapped in its own `LookupResult`.
    """
    await live.ensure_initialized(session, auto_initialize)
    resolved_pool_size = (
        similar_pool_size if similar_pool_size is not None else constants.similar_terms_pool_size
    )
    resolved_max_similar = (
        max_similar_terms if max_similar_terms is not None else constants.max_similar_terms
    )
    if term_or_url.startswith(("http://", "https://")):
        results: list[SearchResult] = [
            result async for result in live.get_results_from_url(session, term_or_url)
        ]
        if not results:
            return SimilarResult(exact=None) if with_similar else None

        # A direct URL fetch is definitionally an exact match: the caller
        # already knows exactly which page they want, so every definition
        # found on it scores `EXACT_MATCH_SCORE`.
        wrapped = [
            LookupResult(
                value=result, source=Source.LIVE, persisted=False, score=EXACT_MATCH_SCORE
            )
            for result in results
        ]
        if not with_similar:
            return wrapped[0]
        return SimilarResult(exact=wrapped[0], similar=tuple(wrapped[1:]))

    term = term_or_url.strip().lower()

    if not with_similar:
        # The correct definition should at least be in the first `similar_pool_size`
        # results, searching at least 2 at a time.
        async for result in live.search(session, term, limit=resolved_pool_size, concurrency=2):
            if result.term and result.term.strip().lower() == term:
                return LookupResult(
                    value=result, source=Source.LIVE, persisted=False, score=EXACT_MATCH_SCORE
                )

        # No exact (case-insensitive) match among the top results;
        # fall back to whatever ranked first, if anything did.
        async for result in live.search(session, term, limit=1):
            score = score_result(term, result)
            return LookupResult(value=result, source=Source.LIVE, persisted=False, score=score)
        return None

    # When `with_similar=True`, we drain the whole pool so alternatives are
    # available regardless of where (or whether) the exact match turned up.
    pool = [
        result
        async for result in live.search(session, term, limit=resolved_pool_size, concurrency=2)
    ]
    exact_result = next(
        (result for result in pool if result.term and result.term.strip().lower() == term), None
    )
    exact = (
        LookupResult(
            value=exact_result, source=Source.LIVE, persisted=False, score=EXACT_MATCH_SCORE
        )
        if exact_result is not None
        else None
    )
    similar_results = tuple(result for result in pool if result is not exact_result)[
        :resolved_max_similar
    ]
    similar = tuple(
        LookupResult(
            value=result, source=Source.LIVE, persisted=False, score=score_result(term, result)
        )
        for result in similar_results
    )
    return SimilarResult(exact=exact, similar=similar)


async def related_terms(
    term_or_url: str,
    *,
    db: Database | None = None,
    session: Session | None = None,
    source: Source = Source.AUTO,
    persist: bool = False,
    language: str | None = None,
    auto_initialize: bool = True,
) -> LookupResult[tuple[RelatedTerm, ...]]:
    """
    Look up the related terms linked from a single term's definition.

    A thin convenience wrapper around `get_term`. Fetches the term, then
    returns just its `SearchResult.related` links.

    :param term_or_url: An exact (case-insensitive) term name, or a
        glossary term detail-page URL.
    :param db: An open local `Database`.
    :param session: An open live `Session`.
    :param source: Which source(s) to read from.
    :param persist: If `True`, and a live fetch happens, cache the looked-up
        term's own result into `db`.
    :param language: Restrict the lookup to this glossary language
        edition. See `get_term`'s parameter of the same name.
    :param auto_initialize: If a live fetch happens and `session` isn't
        initialized yet, initialize it automatically (the default) or
        raise. See `slb_glossary.live.ensure_initialized`.
    :return: A `LookupResult` wrapping the related terms found (empty if
        `term_or_url` wasn't found, or was found but links to nothing).
    :raises slb_glossary.QueryError: If neither `db` nor `session` is given,
        the requested `source` needs one that wasn't given, or `language`
        doesn't match `session`'s own language.
    :raises SessionNotInitializedError: If a live fetch happens, `session`
        isn't initialized, and `auto_initialize` is `False`.
    """
    lookup = await get_term(
        term_or_url,
        db=db,
        session=session,
        source=source,
        persist=persist,
        language=language,
        auto_initialize=auto_initialize,
    )
    related = lookup.value.related if lookup.value is not None else None
    return LookupResult(value=related or (), source=lookup.source, persisted=lookup.persisted)


async def get_random_term(
    *,
    db: Database | None = None,
    session: Session | None = None,
    source: Source = Source.AUTO,
    topic: str | None = None,
    persist: bool = False,
    fuzzy: bool = False,
) -> LookupResult[SearchResult | None]:
    """
    Return one randomly chosen term, optionally restricted to a topic.

    For `Source.LIVE` (and `Source.AUTO` falling back to it), there's
    no "random" endpoint on the live site to call, so this samples one term
    detail-page URL from a random starting letter (or, if `topic` is given,
    from that topic) and fetches it.

    :param db: An open local `Database`.
    :param session: An open live `Session`.
    :param source: Which source(s) to read from.
        `Source.AUTO` here means "pick locally if the local database
        has anything (matching `topic`, if given), otherwise pick form the live site,
        not "try local, then live" the way the streaming functions do,
        since a local miss on one random draw says nothing about whether
        the local database is empty.
    :param topic: Restrict the pick to this topic, or several comma-separated topics.
    :param persist: If `True`, and a live pick happens, cache it into `db`.
        A single-value lookup, so batching doesn't apply.
    :param fuzzy: If `True`, a local pick tolerates minor misspellings/
        partial names in `topic`.
        Live picks already fuzzy-match topics unconditionally, so this has
        no effect on them.
    :return: A `LookupResult` wrapping the picked `SearchResult`, or `None` if
        nothing matched (empty local database/topic, or no live match found).
    :raises slb_glossary.QueryError: If neither `db` nor `session` is given,
        or the requested `source` needs one that wasn't given.
    """
    resolved = resolve_source(db, session, source)
    if resolved is Source.LOCAL:
        assert db is not None
        result = await local_api.get_random_term(db, topic=topic, fuzzy=fuzzy)
        return LookupResult(value=result, source=Source.LOCAL, persisted=False)

    if resolved is Source.LIVE or source is not Source.AUTO:
        assert session is not None
        result = await _fetch_random_term(session, topic=topic)
        persisted = await _maybe_persist(
            db,
            results=[result] if result else [],
            persist=persist,
            language=session.language.value,
        )
        return LookupResult(value=result, source=Source.LIVE, persisted=persisted)

    # AUTO: prefer a local pick when the local database actually has
    # something to pick from; only go live when it doesn't.
    assert db is not None
    result = await local_api.get_random_term(db, topic=topic, fuzzy=fuzzy)
    if result is not None:
        return LookupResult(value=result, source=Source.LOCAL, persisted=False)

    if session is None:
        return LookupResult(value=None, source=Source.LOCAL, persisted=False)

    result = await _fetch_random_term(session, topic=topic, sample_size=25)
    persisted = await _maybe_persist(
        db, results=[result] if result else [], persist=persist, language=session.language.value
    )
    return LookupResult(value=result, source=Source.LIVE, persisted=persisted)


LETTERS = list(string.ascii_lowercase)


def _random_letters(n: int | None = None) -> list[str]:
    """Return a shuffled list of letters a-z, optionally truncated to `n` letters."""
    shuffled = LETTERS[:]
    random.shuffle(shuffled)
    if n is not None:
        shuffled = shuffled[:n]
    return shuffled


async def _fetch_random_term(
    session: Session, *, topic: str | None, sample_size: int = 25
) -> SearchResult | None:
    """Sample up to `sample_size` live term URLs (by topic, or a random letter) and fetch one."""
    urls: list[str] = []
    if topic:
        url_iter = live.get_terms_urls(session, topic=topic, limit=sample_size)
        urls = [url async for url in url_iter]
    else:
        # No topic given: shuffle through starting letters until one yields
        # results, since a purely random letter (e.g. an uncommon one) may
        # have no terms at all.
        for letter in _random_letters():
            url_iter = live.get_terms_urls(session, start_letter=letter, limit=sample_size)
            urls = [url async for url in url_iter]
            if urls:
                break

    if not urls:
        return None

    chosen_url = random.choice(urls)
    return await anext(live.get_results_from_url(session, chosen_url, topic=topic), None)


@typing.overload
async def compare(
    terms: typing.Sequence[str],
    *,
    db: Database | None = None,
    session: Session | None = None,
    source: Source = Source.AUTO,
    persist: bool = False,
    language: str | None = None,
    concurrency: int | None = None,
    with_similar: typing.Literal[False] = False,
) -> dict[str, LookupResult[SearchResult | None]]: ...


@typing.overload
async def compare(
    terms: typing.Sequence[str],
    *,
    db: Database | None = None,
    session: Session | None = None,
    source: Source = Source.AUTO,
    persist: bool = False,
    language: str | None = None,
    concurrency: int | None = None,
    with_similar: typing.Literal[True],
) -> dict[str, LookupResult[SimilarResult]]: ...


async def compare(
    terms: typing.Sequence[str],
    *,
    db: Database | None = None,
    session: Session | None = None,
    source: Source = Source.AUTO,
    persist: bool = False,
    language: str | None = None,
    concurrency: int | None = None,
    with_similar: bool = False,
) -> dict[str, LookupResult]:
    """
    Look up several terms at once, for side-by-side comparison.

    Terms are looked up concurrently via `asyncio.gather`, up to
    `concurrency` at a time, bounded by a semaphore. Each lookup that
    actually reaches the live glossary checks out its own page from
    `session.pages` for the duration of its own fetch, so concurrent
    lookups never race over a single shared page. `session.max_pages`
    should comfortably cover `concurrency`. A local-only lookup never
    touches a page at all.

    :param terms: Term names (or detail-page URLs) to look up. Order is
        preserved in the returned dict, regardless of which order lookups
        actually finish in.
    :param db: An open local `Database`.
    :param session: An open live `Session`.
    :param source: Which source(s) to read from.
    :param persist: If `True`, cache any live fetches into `db`. Each term
        is looked up (and, on a live fetch, persisted) individually via
        `get_term`, so an error partway through this call still leaves
        earlier terms' results saved.
    :param language: Restrict every lookup to this glossary language
        edition. See `get_term`'s parameter of the same name.
    :param concurrency: Number of terms to look up in parallel. `None`
        (the default) uses `constants.compare_concurrency`,
        resolved fresh on this call.
    :param with_similar: If `True`, each entry is a `LookupResult[SimilarResult]`
        instead of `LookupResult[SearchResult | None]`, the same as `get_term`'s.
    :return: `{term_or_url: LookupResult}`, in the order `terms` was given.
        A `LookupResult.value` of `None` (or, with `with_similar=True`, a
        falsy `SimilarResult`) means that term wasn't found by the resolved source(s).
    :raises slb_glossary.QueryError: If neither `db` nor `session` is given,
        the requested `source` needs one that wasn't given, `terms` is
        empty, or `language` doesn't match `session`'s own language.
    :raises ValueError: If `concurrency` is given and is less than 1.
    """
    if not terms:
        raise QueryError("`compare()` needs at least one term to look up.")

    resolved_concurrency = (
        concurrency if concurrency is not None else constants.compare_concurrency
    )
    if resolved_concurrency < 1:
        raise ValueError("concurrency must be at least 1")

    validate_source(db, session, source)
    validate_language(session, language)
    started_at = time.monotonic()

    semaphore = asyncio.Semaphore(resolved_concurrency)

    async def _lookup(term: str) -> tuple[str, LookupResult]:
        async with semaphore:
            lookup = await get_term(
                term,
                db=db,
                session=session,
                source=source,
                persist=persist,
                language=language,
                with_similar=with_similar,
            )
        return term, lookup

    # `asyncio.gather` preserves the order of the awaitables passed to it
    # in its results, regardless of which finishes first, so `results`
    # already comes back in `terms`' own order.
    pairs = await asyncio.gather(*(_lookup(term) for term in terms))
    results = dict(pairs)

    elapsed = time.monotonic() - started_at
    terms_count = len(terms)
    logger.debug(
        "`compare(%d term(s), concurrency=%d)` done in %.3fs (avg %.3fs/term)",
        terms_count,
        resolved_concurrency,
        elapsed,
        elapsed / terms_count,
    )
    return results
