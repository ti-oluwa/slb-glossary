"""
`Config` and its nested options: retry-policy conversion, session kwargs,
file round-trips (JSON/TOML/YAML), dotted `get`/`set`, and value coercion.
"""

import dataclasses
import json
import pathlib

import pytest

from slb_glossary.config import (
    Config,
    DatabaseOptions,
    OutputOptions,
    RetryOptions,
    SessionOptions,
    _cast,
    _parse_bool,
)
from slb_glossary.errors import ConfigError
from slb_glossary.retries import BackoffType, RetryPolicy

pytestmark = pytest.mark.unit


class TestRetryOptions:
    def test_retry_policy_builds_matching_retry_policy(self):
        """`.retry_policy()` builds a `RetryPolicy` with matching fields."""
        options = RetryOptions(
            attempts=5, base_delay=1.0, backoff="linear", factor=3.0, max_delay=20.0, jitter=False
        )
        policy = options.retry_policy()
        assert policy.attempts == 5
        assert policy.base_delay == 1.0
        assert policy.backoff_type is BackoffType.LINEAR
        assert policy.factor == 3.0
        assert policy.max_delay == 20.0
        assert policy.jitter is False

    def test_retry_policy_rejects_unknown_backoff_name(self):
        """An unrecognized `backoff` string raises `ConfigError`."""
        options = RetryOptions(backoff="not-a-real-backoff")
        with pytest.raises(ConfigError, match="Unknown retry backoff"):
            options.retry_policy()

    def test_from_policy_round_trips_fields(self):
        """`RetryOptions.from_policy` reconstructs equivalent `RetryOptions` fields."""
        policy = RetryPolicy(
            attempts=4,
            base_delay=0.5,
            backoff_type=BackoffType.EXPONENTIAL,
            factor=2.5,
            max_delay=15.0,
            jitter=False,
        )
        options = RetryOptions.from_policy(policy)
        assert options.attempts == 4
        assert options.base_delay == 0.5
        assert options.backoff == "exponential"
        assert options.factor == 2.5
        assert options.max_delay == 15.0
        assert options.jitter is False


class TestSessionOptions:
    def test_session_kwargs_resolves_language_enum(self):
        """`session_kwargs()['language']` is a resolved `Language` member, not the raw string."""
        from slb_glossary.types import Language

        kwargs = SessionOptions(language="es").session_kwargs()
        assert kwargs["language"] is Language.SPANISH

    def test_session_kwargs_rejects_unknown_language(self):
        """An unrecognized `language` string raises `ConfigError`."""
        with pytest.raises(ConfigError, match="Unknown language"):
            SessionOptions(language="fr").session_kwargs()

    def test_block_resources_overrides_block_when_non_empty(self):
        """A non-empty `block_resources` list overrides `block` as a lowercased `frozenset`."""
        kwargs = SessionOptions(block=True, block_resources=["Image", "FONT"]).session_kwargs()
        assert kwargs["block"] == frozenset({"image", "font"})

    def test_block_flag_used_when_block_resources_empty(self):
        """With `block_resources` empty, `block_kwargs['block']` is the plain `block` bool."""
        kwargs = SessionOptions(block=False, block_resources=[]).session_kwargs()
        assert kwargs["block"] is False

    def test_retry_is_included_as_a_resolved_retry_policy(self):
        """`session_kwargs()['retry']` is a `RetryPolicy`, built from `self.retry`."""
        kwargs = SessionOptions().session_kwargs()
        assert isinstance(kwargs["retry"], RetryPolicy)


class TestConfigFromDictToDict:
    def test_to_dict_round_trips_through_from_dict(self):
        """`Config.from_dict(config.to_dict())` reproduces an equal `Config`."""
        config = Config(
            session=SessionOptions(headless=False), local=DatabaseOptions(prefer_local=True)
        )
        rebuilt = Config.from_dict(config.to_dict())
        assert rebuilt == config

    def test_from_dict_ignores_unknown_keys(self):
        """Keys with no matching field are silently ignored (forward compatibility)."""
        config = Config.from_dict({"session": {"headless": False, "future_field": "x"}})
        assert config.session.headless is False

    def test_from_dict_fills_missing_fields_with_defaults(self):
        """Fields absent from the input dict fall back to their dataclass defaults."""
        config = Config.from_dict({})
        assert config == Config()


