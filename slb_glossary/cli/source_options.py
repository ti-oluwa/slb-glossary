"""
Shared `--local`/`--live`/`--auto` options for commands built on `slb_glossary.query`.

Also handles resolving *where* the local database lives for those
commands (`--db-path`, or the `Database` section of the loaded `Config`),
and opening it/a live session lazily, so a command that turns out to be
satisfiable locally never pays for launching a browser.
"""

import contextlib
import logging
import pathlib
import typing

import click

from slb_glossary.cli.session_options import (
    DEFAULT_CONFIG_SENTINEL,
    load_named_config,
    resolve_session_kwargs,
)
from slb_glossary.config import Config
from slb_glossary.connectivity import has_internet_connection
from slb_glossary.constants import constants
from slb_glossary.live.browser import Session
from slb_glossary.live.browser import session as browser_session
from slb_glossary.local.connection import database
from slb_glossary.local.types import Database
from slb_glossary.paths import get_data_dir
from slb_glossary.query import QueryResult, Source

logger = logging.getLogger(__name__)

__all__ = [
    "database_option",
    "exclude_option",
    "live_session",
    "load_config",
    "local_storage_enabled",
    "open_configured_db",
    "persist_kwargs",
    "resolve_db_path",
    "resolve_exclude",
    "resolve_lookup",
    "resolve_source",
    "resolve_stream",
    "source_options",
]


F = typing.TypeVar("F", bound=typing.Callable[..., typing.Any])


def source_options(func: F) -> F:
    """
    Attach `--source`, its `--local`/`--live`/`--auto` shorthands, and
    `--cache`/`--cache-batch-size`/`--cache-on-error` to a command.

    Pair with `resolve_source` to turn the parsed flags into one `Source`,
    and `persist_kwargs` to turn the cache-related flags into keyword
    arguments for a `slb_glossary.query` function's `persist*` parameters.

    :param func: The click command callback to attach options to.
    :return: `func`, with the source-selection options attached.
    """
    func = click.option(
        "--cache/--no-cache",
        "cache_results",
        default=None,
        show_default="constants.cli_cache_by_default (True)",
        help=(
            "When a live fetch happens, save its results to the local "
            "database so the same lookup is served locally next time. "
            "Defaults to constants.cli_cache_by_default."
        ),
    )(func)
    func = click.option(
        "--cache-batch-size",
        "cache_batch_size",
        type=click.IntRange(min=1),
        default=20,
        show_default=True,
        help=(
            "Number of live results to buffer before each incremental "
            "write to the local database (only relevant with --cache). "
            "Lower values save progress more often; higher values write "
            "less often but risk losing more unsaved results if the fetch "
            "is interrupted before the next write."
        ),
    )(func)
    func = click.option(
        "--cache-on-error/--no-cache-on-error",
        "cache_on_error",
        default=True,
        show_default=True,
        help=(
            "If a live fetch fails partway through, save whatever's been "
            "buffered so far (only relevant with --cache) instead of "
            "losing it. Disable to only save complete, uninterrupted fetches."
        ),
    )(func)
    func = click.option(
        "--auto",
        "source_auto",
        is_flag=True,
        help="Shorthand for --source auto (the default): local first, live as a fallback.",
    )(func)
    func = click.option(
        "--live",
        "source_live",
        is_flag=True,
        help="Shorthand for --source live: always fetch from the live glossary.",
    )(func)
    func = click.option(
        "--local",
        "source_local",
        is_flag=True,
        help="Shorthand for --source local: only read the local database, never the network.",
    )(func)
    func = click.option(
        "--source",
        "source",
        type=click.Choice([s.value for s in Source], case_sensitive=False),
        default=None,
        help="Where to read from. Same choices as --local/--live/--auto, spelled out.",
    )(func)
    return func


