"""
`env()` casting/validation, `parse_int`, and `split_exclude`.

`Lookup` (`slb_glossary.utils.Lookup`) is a bare `typing.Protocol` alias
with no runtime behavior of its own beyond what `typing.Protocol`
already provides, so it has nothing meaningful to unit test here.
"""

import enum

import pytest

from slb_glossary.utils import EnvironmentVariableError, env, parse_int, split_exclude

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
