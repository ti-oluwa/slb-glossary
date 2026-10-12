"""Tests for `env()`, `parse_int`, and `split_exclude`."""

import asyncio
import enum
import typing

import pytest

from slb_glossary.utils import (
    BACKGROUND_CLOSES,
    EnvironmentVariableError,
    aclose_quietly,
    aclosing_quietly,
    env,
    fold_text,
    parse_int,
    shielded,
    split_exclude,
)
from tests.conftest import cancel_repeatedly

pytestmark = pytest.mark.unit

ENV_VAR = "SLB_GLOSSARY_TEST_UTILS_ENV"


class Choice(enum.Enum):
    A = "a"
    B = "b"


class TestEnv:
    def test_returns_default_when_var_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`env` returns `default` when the variable isn't set."""
        monkeypatch.delenv(ENV_VAR, raising=False)
        assert env(ENV_VAR, "fallback") == "fallback"

    @pytest.mark.parametrize("raw", ["1", "true", "yes", "on", "TRUE", "Yes"])
    def test_casts_truthy_strings_to_bool_true(
        self, monkeypatch: pytest.MonkeyPatch, raw: str
    ) -> None:
        """Recognized truthy string forms cast to `True` for a `bool` default."""
        monkeypatch.setenv(ENV_VAR, raw)
        assert env(ENV_VAR, False) is True

    @pytest.mark.parametrize("raw", ["0", "false", "no", "off", ""])
    def test_casts_falsy_strings_to_bool_false(
        self, monkeypatch: pytest.MonkeyPatch, raw: str
    ) -> None:
        """Unrecognized/falsy string forms cast to `False` for a `bool` default."""
        monkeypatch.setenv(ENV_VAR, raw)
        assert env(ENV_VAR, True) is False

    def test_casts_to_int(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A numeric string casts to `int` when `default` is an `int`."""
        monkeypatch.setenv(ENV_VAR, "42")
        assert env(ENV_VAR, 0) == 42

    def test_casts_to_float(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A numeric string casts to `float` when `default` is a `float`."""
        monkeypatch.setenv(ENV_VAR, "3.14")
        assert env(ENV_VAR, 0.0) == pytest.approx(3.14)

    def test_casts_to_str(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A plain string passes through unchanged when `default` is a `str`."""
        monkeypatch.setenv(ENV_VAR, "hello")
        assert env(ENV_VAR, "") == "hello"

    def test_casts_to_enum_by_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An `Enum` subclass is matched by its member value."""
        monkeypatch.setenv(ENV_VAR, "b")
        assert env(ENV_VAR, Choice.A, type=Choice) is Choice.B

    def test_raises_env_var_error_on_uncastable_value(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An uncastable value raises `EnvironmentVariableError`."""
        monkeypatch.setenv(ENV_VAR, "not-a-number")
        with pytest.raises(EnvironmentVariableError):
            env(ENV_VAR, 0)

    def test_validator_failure_raises_env_var_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A value failing `validator` raises `EnvironmentVariableError`."""
        monkeypatch.setenv(ENV_VAR, "-1")
        with pytest.raises(EnvironmentVariableError):
            env(ENV_VAR, 0, validator=lambda v: v >= 0)


class TestParseInt:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [("42", 42), ("1,204", 1204), (" 42 ", 42), ("1, 204", 1204)],
    )
    def test_parses_valid_int_string(self, text: str, expected: int) -> None:
        """Comma-grouped and whitespace-padded integer strings parse correctly."""
        assert parse_int(text) == expected

    def test_raises_value_error_on_invalid_string(self) -> None:
        """A string with no valid integer raises `ValueError`."""
        with pytest.raises(ValueError):
            parse_int("not a number")


class TestSplitExclude:
    def test_splits_urls_and_term_names(self) -> None:
        """URLs (`http(s)://`-prefixed) and term names are split into separate sets."""
        urls, names = split_exclude(
            [
                "https://glossary.slb.com/en/terms/p/porosity",
                "Permeability",
            ]
        )
        assert urls == frozenset({"https://glossary.slb.com/en/terms/p/porosity"})
        assert names == frozenset({"permeability"})

    def test_term_names_are_normalized_case_and_whitespace_insensitively(self) -> None:
        """Term names are lowercased and whitespace-collapsed for comparison."""
        urls, names = split_exclude(["  Porosity  ", "POROSITY"])
        assert urls == frozenset()
        assert names == frozenset({"porosity"})

    def test_empty_or_none_input_returns_empty_collections(self) -> None:
        """`None` or an empty collection returns two empty `frozenset`s."""
        assert split_exclude(None) == (frozenset(), frozenset())
        assert split_exclude([]) == (frozenset(), frozenset())

    def test_falsy_entries_within_the_collection_are_skipped(self) -> None:
        """An empty-string entry within a non-empty collection is skipped, not treated as a name."""
        urls, names = split_exclude(["", "Porosity"])
        assert urls == frozenset()
        assert names == frozenset({"porosity"})


class TestFoldText:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("Capillary-Pressure", "capillary pressure"),
            ("  capillary_pressure?? ", "capillary pressure"),
            ("Presi\u00f3n", "presion"),
            ("Hooke's law", "hookes law"),
            ("Hooke\u2019s law", "hookes law"),
            ("gas/oil (GOR)", "gas oil gor"),
            ("H2S", "h2s"),
            ("???", ""),
            ("", ""),
        ],
    )
    def test_folds_case_accents_apostrophes_and_symbols(self, text: str, expected: str) -> None:
        assert fold_text(text) == expected


