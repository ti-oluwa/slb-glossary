"""`slb-glossary sync` - check the browser engine is installed, then refresh the local database."""

import typing

import click

from slb_glossary import local
from slb_glossary.cli.browsers import (
    BrowserInstallError,
    install_browsers,
    list_installed_browsers,
)
from slb_glossary.cli.errors import cli_command
from slb_glossary.cli.runner import run_async
from slb_glossary.cli.session_options import config_option, resolve_session_kwargs, session_options
from slb_glossary.cli.source_options import database_option, load_config, resolve_db_path
from slb_glossary.cli.sync_options import (
    print_sync_summary,
    run_configured_sync,
    sync_filter_options,
    validate_sync_filters,
)
from slb_glossary.cli.tui import launch_tui
from slb_glossary.live.browser import session as browser_session
from slb_glossary.local.sync import SyncSummary

__all__ = ["sync"]


def _ensure_browser(browser_type: str, *, auto_install: bool, with_deps: bool) -> bool:
    """
    Make sure a `browser_type` build is installed, installing it if asked to.

    :param browser_type: The Playwright browser family `slb-glossary` would launch.
    :param auto_install: If `True` and no build is found, install one now.
    :param with_deps: Passed to `install_browsers` if `auto_install` installs anything.
    :return: `True` if a build is (now) installed and it's safe to proceed.
    """
    installed = list_installed_browsers([browser_type])
    if installed:
        total_mb = sum(browser.size_bytes for browser in installed) / (1024 * 1024)
        click.echo(f"{browser_type} is installed ({len(installed)} build(s), {total_mb:.0f} MB).")
        return True

    click.secho(f"No {browser_type} browser build found.", fg="yellow")
    if not auto_install:
        click.echo(
            f"Run `slb-glossary install {browser_type}` first, or re-run "
            f"`slb-glossary sync` with --install to do it automatically."
        )
        return False

    click.echo(f"Installing {browser_type} (--install was given)...")
    try:
        install_browsers([browser_type], with_deps=with_deps)
    except BrowserInstallError as exc:
        raise click.ClickException(str(exc)) from exc
    click.secho(f"Installed {browser_type}.", fg="green")
    return True


@click.command("sync")
@click.option(
    "--install/--no-install",
    "auto_install",
    default=False,
    show_default=True,
    help="Automatically install the browser engine if it's missing, instead of just telling you how.",
)
@click.option(
    "--with-deps",
    is_flag=True,
    help="With --install, also install the OS-level packages the browser needs (Linux only).",
)
@click.option(
    "--check-only",
    is_flag=True,
    help="Only check/report the browser installation state; do not touch the local database.",
)
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
def sync(ctx: click.Context, use_tui: bool, **params: typing.Any) -> None:
    """
    Make sure the browser engine slb-glossary needs is installed, then update the local database.

    Checks whether the browser family --browser-type would launch (default
    chromium) is installed first, since a missing browser is the most
    common reason a fresh install's first search fails. If it's missing,
    this reports that and tells you what to run - or installs it itself
    with --install. Once a browser is available (or already was), this
    behaves exactly like `slb-glossary update` with the same
    --topic/--query/--start-letter/--all filters.

    \b
    Examples:
      slb-glossary sync                          # check + light topic refresh
      slb-glossary sync --check-only              # only report browser state
      slb-glossary sync --install                 # install the browser if missing, then sync
      slb-glossary sync --topic Drilling --install
    """
    if use_tui:
        launch_tui(ctx, command_path=("sync",))
        return

    browser_type = params["browser_type"]
    browser_ready = _ensure_browser(
        browser_type, auto_install=params["auto_install"], with_deps=params["with_deps"]
    )

    if params["check_only"]:
        return
    if not browser_ready:
        raise SystemExit(1)

    validate_sync_filters(params)
    config = load_config(params)
    db_path = resolve_db_path(config, params["db_path"])

    async def run() -> SyncSummary:
        async with local.database(db_path) as db:
            async with browser_session(**resolve_session_kwargs(ctx, params)) as session:
                return await run_configured_sync(db, session, params)

    summary = run_async(run())
    print_sync_summary(summary)
