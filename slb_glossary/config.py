"""
User-configurable settings, loadable from JSON/TOML/YAML.

**Disclaimer**: this module only manages configuration; any local data it
points `slb_glossary.local` at is still subject to the data-lifecycle
notice in the package docstring.
"""

import dataclasses
import json
import logging
import pathlib
import sys
import typing
from collections.abc import Mapping, Sequence

from slb_glossary.errors import ConfigError
from slb_glossary.logging import LogSink
from slb_glossary.paths import default_config_path
from slb_glossary.retries import BackoffType, RetryPolicy
from slb_glossary.types import Language, Updatable

if sys.version_info >= (3, 11):
    from typing import Self
else:
    from typing_extensions import Self

logger = logging.getLogger(__name__)

__all__ = [
    "BrowserSessionOptions",
    "Config",
    "DatabaseOptions",
    "OutputOptions",
    "RetryOptions",
]


T = typing.TypeVar("T")


@dataclasses.dataclass(slots=True, kw_only=True)
class RetryOptions(Updatable):
    """Serializable counterpart of `slb_glossary.retries.RetryPolicy`."""

    attempts: int = 3
    """Maximum number of times to retry a flaky initial page load."""

    base_delay: float = 0.8
    """Seconds used as the base of the backoff calculation."""

    backoff: str = "exponential"
    """Backoff strategy name: `"constant"`, `"linear"`, `"exponential"` or `"logarithmic"`."""

    factor: float = 2.0
    """Growth base for exponential backoff, or log base for logarithmic backoff."""

    max_delay: float = 10.0
    """Upper bound in seconds on any single retry delay."""

    jitter: bool = True
    """Whether to randomize each retry delay by up to +/-50%% to avoid retry storms."""

    def retry_policy(self) -> RetryPolicy:
        """Build the `RetryPolicy` this config describes."""
        try:
            backoff_type = BackoffType(self.backoff)
        except ValueError as exc:
            choices = ", ".join(backoff_type.value for backoff_type in BackoffType)
            raise ConfigError(
                f"Unknown retry backoff {self.backoff!r}. Expected one of: {choices}."
            ) from exc
        return RetryPolicy(
            attempts=self.attempts,
            base_delay=self.base_delay,
            backoff_type=backoff_type,
            factor=self.factor,
            max_delay=self.max_delay,
            jitter=self.jitter,
        )

    @classmethod
    def from_policy(cls, policy: RetryPolicy) -> Self:
        """Build a `RetryOptions` from an existing `RetryPolicy`."""
        return cls(
            attempts=policy.attempts,
            base_delay=policy.base_delay,
            backoff=policy.backoff_type.value,
            factor=policy.factor,
            max_delay=policy.max_delay if policy.max_delay is not None else 10.0,
            jitter=policy.jitter,
        )


