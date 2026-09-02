"""`slb-glossary config` - view, edit, and locate the JSON/TOML/YAML config file."""

import dataclasses
import json
import os
import pathlib
import subprocess
import typing

import click
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from slb_glossary.cli.errors import cli_command
from slb_glossary.cli.tui import launch_tui
from slb_glossary.config import Config, _strip_none
from slb_glossary.errors import ConfigError

__all__ = ["config"]


def _load_config(path: str | None) -> tuple[Config, pathlib.Path]:
    """Load a `Config` from `path` (or the default path) and return it with the resolved path."""
    resolved = pathlib.Path(path) if path else Config.default_path()
    if resolved.exists():
        return Config.from_file(resolved), resolved
    return Config(), resolved


def _resolve_path(ctx: click.Context, local_path: str | None) -> str | None:
    """
    Resolve which config path a subcommand should use.

    A `--path` given directly to the subcommand wins; otherwise falls
    back to the one given to the parent `config` group, if any (`slb-glossary
    config --path X show` and `slb-glossary config show --path X` both work).

    :param ctx: The subcommand's click context.
    :param local_path: The subcommand's own `--path` value, if given.
    :return: The path to use, or `None` for the default config path.
    """
    if local_path:
        return local_path
    return ctx.obj.get("config_path") if ctx.obj else None


path_option = click.option(
    "--path",
    "config_path",
    default=None,
    help="Config file to operate on. Defaults to the one given to `config`, "
    "or the global config path (see `config path`) if neither is given.",
)


def _iter_leaf_fields(
    obj: typing.Any, prefix: str = ""
) -> typing.Iterator[tuple[str, typing.Any]]:
    """Yield `(dotted_key, value)` for every non-dataclass field reachable from `obj`."""
    for field in dataclasses.fields(obj):
        value = getattr(obj, field.name)
        path = f"{prefix}{field.name}"
        if dataclasses.is_dataclass(value):
            yield from _iter_leaf_fields(value, prefix=f"{path}.")
        else:
            yield path, value


@click.group("config", invoke_without_command=True)
@click.option(
    "--path",
    "config_path",
    default=None,
    help="Config file to operate on. Defaults to the global config path (see `config path`).",
)
@click.option(
    "--tui",
    "use_tui",
    is_flag=True,
    help="Open the interactive TUI to fill in a config subcommand's flags.",
)
@click.pass_context
def config(ctx: click.Context, config_path: str | None, use_tui: bool) -> None:
    """
    View, edit, and locate slb-glossary's config file.

    Run with no subcommand to open the interactive wizard (same as
    `config wizard`). A guided, section-by-section walkthrough that shows
    each setting's current value and lets you accept it or type a new one.
    For scripting, use `config get`/`config set`/`config show` instead.

    \b
    Examples:
      slb-glossary config                        # interactive wizard
      slb-glossary config show
      slb-glossary config get session.headless
      slb-glossary config set session.headless false
      slb-glossary config path
    """
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config_path
    if use_tui:
        launch_tui(ctx, command_path=("config",))
        ctx.exit(0)
    if ctx.invoked_subcommand is None:
        ctx.invoke(wizard)


@config.command("path")
@path_option
@click.pass_context
def show_path(ctx: click.Context, config_path: str | None) -> None:
    """
    Print the config file path this command group would read/write.

    \b
    Examples:
      slb-glossary config path
    """
    path = _resolve_path(ctx, config_path)
    resolved = pathlib.Path(path) if path else Config.default_path()
    exists = "exists" if resolved.exists() else "does not exist yet"
    click.echo(f"{resolved} ({exists})")


@config.command("show")
@path_option
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["json", "toml", "yaml"], case_sensitive=False),
    default="toml",
    show_default=True,
    help="Format to render the config in.",
)
@click.pass_context
@cli_command
def show(ctx: click.Context, config_path: str | None, output_format: str) -> None:
    """
    Print the effective config (loaded config file, merged over built-in defaults).

    \b
    Examples:
      slb-glossary config show
      slb-glossary config show --format json
      slb-glossary config show --path ~/my-config.toml
    """
    config, _ = _load_config(_resolve_path(ctx, config_path))
    data = config.to_dict()

    if output_format == "json":
        click.echo(json.dumps(data, indent=2))
        return
    if output_format == "toml":
        try:
            import tomlkit  # type: ignore[import]

            click.echo(tomlkit.dumps(_strip_none(data)), nl=False)
            return
        except ImportError:
            pass
    if output_format == "yaml":
        try:
            import yaml  # type: ignore[import]

            click.echo(yaml.safe_dump(data, sort_keys=False), nl=False)
            return
        except ImportError:
            pass
    # Fall back to JSON if the requested format's optional parser isn't installed.
    click.echo(json.dumps(data, indent=2))


@config.command("get")
@click.argument("key")
@path_option
@click.pass_context
@cli_command
def get(ctx: click.Context, key: str, config_path: str | None) -> None:
    """
    Print a single dotted config key's value, e.g. `session.headless`.

    \b
    Examples:
      slb-glossary config get session.headless
      slb-glossary config get local.prefer_local
      slb-glossary config get session.headless --path ~/my-config.toml
    """
    config, _ = _load_config(_resolve_path(ctx, config_path))
    try:
        value = config.get(key)
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(value) if not isinstance(value, str) else value)


