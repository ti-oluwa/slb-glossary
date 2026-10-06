"""
Tests for how `slb` turns session flags into `open_session` kwargs: the page-pool
timeout flag, and the `max_pages` bump that `--concurrency` triggers.
"""

import typing

import click
import pytest
from click.testing import CliRunner

from slb_glossary.cli.session_options import config_option, resolve_session_kwargs, session_options

pytestmark = [pytest.mark.unit, pytest.mark.cli]


def resolved_kwargs(*args: str) -> dict[str, typing.Any]:
    """Run a bare command that has the shared session flags and return what it resolves."""
    captured: dict[str, typing.Any] = {}

    @click.command()
    @config_option
    @session_options
    @click.option("--concurrency", type=int, default=1)
    @click.pass_context
    def probe(ctx: click.Context, **params: typing.Any) -> None:
        captured.update(resolve_session_kwargs(ctx, params))

    result = CliRunner().invoke(probe, ["--config", "none", *args])
    assert result.exit_code == 0, result.output
    return captured


class TestPageAcquireTimeout:
    def test_defaults_to_the_constant(self) -> None:
        assert resolved_kwargs()["page_acquire_timeout"] == 60_000

    def test_the_flag_overrides_it(self) -> None:
        assert resolved_kwargs("--page-acquire-timeout", "5000")["page_acquire_timeout"] == 5000

    def test_zero_is_accepted_to_wait_forever(self) -> None:
        assert resolved_kwargs("--page-acquire-timeout", "0")["page_acquire_timeout"] == 0


class TestMaxPagesBump:
    """`--concurrency` raises `max_pages` to fit: workers, a paging page, and the held base page."""

    @pytest.mark.parametrize(
        ("concurrency", "expected"),
        [(1, 6), (3, 6), (4, 6), (5, 7), (8, 10)],
    )
    def test_covers_workers_plus_two(self, concurrency: int, expected: int) -> None:
        assert resolved_kwargs("--concurrency", str(concurrency))["max_pages"] == expected

    def test_an_explicit_max_pages_always_wins(self) -> None:
        assert resolved_kwargs("--concurrency", "8", "--max-pages", "3")["max_pages"] == 3

    def test_without_an_explicit_concurrency_nothing_is_bumped(self) -> None:
        assert resolved_kwargs()["max_pages"] == 6
