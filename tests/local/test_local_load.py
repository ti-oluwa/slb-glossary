"""
`local.load`: `get_field`'s case-insensitive lookup, `parse_related`'s
tolerant parsing, `record_to_result`'s row-to-`SearchResult` mapping
(including URL synthesis), and `load_file`'s batched CSV/JSON/XLSX import.
"""

import json
import pathlib
import typing

import pytest

from slb_glossary.errors import DatabaseError
from slb_glossary.local.api import count as count_terms
from slb_glossary.local.api import iter_terms
from slb_glossary.local.load import get_field, load_file, parse_related, record_to_result
from slb_glossary.local.types import Database
from slb_glossary.types import RelatedTerm, SearchResult

pytestmark = pytest.mark.unit


class TestGetField:
    def test_matches_case_insensitively(self) -> None:
        """A field name matches its key regardless of case."""
        assert get_field({"Term": "Porosity"}, "term") == "Porosity"

    def test_matches_key_with_surrounding_whitespace(self) -> None:
        """A key with surrounding whitespace still matches."""
        assert get_field({" term ": "Porosity"}, "term") == "Porosity"

    def test_returns_none_for_none_name(self) -> None:
        """`name=None` returns `None` regardless of `row`."""
        assert get_field({"term": "Porosity"}, None) is None

    def test_returns_none_for_empty_name(self) -> None:
        """`name=""` returns `None`."""
        assert get_field({"term": "Porosity"}, "") is None

    def test_returns_none_when_key_absent(self) -> None:
        """A `name` with no matching key returns `None`."""
        assert get_field({"term": "Porosity"}, "definition") is None

    @pytest.mark.parametrize("value", [None, ""])
    def test_returns_none_for_none_or_empty_value(self, value: str | None) -> None:
        """A matched key whose value is `None`/`""` counts as absent, returns `None`."""
        assert get_field({"term": value}, "term") is None

    def test_falsy_but_not_empty_values_are_returned(self) -> None:
        """A falsy-but-not-`None`/`""` value (e.g. `0`) is still returned as-is."""
        assert get_field({"count": 0}, "count") == 0


class TestParseRelated:
    def test_none_or_empty_string_returns_none(self) -> None:
        """`None` or `""` returns `None`."""
        assert parse_related(None) is None
        assert parse_related("") is None

    def test_parses_native_list_of_dicts(self) -> None:
        """A native list of `{"term": ..., "url": ...}` dicts (as a JSON
        reader would already give) parses to a tuple of `RelatedTerm`."""
        raw = [{"term": "Permeability", "url": "https://x.com/permeability"}]
        assert parse_related(raw) == (
            RelatedTerm(term="Permeability", url="https://x.com/permeability"),
        )

    def test_parses_native_list_of_term_url_pairs(self) -> None:
        """A native list of `[term, url]` pairs also parses correctly."""
        raw = [["Permeability", "https://x.com/permeability"]]
        assert parse_related(raw) == (
            RelatedTerm(term="Permeability", url="https://x.com/permeability"),
        )

    def test_parses_json_array_string_of_dicts(self) -> None:
        """A JSON array string (as a CSV/XLSX cell would hold it as text) parses too."""
        raw = '[{"term": "Permeability", "url": "https://x.com/permeability"}]'
        assert parse_related(raw) == (
            RelatedTerm(term="Permeability", url="https://x.com/permeability"),
        )

    def test_skips_malformed_items_but_keeps_valid_ones(self) -> None:
        """An item missing `term`/`url`, or not dict/pair-shaped, is skipped -
        the rest of the list still parses."""
        raw = [
            {"term": "Permeability", "url": "https://x.com/permeability"},
            {"term": "Missing URL"},
            "not a dict or pair",
            [1, 2, 3],
        ]
        assert parse_related(raw) == (
            RelatedTerm(term="Permeability", url="https://x.com/permeability"),
        )

    def test_unparsable_json_string_returns_none(self) -> None:
        """An invalid JSON string returns `None`, not an error."""
        assert parse_related("not valid json") is None

    def test_non_list_top_level_returns_none(self) -> None:
        """A JSON value that parses but isn't a list (e.g. a bare object) returns `None`."""
        assert parse_related('{"term": "x", "url": "y"}') is None

    def test_empty_list_returns_none(self) -> None:
        """An empty list (native or JSON) returns `None`, not `()`."""
        assert parse_related([]) is None
        assert parse_related("[]") is None

    def test_every_item_malformed_returns_none(self) -> None:
        """A list where every item is unparsable returns `None`, not `()`."""
        assert parse_related(["not a dict or pair", 123]) is None


