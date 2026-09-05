"""Launch trogon's auto-generated TUI for the CLI, on demand."""

import logging
import typing

import click

logger = logging.getLogger(__name__)


__all__ = ["TuiUnavailableError", "launch_tui"]


class TuiUnavailableError(RuntimeError):
    """Raised when `--tui` is used but `trogon` (or `textual`) is not installed."""


def _prefill_schema(command_schema: typing.Any, ctx: click.Context) -> None:
    """
    Overwrite `command_schema`'s option/argument defaults with `ctx.params`.

    trogon builds `command_schema.options`/`.arguments` by walking
    `ctx.command.params` once and splitting each `click.Option` into
    `.options`, each `click.Argument` into `.arguments`, in declaration
    order - see `trogon.introspect.introspect_click_app`. This replays
    that same split to pair each schema entry back up with the click
    `Parameter` (and so the `ctx.params` value) it came from, which
    trogon does not expose a mapping for itself, then overwrites the
    schema's default with whatever this run was actually called with -
    so the form trogon builds from `command_schema` opens pre-filled
    instead of blank/click's-own-defaults.

    A param whose resolved value is `None` (an optional option/argument
    that was not actually given on this run, e.g. `--topic`/`--source`
    left unset) is prefilled with its click-level default (`param.default`)
    instead, if it has one. For a choice-backed field (rendered as a
    `Select` widget) with no click-level default either, this falls back
    to that field's *first* listed choice rather than leaving it blank:
    programmatically preselecting the command tree node here (below, via
    `tree.select_node(target)`) builds the initial form/command preview
    outside the ordinary interactive flow, and a `Select` left with
    nothing chosen at that point can leak its internal blank-value
    sentinel into the built command line as literal text (e.g.
    `--source Select.NULL`) instead of just omitting the flag. Every
    shared choice-backed option in this CLI (see e.g.
    `slb_glossary.cli.source_options.source_options`'s `--source`) is
    expected to list its safe/no-op default value first for exactly this
    reason. A plain (non-choice) field with no default is left alone -
    those render as a text `Input`, which has no such blank-value pitfall.

    Best-effort otherwise: `ctx.command.params` and `command_schema.options`/
    `.arguments` are expected to zip up one-to-one in order, matching
    trogon's own construction. If a future trogon version changes that,
    this just stops pairing correctly rather than raising - the caller
    wraps this in its own broad `except` regardless, since reaching into
    another package's internals like this can not be made fully future-proof.

    :param command_schema: A `trogon.introspect.CommandSchema` for the
        command `ctx` was resolved against.
    :param ctx: The click context actually used for this run, whose
        `.params` holds every option/argument's resolved value.
    """
    from trogon.introspect import MultiValueParamData

    options = iter(command_schema.options)
    arguments = iter(command_schema.arguments)
    for param in ctx.command.params:
        if isinstance(param, click.Argument):
            schema = next(arguments, None)
        elif isinstance(param, click.Option):
            schema = next(options, None)
        else:
            continue
        if schema is None or param.name not in ctx.params:
            continue

        value = ctx.params[param.name]
        if value is None:
            value = param.default
        if value is None and isinstance(param.type, click.Choice) and param.type.choices:
            value = param.type.choices[0]
        if value is None:
            if param.required:
                # Shouldn't normally happen as click resolves required
                # params (or fails) before `launch_tui` is ever reached.
                # But if it does, there's nothing meaningful to prefill
                # with; leave it for the user to fill in on the form
                # rather than forcing a default that is not there.
                logger.debug(
                    "Required param %r has no resolved value to prefill the TUI with; "
                    "leaving its field for the user to fill in",
                    param.name,
                )
            continue

        schema.default = MultiValueParamData.process_cli_option(value)


def _find_node(node: typing.Any, path: typing.Sequence[str]) -> typing.Any:
    """
    Walk `node`'s children by `CommandSchema.name`, following `path` one segment at a time.

    :param node: A `textual.widgets.tree.TreeNode` to search from.
    :param path: Command names to follow, e.g. `("local", "search")` for
        `slb-glossary local search`.
    :return: The `TreeNode` at the end of `path`, or `None` if any segment
        along the way has no matching child.
    """
    if not path:
        return node
    head, *rest = path
    for child in node.children:
        data = getattr(child, "data", None)
        if data is not None and str(data.name) == head:
            return _find_node(child, rest) if rest else child
    return None


