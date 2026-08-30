"""Plain builder functions for constructing test data with sensible defaults."""

import typing

from slb_glossary.config import Config, DatabaseOptions
from slb_glossary.types import RelatedTerm, SearchResult


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