class Recorder:
    """An async generator that records when it was closed, and what it was doing then."""

    def __init__(self) -> None:
        self.closed = False

    async def items(self) -> typing.AsyncIterator[int]:
        try:
            for i in range(10):
                yield i
        finally:
            self.closed = True


@pytest.mark.anyio
class TestAclosingQuietly:
    @pytest.fixture
    def anyio_backend(
        self, anyio_backend_asyncio_only: tuple[str, dict[str, typing.Any]]
    ) -> tuple[str, dict[str, typing.Any]]:
        return anyio_backend_asyncio_only

    async def test_closes_on_break(self) -> None:
        recorder = Recorder()
        async with aclosing_quietly(recorder.items()) as items:
            async for _ in items:
                break
        assert recorder.closed, "closed right away, not whenever the garbage collector gets to it"

    async def test_closes_on_return_from_the_loop(self) -> None:
        recorder = Recorder()

        async def first() -> int:
            async with aclosing_quietly(recorder.items()) as items:
                async for item in items:
                    return item
            return -1

        assert await first() == 0
        assert recorder.closed

    async def test_closes_when_the_body_raises(self) -> None:
        recorder = Recorder()
        with pytest.raises(RuntimeError):
            async with aclosing_quietly(recorder.items()) as items:
                async for _ in items:
                    raise RuntimeError("boom")
        assert recorder.closed

    async def test_leaves_things_without_aclose_alone(self) -> None:
        await aclose_quietly([1, 2, 3])
        async with aclosing_quietly(iter([1, 2])) as items:  # type: ignore[arg-type]
            assert list(items) == [1, 2]  # type: ignore[call-overload]

    async def test_never_raises_if_closing_fails(self) -> None:
        async def failing() -> typing.AsyncIterator[int]:
            try:
                yield 1
            finally:
                raise RuntimeError("cannot close")

        iterator = failing()
        await iterator.__anext__()
        await aclose_quietly(iterator)  # logs a warning, but does not raise


@pytest.mark.anyio
class TestShielded:
    @pytest.fixture
    def anyio_backend(
        self, anyio_backend_asyncio_only: tuple[str, dict[str, typing.Any]]
    ) -> tuple[str, dict[str, typing.Any]]:
        return anyio_backend_asyncio_only

    async def test_returns_the_result_and_raises_the_error(self) -> None:
        async def value() -> int:
            await asyncio.sleep(0)
            return 7

        async def broken() -> int:
            await asyncio.sleep(0)
            raise ValueError("boom")

        assert await shielded(value()) == 7
        with pytest.raises(ValueError, match="boom"):
            await shielded(broken())

    async def test_work_finishes_before_a_single_cancellation_is_raised(self) -> None:
        done: list[str] = []

        async def work() -> None:
            await asyncio.sleep(0.05)
            done.append("work")

        async def caller() -> None:
            await shielded(work())

        task = asyncio.create_task(caller())
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert done == ["work"], "the cancellation waited for the work to finish"

    async def test_work_carries_on_in_the_background_if_cancelled_again_and_again(self) -> None:
        done: list[str] = []

        async def work() -> None:
            await asyncio.sleep(0.05)
            done.append("work")

        async def caller() -> None:
            await shielded(work())

        task = asyncio.create_task(caller())
        await asyncio.sleep(0.01)
        await cancel_repeatedly(task)
        await asyncio.sleep(0.2)
        assert done == ["work"]

    async def test_does_not_wait_longer_than_the_grace_period(self) -> None:
        async def stuck() -> None:
            await asyncio.sleep(30)

        async def caller() -> None:
            await shielded(stuck(), grace=0.05)

        task = asyncio.create_task(caller())
        await asyncio.sleep(0.01)
        task.cancel()
        started = asyncio.get_running_loop().time()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert asyncio.get_running_loop().time() - started < 1.0
        for leftover in list(BACKGROUND_CLOSES):
            leftover.cancel()

    async def test_finished_work_is_not_kept_alive(self) -> None:
        await shielded(asyncio.sleep(0))
        await asyncio.sleep(0)
        assert not BACKGROUND_CLOSES
