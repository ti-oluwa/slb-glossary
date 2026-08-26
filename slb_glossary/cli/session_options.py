"""Shared, fully-configurable click options for opening a glossary `Session`."""

import logging
import typing

import click

from slb_glossary.config import Config
from slb_glossary.live.browser import BrowserType, ResourceType
from slb_glossary.logging import set_log_level
from slb_glossary.retries import BackoffType
from slb_glossary.types import Language

logger = logging.getLogger(__name__)

__all__ = [
    "config_option",
    "load_named_config",
    "log_level_option",
    "resolve_session_kwargs",
    "session_options",
]

DEFAULT_CONFIG_SENTINEL = "default"
"""`--config` value meaning "load the global config" (see `Config.load`)."""

NULL_CONFIG_SENTINEL = "none"
"""`--config` value meaning "skip config entirely, use built-in defaults"."""

SESSION_PARAM_TO_CONFIG_KEY: dict[str, str] = {
    "language": "session.language",
    "browser_type": "session.browser_type",
    "headless": "session.headless",
    "block": "session.block",
    "block_resources": "session.block_resources",
    "timeout": "session.timeout",
    "terms_per_tab": "session.terms_per_tab",
    "max_pages": "session.max_pages",
    "settle_timeout": "session.settle_timeout",
    "poll_interval": "session.poll_interval",
    "executable_path": "session.executable_path",
    "proxy": "session.proxy",
    "viewport": "session.viewport",
    "use_stealth": "session.use_stealth",
    "initialize": "session.initialize",
    "retry_attempts": "session.retry.attempts",
    "retry_base_delay": "session.retry.base_delay",
    "retry_backoff": "session.retry.backoff",
    "retry_factor": "session.retry.factor",
    "retry_max_delay": "session.retry.max_delay",
    "retry_jitter": "session.retry.jitter",
    # Both map to the same config key: whichever was actually typed on the
    # command line wins (see `resolve_session_kwargs`), and since
    # `--log-sink` is processed after `--log-to` below, a custom
    # `--log-sink` given alongside `--log-to` takes priority.
    "log_to": "session.log_sink",
    "log_sink": "session.log_sink",
}
"""Maps each `session_options` destination to the dotted `Config` key
(see `Config.get`/`Config.set`) it overrides in `resolve_session_kwargs`."""


F = typing.TypeVar("F", bound=typing.Callable[..., typing.Any])


def _parse_viewport(
    ctx: click.Context, param: click.Parameter, value: str | None
) -> dict[str, int] | None:
    """Parse a `--viewport WIDTHxHEIGHT` option value into a Playwright viewport dict."""
    if value is None:
        return None
    try:
        width, height = value.lower().split("x", 1)
        return {"width": int(width), "height": int(height)}
    except ValueError as exc:
        raise click.BadParameter(
            f"{value!r} is not a valid WIDTHxHEIGHT viewport, e.g. '1920x1080'."
        ) from exc


def _parse_proxy(
    ctx: click.Context, param: click.Parameter, value: str | None
) -> dict[str, str] | None:
    """Parse a `--proxy SERVER[,username=U][,password=P]` option value into a Playwright proxy dict."""
    if value is None:
        return None
    parts = [part.strip() for part in value.split(",") if part.strip()]
    if not parts:
        raise click.BadParameter("--proxy must include at least a server URL.")

    proxy: dict[str, str] = {"server": parts[0]}
    for part in parts[1:]:
        key, _, val = part.partition("=")
        key = key.strip().lower()
        if key not in {"username", "password", "bypass"}:
            raise click.BadParameter(
                f"Unknown proxy option {key!r}. Expected 'username', 'password' or 'bypass'."
            )
        proxy[key] = val.strip()
    return proxy


