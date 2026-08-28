"""`slb-glossary compare` - look up several glossary terms side by side."""

import asyncio
import typing

import click

from slb_glossary import query
from slb_glossary.cli.errors import cli_command
from slb_glossary.cli.output_options import output_options, output_results
from slb_glossary.cli.runner import run_async
from slb_glossary.cli.session_options import config_option, session_options
from slb_glossary.cli.source_options import (
    database_option,
    live_session,
    load_config,
    open_configured_db,
    resolve_source,
    source_options,
)
from slb_glossary.cli.tui import launch_tui
from slb_glossary.query import QueryResult, Source
from slb_glossary.types import SearchResult

__all__ = ["compare"]


def _validate_terms(
    ctx: click.Context, param: click.Parameter, value: tuple[str, ...]
) -> tuple[str, ...]:
    """Validate that the user provided at least two terms to compare."""
    if len(value) < 2:
        raise click.BadParameter("Give at least two terms to compare.")
    return value


async def _gather(
    calls: typing.Sequence[
        typing.Callable[[], typing.Awaitable[QueryResult[SearchResult | None]]]
    ],
    concurrency: int,
) -> list[QueryResult[SearchResult | None]]:
    """Run `calls` concurrently, at most `concurrency` at once, preserving their order."""
    semaphore = asyncio.Semaphore(max(concurrency, 1))

    async def _bounded(
        call: typing.Callable[[], typing.Awaitable[QueryResult[SearchResult | None]]],
    ) -> QueryResult[SearchResult | None]:
        async with semaphore:
            return await call()

    return list(await asyncio.gather(*(_bounded(call) for call in calls)))


@click.command("compare")
@click.argument("terms", nargs=-1, callback=_validate_terms)
@source_options
@database_option
@click.option(
    "--topic",
    "-t",
    default=None,
    help="Pick a specific stored definition for any term/URL that has "
    "several locally (one per topic it's filed under). Only affects a "
    "local read; a live read always returns whatever the site serves.",
)
@click.option(
    "--show-related/--hide-related",
    "show_related",
    default=False,
    show_default=True,
    help="Show/hide the related-terms column.",
)
@click.option(
    "--concurrency",
    type=click.IntRange(min=1),
    default=3,
    show_default=True,
    help="Number of terms to look up concurrently, instead of one at a "
    "time. Higher values may be faster, but use with discretion so as "
    "not to overload the glossary server on a live/auto lookup.",
)
@config_option
@session_options
@output_options
@click.option(
    "--tui",
    "use_tui",
    is_flag=True,
    help="Open this command in the interactive TUI instead of running it directly.",
)
@click.pass_context
@cli_command
def compare(
    ctx: click.Context, terms: tuple[str, ...], use_tui: bool, **params: typing.Any
) -> None:
    """
    Look up TERMS (two or more) and print their definitions side by side for comparison.

    Same --local/--live/--auto source selection as `define`. Terms
    not found by the resolved source(s) are skipped, with a note printed
    to stderr. Terms are looked up concurrently (see --concurrency),
    rather than one at a time.

    \b
    Examples:
      slb-glossary compare "water flooding" "gas flooding"
      slb-glossary compare porosity permeability --local
      slb-glossary compare porosity permeability --local --topic Petrophysics
      slb-glossary compare "black oil" "heavy oil" --save comparison.csv
      slb-glossary compare a b c d e --concurrency 5
    """
    if use_tui:
        launch_tui(ctx, command_path=("compare",))
        return

    source = resolve_source(params)
    config = load_config(params)
    title = f"Comparing: {', '.join(terms)}"
    concurrency = params["concurrency"]
    language = params["language"]
    topic = params["topic"]
    sources_seen: set[str] = set()

    def _local_call(
        db: typing.Any, term: str
    ) -> typing.Callable[[], typing.Awaitable[QueryResult[SearchResult | None]]]:
        return lambda: query.get_term(
            term, db=db, source=Source.LOCAL, language=language, topic=topic
        )

    def _live_call(
        db: typing.Any, session: typing.Any, term: str
    ) -> typing.Callable[[], typing.Awaitable[QueryResult[SearchResult | None]]]:
        return lambda: query.get_term(
            term,
            db=db,
            session=session,
            source=Source.LIVE,
            persist=params["cache_results"],
            language=language,
        )

    async def run() -> int:
        async with open_configured_db(config, db_path_override=params["db_path"]) as db:
            if source is Source.LOCAL:
                assert db is not None
                results = await _gather([_local_call(db, term) for term in terms], concurrency)
            elif source is Source.LIVE:
                async with live_session(ctx, params) as session:
                    results = await _gather(
                        [_live_call(db, session, term) for term in terms], concurrency
                    )
            else:
                # Source.AUTO: try every term against the local database
                # first, concurrently, with no browser involved at all. A
                # live session is opened, once, only if at least one term
                # came back empty. Even then only the still-missing terms
                # are looked up against it, concurrently, rather than
                # opening a fresh session per term the way running each
                # term through a single-lookup helper independently would.
                results = (
                    await _gather([_local_call(db, term) for term in terms], concurrency)
                    if db is not None
                    else [None] * len(terms)
                )
                missing = [
                    (index, term)
                    for index, (term, result) in enumerate(zip(terms, results, strict=True))
                    if result is None or result.value is None
                ]
                if missing:
                    async with live_session(ctx, params) as session:
                        live_lookups = await _gather(
                            [_live_call(db, session, term) for _, term in missing], concurrency
                        )
                    for (index, _), result in zip(missing, live_lookups, strict=True):
                        results[index] = result  # type: ignore[arg-type]

            async def stream() -> typing.AsyncIterator[SearchResult]:
                for term, result in zip(terms, results, strict=True):
                    if result is not None and result.value is not None:
                        sources_seen.add(result.source.value)
                        yield result.value
                    elif not params["quiet"]:
                        click.secho(f"Not found: {term!r}", fg="yellow", err=True)

            return await output_results(
                stream(),
                title=title,
                save_paths=params["save_paths"],
                format=params["format"],
                quiet=params["quiet"],
                json_output=params["json_output"],
                show_related=params["show_related"],
            )

    count = run_async(run())
    if not params["quiet"] and sources_seen:
        click.secho(f"(source: {', '.join(sorted(sources_seen))})", fg="bright_black", err=True)
    if not params["quiet"] and count == 0:
        click.echo("None of the given terms were found.", err=True)