def _split_exclude_values(
    ctx: click.Context, param: click.Parameter, value: tuple[str, ...]
) -> tuple[str, ...] | None:
    """
    Flatten a repeatable, comma-splittable `--exclude` option into one tuple.

    Each occurrence of `--exclude` can itself hold a comma-separated list,
    so `--exclude a,b --exclude c` and `--exclude a --exclude b --exclude c`
    both resolve the same way, to `("a", "b", "c")`.

    :param ctx: The current click context (unused; required by click's callback signature).
    :param param: The option this callback is attached to (unused; required by click's callback signature).
    :param value: The raw tuple of strings click collected, one per
        `--exclude` occurrence, before any comma-splitting.
    :return: A flattened tuple of individual entries, with surrounding
        whitespace stripped and empty entries dropped, or `None` if
        nothing was given (so it can be passed straight through as
        `slb_glossary.query`'s `exclude=None` default).
    """
    if not value:
        return None
    flattened: list[str] = []
    for raw in value:
        flattened.extend(part.strip() for part in raw.split(",") if part.strip())
    return tuple(flattened) or None


def exclude_option(func: F) -> F:
    """
    Attach a repeatable, comma-splittable `--exclude` option to a command.

    Pair with `resolve_exclude` to read the parsed value back out, ready
    to pass as `slb_glossary.query`'s `exclude` keyword argument.

    :param func: The click command callback to attach the option to.
    :return: `func`, with `--exclude` attached.
    """
    return click.option(
        "--exclude",
        "exclude",
        multiple=True,
        callback=_split_exclude_values,
        metavar="URL_OR_TERM[,URL_OR_TERM...]",
        help=(
            "A URL or term name to leave out of the results entirely, e.g. "
            "ones already stored locally. Repeatable (--exclude a --exclude "
            "b), and each occurrence may itself be a comma-separated list "
            "(--exclude a,b,c). The two forms combine freely."
        ),
    )(func)


def resolve_exclude(params: typing.Mapping[str, typing.Any]) -> tuple[str, ...] | None:
    """
    Read `exclude_option`'s parsed `--exclude` value back out.

    :param params: The command's parsed parameters, as attached by `exclude_option`.
    :return: The flattened `--exclude` entries, or `None` if none were given.
    """
    return params.get("exclude")


def resolve_source(params: typing.Mapping[str, typing.Any]) -> Source:
    """
    Resolve `--source`/`--local`/`--live`/`--auto` to one `Source`.

    :param params: The command's parsed parameters, as attached by `source_options`.
    :return: The resolved `Source`. `Source.AUTO` if nothing was given.
    :raises click.UsageError: If more than one of the flags/`--source` was given.
    """
    chosen: list[Source] = []
    if params.get("source_local"):
        chosen.append(Source.LOCAL)
    if params.get("source_live"):
        chosen.append(Source.LIVE)
    if params.get("source_auto"):
        chosen.append(Source.AUTO)
    if params.get("source"):
        chosen.append(Source(str(params["source"]).lower()))

    unique = list(dict.fromkeys(chosen))
    if len(unique) > 1:
        raise click.UsageError("Give at most one of --source/--local/--live/--auto.")
    return unique[0] if unique else Source.AUTO


def persist_kwargs(params: typing.Mapping[str, typing.Any]) -> dict[str, typing.Any]:
    """
    Turn `source_options`'s `--cache*` flags into `slb_glossary.query` `persist*` kwargs.

    :param params: The command's parsed parameters, as attached by `source_options`.
    :return: A kwargs dict with `persist`, `persist_batch_size`, and
        `persist_on_error`, ready to splat into `query.search`/`query.get_terms_on`.
    """
    cache_results = params.get("cache_results")
    return {
        "persist": constants.cli_cache_by_default if cache_results is None else cache_results,
        "persist_batch_size": params.get("cache_batch_size") or 20,
        "persist_on_error": params.get("cache_on_error", True),
    }


def database_option(func: F) -> F:
    """
    Attach `--db-path` to a command that may open the local search database.

    :param func: The click command callback to attach the option to.
    :return: `func`, with `--db-path` attached.
    """
    return click.option(
        "--db-path",
        "db_path",
        type=click.Path(dir_okay=False, path_type=pathlib.Path),
        default=None,
        help=(
            "Path to the local search database file. Defaults to the path "
            "configured in `slb-glossary config` (or the OS default - see "
            "`slb-glossary local path`)."
        ),
    )(func)