def _apply_log_level(ctx: click.Context, param: click.Parameter, value: str | None) -> str | None:
    """
    Eager `--log-level` callback: apply the override immediately if given.

    Runs as this command's own options are processed - after the root
    `slb-glossary --log-level` group option has already set its default
    (`WARNING` unless overridden there), so a per-command `--log-level`
    reliably wins for the duration of this command. Leaves the level
    untouched (falls through to whatever the root option set) when this
    command's own `--log-level` wasn't given.

    :param value: The parsed `--log-level` choice, or `None` if not given.
    :return: `value`, unchanged - so it's still available as a keyword
        argument to the command callback, the same as any other option.
    """
    if value is not None:
        set_log_level(value)
    return value


def log_level_option(func: F) -> F:
    """
    Attach a per-command `--log-level` override to a click command.

    The root `slb-glossary --log-level` group option only applies for the
    process as a whole; there's no way to ask for more (or less) verbosity
    for just one subcommand's own run without it. This adds that: given
    alongside the root option (or on its own, since the root option
    already defaults to `WARNING`), whichever `--log-level` was actually
    typed *last* on the command line wins, since both ultimately just call
    `slb_glossary.logging.set_log_level`.

    Stack this directly above a command's `def`, alongside
    `@click.command()`. Included automatically by `session_options`, so
    most commands that need it don't have to attach it separately.

    :param func: The click command callback to attach the option to.
    :return: `func`, with `--log-level` attached.
    """
    return click.option(
        "--log-level",
        "log_level",
        type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], case_sensitive=False),
        default=None,
        is_eager=True,
        expose_value=True,
        callback=_apply_log_level,
        help=(
            "Verbosity of the package's own logging output, for this "
            "command only. Overrides the root --log-level (or its "
            "default) for the duration of this run."
        ),
    )(func)


