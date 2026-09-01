"""
`constants.persist_by_default`/`cli_cache_by_default`, and
`cli.source_options.persist_kwargs`'s resolution of an unset `--cache` into one of them.
"""

import pytest

from slb_glossary.cli.source_options import persist_kwargs
from slb_glossary.constants import constants

pytestmark = pytest.mark.unit


class TestPersistByDefaultConstant:
    def test_default_value_is_false(self):
        """Out of the box, `persist_by_default` is `False` (a library caller opts in)."""
        assert constants.persist_by_default is False

    def test_env_var_overrides_default(self, monkeypatch: pytest.MonkeyPatch):
        """`SLB_GLOSSARY_PERSIST_BY_DEFAULT` overrides the default."""
        monkeypatch.setenv("SLB_GLOSSARY_PERSIST_BY_DEFAULT", "true")
        assert constants.persist_by_default is True


class TestCliCacheByDefaultConstant:
    def test_default_value_is_true(self):
        """Out of the box, `cli_cache_by_default` is `True`, matching `--cache`'s
        long-standing default."""
        assert constants.cli_cache_by_default is True

    def test_env_var_overrides_default(self, monkeypatch: pytest.MonkeyPatch):
        """`SLB_GLOSSARY_CLI_CACHE_BY_DEFAULT` overrides the default."""
        monkeypatch.setenv("SLB_GLOSSARY_CLI_CACHE_BY_DEFAULT", "false")
        assert constants.cli_cache_by_default is False

    def test_is_a_separate_constant_from_persist_by_default(self):
        """Setting one doesn't affect the other - they're intentionally independent
        (see the constants' own docstrings for why they aren't unified)."""
        constants.cli_cache_by_default = False
        assert constants.persist_by_default is False  # still its own unrelated default


class TestPersistKwargs:
    def test_cache_results_none_resolves_to_cli_cache_by_default(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """An absent (`None`) `--cache`/`--no-cache` choice resolves via
        `constants.cli_cache_by_default`, not the library's `persist_by_default`."""
        monkeypatch.setattr(constants, "cli_cache_by_default", True)
        kwargs = persist_kwargs({"cache_results": None})
        assert kwargs["persist"] is True

        monkeypatch.setattr(constants, "cli_cache_by_default", False)
        kwargs = persist_kwargs({"cache_results": None})
        assert kwargs["persist"] is False

    def test_explicit_cache_results_true_is_used_as_is(self):
        """An explicit `--cache` (`True`) always wins, regardless of the constant."""
        constants.cli_cache_by_default = False
        kwargs = persist_kwargs({"cache_results": True})
        assert kwargs["persist"] is True

    def test_explicit_cache_results_false_is_used_as_is(self):
        """An explicit `--no-cache` (`False`) always wins, regardless of the constant."""
        constants.cli_cache_by_default = True
        kwargs = persist_kwargs({"cache_results": False})
        assert kwargs["persist"] is False

    def test_missing_cache_results_key_also_resolves_via_constant(self):
        """A `params` dict missing the key entirely (not just `None`-valued) still
        resolves the same way as an explicit `None`."""
        constants.cli_cache_by_default = True
        kwargs = persist_kwargs({})
        assert kwargs["persist"] is True

    def test_includes_batch_size_and_on_error(self):
        """The returned dict also carries `persist_batch_size`/`persist_on_error`."""
        kwargs = persist_kwargs(
            {"cache_results": True, "cache_batch_size": 5, "cache_on_error": False}
        )
        assert kwargs["persist_batch_size"] == 5
        assert kwargs["persist_on_error"] is False