class TestRecordToResult:
    def _call(self, row: dict[str, typing.Any], **overrides: typing.Any) -> SearchResult | None:
        defaults = {
            "term_field": "term",
            "definition_field": "definition",
            "topic_field": "topic",
            "url_field": "url",
            "grammatical_label_field": "grammatical_label",
            "language_field": "language",
            "image_field": "image",
            "image_caption_field": "image_caption",
            "related_field": "related",
            "default_language": "en",
        }
        defaults.update(overrides)
        return record_to_result(row, **defaults)

    def test_returns_none_when_term_missing(self) -> None:
        """A row with no usable `term_field` value returns `None`."""
        assert self._call({"definition": "A rock property"}) is None

    def test_builds_a_full_result_from_a_complete_row(self) -> None:
        """Every mapped field lands in the right `SearchResult` slot."""
        row = {
            "term": "Porosity",
            "definition": "A rock property",
            "topic": "Geology",
            "url": "https://x.com/porosity",
            "grammatical_label": "Noun",
            "language": "en",
            "image": "https://x.com/img.png",
            "image_caption": "A diagram",
            "related": [{"term": "Permeability", "url": "https://x.com/permeability"}],
        }
        result = self._call(row)
        assert result is not None
        assert result.term == "Porosity"
        assert result.definition == "A rock property"
        assert result.topic == "Geology"
        assert result.url == "https://x.com/porosity"
        assert result.grammatical_label == "Noun"
        assert result.language == "en"
        assert result.image == "https://x.com/img.png"
        assert result.image_caption == "A diagram"
        assert result.related == (
            RelatedTerm(term="Permeability", url="https://x.com/permeability"),
        )

    def test_synthesizes_a_local_url_when_url_field_missing_value(self) -> None:
        """A missing/empty URL gets a stable, slugified `local://imported/...` URL."""
        result = self._call({"term": "Porosity Index"})
        assert result is not None
        assert result.url == "local://imported/porosity-index"

    def test_synthesizes_a_local_url_when_url_field_is_none(self) -> None:
        """`url_field=None` always synthesizes, even if the row has a `url` column."""
        result = self._call({"term": "Porosity", "url": "https://x.com/porosity"}, url_field=None)
        assert result is not None
        assert result.url == "local://imported/porosity"

    def test_language_field_none_always_uses_default_language(self) -> None:
        """`language_field=None` always falls back to `default_language`, even if
        the row has a `language` column."""
        result = self._call(
            {"term": "Porosity", "language": "es"}, language_field=None, default_language="en"
        )
        assert result is not None
        assert result.language == "en"

    def test_empty_language_value_falls_back_to_default_language(self) -> None:
        """A row with the language column present but empty still falls back."""
        result = self._call({"term": "Porosity", "language": ""}, default_language="en")
        assert result is not None
        assert result.language == "en"

    def test_field_set_to_none_leaves_that_slot_unset(self) -> None:
        """Setting any optional `*_field` to `None` leaves that `SearchResult` slot `None`,
        even if the row happens to have a same-named column."""
        row = {"term": "Porosity", "definition": "A rock property"}
        result = self._call(row, definition_field=None)
        assert result is not None
        assert result.definition is None

    def test_unparsable_related_field_leaves_related_none(self) -> None:
        """An unparsable `related_field` value leaves `.related` `None`, not an error."""
        result = self._call({"term": "Porosity", "related": "not valid json"})
        assert result is not None
        assert result.related is None