class TestConfigFileRoundTrip:
    def test_json_round_trip(self, tmp_path: pathlib.Path):
        """Saving then loading a JSON config file reproduces an equal `Config`."""
        config = Config(session=SessionOptions(headless=False))
        path = tmp_path / "config.json"
        config.to_file(path)
        loaded = Config.from_file(path)
        assert loaded == config

    def test_toml_round_trip(self, tmp_path: pathlib.Path):
        """Saving then loading a TOML config file reproduces an equal `Config`."""
        pytest.importorskip("tomlkit")
        config = Config(local=DatabaseOptions(prefer_local=True))
        path = tmp_path / "config.toml"
        config.to_file(path)
        loaded = Config.from_file(path)
        assert loaded == config

    def test_yaml_round_trip(self, tmp_path: pathlib.Path):
        """Saving then loading a YAML config file reproduces an equal `Config`."""
        pytest.importorskip("yaml")
        config = Config(output=OutputOptions(show_url=False))
        path = tmp_path / "config.yaml"
        config.to_file(path)
        loaded = Config.from_file(path)
        assert loaded == config

    def test_toml_write_drops_none_valued_fields(self, tmp_path: pathlib.Path):
        """`None`-valued fields (e.g. `local.data_dir`) are stripped before TOML serialization."""
        pytest.importorskip("tomlkit")
        config = Config(local=DatabaseOptions(data_dir=None))
        path = tmp_path / "config.toml"
        config.to_file(path)
        # A round trip still succeeds and the field falls back to its default (None).
        loaded = Config.from_file(path)
        assert loaded.local.data_dir is None

    def test_format_argument_overrides_extension(self, tmp_path: pathlib.Path):
        """An explicit `format=` on `to_file`/`from_file` overrides the path's extension."""
        config = Config(session=SessionOptions(headless=False))
        path = tmp_path / "config.data"
        config.to_file(path, format="json")
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["session"]["headless"] is False

    def test_from_file_raises_config_error_for_unsupported_format(self, tmp_path: pathlib.Path):
        """An unsupported file extension raises `ConfigError`."""
        path = tmp_path / "config.ini"
        path.write_text("headless = false", encoding="utf-8")
        with pytest.raises(ConfigError):
            Config.from_file(path)

    def test_from_file_wraps_missing_path_in_config_error(self, tmp_path: pathlib.Path):
        """A nonexistent path is wrapped in `ConfigError`, not raised as a bare
        `FileNotFoundError` - the docstring says `FileNotFoundError`, but
        `from_file`'s `except Exception` catches it too and re-wraps it
        (verified directly against the running code)."""
        with pytest.raises(ConfigError, match="Could not parse config file"):
            Config.from_file(tmp_path / "nope.json")

    def test_empty_json_file_yields_default_config(self, tmp_path: pathlib.Path):
        """An empty/whitespace-only JSON file loads as an all-default `Config`."""
        path = tmp_path / "config.json"
        path.write_text("   ", encoding="utf-8")
        assert Config.from_file(path) == Config()

    def test_to_file_creates_missing_parent_directories(self, tmp_path: pathlib.Path):
        """`to_file` creates missing parent directories before writing."""
        path = tmp_path / "nested" / "dir" / "config.json"
        Config().to_file(path)
        assert path.exists()


class TestConfigLoad:
    def test_load_returns_defaults_when_no_path_and_no_default_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ):
        """`Config.load()` with no `path` and no file at the default path returns defaults."""
        monkeypatch.setattr(
            "slb_glossary.config.default_config_path", lambda: tmp_path / "missing.json"
        )
        assert Config.load() == Config()

    def test_load_reads_explicit_path_when_given(self, tmp_path: pathlib.Path):
        """`Config.load(path)` reads the given path directly."""
        config = Config(session=SessionOptions(headless=False))
        path = tmp_path / "config.json"
        config.to_file(path)
        assert Config.load(path) == config

    def test_load_reads_default_path_when_it_exists(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ):
        """`Config.load()` reads the default config path when it exists and no `path` is given."""
        default_path = tmp_path / "config.json"
        config = Config(session=SessionOptions(headless=False))
        config.to_file(default_path)
        monkeypatch.setattr("slb_glossary.config.default_config_path", lambda: default_path)
        assert Config.load() == config


