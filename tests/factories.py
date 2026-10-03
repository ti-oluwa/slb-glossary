"""Plain builder functions for constructing test data with sensible defaults."""

import typing

from slb_glossary.config import Config, DatabaseOptions, SessionOptions
from slb_glossary.live import Runtime, SessionPool
from slb_glossary.types import Language, RelatedTerm, SearchResult


def make_related_term(**overrides: typing.Any) -> RelatedTerm:
    """Build a `RelatedTerm` with sensible defaults, overriding any subset of fields."""
    defaults: dict[str, typing.Any] = {
        "term": "Porosity",
        "url": "https://glossary.slb.com/en/terms/p/porosity",
    }
    defaults.update(overrides)
    return RelatedTerm(**defaults)


def make_search_result(**overrides: typing.Any) -> SearchResult:
    """Build a `SearchResult` with sensible defaults, overriding any subset of fields."""
    defaults: dict[str, typing.Any] = {
        "term": "Porosity",
        "definition": "The percentage of pore volume in a rock.",
        "grammatical_label": "Noun",
        "topic": "Geology",
        "url": "https://glossary.slb.com/en/terms/p/porosity",
        "image": None,
        "image_caption": None,
        "related": None,
        "language": "en",
    }
    defaults.update(overrides)
    return SearchResult(**defaults)


def make_search_results(n: int, **overrides: typing.Any) -> list[SearchResult]:
    """Build `n` distinct `SearchResult`s (distinct term/url pairs), same override pattern."""
    results = []
    for i in range(n):
        per_item_defaults: dict[str, typing.Any] = {
            "term": f"Term {i}",
            "url": f"https://glossary.slb.com/en/terms/t/term-{i}",
        }
        per_item_defaults.update(overrides)
        results.append(make_search_result(**per_item_defaults))
    return results


def make_config(**overrides: typing.Any) -> Config:
    """
    Build a `Config` with every nested section at sane test defaults.

    Pass a full replacement for a nested section (`session=SessionOptions(...)`)
    or, for the common case of just pointing the local database at a
    throwaway path, `local_data_dir=...` as shorthand for
    `local=DatabaseOptions(data_dir=...)`.
    """
    local_data_dir = overrides.pop("local_data_dir", None)
    config = Config()
    if local_data_dir is not None:
        config = config.update(local=DatabaseOptions(data_dir=str(local_data_dir)))
    if overrides:
        config = config.update(**overrides)
    return config


def make_session_pool(**overrides: typing.Any) -> SessionPool:
    """
    Build a `SessionPool` with test defaults (English, default `SessionOptions`, room for
    5 browsers), overriding any subset of its init arguments.

    Pair with the `mock_launcher` fixture so no real browser is launched.
    """
    defaults: dict[str, typing.Any] = {
        "language": Language.ENGLISH,
        "options": SessionOptions(),
        "max_sessions": 5,
    }
    defaults.update(overrides)
    return SessionPool(**defaults)


def make_runtime(**overrides: typing.Any) -> Runtime:
    """
    Build a `Runtime` with test defaults (local access off so only live sessions are
    exercised, no idle reaper, room for 5 browsers), overriding any subset of its init
    arguments.

    Pair with the `mock_launcher` fixture so no real browser or database is opened.
    """
    defaults: dict[str, typing.Any] = {
        "local_enabled": False,
        "live_enabled": True,
        "idle_timeout": None,
        "max_sessions": 5,
    }
    defaults.update(overrides)
    return Runtime(**defaults)
