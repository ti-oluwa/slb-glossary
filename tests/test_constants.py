"""
The `Constant` descriptor's env-var resolution, caching, and override/reset behavior.
"""

import enum

import pytest

from slb_glossary.constants import Constant, Constants, constants
from slb_glossary.utils import EnvVarError

pytestmark = pytest.mark.unit

ENV_VAR = "SLB_GLOSSARY_TEST_CONSTANT"


class Choice(enum.Enum):
    A = "a"
    B = "b"


class Holder:
    """A standalone class carrying one `Constant`, isolated from the real `Constants`."""

    value: Constant = Constant(10, env_var=ENV_VAR)


class TestConstantResolution:
    def test_uses_default_when_env_var_unset(self, monkeypatch: pytest.MonkeyPatch):
        """A `Constant` resolves to its `default` when its `env_var` isn't set."""
        monkeypatch.delenv(ENV_VAR, raising=False)
        holder = Holder()
        assert holder.value == 10

    def test_env_var_overrides_default(self, monkeypatch: pytest.MonkeyPatch):
        """Setting the env var overrides `default`."""
        monkeypatch.setenv(ENV_VAR, "42")
        holder = Holder()
        assert holder.value == 42

    def test_validator_rejects_invalid_env_value(self, monkeypatch: pytest.MonkeyPatch):
        """An env value failing `validator` raises `EnvVarError`."""

        class _ValidatedHolder:
            value: Constant = Constant(10, env_var=ENV_VAR, validator=lambda v: v > 0)

        monkeypatch.setenv(ENV_VAR, "-5")
        with pytest.raises(EnvVarError):
            _ = _ValidatedHolder().value

    @pytest.mark.parametrize(
        ("constant_type", "default", "raw", "expected"),
        [
            (bool, False, "true", True),
            (int, 0, "7", 7),
            (float, 0.0, "1.5", 1.5),
            (Choice, Choice.A, "b", Choice.B),
        ],
    )
    def test_type_casts_env_value_correctly(
        self, monkeypatch: pytest.MonkeyPatch, constant_type, default, raw, expected
    ):
        """The env string is cast per the constant's type: `bool`/`int`/`float`/`Enum`."""

        class TypedHolder:
            value: Constant = Constant(default, env_var=ENV_VAR, type=constant_type)

        monkeypatch.setenv(ENV_VAR, raw)
        assert TypedHolder().value == expected


class TestConstantCaching:
    def test_cache_false_rereads_env_var_every_access(self, monkeypatch: pytest.MonkeyPatch):
        """`cache=False` (the default) re-reads the env var on every access."""

        class _UncachedHolder:
            value: Constant = Constant(10, env_var=ENV_VAR, cache=False)

        holder = _UncachedHolder()
        monkeypatch.setenv(ENV_VAR, "1")
        assert holder.value == 1
        monkeypatch.setenv(ENV_VAR, "2")
        assert holder.value == 2

    def test_cache_true_reads_env_var_only_once(self, monkeypatch: pytest.MonkeyPatch):
        """`cache=True` resolves the env var once, then holds that value."""

        class CachedHolder:
            value: Constant = Constant(10, env_var=ENV_VAR, cache=True)

        holder = CachedHolder()
        monkeypatch.setenv(ENV_VAR, "1")
        assert holder.value == 1
        monkeypatch.setenv(ENV_VAR, "2")
        assert holder.value == 1


class TestConstantOverride:
    def test_explicit_set_wins_over_env_var_regardless_of_cache(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """An explicit `instance.constant = value` assignment wins over the env var."""

        class OverriddenHolder:
            value: Constant = Constant(10, env_var=ENV_VAR, cache=False)

        holder = OverriddenHolder()
        monkeypatch.setenv(ENV_VAR, "99")
        holder.value = 5
        assert holder.value == 5

    def test_reset_clears_override_and_cache(self, monkeypatch: pytest.MonkeyPatch):
        """`reset()` clears an explicit override, going back to default/env resolution."""

        class ResettableHolder:
            value: Constant = Constant(10, env_var=ENV_VAR, cache=False)

        holder = ResettableHolder()
        holder.value = 5
        assert holder.value == 5
        type(holder).__dict__["value"].reset()
        monkeypatch.delenv(ENV_VAR, raising=False)
        assert holder.value == 10

    def test_set_runs_validator_and_rejects_invalid_value(self):
        """An explicit `__set__` still runs `validator` and raises on failure."""

        class ValidatedSetHolder:
            value: Constant = Constant(10, validator=lambda v: v > 0)

        holder = ValidatedSetHolder()
        with pytest.raises(ValueError):
            holder.value = -1


def test_constants_is_a_singleton():
    """`Constants()` always returns the same shared instance as `constants`."""
    assert Constants() is Constants() is constants
