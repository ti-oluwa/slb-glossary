"""`slb-glossary urls` - list term detail-page URLs, and fetch results from one directly."""

import typing

import click

from slb_glossary import query
from slb_glossary.cli.errors import cli_command
from slb_glossary.cli.output_options import output_options, output_results
from slb_glossary.cli.runtime import run_async
from slb_glossary.cli.session_options import config_option, resolve_session_kwargs, session_options
from slb_glossary.cli.source_options import (
    database_option,
    exclude_option,
    get_loaded_config,
    open_configured_db,
    resolve_exclude,
    resolve_source,
    resolve_stream,
    source_options,
)
from slb_glossary.cli.tui import launch_tui
from slb_glossary.live import get_results_from_url
from slb_glossary.live.browser import session as browser_session
from slb_glossary.query import Source

__all__ = ["urls"]


class UrlRecord(typing.NamedTuple):
    """A single glossary term detail-page URL."""

    url: str
    """The term detail-page URL."""

    @property
    def fields(self) -> list[str]:
        """Return a list of the field names in this record."""
        return list(self._fields)

    def asdict(self) -> dict[str, typing.Any]:
        """Return a dictionary representation of this record."""
        return self._asdict()


@click.group("urls")
def urls() -> None:
    """List glossary term URLs, or fetch results directly from one."""


@urls.command("list")
@click.option(
    "--query",
    "-Q",
    default=None,
    help="A free-text search query to filter URLs by.",
)
@click.option(
    "--topic",
    "-t",
    default=None,
    help="Restrict URLs to this topic, or several comma-separated topics.",
)
@click.option(
    "--start-letter",
    "-a",
    default=None,
    help="Restrict URLs to terms starting with this letter.",
)
@click.option(
    "--limit",
    "-n",
    type=int,
    default=0,
    help="Maximum number of URLs to fetch. Defaults to every matching URL.",
)
@click.option(
    "--fuzzy",
    is_flag=True,
    help="Tolerate minor misspellings/partial names in --topic when reading "
    "the local database, matched against topics actually stored locally, "
    "instead of requiring an exact (case-insensitive) match.",
)
@source_options
@exclude_option
@database_option
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
def list_urls(ctx: click.Context, use_tui: bool, **params: typing.Any) -> None:
    """
    List glossary term detail-page URLs matching --query/--topic/--start-letter.

    At least one of --query, --topic or --start-letter must be given.

    Reads from the local database, the live glossary, or both, depending on
    --local/--live/--auto (--auto is the default): with a
    local database available, cached URLs are used first and the live site
    is only visited if the local database has nothing matching the filters.

    Use --exclude to leave specific URLs or term names out of the
    results entirely, e.g. ones you already have. Repeatable, and each
    occurrence may itself be a comma-separated list.

    \b
    Examples:
      slb-glossary urls list --topic Geophysics
      slb-glossary urls list --query porosity --limit 5
      slb-glossary urls list --start-letter a --save urls.txt
      slb-glossary urls list --topic Geophysics --local --fuzzy
      slb-glossary urls list --topic Geophysics --exclude porosity,permeability
    """
    if use_tui:
        launch_tui(ctx, command_path=("urls", "list"))
        return

    if not any([params["query"], params["topic"], params["start_letter"]]):
        raise click.UsageError("Give at least one of --query, --topic or --start-letter.")

    limit = params["limit"] or None
    exclude = resolve_exclude(params)
    source = resolve_source(params)
    config = get_loaded_config(params)

    title_bits = []
    if params["query"]:
        title_bits.append(f"query={params['query']!r}")
    if params["topic"]:
        title_bits.append(f"topic={params['topic']!r}")
    if params["start_letter"]:
        title_bits.append(f"start_letter={params['start_letter']!r}")
    title = f"Term URLs ({', '.join(title_bits)})" if title_bits else "Term URLs"

    async def run() -> int:
        async with open_configured_db(config, db_path_override=params["db_path"]) as db:
            url_iter = resolve_stream(
                ctx,
                params,
                db,
                source=source,
                local_call=lambda db: query.get_terms_urls(
                    db=db,
                    source=Source.LOCAL,
                    query=params["query"],
                    topic=params["topic"],
                    start_letter=params["start_letter"],
                    limit=limit,
                    fuzzy=params["fuzzy"],
                    exclude=exclude,
                ),
                live_call=lambda session: query.get_terms_urls(
                    db=db,
                    session=session,
                    source=Source.LIVE,
                    query=params["query"],
                    topic=params["topic"],
                    start_letter=params["start_letter"],
                    limit=limit,
                    exclude=exclude,
                ),
            )
            records = (UrlRecord(url=url) async for url in url_iter)
            return await output_results(
                records,
                title=title,
                save_paths=params["save_paths"],
                format=params["format"],
                quiet=params["quiet"],
                json_output=params["json_output"],
                show_url=False,
                show_topic=False,
                show_grammar=False,
            )

    count = run_async(run())
    if not params["quiet"] and count == 0:
        click.echo("No URLs found.", err=True)


def _validate_url(
    ctx: click.Context, param: click.Parameter, value: tuple[str, ...]
) -> tuple[str, ...]:
    """Validate that the user provided a non-empty URL argument."""
    if not value or not any(value):
        raise click.BadParameter("Missing URL argument.")
    return value


@urls.command("fetch")
@click.argument("url", default="", callback=_validate_url)
@click.option(
    "--topic",
    "-t",
    default=None,
    help="Resolve this topic (or comma-separated topics) against the page's definitions.",
)
@click.option(
    "--url-column/--no-url-column",
    "show_url",
    default=True,
    show_default=True,
    help="Show/hide the source URL column.",
)
@click.option(
    "--show-topic/--hide-topic",
    "show_topic",
    default=True,
    show_default=True,
    help="Show/hide the topic column.",
)
@click.option(
    "--show-grammar/--hide-grammar",
    "show_grammar",
    default=True,
    show_default=True,
    help="Show/hide the grammatical label column.",
)
@click.option(
    "--show-image/--hide-image",
    "show_image",
    default=False,
    show_default=True,
    help="Show/hide the illustrative image URL column.",
)
@click.option(
    "--show-related/--hide-related",
    "show_related",
    default=False,
    show_default=True,
    help="Show/hide the related-terms column.",
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
def fetch_url(ctx: click.Context, url: str, use_tui: bool, **params: typing.Any) -> None:
    """
    Fetch every definition found on a single glossary term detail page URL.

    \b
    Examples:
      slb-glossary urls fetch "https://glossary.slb.com/en/terms/p/porosity"
      slb-glossary urls fetch "$URL" --save porosity.json
    """
    if use_tui:
        launch_tui(ctx, command_path=("urls", "fetch"))
        return

    async def run() -> int:
        async with browser_session(**resolve_session_kwargs(ctx, params)) as session:
            results = get_results_from_url(session, url, topic=params["topic"])
            return await output_results(
                results,
                title=f"Definitions from {url}",
                save_paths=params["save_paths"],
                format=params["format"],
                quiet=params["quiet"],
                json_output=params["json_output"],
                show_url=params["show_url"],
                show_topic=params["show_topic"],
                show_grammar=params["show_grammar"],
                show_image=params["show_image"],
                show_related=params["show_related"],
            )

    count = run_async(run())
    if not params["quiet"] and count == 0:
        click.echo("No definitions found at that URL.", err=True)