@dataclasses.dataclass(slots=True, kw_only=True)
class BrowserSessionOptions(Updatable):
    """Serializable counterpart of the options `slb_glossary.browser.open_session` takes."""

    language: str = "en"
    """Glossary language edition to search: `"en"` or `"es"`."""

    browser_type: str = "chromium"
    """Playwright browser family to launch: `"chromium"`, `"firefox"` or `"webkit"`."""

    headless: bool = True
    """Whether to run the browser without a visible window."""

    block: bool = True
    """Whether to block images/media/fonts/stylesheets for faster page loads."""

    block_resources: Sequence[str] = dataclasses.field(default_factory=list)
    """Specific request resource types to block, e.g. `["image", "font"]`.
    Overrides `block` when non-empty."""

    timeout: float = 60_000.0
    """Milliseconds to wait for page loads and element lookups."""

    terms_per_tab: int = 12
    """Number of results the glossary site returns per results page."""

    max_pages: int = 6
    """
    Maximum number of browser pages a session will have open at once. Each
    independent operation (the tab-paging search page, each concurrent
    term-fetch worker) checks out its own page, so this should comfortably
    cover the highest `--concurrency` a command is run with, plus one for
    a search page paging through tabs at the same time.
    """

    initialize: bool | None = None
    """
    Whether to eagerly load the glossary's topics/size when a session
    opens, so it's immediately ready to search. `None` (the default) lets
    `open_session`/`session` resolve it themselves from
    `constants.session_auto_initialize` (`False` by default). 
    
    Set this explicitly if you want every session
    opened from this config to always initialize eagerly (`True`) or
    always defer it (`False`), overriding that default. While
    uninitialized, search functions raise `SessionNotInitializedError`
    until `Session.initialize()` is called.
    """

    settle_timeout: float = 8000
    """Milliseconds to wait for the results list to update after a search filter changes."""

    poll_interval: float = 300
    """Milliseconds to wait between polls while waiting on `settle_timeout`."""

    executable_path: str | None = None
    """Path to a specific browser build to launch, or `None` for patchright's default."""

    proxy: dict[str, str] | None = None
    """Playwright proxy settings, e.g. `{"server": "http://myproxy:3128"}`."""

    viewport: dict[str, int] | None = None
    """Browser viewport size, e.g. `{"width": 1920, "height": 1080}`."""

    use_stealth: bool | None = None
    """
    Whether to apply Playwright stealth patches to the browser context.
    `None` (the default) resolves this automatically instead, based on
    `session.headless`: applied when headless, skipped when headed.
    Stealth patches have been observed to be counterproductive in headed
    mode, making the glossary consistently harder to scrape reliably,
    not easier. See `slb_glossary.browser.open_session`.
    """

    log_sink: LogSink | type[LogSink] | str | pathlib.Path | None = None
    """
    Where to route the application's logging for this run. Can be a file path,
    `"stderr"`/`"stdout"`, or a `"module:ClassName"` import path to a
    custom `slb_glossary.logging.LogSink`. `
    
    None` (the default) leaves whatever logging setup is already in place 
    untouched.
    """

    retry: RetryOptions = dataclasses.field(default_factory=RetryOptions)
    """Policy for retrying a flaky initial page load."""

    def session_kwargs(self) -> dict[str, typing.Any]:
        """
        Build keyword arguments for `slb_glossary.browser.open_session`.

        :return: A kwargs dict ready to splat into `open_session`.
        """
        block: bool | frozenset[str]
        if self.block_resources:
            block = frozenset(name.lower() for name in self.block_resources)
        else:
            block = self.block

        try:
            language = Language(self.language)
        except ValueError as exc:
            choices = ", ".join(language.value for language in Language)
            raise ConfigError(
                f"Unknown language {self.language!r}. Expected one of: {choices}."
            ) from exc

        logger.debug(
            "Built session kwargs from config: language=%s browser_type=%s headless=%s",
            language.value,
            self.browser_type,
            self.headless,
        )
        return {
            "language": language,
            "browser_type": self.browser_type,
            "headless": self.headless,
            "block": block,
            "timeout": self.timeout,
            "terms_per_tab": self.terms_per_tab,
            "max_pages": self.max_pages,
            "retry": self.retry.retry_policy(),
            "settle_timeout": self.settle_timeout,
            "poll_interval": self.poll_interval,
            "executable_path": self.executable_path,
            "proxy": self.proxy,
            "viewport": self.viewport,
            "use_stealth": self.use_stealth,
            "initialize": self.initialize,
            "log_sink": self.log_sink,
        }


@dataclasses.dataclass(slots=True, kw_only=True)
class DatabaseOptions(Updatable):
    """Configuration for `slb_glossary.local`'s local search database."""

    enabled: bool = True
    """Whether commands/functions that offer local-database fallback should use it."""

    data_dir: str | None = None
    """
    Directory the local database and its metadata are stored in. Defaults
    to the OS-appropriate user data directory (see `slb_glossary.paths`).
    """

    db_filename: str = "glossary.db"
    """Filename of the local SQLite database within `data_dir`."""

    prefer_local: bool = False
    """
    If `True`, functions that can fall back to the local database (e.g.
    the CLI's `db` commands) prefer it over opening a live browser session.
    """

    sync_max_age_days: float | None = 7.0
    """
    Age, in days, after which `slb_glossary.local.sync` should be told
    the local database is stale. `None` means it is never considered stale
    by age alone. This is purely advisory and nothing here syncs automatically,
    keeping this package's traffic to the live glossary opt-in only.
    """


@dataclasses.dataclass(slots=True, kw_only=True)
class OutputOptions(Updatable):
    """Default CLI/print output formatting."""

    default_format: str | None = None
    """Default file format for `--save`, e.g. `"csv"`. `None` infers it
    from each save path's extension."""

    show_url: bool = True
    """Whether to show the source URL column by default."""

    show_topic: bool = True
    """Whether to show the topic column by default."""

    show_grammar: bool = True
    """Whether to show the grammatical label column by default."""

    show_image: bool = False
    """Whether to show the illustrative image URL column by default."""

    show_related: bool = False
    """Whether to show the related-terms column by default."""