class TestConfigGetSet:
    def test_get_reads_a_nested_dotted_key(self):
        """`.get('session.headless')` reads the nested field value."""
        config = Config(session=SessionOptions(headless=False))
        assert config.get("session.headless") is False

    def test_get_raises_config_error_for_unknown_key(self):
        """An unknown segment anywhere in the key path raises `ConfigError`."""
        with pytest.raises(ConfigError):
            Config().get("session.not_a_real_field")

    def test_set_writes_a_nested_dotted_key(self):
        """`.set('session.headless', False)` mutates the nested field in place."""
        config = Config()
        config.set("session.headless", False)
        assert config.session.headless is False

    def test_set_coerces_string_value_to_current_fields_type(self):
        """A string value is coerced to match the existing field's type (here, `bool`)."""
        config = Config()
        config.set("session.headless", "false")
        assert config.session.headless is False

    def test_set_raises_config_error_for_unknown_key(self):
        """An unknown segment anywhere in the key path raises `ConfigError`."""
        with pytest.raises(ConfigError):
            Config().set("session.not_a_real_field", "x")

    def test_set_none_valued_field_coerces_using_field_type_annotation(self):
        """Setting a currently-`None` bool-typed field still coerces the string via `field_type`."""
        config = Config(session=SessionOptions(use_stealth=None))
        config.set("session.use_stealth", "false")
        assert config.session.use_stealth is False

    def test_default_path_matches_paths_default_config_path(self):
        """`Config.default_path()` matches `paths.default_config_path()`."""
        from slb_glossary.paths import default_config_path

        assert Config.default_path() == default_config_path()


class TestParseBool:
    @pytest.mark.parametrize("raw", ["true", "1", "yes", "on", "TRUE", " Yes "])
    def test_recognizes_truthy_strings(self, raw: str):
        """Recognized truthy strings (case/whitespace-insensitive) parse to `True`."""
        assert _parse_bool(raw) is True

    @pytest.mark.parametrize("raw", ["false", "0", "no", "off", "FALSE"])
    def test_recognizes_falsy_strings(self, raw: str):
        """Recognized falsy strings (case-insensitive) parse to `False`."""
        assert _parse_bool(raw) is False

    def test_raises_value_error_for_unrecognized_string(self):
        """A string in neither set raises `ValueError`."""
        with pytest.raises(ValueError):
            _parse_bool("maybe")


class TestCast:
    def test_non_string_value_passes_through_unchanged(self):
        """A non-`str` `value` is returned unchanged, regardless of `like`."""
        assert _cast(5, like=0) == 5

    def test_string_like_passes_value_through_unchanged(self):
        """When `like` is itself a `str`, `value` is returned unchanged (no coercion needed)."""
        assert _cast("hello", like="world") == "hello"

    @pytest.mark.parametrize(
        ("value", "like", "expected"),
        [("true", False, True), ("5", 0, 5), ("1.5", 0.0, 1.5)],
    )
    def test_coerces_to_likes_type(self, value, like, expected):
        """`value` is coerced to `bool`/`int`/`float` matching `like`'s type."""
        assert _cast(value, like=like) == expected

    def test_coerces_comma_separated_string_to_list(self):
        """A comma-separated string coerces to a stripped list of items when `like` is a list."""
        assert _cast("a, b ,c", like=["x"]) == ["a", "b", "c"]

    def test_coerces_json_string_to_dict(self):
        """A JSON string coerces to a dict when `like` is a dict."""
        assert _cast('{"a": 1}', like={"x": 1}) == {"a": 1}

    def test_raises_config_error_on_uncoercible_value(self):
        """An uncoercible string raises `ConfigError` for a non-`str` `like`."""
        with pytest.raises(ConfigError):
            _cast("not-a-number", like=0)

    def test_like_none_and_bool_shaped_field_type_parses_as_bool(self):
        """`like=None` with a bool-shaped `field_type` still parses the string as bool."""
        field_type = next(
            f.type for f in dataclasses.fields(SessionOptions) if f.name == "use_stealth"
        )
        assert _cast("false", like=None, field_type=field_type) is False

    def test_like_none_and_non_bool_field_type_returns_value_unchanged(self):
        """`like=None` with a non-bool-shaped `field_type` returns the raw string unchanged."""
        field_type = next(
            f.type for f in dataclasses.fields(SessionOptions) if f.name == "executable_path"
        )
        assert _cast("some/path", like=None, field_type=field_type) == "some/path"
