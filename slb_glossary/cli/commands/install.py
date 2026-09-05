"""`slb-glossary install` - manage the browser engines patchright launches."""

import click

from slb_glossary.cli.browsers import (
    DEFAULT_INSTALL_RETRY_POLICY,
    KNOWN_BROWSERS,
    install_browsers,
    list_installed_browsers,
    remove_browsers,
)
from slb_glossary.cli.errors import cli_command
from slb_glossary.cli.tui import launch_tui
from slb_glossary.retries import RetryPolicy

__all__ = ["install"]


def _validate_browsers(
    ctx: click.Context, param: click.Parameter, value: tuple[str, ...]
) -> tuple[str, ...]:
    """Reject any browser name click accepted but patchright does not recognize."""
    unknown = sorted(set(value) - set(KNOWN_BROWSERS))
    if unknown:
        raise click.BadParameter(
            f"Unknown browser(s) {', '.join(unknown)!r}. "
            f"Supported browsers: {', '.join(KNOWN_BROWSERS)}."
        )
    return value


@click.command("install")
@click.argument("browsers", nargs=-1, callback=_validate_browsers)
@click.option(
    "--list",
    "list_only",
    is_flag=True,
    help="List installed browsers instead of installing anything.",
)
@click.option(
    "--remove",
    "remove_only",
    is_flag=True,
    help="Remove the given (or all) installed browsers instead of installing.",
)
@click.option(
    "--update",
    "update_only",
    is_flag=True,
    help="Reinstall the given (or all previously installed) browsers.",
)
@click.option(
    "--with-deps",
    is_flag=True,
    help="Also install the OS-level packages browsers need to run (Linux only).",
)
@click.option(
    "--force",
    is_flag=True,
    help="Reinstall even if the browser is already present.",
)
@click.option(
    "--only-shell",
    is_flag=True,
    help="Install Chromium's headless-shell build instead of the full browser.",
)
@click.option(
    "--timeout",
    "timeout_ms",
    type=click.IntRange(min=1),
    default=None,
    metavar="MILLISECONDS",
    help=(
        "Wait this long per browser-build download before giving up "
        "(sets `PLAYWRIGHT_DOWNLOAD_CONNECTION_TIMEOUT` for this run). "
        "Raise this - e.g. --timeout 120000 - if installs keep timing out "
        "on a slow connection; patchright's own default is around 30000 (30s)."
    ),
)
@click.option(
    "--download-host",
    "download_host",
    default=None,
    metavar="URL",
    help=(
        "Download browser builds from this host instead of the default CDN "
        "(sets PLAYWRIGHT_DOWNLOAD_HOST for this run). Useful if the "
        "default CDN is slow or unreachable from your network."
    ),
)
@click.option(
    "--retries",
    "retries",
    type=click.IntRange(min=1),
    default=DEFAULT_INSTALL_RETRY_POLICY.attempts,
    show_default=True,
    help="Retry a failed download this many times, with backoff, before giving up.",
)
@click.option(
    "--tui",
    "use_tui",
    is_flag=True,
    help="Open this command in the interactive TUI instead of running it directly.",
)
@click.pass_context
@cli_command
def install(
    ctx: click.Context,
    browsers: tuple[str, ...],
    list_only: bool,
    remove_only: bool,
    update_only: bool,
    with_deps: bool,
    force: bool,
    only_shell: bool,
    timeout_ms: int | None,
    download_host: str | None,
    retries: int,
    use_tui: bool,
) -> None:
    """
    Install, list, remove, or update the browser engines patchright launches.

    BROWSERS is zero or more of: chromium, firefox, webkit. If omitted,
    installs/updates patchright's default browser set, or lists/removes
    every installed browser.

    Browser builds are large downloads from a single CDN, so a slow or
    congested connection can time out partway through - if that's what
    you are hitting, --timeout raises how long a download gets before
    patchright gives up on it, --download-host points at a mirror/proxy if
    the default CDN itself is the problem, and --retries controls how many
    times a failed download is retried (with backoff) before this command
    gives up.

    \b
    Examples:
      slb-glossary install                     # install the default browsers
      slb-glossary install firefox webkit       # install specific browsers
      slb-glossary install --list               # show what's installed
      slb-glossary install --update chromium     # reinstall chromium
      slb-glossary install --remove firefox      # delete an installed browser
      slb-glossary install --timeout 120000      # allow 2 minutes per download
      slb-glossary install --download-host https://playwright.download.prss.microsoft.com
      slb-glossary install --retries 5           # retry a flaky download more times
    """
    if use_tui:
        launch_tui(ctx, command_path=("install",))
        return

    modes_selected = sum([list_only, remove_only, update_only])
    if modes_selected > 1:
        raise click.UsageError("--list, --remove and --update are mutually exclusive.")

    if list_only:
        installed = list_installed_browsers(browsers or None)
        if not installed:
            click.echo("No browsers installed.")
            return
        for browser in installed:
            size_mb = browser.size_bytes / (1024 * 1024)
            click.echo(
                f"{browser.family:<10} {browser.name:<24} {size_mb:8.1f} MB  {browser.path}"
            )
        return

    if remove_only:
        removed = remove_browsers(browsers or list(KNOWN_BROWSERS))
        if not removed:
            click.echo("No matching browsers were installed; nothing removed.")
            return
        for name in removed:
            click.echo(f"Removed {name}")
        return

    retry_policy = RetryPolicy.exponential(
        base_delay=DEFAULT_INSTALL_RETRY_POLICY.base_delay,
        attempts=retries,
        max_delay=DEFAULT_INSTALL_RETRY_POLICY.max_delay,
    )

    if update_only:
        targets = (
            browsers
            or [browser.family for browser in list_installed_browsers()]
            or list(KNOWN_BROWSERS)
        )
        install_browsers(
            sorted(set(targets)),
            force=True,
            with_deps=with_deps,
            only_shell=only_shell,
            timeout_ms=timeout_ms,
            download_host=download_host,
            retry=retry_policy,
        )
        click.echo(f"Updated: {', '.join(sorted(set(targets)))}")
        return

    install_browsers(
        browsers,
        with_deps=with_deps,
        force=force,
        only_shell=only_shell,
        timeout_ms=timeout_ms,
        download_host=download_host,
        retry=retry_policy,
    )
    click.echo(f"Installed: {', '.join(browsers) or 'default browsers'}")