def session_options(func: F) -> F:
    """
    Attach every glossary-session configuring option to a click command.

    Stack this directly above a command's `def`, alongside `@click.command()`
    and `config_option`. The decorated callback receives each option as a
    keyword argument named after its destination (e.g. `browser_type`,
    `settle_timeout`); pass the command's `**kwargs` to
    `resolve_session_kwargs` to turn them into arguments for
    `slb_glossary.browser.open_session`/`session`.

    Also attaches `log_level_option` (`--log-level`), so every command
    using this doesn't need to attach it separately.

    :param func: The click command callback to attach options to.
    :return: `func`, with session-configuration options attached.
    """
    options: list[typing.Callable[[F], F]] = [
        click.option(
            "--language",
            "-L",
            type=click.Choice([language.value for language in Language], case_sensitive=False),
            default=Language.ENGLISH.value,
            show_default=True,
            help="Glossary language edition to search.",
        ),
        click.option(
            "--browser-type",
            "-b",
            type=click.Choice(
                [browser_type.value for browser_type in BrowserType], case_sensitive=False
            ),
            default=BrowserType.CHROMIUM.value,
            show_default=True,
            help="Playwright browser family to launch.",
        ),
        click.option(
            "--headless/--headed",
            default=True,
            show_default=True,
            help="Run the browser without (or with) a visible window.",
        ),
        click.option(
            "--block/--no-block",
            "block",
            default=True,
            show_default=True,
            help="Block images/media/fonts/stylesheets for faster page loads.",
        ),
        click.option(
            "--block-resource",
            "block_resources",
            type=click.Choice(
                [name.lower() for name in ResourceType.__members__ if name != "ALL"],
                case_sensitive=False,
            ),
            multiple=True,
            help=(
                "Request resource type to block, e.g. 'image'. Repeatable. "
                "Overrides --block/--no-block when given."
            ),
        ),
        click.option(
            "--timeout",
            type=float,
            default=60_000.0,
            show_default=True,
            help="Milliseconds to wait for page loads and element lookups.",
        ),
        click.option(
            "--terms-per-tab",
            type=int,
            default=12,
            show_default=True,
            help="Number of results the glossary site returns per results page.",
        ),
        click.option(
            "--max-pages",
            type=int,
            default=6,
            show_default=True,
            help=(
                "Maximum browser pages the session keeps open at once. Raise "
                "this if you use a higher --concurrency than the default covers."
            ),
        ),
        click.option(
            "--settle-timeout",
            type=float,
            default=8000,
            show_default=True,
            help="Milliseconds to wait for the results list to update after a search filter changes.",
        ),
        click.option(
            "--poll-interval",
            type=float,
            default=300,
            show_default=True,
            help="Milliseconds to wait between polls while waiting on --settle-timeout.",
        ),
        click.option(
            "--executable-path",
            type=click.Path(dir_okay=False),
            default=None,
            help="Path to a specific browser build to launch.",
        ),
        click.option(
            "--proxy",
            callback=_parse_proxy,
            default=None,
            metavar="SERVER[,username=U][,password=P]",
            help="Proxy server for the browser to use, e.g. 'http://myproxy:3128'.",
        ),
        click.option(
            "--viewport",
            callback=_parse_viewport,
            default=None,
            metavar="WIDTHxHEIGHT",
            help="Browser viewport size, e.g. '1920x1080'. Defaults to full-screen.",
        ),
        click.option(
            "--stealth/--no-stealth",
            "use_stealth",
            default=None,
            help=(
                "Apply Playwright stealth patches to the browser context. "
                "Leave unset (the default) to resolve this automatically "
                "based on --headless/--headed instead: stealth on when "
                "headless, off when headed as stealth patches have been "
                "observed to be counterproductive in headed mode."
            ),
        ),
        click.option(
            "--initialize/--no-initialize",
            "initialize",
            default=True,
            show_default=True,
            help=(
                "Load the glossary's topics/size as soon as the session "
                "opens. Leave this on unless you have a specific reason to "
                "defer it."
            ),
        ),
        click.option(
            "--log-to",
            "log_to",
            default=None,
            metavar="PATH|stderr|stdout",
            help=(
                "Where to route the applications's logging for this run: a file "
                "path to append log lines to (handy for bug reports), or "
                "'stderr'/'stdout' for the console. Defaults to whatever "
                "logging is already configured (console, via "
                "logging.basicConfig)."
            ),
        ),
        click.option(
            "--log-sink",
            "log_sink",
            default=None,
            metavar="module:ClassName",
            help=(
                "Import path of a custom `slb_glossary.logging.LogSink` class "
                "(or instance) to route logging to instead of a built-in "
                "sink, e.g. 'myapp.logging:BugReportSink'. Takes priority "
                "over --log-to if both are given."
            ),
        ),
        click.option(
            "--retry-attempts",
            type=int,
            default=3,
            show_default=True,
            help="Maximum attempts when retrying a flaky initial page load.",
        ),
        click.option(
            "--retry-base-delay",
            type=float,
            default=0.8,
            show_default=True,
            help="Base delay in seconds for the retry backoff calculation.",
        ),
        click.option(
            "--retry-backoff",
            type=click.Choice(
                [browser_type.value for browser_type in BackoffType], case_sensitive=False
            ),
            default=BackoffType.EXPONENTIAL.value,
            show_default=True,
            help="Strategy used to grow the delay between retry attempts.",
        ),
        click.option(
            "--retry-factor",
            type=float,
            default=2.0,
            show_default=True,
            help="Growth base for exponential backoff, or log base for logarithmic backoff.",
        ),
        click.option(
            "--retry-max-delay",
            type=float,
            default=10.0,
            show_default=True,
            help="Upper bound in seconds on any single retry delay.",
        ),
        click.option(
            "--retry-jitter/--no-retry-jitter",
            default=True,
            show_default=True,
            help="Randomize each retry delay by up to +/-50%% to avoid retry storms.",
        ),
    ]
    for option in reversed(options):
        func = option(func)
    return log_level_option(func)


