"""`slb-glossary mcp` - run the SLB Energy Glossary as an MCP server for LLM agents."""

import pathlib
import typing

import click

from slb_glossary.cli.errors import cli_command
from slb_glossary.cli.runner import run_async
from slb_glossary.config import Config
from slb_glossary.query import Source
from slb_glossary.types import Language

__all__ = ["mcp"]

_APP_PATH_IGNORED_OPTIONS = (
    "config_path",
    "tools",
    "source",
    "no_local",
    "no_live",
    "allow_write",
    "timeout",
    "auth_tokens",
    "auth_provider_path",
    "require_scopes",
    "limit",
    "rate_limit_window",
    "rate_limit_algorithm",
    "language",
)
"""
Option names that only make sense when *building* an `MCPConfig` from
flags. Those options are meaningless (and silently ignored, if we let them through) once
`APP_PATH` hands over an already-built app.
"""


def _reject_flags_with_app_path(ctx: click.Context) -> None:
    """Raise a `click.UsageError` if `APP_PATH` and any config-building flag were both given."""
    explicit = [
        name
        for name in _APP_PATH_IGNORED_OPTIONS
        if ctx.get_parameter_source(name) is click.core.ParameterSource.COMMANDLINE
    ]
    if explicit:
        flags = ", ".join(f"--{name.replace('_', '-')}" for name in explicit)
        raise click.UsageError(
            f"`APP_PATH` loads an already-built app, so {flags} would be ignored. Configure "
            f"that app in Python instead, or drop `APP_PATH` and let this command build the "
            f"`MCPConfig` from flags."
        )


@click.group("mcp")
def mcp() -> None:
    """Run the SLB Energy Glossary API as an MCP server for LLM agents."""


