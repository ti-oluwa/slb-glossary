"""
`--tui` opens the interactive form instead of running the command, so it has to work
without the command's required argument (which the TUI collects itself).
"""

import pytest
from click.testing import CliRunner

from slb_glossary.cli.main import cli

pytestmark = [pytest.mark.unit, pytest.mark.cli]


@pytest.mark.parametrize(
    ("args", "module"),
    [
        (["search"], "search"),
        (["define"], "define"),
        (["related"], "related"),
        (["compare"], "compare"),
        (["terms"], "terms"),
        (["urls", "fetch"], "urls"),
    ],
)
def test_tui_flag_does_not_need_the_commands_argument(
    monkeypatch: pytest.MonkeyPatch, args: list[str], module: str
) -> None:
    opened: list[object] = []
    command_module = __import__(f"slb_glossary.cli.commands.{module}", fromlist=["launch_tui"])
    monkeypatch.setattr(command_module, "launch_tui", lambda ctx, **kwargs: opened.append(ctx))

    result = CliRunner().invoke(cli, [*args, "--tui"])

    assert result.exit_code == 0, result.output
    assert len(opened) == 1


@pytest.mark.parametrize(
    "args", [["search"], ["define"], ["related"], ["compare", "only-one"], ["terms"]]
)
def test_the_argument_is_still_required_without_tui(args: list[str]) -> None:
    result = CliRunner().invoke(cli, args)
    assert result.exit_code != 0
    assert "Missing" in result.output or "at least two" in result.output