def _is_dataclass_type(candidate: typing.Any) -> bool:
    """Return whether `candidate` is a dataclass *type* (not instance)."""
    return isinstance(candidate, type) and dataclasses.is_dataclass(candidate)


def _load_dataclass(cls: type[T], data: Mapping[str, typing.Any]) -> T:
    """
    Build a `cls` instance from `data`, recursing into nested dataclass fields.

    Keys in `data` with no matching field are ignored (forward-compatible
    with newer config files which may have been acceptable by an older
    package version); fields absent from `data` fall back to the dataclass's
    own defaults.

    :param cls: The dataclass type to build.
    :param data: A mapping of field name to value, as parsed from a config file.
    :return: A `cls` instance.
    """
    kwargs: dict[str, typing.Any] = {}
    for field in dataclasses.fields(cls):  # type: ignore[arg-type]
        if field.name not in data:
            continue
        value = data[field.name]
        if _is_dataclass_type(field.type) and isinstance(value, Mapping):
            kwargs[field.name] = _load_dataclass(typing.cast(type, field.type), value)
        else:
            kwargs[field.name] = value
    return cls(**kwargs)


def _read_config_file(path: pathlib.Path) -> dict[str, typing.Any]:
    """
    Parse `path` into a plain dict, choosing a parser by its file extension.

    :param path: Path to a `.json`, `.toml` or `.yaml`/`.yml` config file.
    :return: The parsed file content.
    :raises ConfigError: If `path`'s extension isn't a supported format, or
        the format's parser package isn't installed.
    """
    suffix = path.suffix.lstrip(".").lower()
    text = path.read_text(encoding="utf-8")

    if suffix == "json":
        return json.loads(text) if text.strip() else {}

    if suffix == "toml":
        try:
            import tomlkit
        except ImportError as exc:
            raise ConfigError(
                "Reading a .toml config requires the 'tomlkit' package. "
                "Install it with `pip install slb-glossary[config]`."
            ) from exc
        return dict(tomlkit.parse(text)) if text.strip() else {}

    if suffix in ("yaml", "yml"):
        try:
            import yaml
        except ImportError as exc:
            raise ConfigError(
                "Reading a .yaml config requires the 'pyyaml' package. "
                "Install it with `pip install slb-glossary[config]`."
            ) from exc
        return yaml.safe_load(text) or {}

    raise ConfigError(
        f"Unsupported config file format {suffix!r} for {path!s}. "
        "Supported formats: json, toml, yaml/yml."
    )


def _strip_none(data: typing.Any) -> typing.Any:
    """
    Recursively drop `None`-valued dict entries from `data`.

    TOML has no null type, so `tomlkit.dumps` raises on any `None` value
    anywhere in the structure. `Config.to_dict()` includes several
    `Optional` fields that default to `None` (e.g.
    `BrowserSessionOptions.executable_path`, `DatabaseOptions.data_dir`), so those
    need to be dropped rather than written before a TOML dump can succeed.

    A dropped key round-trips safely as `_load_dataclass` falls back
    to the field's own default for any key missing from a loaded config,
    and every field this can drop already defaults to `None`.

    :param data: A plain dict/list/scalar structure, e.g. from `Config.to_dict()`.
    :return: `data` with `None`-valued dict entries removed, recursing into
        nested dicts and lists. Non-dict/list values are returned unchanged.
    """
    if isinstance(data, dict):
        return {key: _strip_none(value) for key, value in data.items() if value is not None}
    if isinstance(data, list):
        return [_strip_none(item) for item in data]
    return data


def _write_config_file(data: dict[str, typing.Any], path: pathlib.Path, format: str) -> None:
    """
    Serialize `data` to `path` using the parser for `format`.

    :param data: A plain, JSON/TOML/YAML-safe dict, e.g. from `Config.to_dict`.
    :param path: Destination path.
    :param format: One of `"json"`, `"toml"`, `"yaml"`/`"yml"`.
    :raises ConfigError: If `format` isn't supported, or its writer package
        isn't installed.
    """
    if format == "json":
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return

    if format == "toml":
        try:
            import tomlkit
        except ImportError as exc:
            raise ConfigError(
                "Writing a .toml config requires the 'tomlkit' package. "
                "Install it with `pip install slb-glossary[config]`."
            ) from exc
        # tomlkit has no null type and can't serialize None - see _strip_none.
        path.write_text(tomlkit.dumps(_strip_none(data)), encoding="utf-8")
        return

    if format in ("yaml", "yml"):
        try:
            import yaml
        except ImportError as exc:
            raise ConfigError(
                "Writing a .yaml config requires the 'pyyaml' package. "
                "Install it with `pip install slb-glossary[config]`."
            ) from exc
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        return

    raise ConfigError(
        f"Unsupported config file format {format!r} for {path!s}. "
        "Supported formats: json, toml, yaml/yml."
    )