def resolve_db_path(config: Config, override: pathlib.Path | None) -> pathlib.Path:
    """
    Resolve the local database file path for this run.

    :param config: The loaded `Config` (see `slb_glossary.cli.session_options.load_named_config`).
    :param override: An explicit `--db-path` value, if given. Takes precedence over `config`.
    :return: The resolved database file path.
    """
    if override is not None:
        return pathlib.Path(override)
    return get_data_dir(config.local.data_dir) / config.local.db_filename


def local_storage_enabled(config: Config, *, db_path_override: pathlib.Path | None) -> bool:
    """
    Return whether commands should try the local database by default for this run.

    An explicit `--db-path` always counts as "yes" (the user named a
    database, so use it), regardless of `config.local.enabled`.

    :param config: The loaded `Config`.
    :param db_path_override: An explicit `--db-path` value, if given.
    :return: `True` if local-database fallback should be attempted.
    """
    return db_path_override is not None or config.local.enabled


@contextlib.asynccontextmanager
async def open_configured_db(
    config: Config, *, db_path_override: pathlib.Path | None
) -> typing.AsyncIterator[Database | None]:
    """
    Open the configured local database for the duration of an `async with` block.

    Yields `None` (opens nothing) if local storage isn't enabled for this
    run - see `local_storage_enabled`.

    :param config: The loaded `Config`.
    :param db_path_override: An explicit `--db-path` value, if given.
    :yield: An open `Database`, or `None` if local storage is disabled.
    """
    if not local_storage_enabled(config, db_path_override=db_path_override):
        yield None
        return
    async with database(resolve_db_path(config, db_path_override)) as db:
        yield db


@contextlib.asynccontextmanager
async def live_session(
    ctx: click.Context, params: typing.Mapping[str, typing.Any]
) -> typing.AsyncIterator[Session]:
    """
    Open a live `Session` for the duration of an `async with` block.

    A thin wrapper around `slb_glossary.browser.session` using this
    run's resolved `--config`/session flags - kept here so query commands
    have one obvious way to open the browser only once they've actually
    decided they need it (e.g. after a local-only pass came back empty).

    :param ctx: The current click context.
    :param params: The command's parsed parameters, including everything
        `slb_glossary.cli.session_options.session_options`/`config_option` attach.
    :yield: An open `Session`.
    """
    async with browser_session(**resolve_session_kwargs(ctx, params)) as session:
        yield session


def load_config(params: typing.Mapping[str, typing.Any]) -> Config:
    """Load the `Config` named by this run's `--config` option (see `config_option`)."""
    return load_named_config(params.get("config_path", DEFAULT_CONFIG_SENTINEL))


T = typing.TypeVar("T")


async def resolve_lookup(
    ctx: click.Context,
    params: typing.Mapping[str, typing.Any],
    db: Database | None,
    *,
    source: Source,
    local_call: typing.Callable[[Database], typing.Awaitable[QueryResult[T]]],
    live_call: typing.Callable[[Session], typing.Awaitable[QueryResult[T]]],
) -> QueryResult[T]:
    """
    Run a single-value `slb_glossary.query` lookup, opening a live session only if actually needed.

    For `Source.AUTO`, `local_call` is tried first (no browser
    launched); a live session is opened via `live_call` only if that came
    back empty (`QueryResult.value` falsy) - and even then, only if
    `constants.check_internet_before_live` doesn't find a reason not to
    (see `slb_glossary.query.resolve_source`). No internet logs a
    warning and returns local's (empty) result rather than opening a
    browser for a live fetch that would just time out.

    :param ctx: The current click context.
    :param params: The command's parsed parameters (for `live_session`).
    :param db: An already-open local `Database`, or `None` if local storage
        is disabled for this run.
    :param source: The resolved `Source` to honor (see `resolve_source`).
    :param local_call: Awaitable-returning callable given `db`, e.g.
        `lambda db: query.get_term(term, db=db, source=Source.LOCAL)`.
    :param live_call: Awaitable-returning callable given an opened
        `Session`, e.g. `lambda s: query.get_term(term, db=db,
        session=s, source=Source.LIVE, persist=cache_results)`.
    :return: The resolved `QueryResult`.
    :raises click.UsageError: If `source` is `Source.LOCAL` but `db` is `None`.
    """
    if source is Source.LOCAL:
        if db is None:
            raise click.UsageError(
                "--local needs a local database, but local storage is disabled "
                "for this run (see `slb-glossary config get local.enabled`) "
                "and no --db-path was given."
            )
        return await local_call(db)

    if source is Source.LIVE:
        async with live_session(ctx, params) as session:
            return await live_call(session)

    # Source.AUTO: a local hit never opens a browser.
    if db is not None:
        local_result = await local_call(db)
        if local_result.value:
            return local_result

        if constants.check_internet_before_live and not await has_internet_connection():
            logger.warning(
                "No internet connection detected; the local database had "
                "nothing for this query, but skipping the live fetch rather "
                "than opening a browser for it. Pass --live to force it "
                "anyway, or set SLB_GLOSSARY_CHECK_INTERNET_BEFORE_LIVE=false."
            )
            return local_result

    async with live_session(ctx, params) as session:
        return await live_call(session)