@config.command("set")
@click.argument("key")
@click.argument("value")
@path_option
@click.option(
    "--format",
    "output_format",
    default=None,
    help="File format to save as. Inferred from the config path's extension if omitted.",
)
@click.pass_context
@cli_command
def set_(
    ctx: click.Context, key: str, value: str, config_path: str | None, output_format: str | None
) -> None:
    """
    Set a single dotted config key and save the config file.

    \b
    Examples:
      slb-glossary config set session.headless false
      slb-glossary config set session.browser_type firefox
      slb-glossary config set local.sync_max_age_days 3.5
      slb-glossary config set session.headless false --path ~/my-config.toml
    """
    resolved_path = _resolve_path(ctx, config_path)
    config, resolved = _load_config(resolved_path)
    try:
        config.set(key, value)
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    config.to_file(resolved, format=output_format)
    click.echo(f"Set {key} = {config.get(key)!r} in {resolved}")


@config.command("init")
@path_option
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["json", "toml", "yaml"], case_sensitive=False),
    default=None,
    help="File format to write. Inferred from the path's extension if omitted (defaults to toml).",
)
@click.option("--force", is_flag=True, help="Overwrite the file if it already exists.")
@click.pass_context
@cli_command
def init(
    ctx: click.Context, config_path: str | None, output_format: str | None, force: bool
) -> None:
    """
    Write a fresh, all-defaults config file.

    \b
    Examples:
      slb-glossary config init
      slb-glossary config init --format json --force
      slb-glossary config init --path ~/my-config.toml
    """
    resolved_path = _resolve_path(ctx, config_path)
    resolved = pathlib.Path(resolved_path) if resolved_path else Config.default_path()
    if resolved.exists() and not force:
        raise click.ClickException(f"{resolved} already exists. Use --force to overwrite it.")
    Config().to_file(resolved, format=output_format)
    click.echo(f"Wrote default config to {resolved}")


@config.command("edit")
@path_option
@click.pass_context
@cli_command
def edit(ctx: click.Context, config_path: str | None) -> None:
    """
    Open the config file in $EDITOR (or $VISUAL), creating it with defaults first if missing.

    \b
    Examples:
      slb-glossary config edit
      slb-glossary config edit --path ~/my-config.toml
    """
    resolved_path = _resolve_path(ctx, config_path)
    resolved = pathlib.Path(resolved_path) if resolved_path else Config.default_path()
    if not resolved.exists():
        Config().to_file(resolved)
        click.echo(f"Created default config at {resolved}")

    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if not editor:
        raise click.ClickException(
            "No $VISUAL or $EDITOR set. Set one, or use `slb-glossary config` "
            "(the interactive wizard) / `config set` instead."
        )
    try:
        subprocess.run([editor, str(resolved)], check=True)
    except OSError as exc:
        raise click.ClickException(f"Could not launch editor {editor!r}: {exc}") from exc
    except subprocess.CalledProcessError as exc:
        raise click.ClickException(f"{editor} exited with status {exc.returncode}.") from exc


SECTION_TITLES: dict[str, str] = {
    "session": "Browser session",
    "Database": "Local search database",
    "output": "Output formatting",
}


@config.command("wizard")
@path_option
@click.pass_context
@cli_command
def wizard(ctx: click.Context, config_path: str | None) -> None:
    """
    Walk through the config section by section, showing current values and prompting for new ones.

    Nothing is written until you confirm the save at the end; Ctrl-C at
    any point leaves the config file untouched.

    \b
    Examples:
      slb-glossary config wizard
      slb-glossary config          # same thing, the group's default action
      slb-glossary config wizard --path ~/my-config.toml
    """
    resolved_path = _resolve_path(ctx, config_path)
    config, resolved = _load_config(resolved_path)
    console = Console()

    console.print(
        Panel.fit(
            f"[bold]slb-glossary config wizard[/bold]\nEditing: [cyan]{resolved}[/cyan]"
            + (
                ""
                if resolved.exists()
                else " [dim](not created yet - built-in defaults shown)[/dim]"
            ),
            border_style="bright_blue",
        )
    )

    for field in dataclasses.fields(config):
        section_value = getattr(config, field.name)
        if not dataclasses.is_dataclass(section_value):
            continue

        leaves = list(_iter_leaf_fields(section_value, prefix=f"{field.name}."))
        table = Table(
            title=SECTION_TITLES.get(field.name, field.name),
            box=box.SIMPLE,
            show_header=True,
            header_style="bold cyan",
        )
        table.add_column("Key")
        table.add_column("Current value")
        for key, value in leaves:
            table.add_row(key, str(value))
        console.print(table)

        if not click.confirm(f"Edit {SECTION_TITLES.get(field.name, field.name)}?", default=False):
            continue

        for key, current in leaves:
            raw = click.prompt(f"  {key}", default=str(current), show_default=True)
            if raw == str(current):
                continue
            try:
                config.set(key, raw)
            except ConfigError as exc:
                click.secho(f"  Skipped ({exc})", fg="red")

    console.print()
    if not click.confirm(f"Save changes to {resolved}?", default=True):
        click.echo("Discarded - nothing was written.")
        return

    output_format = None
    if not resolved.suffix:
        output_format = click.prompt(
            "File format", type=click.Choice(["toml", "json", "yaml"]), default="toml"
        )
    config.to_file(resolved, format=output_format)
    click.secho(f"Saved to {resolved}", fg="green")