def config_option(func: F) -> F:
    """
    Attach `--config` to a click command, selecting the base `Config` for the run.

    Stack this alongside `session_options`; pair the parsed parameters with
    `resolve_session_kwargs` to layer any `session_options` flag the user
    actually typed on top of the loaded config.

    :param func: The click command callback to attach the option to.
    :return: `func`, with `--config` attached.
    """
    return click.option(
        "--config",
        "config_path",
        default=DEFAULT_CONFIG_SENTINEL,
        show_default=True,
        metavar="default|none|PATH",
        help=(
            "Config file to load session defaults from: 'default' for the "
            "global config (see `slb-glossary config path`), 'none' to use "
            "built-in defaults only, or a path to a specific JSON/TOML/YAML "
            "file. Any other option given on this command overrides the "
            "config's value, for this run only."
        ),
    )(func)


def load_named_config(config_path: str) -> Config:
    """
    Resolve a `--config` option value (see `config_option`) to a `Config`.

    :param config_path: `"default"` to load the global config (see
        `Config.load`), `"none"` for built-in defaults only, or a path to a
        specific JSON/TOML/YAML config file.
    :return: The resolved `Config`.
    """
    if config_path == NULL_CONFIG_SENTINEL:
        return Config()
    if config_path == DEFAULT_CONFIG_SENTINEL:
        return Config.load()
    return Config.from_file(config_path)


def resolve_session_kwargs(
    ctx: click.Context, params: typing.Mapping[str, typing.Any]
) -> dict[str, typing.Any]:
    """
    Build `open_session`/`session` kwargs from `--config`, overridden by explicit flags.

    Loads the `Config` named by `params["config_path"]` (see
    `config_option`) as the baseline, then applies every `session_options`
    value the user actually typed on the command line on top of it - typed,
    not merely "differs from its default", per `ctx.get_parameter_source`.
    Anything the user didn't pass keeps the loaded config's value, so a
    config file sets the defaults for every run and CLI flags are one-off
    overrides for this run only; nothing is written back to the file.

    If the command has an explicit `--concurrency` (e.g. `search`, `terms`,
    `sync`/`update`) and the user typed it without also typing `--max-pages`,
    `max_pages` is bumped up to cover it (`concurrency + 1`, the extra one
    for a search page paging through tabs alongside the workers) rather than
    leaving it at whatever `--config`/the built-in default says. An
    explicit `--max-pages` always wins over this; with neither given,
    both stay at their config/default values untouched.

    :param ctx: The current click context, used to tell an explicitly
        passed flag from click's own default via `ctx.get_parameter_source`.
    :param params: The command's parsed parameters, including `config_path`
        (from `config_option`) and everything `session_options` attaches.
    :return: A keyword-argument dict ready to splat into
        `slb_glossary.browser.open_session` or `session`.
    """
    resolved = load_named_config(params.get("config_path", DEFAULT_CONFIG_SENTINEL))

    for param_name, config_key in SESSION_PARAM_TO_CONFIG_KEY.items():
        if param_name not in params:
            continue
        if ctx.get_parameter_source(param_name) != click.core.ParameterSource.COMMANDLINE:
            continue
        value = params[param_name]
        if param_name == "block_resources":
            value = list(value)
            if not value:
                continue
        resolved.set(config_key, value)

    if (
        "concurrency" in params
        and ctx.get_parameter_source("concurrency") == click.core.ParameterSource.COMMANDLINE
        and ctx.get_parameter_source("max_pages") != click.core.ParameterSource.COMMANDLINE
    ):
        needed = (params["concurrency"] or 1) + 1
        if needed > resolved.session.max_pages:
            logger.debug(
                "Bumping `session.max_pages` %d -> %d to cover --concurrency=%r",
                resolved.session.max_pages,
                needed,
                params["concurrency"],
            )
            resolved.set("session.max_pages", needed)

    kwargs = resolved.session_kwargs()
    if kwargs.get("log_sink"):
        logger.debug("Resolved session log sink: %r", kwargs["log_sink"])
    return kwargs
