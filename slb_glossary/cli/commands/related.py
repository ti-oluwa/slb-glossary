"""`slb-glossary related` - list the terms related to a glossary term."""

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
from slb_glossary.query import Source

__all__ = ["related"]


class RelatedTermRecord(typing.NamedTuple):
    """A single related-term link"""

    term: str
    """The related term's display name."""

    url: str
    """The related term's glossary detail-page URL."""

    @property
    def fields(self) -> list[str]:
        """Return a list of the field names in this record."""
        return list(self._fields)

    def asdict(self) -> dict[str, typing.Any]:
        """Return a dictionary representation of this record."""
        return self._asdict()


def _validate_term(ctx: click.Context, param: click.Parameter, value: str) -> str:
    """Validate that the user provided a non-empty term."""
    if not value or not value.strip():
        raise click.BadParameter("Missing term. Provide a term name or detail-page URL.")
    return value


@click.command("related")
@click.argument("term", default="", callback=_validate_term)
@source_options
@database_option
@click.option(
    "--topic",
    "-t",
    default=None,
    help="Pick a specific stored definition for TERM/URL if it has "
    "several locally (one per topic it's filed under). Only affects a "
    "local read; a live read always returns whatever the site serves.",
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
def related(ctx: click.Context, term: str, use_tui: bool, **params: typing.Any) -> None:
    """
    List the terms TERM's definition links to under "related terms".

    Same --local/--live/--auto source selection as `define`.

    \b
    Examples:
      slb-glossary related "water saturation"
      slb-glossary related porosity --local
      slb-glossary related porosity --local --topic Petrophysics
      slb-glossary related "black oil" --save related.csv --quiet
    """
    if use_tui:
        launch_tui(ctx, command_path=("related",))
        return

    source = resolve_source(params)
    config = get_loaded_config(params)

    async def run() -> int:
        async with open_configured_db(config, db_path_override=params["db_path"]) as db:
            lookup = await resolve_lookup(
                ctx,
                params,
                db,
                source=source,
                local_call=lambda db: query.related_terms(
                    term,
                    db=db,
                    source=Source.LOCAL,
                    language=params["language"],
                    topic=params["topic"],
                ),
                live_call=lambda session: query.related_terms(
                    term,
                    db=db,
                    session=session,
                    source=Source.LIVE,
                    persist=params["cache_results"],
                    language=params["language"],
                ),
            )

        if not params["quiet"] and lookup.value:
            click.secho(f"(source: {lookup.source.value})", fg="bright_black", err=True)

        async def _records() -> typing.AsyncIterator[RelatedTermRecord]:
            for related_term in lookup.value:
                yield RelatedTermRecord(term=related_term.term, url=related_term.url)

        return await output_results(
            _records(),
            title=f"Terms related to {term!r}",
            save_paths=params["save_paths"],
            format=params["format"],
            quiet=params["quiet"],
            json_output=params["json_output"],
        )

    count = run_async(run())
    if not params["quiet"] and count == 0:
        click.echo(f"No related terms found for {term!r}.", err=True)