def _find_tree_start_node(tree_root: typing.Any) -> typing.Any:
    """
    Resolve where `command_path` should start being matched from.

    `trogon`'s `CommandTree` does not put the CLI's own top-level commands
    directly under the tree's (invisible) root - it wraps them all under
    one extra node named after the root click command (`"root"` for a
    root group named e.g. `slb-glossary`, since `trogon` looks up the group
    by its click-internal name rather than `info_name`). `command_path`
    is written relative to the CLI's own top-level commands (e.g.
    `("search",)`), so that wrapper needs to be stepped over first.

    :param tree_root: The command tree's actual root `TreeNode`.
    :return: The node `command_path` should be matched against. The
        wrapper's child if there is exactly one such wrapper, otherwise
        `tree_root` itself unchanged.
    """
    children = tree_root.children
    if len(children) == 1 and getattr(children[0].data, "name", None) == "root":
        return children[0]
    return tree_root


def _prefilling_screen_factory(
    command_builder_cls: type,
    command_tree_cls: type,
    command_path: tuple[str, ...],
    ctx: click.Context,
) -> typing.Callable[..., typing.Any]:
    """
    Build a `command_builder_cls` (trogon's `CommandBuilder`) subclass that,
    once mounted, preselects `command_path`'s node in the command tree and
    prefills its form from `ctx.params`, instead of opening on the tree
    root with nothing selected, which is all trogon does on its own.

    A factory returning a *subclass*, not an instance, since
    `Trogon.get_default_screen` is what actually constructs the screen
    (with its own `cli`/`app_name`/`command_name` arguments); `command_path`
    and `ctx` are closed over here instead, since `launch_tui` does not
    control `Trogon.__init__`'s own signature.

    :param command_builder_cls: trogon's own `CommandBuilder` screen class.
    :param command_tree_cls: trogon's own `CommandTree` widget class.
    :param command_path: Command names to preselect, e.g. `("search",)`.
    :param ctx: The click context actually used for this run.
    :return: A zero-argument-beyond-`self` factory matching what
        `Trogon.get_default_screen` is expected to return when called.
    """

    class _PrefillingCommandBuilder(command_builder_cls):  # type: ignore[valid-type,misc]
        def on_mount(self) -> None:
            self.call_after_refresh(self._preselect_and_prefill)

        def _preselect_and_prefill(self) -> None:
            try:
                tree = self.queryone(command_tree_cls)
                start = _find_tree_start_node(tree.root)
                target = _find_node(start, command_path)
                if target is None or target.data is None:
                    logger.debug(
                        "No command-tree node found for %r; opening the TUI "
                        "without preselecting a command",
                        command_path,
                    )
                    return
                _prefill_schema(target.data, ctx)
                tree.select_node(target)
            except Exception:
                logger.debug(
                    "Could not preselect/prefill %r in the TUI's command tree; "
                    "opening it without preselecting a command",
                    command_path,
                    exc_info=True,
                )

    return _PrefillingCommandBuilder


def launch_tui(ctx: click.Context, *, command_path: typing.Sequence[str] = ()) -> None:
    """
    Open trogon's interactive TUI for the CLI rooted at `ctx`.

    :param ctx: The click context of the command `--tui` was passed to.
        Its top-level command group is what the TUI renders and drives -
        every command and option in the group becomes browsable/fillable.
        This run's own `.params` are also used to preselect and prefill
        `command_path`'s node in the TUI, so e.g. `slb-glossary search
        porosity --topic Drilling --tui` opens straight on `search`'s
        form with `porosity`/`Drilling` already filled in, rather than on
        an empty tree root.
    :param command_path: Names of the command (and parent groups, if any)
        to preselect in the TUI, e.g. `("search",)`. Best-effort: this
        reaches into trogon's own widget tree to do it (trogon has no
        public API for it), so a future trogon version could stop this
        from working - if that happens, the TUI just opens on its default
        tree-root view instead of raising.
    :raises TuiUnavailableError: If `trogon` (or one of its own
        dependencies, e.g. `textual`) is not installed.
    """
    try:
        from trogon.trogon import CommandBuilder, Trogon
        from trogon.widgets.command_tree import CommandTree
    except ImportError as exc:
        raise TuiUnavailableError(
            "The --tui flag requires the 'trogon' and 'textual' packages. "
            "Install them with `pip install slb-glossary[tui]`."
        ) from exc

    root_ctx = ctx.find_root()
    root_command = root_ctx.command
    if not isinstance(root_command, click.Group):
        raise TuiUnavailableError("The TUI requires a click.Group as the CLI's root command.")

    app = Trogon(
        root_command, click_context=root_ctx, app_name=root_ctx.info_name or "slb-glossary"
    )

    if command_path:
        try:
            screen_cls = _prefilling_screen_factory(
                CommandBuilder, CommandTree, tuple(command_path), ctx
            )
            app.get_default_screen = lambda: screen_cls(  # type: ignore[method-assign]
                app.cli, app.app_name, app.command_name
            )
        except Exception:
            logger.debug(
                "Could not set up command preselection for the TUI; "
                "opening it without preselecting %r",
                command_path,
                exc_info=True,
            )

    app.run()
