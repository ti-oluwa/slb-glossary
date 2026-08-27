"""`slb-glossary update` - refresh the local search database from the live glossary."""

import typing

import click

from slb_glossary import local
from slb_glossary.cli.errors import cli_command
from slb_glossary.cli.runner import run_async
from slb_glossary.cli.session_options import config_option, resolve_session_kwargs, session_options
from slb_glossary.cli.source_options import database_option, get_loaded_config, resolve_db_path
from slb_glossary.cli.sync_options import (
    print_sync_summary,
    run_configured_sync,
    sync_filter_options,
    validate_sync_filters,
)
from slb_glossary.cli.tui import launch_tui
from slb_glossary.live.browser import session as browser_session
from slb_glossary.local.sync import SyncSummary

__all__ = ["update"]


@click.command("update")
@sync_filter_options
@database_option
@config_option
@session_options
@click.option(
    "--tui",
    "use_tui",
    is_flag=True,
    help="Open this command in the interactive TUI instead of running it directly.",
)
@click.pass_context
@cli_command
def update(ctx: click.Context, use_tui: bool, **params: typing.Any) -> None:
    """
    Refresh the local search database from the live glossary.

    Unlike `slb-glossary sync`, this assumes a browser engine is already
    installed and goes straight to fetching - use `sync` first if you're
    not sure one is.

    --topic, --query and --start-letter can be combined to narrow a fetch
    (e.g. --topic Drilling --start-letter p); --all fetches the *entire*
    glossary on its own and should be used sparingly - see
    `slb_glossary.local`'s docstring on responsible use. Without any
    filter, only the topic list/counts are refreshed (cheap, no term
    pages fetched).

    \b
    Examples:
      slb-glossary update --topic Drilling
      slb-glossary update --start-letter p --limit 50
      slb-glossary update --query "drilling fluid"
      slb-glossary update --all --yes
    """
    if use_tui:
        launch_tui(ctx, command_path=("update",))
        return

    validate_sync_filters(params)
    config = get_loaded_config(params)
    db_path = resolve_db_path(config, params["db_path"])

    async def run() -> SyncSummary:
        async with local.database(db_path) as db:
            async with browser_session(**resolve_session_kwargs(ctx, params)) as session:
                return await run_configured_sync(db, session, params)

    summary = run_async(run())
    print_sync_summary(summary)