@dataclasses.dataclass(slots=True, kw_only=True)
class Config(Updatable):
    """
    Top-level, file-loadable configuration for a `Session` and local database.

    ```python
    config = Config.load()  # default path if it exists, else built-in defaults
    async with session_from_config(config) as session:
        ...
    ```
    """

    session: BrowserSessionOptions = dataclasses.field(default_factory=BrowserSessionOptions)
    """Browser/session options."""

    local: DatabaseOptions = dataclasses.field(default_factory=DatabaseOptions)
    """Local search database options."""

    output: OutputOptions = dataclasses.field(default_factory=OutputOptions)
    """Default output formatting options."""

    @classmethod
    def from_dict(cls, data: Mapping[str, typing.Any]) -> Self:
        """
        Build a `Config` from a plain nested dict, e.g. one already parsed from JSON.

        :param data: A mapping shaped like `Config.to_dict`'s output.
            Unknown keys are ignored; missing keys use their field defaults.
        :return: The built `Config`.
        """
        return _load_dataclass(cls, data)

    def to_dict(self) -> dict[str, typing.Any]:
        """Return this config as a plain, JSON/TOML/YAML-safe nested dict."""
        return dataclasses.asdict(self)

    @classmethod
    def from_file(cls, path: str | pathlib.Path) -> Self:
        """
        Load a `Config` from a JSON, TOML or YAML file.

        :param path: Path to the config file. Its extension selects the parser.
        :return: The loaded `Config`.
        :raises ConfigError: If the file's format is unsupported, its
            parser package isn't installed, or the file content is invalid.
        :raises FileNotFoundError: If `path` does not exist.
        """
        path = pathlib.Path(path)
        logger.debug("Loading config from %s", path)
        try:
            data = _read_config_file(path)
        except ConfigError:
            raise
        except Exception as exc:
            raise ConfigError(f"Could not parse config file {path!s}: {exc}") from exc
        return cls.from_dict(data)

    @classmethod
    def load(cls, path: str | pathlib.Path | None = None) -> Self:
        """
        Load a `Config` from `path`, or the default config path if it exists.

        Unlike `from_file`, this never raises `FileNotFoundError`: if
        neither `path` nor the default config path exists, it returns a
        `Config` built from defaults, so callers can always use the
        result without checking for a config file first.

        :param path: Path to a specific config file. Defaults to
            `slb_glossary.paths.default_config_path()`.
        :return: The loaded (or default) `Config`.
        """
        resolved = pathlib.Path(path) if path is not None else default_config_path()
        if resolved.exists():
            return cls.from_file(resolved)
        logger.debug("No config file at %s; using built-in defaults", resolved)
        return cls()

    def to_file(self, path: str | pathlib.Path, *, format: str | None = None) -> None:
        """
        Save this config to `path` as JSON, TOML or YAML.

        :param path: Destination path. Its parent directory is created if
            it doesn't exist.
        :param format: File format to write, overriding `path`'s extension.
            One of `"json"`, `"toml"`, `"yaml"`/`"yml"`.
        :raises ConfigError: If the resolved format is unsupported or its
            writer package isn't installed.
        """
        path = pathlib.Path(path)
        resolved_format = (format or path.suffix.lstrip(".") or "toml").lower()
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_config_file(self.to_dict(), path, resolved_format)
        logger.info("Saved config to %s (%s)", path, resolved_format)

    def session_kwargs(self) -> dict[str, typing.Any]:
        """Build keyword arguments for `open_session`/`session` from `self.session`."""
        return self.session.session_kwargs()

    def get(self, key: str) -> typing.Any:
        """
        Read a dotted config key, e.g. `"session.headless"` or `"local.prefer_local"`.

        :param key: A dot-separated path of field names, rooted at this `Config`.
        :return: The value at `key`.
        :raises ConfigError: If any segment of `key` does not name a field.
        """
        target: typing.Any = self
        for part in key.split("."):
            if not dataclasses.is_dataclass(target) or not hasattr(target, part):
                raise ConfigError(f"Unknown config key {key!r} (failed at {part!r}).")
            target = getattr(target, part)
        return target

    def set(self, key: str, value: typing.Any) -> None:
        """
        Write a dotted config key, coercing `value` to match the existing field's type.

        String `value`s are coerced to match the current value's type
        (`bool`/`int`/`float`/comma-separated `list`) so CLI callers can
        pass plain strings; non-`str` values (e.g. already-parsed JSON) are
        set as-is.

        :param key: A dot-separated path of field names, rooted at this `Config`.
        :param value: The new value. Coerced to the current field's type if
            it's a `str` and the field isn't already a `str`.
        :raises ConfigError: If any segment of `key` does not name a field.
        """
        *parents, leaf = key.split(".")
        target: typing.Any = self
        for part in parents:
            if not dataclasses.is_dataclass(target) or not hasattr(target, part):
                raise ConfigError(f"Unknown config key {key!r} (failed at {part!r}).")
            target = getattr(target, part)

        if not dataclasses.is_dataclass(target) or not hasattr(target, leaf):
            raise ConfigError(f"Unknown config key {key!r} (failed at {leaf!r}).")

        current = getattr(target, leaf)
        field = next((f for f in dataclasses.fields(target) if f.name == leaf), None)
        setattr(
            target,
            leaf,
            _cast(value, like=current, field_type=field.type if field is not None else None),
        )
        logger.debug("Set config key %s = %r", key, getattr(target, leaf))

    @classmethod
    def default_path(cls) -> pathlib.Path:
        """Return the default path `Config.load` reads from and `config init` writes to."""
        return default_config_path()


