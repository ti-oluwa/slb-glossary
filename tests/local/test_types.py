"""
`local.types.Database`/`Metadata`: load/save round-trip and defaults.
"""

import dataclasses
import pathlib

import pytest

from slb_glossary.local.schema import SCHEMA_VERSION
from slb_glossary.local.types import Metadata

pytestmark = [pytest.mark.unit]


class TestMetadata:
    def test_load_returns_defaults_when_file_missing(self, tmp_path: pathlib.Path) -> None:
        """`Metadata.load()` on a nonexistent path returns fresh defaults, no error."""
        metadata = Metadata.load(tmp_path / "missing.json")
        assert metadata == Metadata()

    def test_save_then_load_round_trips(self, tmp_path: pathlib.Path) -> None:
        """Saving then loading reproduces an equal `Metadata`."""
        path = tmp_path / "metadata.json"
        original = Metadata(
            last_synced_at="2024-01-01T00:00:00+00:00",
            last_sync_language="en",
            term_count=42,
            topics={"Geology": 20, "Drilling": 22},
        )
        original.save(path)
        loaded = Metadata.load(path)
        assert loaded == original

    def test_save_creates_missing_parent_directories(self, tmp_path: pathlib.Path) -> None:
        """`save` creates missing parent directories before writing."""
        path = tmp_path / "nested" / "dir" / "metadata.json"
        Metadata().save(path)
        assert path.exists()

    def test_load_ignores_unknown_keys(self, tmp_path: pathlib.Path) -> None:
        """Unknown keys in the JSON file are silently ignored (forward compatibility)."""
        path = tmp_path / "metadata.json"
        path.write_text('{"term_count": 5, "future_field": "x"}', encoding="utf-8")
        metadata = Metadata.load(path)
        assert metadata.term_count == 5

    def test_load_fills_missing_fields_with_defaults(self, tmp_path: pathlib.Path) -> None:
        """Fields absent from the JSON file fall back to the dataclass's own defaults."""
        path = tmp_path / "metadata.json"
        path.write_text("{}", encoding="utf-8")
        assert Metadata.load(path) == Metadata()

    def test_default_schema_version_matches_current(self) -> None:
        """A freshly constructed `Metadata` reports the current schema version."""

        assert Metadata().schema_version == SCHEMA_VERSION

    def test_is_a_plain_dataclass_with_expected_fields(self) -> None:
        """`Metadata` exposes exactly the fields the sync/provenance bookkeeping needs."""
        field_names = {f.name for f in dataclasses.fields(Metadata)}
        assert field_names == {
            "schema_version",
            "last_synced_at",
            "last_sync_language",
            "term_count",
            "topics",
        }
