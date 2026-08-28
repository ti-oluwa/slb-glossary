"""`slb-glossary define` - look up a single glossary term's definition."""

import sys
import typing

import click

from slb_glossary import query
from slb_glossary.cli.errors import cli_command
from slb_glossary.cli.output_options import output_options, output_results
from slb_glossary.cli.runner import run_async
from slb_glossary.cli.session_options import config_option, session_options
from slb_glossary.cli.source_options import (
    database_option,
    get_loaded_config,
    open_configured_db,
    resolve_lookup,
    resolve_source,
    source_options,
)
from slb_glossary.cli.tui import launch_tui
from slb_glossary.query import QueryResult, SimilarResult, Source
from slb_glossary.types import SearchResult

__all__ = ["define"]


def _validate_term(ctx: click.Context, param: click.Parameter, value: str) -> str:
    """Validate that the user provided a non-empty term."""
    if not value or not value.strip():
        raise click.BadParameter("Missing term. Provide a term name or detail-page URL.")
    return value


@click.command("define")
@click.argument("term", default="", callback=_validate_term)
@source_options
@database_option
@click.option(
    "--topic",
    "-t",
    default=None,
    help="Pick a specific stored definition when TERM/URL has several "
    "locally (one per topic it's filed under). Only affects a local "
    "read; a live read always returns whatever the site itself serves.",
)
@click.option(
    "--show-related/--hide-related",
    "show_related",
    default=True,
    show_default=True,
    help="Show/hide the related-terms column.",
)
@click.option(
    "--show-image/--hide-image",
    "show_image",
    default=False,
    show_default=True,
    help="Show/hide the illustrative image URL column.",
)
@click.option(
    "--suggest/--no-suggest",
    "suggest_similar",
    default=True,
    show_default=True,
    help=(
        "When a lookup finds no exact match for TERM, offer up to "
        "--max-similar similarly-named alternatives instead of just "
        "reporting nothing found. On an interactive terminal, lets you "
        "pick one to view its definition."
    ),
)
@click.option(
    "--similar-pool-size",
    type=click.IntRange(min=1),
    default=None,
    metavar="N",
    help="Candidates pulled while looking for the exact match, and to draw "
    "--suggest alternatives from. Defaults to constants.similar_terms_pool_size.",
)
@click.option(
    "--max-similar",
    "max_similar_terms",
    type=click.IntRange(min=1),
    default=None,
    metavar="N",
    help="Max --suggest alternatives offered. Defaults to constants.max_similar_terms.",
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
def define(ctx: click.Context, term: str, use_tui: bool, **params: typing.Any) -> None:
    """
    Look up TERM (an exact term name, or a term detail-page URL) and print its definition.

    Reads from the local database, the live glossary, or both, depending on
    --local/--live/--auto (--auto is the default): with a
    local database available, the local copy is used first and the live
    site is only visited if TERM isn't cached yet.

    \b
    Examples:
      slb-glossary define "black oil"
      slb-glossary define porosity --local
      slb-glossary define porosity --local --topic Petrophysics
      slb-glossary define "water saturation" --live --cache
      slb-glossary define "black oil" --save black_oil.json
    """
    if use_tui:
        launch_tui(ctx, command_path=("define",))
        return

    source = resolve_source(params)
    config = get_loaded_config(params)

    suggest_similar = params["suggest_similar"]

    async def run() -> tuple[int, tuple[QueryResult[SearchResult], ...]]:
        async with open_configured_db(config, db_path_override=params["db_path"]) as db:
            lookup = await resolve_lookup(
                ctx,
                params,
                db,
                source=source,
                local_call=lambda db: query.get_term(
                    term,
                    db=db,
                    source=Source.LOCAL,
                    language=params["language"],
                    topic=params["topic"],
                    with_similar=suggest_similar,
                    similar_pool_size=params["similar_pool_size"],
                    max_similar_terms=params["max_similar_terms"],
                ),
                live_call=lambda session: query.get_term(
                    term,
                    db=db,
                    session=session,
                    source=Source.LIVE,
                    persist=params["cache_results"],
                    language=params["language"],
                    topic=params["topic"],
                    with_similar=suggest_similar,
                    similar_pool_size=params["similar_pool_size"],
                    max_similar_terms=params["max_similar_terms"],
                ),
            )

        if suggest_similar:
            assert isinstance(lookup.value, SimilarResult)
            exact_lookup = lookup.value.exact
            similar = lookup.value.similar
        else:
            assert not isinstance(lookup.value, SimilarResult)
            exact_lookup = lookup
            similar = ()

        if exact_lookup is None or exact_lookup.value is None:
            return 0, similar

        exact = exact_lookup.value
        if not params["quiet"]:
            click.secho(f"(source: {lookup.source.value})", fg="bright_black", err=True)

        async def _one() -> typing.AsyncIterator[typing.Any]:  # ruff: ignore[unused-async]
            yield exact

        count = await output_results(
            _one(),
            title=f"Definition: {term}",
            save_paths=params["save_paths"],
            format=params["format"],
            quiet=params["quiet"],
            json_output=params["json_output"],
            show_related=params["show_related"],
            show_image=params["show_image"],
        )
        return count, similar

    count, similar = run_async(run())
    if count > 0 or params["quiet"]:
        return

    if similar and sys.stdin.isatty() and not params["json_output"]:
        _show_similar_prompt(term, similar)
    else:
        click.echo(f"{term!r} was not found.", err=True)
        if similar:
            click.echo("Did you mean:", err=True)
            for candidate in similar:
                click.echo(f"  - {candidate.value.term}", err=True)


def _show_similar_prompt(
    term: str, similar: typing.Sequence["query.QueryResult[SearchResult]"]
) -> None:
    """
    Print `similar` as a "Did you mean" list and let the user interactively view one.

    Loops: after printing a picked term's definition, the user can pick
    another right away, or quit with `q`/`x`. Only called on an interactive terminal.

    :param term: The originally looked-up term, only used for messaging.
    :param similar: Similarly-named live results to offer as alternatives,
        each already wrapped in its own `QueryResult` (with its own
        score), best match first.
    """
    click.echo(f'No exact definition found for "{term}".')
    click.echo("Did you mean:")
    for index, candidate in enumerate(similar, start=1):
        click.echo(f"  {index}. {candidate.value.term}")
    click.echo(
        f"\nUse `slb-glossary search {term}` to search for related terms. "
        f"Or enter a number (1-{len(similar)}) to view that term's definition (q to quit): ",
        nl=False,
    )

    while True:
        choice = click.getchar().strip().lower()
        click.echo(choice)
        if choice in ("q", "x", ""):
            return

        if not choice.isdigit() or not (1 <= int(choice) <= len(similar)):
            click.echo(f"Enter a number between 1 and {len(similar)}, or q to quit: ", nl=False)
            continue

        picked = similar[int(choice) - 1].value
        click.echo()
        click.secho(picked.term, bold=True)
        click.echo(picked.definition or "(no definition available)")
        click.echo()
        click.echo("Enter another number to view, or q to quit: ", nl=False)