def _parse_bool(value: str) -> bool:
    """
    Parse a CLI-style boolean string.

    :param value: `"true"`/`"1"`/`"yes"`/`"on"` for `True`,
        `"false"`/`"0"`/`"no"`/`"off"` for `False` (case-insensitive,
        surrounding whitespace ignored).
    :raises ValueError: If `value` doesn't match either set.
    """
    lowered = value.strip().lower()
    if lowered in ("true", "1", "yes", "on"):
        return True
    if lowered in ("false", "0", "no", "off"):
        return False
    raise ValueError(f"{value!r} is not a boolean")


def _cast(value: typing.Any, *, like: typing.Any, field_type: typing.Any = None) -> typing.Any:
    """
    Coerce a string `value` to the type of `like`, for CLI-style key=value input.

    :param value: The raw value, typically a `str` from a CLI argument.
    :param like: The field's current value, whose type `value` is coerced
        to. When `like` is `None`, there's no runtime value to infer a
        type from - `field_type` is consulted instead (see below) rather
        than giving up and returning the raw string.
    :param field_type: The field's own declared type annotation (e.g.
        `dataclasses.Field.type`), used only as a fallback when `like` is
        `None`. This matters for a field that legitimately defaults to
        `None` while still being fundamentally `bool`-shaped, e.g.
        `BrowserSessionOptions.use_stealth: bool | None = None`, whose
        `None` means "resolve automatically" rather than "no type at
        all" - without this, `--set session.use_stealth false` would
        silently store the literal (truthy!) string `"false"` instead of
        `False`. Ignored when `like` isn't `None`.
    :return: `value` unchanged if it isn't a `str`, or `like` is a `str`;
        `value` parsed as `like`'s type if `like` isn't `None`; if `like`
        *is* `None`, `value` parsed as `bool` when `field_type` looks
        bool-shaped, else `value` unchanged.
    :raises ConfigError: If `value` cannot be parsed as the target type.
    """
    if not isinstance(value, str):
        return value

    if like is None:
        if field_type is not None and "bool" in str(field_type):
            try:
                return _parse_bool(value)
            except ValueError as exc:
                raise ConfigError(f"Could not parse {value!r} as bool: {exc}") from exc
        return value

    if isinstance(like, str):
        return value

    try:
        if isinstance(like, bool):
            return _parse_bool(value)
        if isinstance(like, int):
            return int(value)
        if isinstance(like, float):
            return float(value)
        if isinstance(like, list):
            return [item.strip() for item in value.split(",") if item.strip()]
        if isinstance(like, dict):
            return json.loads(value)
    except (ValueError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Could not parse {value!r} as {type(like).__name__}: {exc}") from exc
    return value
