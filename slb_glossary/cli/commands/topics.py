"""`slb-glossary topics` - list and refresh the glossary's topic (discipline) list."""

import typing

import click

from slb_glossary import query
from slb_glossary.cli.errors import cli_command
from slb_glossary.cli.output_options import output_options, output_results
from slb_glossary.cli.runtime import run_async
from slb_glossary.cli.session_options import config_option, resolve_session_kwargs, session_options
from slb_glossary.cli.source_options import (
    database_option,
    get_loaded_config,
    open_configured_db,
    resolve_source,
    resolve_stream,
    source_options,
)
from slb_glossary.cli.tui import launch_tui
from slb_glossary.live.browser import session as browser_session
from slb_glossary.live.topics import refresh_topics
from slb_glossary.query import Source

__all__ = ["topics"]


class TopicRecord(typing.NamedTuple):
    """A single glossary topic (discipline) and its term count."""

    topic: str
    """The topic's display name."""

    term_count: int
    """Number of glossary terms filed under this topic."""

    @property
    def fields(self) -> list[str]:
        """Return a list of the field names in this record."""
        return list(self._fields)

    def asdict(self) -> dict[str, typing.Any]:
        """Return a dictionary representation of this record."""
        return self._asdict()


@click.group("topics")
def topics() -> None:
    """List or refresh the glossary's topic (discipline) list."""


async def iter_topic_records(
    topic_counts: typing.Mapping[str, int],
) -> typing.AsyncIterator[TopicRecord]:
    """Yield a `TopicRecord` for each entry in `topic_counts`, sorted by name."""
    for name, count in sorted(topic_counts.items()):
        yield TopicRecord(topic=name, term_count=count)


@topics.command("list")
@source_options
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
def list_topics(ctx: click.Context, use_tui: bool, **params: typing.Any) -> None:
    """
    List every topic (discipline) the glossary is organized under, with term counts.

    Reads from the local database, the live glossary, or both, depending on
    --local/--live/--auto (--auto is the default): with a
    local database available, its topics are listed first (only the topics
    actually cached so far) and the live site is only visited if the local
    database has none.

    \b
    Examples:
      slb-glossary topics list
      slb-glossary topics list --save topics.csv --quiet
      slb-glossary topics list --local
    """
    if use_tui:
        launch_tui(ctx, command_path=("topics", "list"))
        return

    source = resolve_source(params)
    config = get_loaded_config(params)

    async def _local_records(db: typing.Any) -> typing.AsyncIterator[TopicRecord]:
        topics = await query.get_topics(db=db, source=Source.LOCAL)
        async for record in iter_topic_records(topics):
            yield record

    async def _live_records(session: typing.Any) -> typing.AsyncIterator[TopicRecord]:
        topics = await query.get_topics(session=session, source=Source.LIVE)
        async for record in iter_topic_records(topics):
            yield record

    async def run() -> int:
        async with open_configured_db(config, db_path_override=params["db_path"]) as db:
            records = resolve_stream(
                ctx,
                params,
                db,
                source=source,
                local_call=_local_records,
                live_call=_live_records,
            )
            return await output_results(
                records,
                title="Topics",
                save_paths=params["save_paths"],
                format=params["format"],
                quiet=params["quiet"],
                json_output=params["json_output"],
                show_url=False,
                show_topic=False,
                show_grammar=False,
                show_image=False,
                show_related=False,
            )

    count = run_async(run())
    if not params["quiet"] and count == 0:
        click.echo("No topics found.", err=True)


@topics.command("refresh")
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
def refresh(ctx: click.Context, use_tui: bool, **params: typing.Any) -> None:
    """
    Reload the glossary's topic list and term counts directly from the site.

    Unlike `topics list`, this reloads the search page instead of trusting
    the topic list captured when the session opened - use it if the
    glossary's topics may have changed since.

    \b
    Examples:
      slb-glossary topics refresh
    """
    if use_tui:
        launch_tui(ctx, command_path=("topics", "refresh"))
        return

    async def run() -> int:
        async with browser_session(**resolve_session_kwargs(ctx, params)) as session:
            session = await refresh_topics(session)
            return await output_results(
                iter_topic_records(session.topics),
                title="Topics (refreshed)",
                save_paths=params["save_paths"],
                format=params["format"],
                quiet=params["quiet"],
                json_output=params["json_output"],
                show_url=False,
                show_topic=False,
                show_grammar=False,
                show_image=False,
                show_related=False,
            )

    count = run_async(run())
    if not params["quiet"] and count == 0:
        click.echo("No topics found.", err=True)