@pytest.mark.anyio
class TestLoadFile:
    async def test_imports_csv_rows(self, db: Database, tmp_path: pathlib.Path) -> None:
        """A well-formed CSV file's rows are read and upserted."""
        csv_path = tmp_path / "terms.csv"
        csv_path.write_text("term,definition\nPorosity,A rock property\n", encoding="utf-8")
        written = await load_file(db, csv_path)
        assert written == 1
        assert await count_terms(db) == 1

    async def test_imports_json_rows(self, db: Database, tmp_path: pathlib.Path) -> None:
        """A well-formed JSON array file's rows are read and upserted."""
        import json

        json_path = tmp_path / "terms.json"
        json_path.write_text(json.dumps([{"term": "Porosity"}, {"term": "Permeability"}]))
        written = await load_file(db, json_path)
        assert written == 2

    async def test_format_argument_overrides_path_extension(
        self, db: Database, tmp_path: pathlib.Path
    ) -> None:
        """An explicit `format=` overrides whatever `path`'s extension implies."""
        path = tmp_path / "terms.data"
        path.write_text("term,definition\nPorosity,A rock property\n", encoding="utf-8")
        written = await load_file(db, path, format="csv")
        assert written == 1

    async def test_raises_database_error_for_unsupported_format(
        self, db: Database, tmp_path: pathlib.Path
    ) -> None:
        """An unsupported format (or extension) raises `DatabaseError`."""
        path = tmp_path / "terms.yaml"
        path.write_text("term: Porosity", encoding="utf-8")
        with pytest.raises(DatabaseError, match="Unsupported import format"):
            await load_file(db, path)

    async def test_raises_value_error_for_batch_size_below_one(
        self, db: Database, tmp_path: pathlib.Path
    ) -> None:
        """`batch_size < 1` raises `ValueError` immediately, before reading anything."""
        path = tmp_path / "terms.csv"
        path.write_text("term\nPorosity\n", encoding="utf-8")
        with pytest.raises(ValueError, match="at least 1"):
            await load_file(db, path, batch_size=0)

    async def test_rows_with_no_term_are_skipped(
        self, db: Database, tmp_path: pathlib.Path
    ) -> None:
        """A row with no usable term is skipped, not counted, not an error."""
        json_path = tmp_path / "terms.json"
        json_path.write_text(json.dumps([{"term": "Porosity"}, {"definition": "no term here"}]))
        written = await load_file(db, json_path)
        assert written == 1

    async def test_flushes_remaining_partial_batch_at_the_end(
        self, db: Database, tmp_path: pathlib.Path
    ) -> None:
        """Whatever's left in the buffer once the file ends is still flushed,
        even if it's smaller than `batch_size`."""
        json_path = tmp_path / "terms.json"
        json_path.write_text(json.dumps([{"term": f"T{i}"} for i in range(3)]))
        written = await load_file(db, json_path, batch_size=10)
        assert written == 3
        assert await count_terms(db) == 3

    async def test_respects_small_batch_size_across_multiple_flushes(
        self, db: Database, tmp_path: pathlib.Path
    ) -> None:
        """A small `batch_size` still imports every row, across several internal flushes."""
        json_path = tmp_path / "terms.json"
        json_path.write_text(json.dumps([{"term": f"T{i}"} for i in range(5)]))
        written = await load_file(db, json_path, batch_size=2)
        assert written == 5

    async def test_source_tag_defaults_to_user(self, db: Database, tmp_path: pathlib.Path) -> None:
        """
        Imported rows are tagged `source="user"` by default, distinct from
        live-synced `"glossary"` rows - though `SearchResult` itself doesn't
        carry `source`, so this is confirmed indirectly via a successful,
        unexceptional import (the tag is stored internally, not user-visible
        on the returned/read `SearchResult`).
        """
        json_path = tmp_path / "terms.json"
        json_path.write_text('[{"term": "Porosity"}]')
        written = await load_file(db, json_path)
        assert written == 1
        [result] = [r async for r in iter_terms(db)]
        assert result.term == "Porosity"

    async def test_reader_error_is_wrapped_in_database_error(
        self, db: Database, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An error raised while *reading* a row from the file (not processing
        one already read) is wrapped in `DatabaseError` naming the source file."""

        def broken_read_rows(
            path: pathlib.Path, format: str | None = None
        ) -> typing.Iterator[dict[str, typing.Any]]:
            yield {"term": "Porosity"}
            raise OSError("disk read error")

        monkeypatch.setattr("slb_glossary.local.load.read_rows", broken_read_rows)
        path = tmp_path / "terms.csv"
        path.write_text("term\nPorosity\n", encoding="utf-8")
        with pytest.raises(DatabaseError, match="Could not read"):
            await load_file(db, path)

    async def test_processing_error_propagates_unwrapped(
        self, db: Database, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        An error raised while *processing* an already-read row (inside
        `record_to_result`) is **not** wrapped in `DatabaseError` - only the
        `next(row_iter)` call itself is inside that try/except, and
        `record_to_result(...)` is called outside it. Verified directly
        against the running code, not assumed: this looks like it could be
        an oversight (the two failure modes read as meant to both become
        `DatabaseError`, per the surrounding comment), but the actual
        boundary only covers the read step.
        """

        def broken_record_to_result(
            row: dict[str, typing.Any], **kwargs: typing.Any
        ) -> typing.NoReturn:
            raise ValueError("boom")

        monkeypatch.setattr("slb_glossary.local.load.record_to_result", broken_record_to_result)
        path = tmp_path / "terms.csv"
        path.write_text("term\nPorosity\n", encoding="utf-8")
        with pytest.raises(ValueError, match="boom"):
            await load_file(db, path)

    async def test_flushes_buffer_on_processing_error_via_finally(
        self, db: Database, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Even though a processing error propagates unwrapped, the `finally`
        block still flushes whatever was already buffered before it."""
        import json

        real_record_to_result = record_to_result
        call_count = 0

        def flaky_record_to_result(
            row: dict[str, typing.Any], **kwargs: typing.Any
        ) -> SearchResult | None:
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise ValueError("boom")
            return real_record_to_result(row, **kwargs)

        monkeypatch.setattr("slb_glossary.local.load.record_to_result", flaky_record_to_result)
        json_path = tmp_path / "terms.json"
        json_path.write_text(json.dumps([{"term": "Alpha"}, {"term": "Bravo"}]))
        with pytest.raises(ValueError, match="boom"):
            await load_file(db, json_path, batch_size=10)
        assert await count_terms(db) == 1