async def resolve_stream(
    ctx: click.Context,
    params: typing.Mapping[str, typing.Any],
    db: Database | None,
    *,
    source: Source,
    local_call: typing.Callable[[Database], typing.AsyncIterator[T]],
    live_call: typing.Callable[[Session], typing.AsyncIterator[T]],
) -> typing.AsyncIterator[T]:
    """
    Stream a `slb_glossary.query`-style lookup, opening a live session
    only if actually needed.

    The streaming counterpart to `resolve_lookup`, for commands built on a
    `slb_glossary.query` function that yields several results (`search`,
    `get_terms_on`, `get_terms_urls`) rather than a single `QueryResult`.

    For `Source.AUTO`, `local_call` is fully drained first (no
    browser launched); a live session is opened via `live_call` only if
    that came back with nothing at all. Even then, only if
    `constants.check_internet_before_live` doesn't find a reason not to
    (see `slb_glossary.query.resolve_source`). No internet logs a
    warning and returns local's (empty) results rather than opening a
    browser for a live fetch that would just time out.

    :param ctx: The current click context.
    :param params: The command's parsed parameters (for `live_session`).
    :param db: An already-open local `Database`, or `None` if local storage
        is disabled for this run.
    :param source: The resolved `Source` to honor (see `resolve_source`).
    :param local_call: Async-generator-returning callable given `db`, e.g.
        `lambda db: query.search(term, db=db, source=Source.LOCAL)`.
    :param live_call: Async-generator-returning callable given an opened
        `Session`, e.g. `lambda s: query.search(term, db=db,
        session=s, source=Source.LIVE, persist=cache_results)`.
    :yield: Whatever `local_call`/`live_call` yield.
    :raises click.UsageError: If `source` is `Source.LOCAL` but `db` is `None`.
    """
    if source is Source.LOCAL:
        if db is None:
            raise click.UsageError(
                "--local needs a local database, but local storage is disabled "
                "for this run (see `slb-glossary config get local.enabled`) "
                "and no --db-path was given."
            )
        async for item in local_call(db):
            yield item
        return

    if source is Source.LIVE:
        async with live_session(ctx, params) as session:
            async for item in live_call(session):
                # This intentionally holds `live_session` open across the yield: the
                # whole point is to stream results as the live fetch produces them,
                # rather than buffering the entire live fetch before yielding
                # anything. Safe because every caller consumes `resolve_stream`
                # through `output_results`, which wraps it in `contextlib.aclosing`
                # so the session is still closed promptly on an early break/cancel.
                yield item
        return

    # Source.AUTO: a local hit never opens a browser.
    if db is not None:
        local_items = [item async for item in local_call(db)]
        if local_items:
            for item in local_items:
                yield item
            return

        if constants.check_internet_before_live and not await has_internet_connection():
            logger.warning(
                "No internet connection detected; the local database had "
                "nothing for this query, but skipping the live fetch rather "
                "than opening a browser for it. Pass --live to force it "
                "anyway, or set `SLB_GLOSSARY_CHECK_INTERNET_BEFORE_LIVE=false`."
            )
            return

    async with live_session(ctx, params) as session:
        async for item in live_call(session):
            yield item