@mcp.command("serve")
@click.argument("app_path", required=False, metavar="[APP_PATH]")
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=pathlib.Path),
    default=None,
    help="An `slb_glossary` Config file (JSON/TOML/YAML) to source session/local settings from.",
)
@click.option(
    "--transport",
    type=click.Choice(["stdio", "http", "sse"], case_sensitive=False),
    default="stdio",
    show_default=True,
    help="MCP transport to serve over.",
)
@click.option("--host", default="127.0.0.1", show_default=True, help="Bind host (http/sse only).")
@click.option(
    "--port", type=int, default=8000, show_default=True, help="Bind port (http/sse only)."
)
@click.option(
    "--tools",
    default="read_only",
    show_default=True,
    help="Comma-separated tool set to build, e.g. 'search,get_term' or 'all'/'read_only'.",
)
@click.option(
    "--source",
    type=click.Choice([s.value for s in Source]),
    multiple=True,
    default=(),
    help="Restrict which source(s) tools may use. May be given more than once. Default: all enabled.",
)
@click.option("--no-local", is_flag=True, help="Disable local database access entirely.")
@click.option("--no-live", is_flag=True, help="Disable live glossary access entirely.")
@click.option(
    "--allow-write",
    is_flag=True,
    help="Allow local-database writes (glossary_sync, and read tools' `persist` argument).",
)
@click.option(
    "--timeout",
    type=float,
    default=60.0,
    show_default=True,
    help="Global per-tool-call timeout in seconds. 0 disables it.",
)
@click.option(
    "--auth-token",
    "auth_tokens",
    multiple=True,
    help="A 'token' or 'token:client_id' pair to accept as a valid caller, backed by "
    "FastMCP's own auth layer (slb_glossary.mcp.auth.StaticTokenVerifier). May be given "
    "more than once. Mutually exclusive with --auth-provider.",
)
@click.option(
    "--auth-provider",
    "auth_provider_path",
    default=None,
    help="Dotted import path ('module:ClassName' or 'package.module.ClassName') to a "
    "FastMCP AuthProvider (e.g. a TokenVerifier subclass), instantiated with no "
    "constructor arguments. For one that needs constructor arguments (a DB pool, API "
    "client, etc.), build the `MCPApp` in Python yourself instead of going through this "
    "flag. Mutually exclusive with --auth-token.",
)
@click.option(
    "--require-scope",
    "require_scopes",
    multiple=True,
    help="An OAuth scope every caller must have, checked by FastMCP's own authorization "
    "middleware ahead of every tool call. May be given more than once. Requires "
    "--auth-token or --auth-provider (there's no authenticated caller to check "
    "scopes against otherwise).",
)
@click.option(
    "--rate-limit",
    "limit",
    type=int,
    default=None,
    help="Enable rate limiting: max requests per client per tool per --rate-limit-window.",
)
@click.option(
    "--rate-limit-window",
    type=float,
    default=60.0,
    show_default=True,
    help="Rate-limit window in seconds. Only used when --rate-limit is set.",
)
@click.option(
    "--rate-limit-algorithm",
    type=click.Choice(["sliding_window", "token_bucket"], case_sensitive=False),
    default="sliding_window",
    show_default=True,
    help="Rate-limiting algorithm. 'sliding_window' allows no burst above the limit; "
    "'token_bucket' allows short bursts above it. Only used when --rate-limit is set.",
)
@click.option(
    "--language",
    "-L",
    "language",
    type=click.Choice([lang.value for lang in Language], case_sensitive=False),
    default=None,
    help="Glossary language edition the server's live session searches. "
    "Overrides session.language from --config (or the built-in default, 'en') "
    "for this run only.",
)
@click.option(
    "--log-level",
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], case_sensitive=False),
    default=None,
    help="Verbosity of `slb_glossary`'s own logging output.",
)
@cli_command
@click.pass_context
def serve(
    ctx: click.Context,
    app_path: str | None,
    config_path: pathlib.Path | None,
    transport: str,
    host: str,
    port: int,
    tools: str,
    source: tuple[str, ...],
    no_local: bool,
    no_live: bool,
    allow_write: bool,
    timeout: float,
    auth_tokens: tuple[str, ...],
    auth_provider_path: str | None,
    require_scopes: tuple[str, ...],
    limit: int | None,
    rate_limit_window: float,
    rate_limit_algorithm: str,
    language: str | None,
    log_level: str | None,
) -> None:
    """
    Serve an MCP server, either built from the flags below or loaded from APP_PATH.

    \b
    `APP_PATH`, if given, is a "module:attr" import path (uvicorn-style) to an
    already-built `MCPApp` or `fastmcp.FastMCP`, e.g. `app.main:app` for
    `app/main.py` containing a module-level `app = MCPApp(...)`. `attr` may
    also be a zero-argument factory function returning either.

    When `APP_PATH` is given, every flag below except --transport/--host/--port/
    --log-level is ignored (the app is already fully configured); passing
    one of the ignored flags explicitly alongside `APP_PATH` is an error, to
    avoid silently doing something other than what was asked.
    """
    try:
        from slb_glossary.mcp.api import MCPApp, load_app
    except ImportError as exc:
        raise click.ClickException(
            "The MCP server needs the 'mcp' extra: `pip install slb-glossary[mcp]`."
        ) from exc

    transport_kwargs: dict[str, typing.Any] = {"transport": transport}
    if transport != "stdio":
        transport_kwargs.update(host=host, port=port)

    if app_path is not None:
        _reject_flags_with_app_path(ctx)
        app = load_app(app_path)
        run_async(app.run_async(**transport_kwargs))
        return

    from slb_glossary.mcp.auth import StaticTokenVerifier, import_provider
    from slb_glossary.mcp.config import (
        Auth,
        LocalAccess,
        MCPConfig,
        RateLimit,
        RateLimitAlgorithm,
        SessionAccess,
        SourcePolicy,
        Timeout,
        resolve_tools,
    )

    if auth_tokens and auth_provider_path:
        raise click.UsageError("--auth-token and --auth-provider are mutually exclusive.")
    if require_scopes and not (auth_tokens or auth_provider_path):
        raise click.UsageError(
            "--require-scope needs --auth-token or --auth-provider - there's no "
            "authenticated caller to check scopes against otherwise."
        )

    glossary_config = Config.load(config_path) if config_path is not None else Config()
    session_browser = glossary_config.session
    if language is not None:
        session_browser = session_browser.update(language=Language(language).value)

    session = SessionAccess(enabled=not no_live, browser=session_browser)
    local = LocalAccess(
        enabled=not no_local, allow_write=allow_write, database=glossary_config.local
    )
    allowed_sources = frozenset(Source(value) for value in source) or None
    source_policy = SourcePolicy(allowed=allowed_sources)

    if auth_tokens:
        token_map: dict[str, str] = {}
        for entry in auth_tokens:
            token, _, client_id = entry.partition(":")
            token_map[token] = client_id or token
        auth_provider = StaticTokenVerifier(token_map)
    elif auth_provider_path:
        auth_provider = import_provider(auth_provider_path)
    else:
        auth_provider = None
    auth_config = Auth(provider=auth_provider, required_scopes=tuple(require_scopes))

    if limit is not None:
        rate_limit = RateLimit(
            enabled=True,
            limit=limit,
            window=rate_limit_window,
            algorithm=RateLimitAlgorithm(rate_limit_algorithm),
        )
    else:
        rate_limit = RateLimit()

    config = MCPConfig(
        session=session,
        local=local,
        source_policy=source_policy,
        tools=resolve_tools(tools.split(",")),
        timeouts=Timeout(default=timeout or None),
        auth=auth_config,
        rate_limit=rate_limit,
        logging=MCPConfig.default(language=language).logging.update(level=log_level),
    )
    app = MCPApp(config)
    run_async(app.run_async(**transport_kwargs))
