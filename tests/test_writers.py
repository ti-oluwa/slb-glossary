"""`save` and format-specific writers for CSV/JSON/JSONL/TXT/XLSX."""

import json
import pathlib

import pytest

from slb_glossary.errors import UnsupportedFormatError, WriterError
from slb_glossary.writers import (
    WRITERS,
    field_names,
    humanize_field,
    records_to_dicts,
    save,
    write_csv,
    write_json,
    write_jsonl,
    write_txt,
    writer,
)
from tests.factories import make_related_term, make_search_result, make_search_results

pytestmark = pytest.mark.unit


class TestFieldNames:
    def test_returns_first_records_fields(self):
        """Returns the field names of the first record."""
        results = make_search_results(2)
        assert field_names(results) == results[0].fields

    def test_returns_empty_list_for_empty_records(self):
        """Returns `[]` when `records` is empty."""
        assert field_names([]) == []


class TestHumanizeField:
    @pytest.mark.parametrize(
        ("field", "expected"),
        [
            ("term", "Term"),
            ("grammatical_label", "Grammatical Label"),
            ("url", "URL"),
            ("image_caption", "Image Caption"),
        ],
    )
    def test_title_cases_words_and_uppercases_acronyms(self, field: str, expected: str):
        """Snake-case words are title-cased, except acronyms (`url`/`id`), which are upper-cased."""
        assert humanize_field(field) == expected


class TestRecordsToDicts:
    def test_preserves_field_order_from_asdict(self):
        """Each record's dict preserves its `asdict()` field order."""
        result = make_search_result()
        [as_dict] = records_to_dicts([result])
        assert list(as_dict.keys()) == result.fields

    def test_excludes_given_field_names(self):
        """Fields named in `exclude` are omitted from each dict."""
        result = make_search_result()
        [as_dict] = records_to_dicts([result], exclude=["url", "image"])
        assert "url" not in as_dict
        assert "image" not in as_dict

    def test_nested_namedtuple_values_are_recursively_converted(self):
        """A nested `RelatedTerm` list converts to a list of plain dicts, not flattened text."""
        related = (make_related_term(term="Permeability"),)
        result = make_search_result(related=related)
        [as_dict] = records_to_dicts([result])
        assert as_dict["related"] == [{"term": "Permeability", "url": related[0].url}]


@pytest.mark.anyio
class TestWriteCsv:
    async def test_writes_a_humanized_header_and_display_text_rows(
        self, tmp_path: pathlib.Path, anyio_backend
    ):
        """The header row is humanized, and cells render display text (e.g. `related` joined)."""
        destination = tmp_path / "out.csv"
        result = make_search_result(related=(make_related_term(term="Permeability"),))
        await write_csv([result], destination)

        content = destination.read_text(encoding="utf-8")
        lines = content.splitlines()
        assert "Term" in lines[0]
        assert "URL" in lines[0]
        assert "Permeability" in lines[1]


@pytest.fixture
def anyio_backend(anyio_backend_asyncio_only):
    return anyio_backend_asyncio_only


@pytest.mark.anyio
class TestWriteJson:
    async def test_keys_output_by_each_records_first_field(self, tmp_path: pathlib.Path):
        """Output JSON is an object keyed by each record's first field (`term`)."""
        destination = tmp_path / "out.json"
        results = make_search_results(2)
        await write_json(results, destination)

        data = json.loads(destination.read_text(encoding="utf-8"))
        assert set(data.keys()) == {r.term for r in results}
        assert "term" not in data[results[0].term]

    async def test_repeated_key_field_overwrites_earlier_entry(self, tmp_path: pathlib.Path):
        """Two records sharing the same first-field value collapse to the later one."""
        destination = tmp_path / "out.json"
        first = make_search_result(term="Porosity", definition="first")
        second = make_search_result(term="Porosity", definition="second")
        await write_json([first, second], destination)

        data = json.loads(destination.read_text(encoding="utf-8"))
        assert data["Porosity"]["definition"] == "second"


@pytest.mark.anyio
class TestWriteJsonl:
    async def test_writes_one_json_object_per_line_in_original_order(
        self, tmp_path: pathlib.Path
    ):
        """Each line is one record's dict, unmodified field order, not re-keyed."""
        destination = tmp_path / "out.jsonl"
        results = make_search_results(2)
        await write_jsonl(results, destination)

        lines = destination.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        first_obj = json.loads(lines[0])
        assert list(first_obj.keys()) == results[0].fields

    async def test_duplicate_first_field_values_are_not_deduplicated(
        self, tmp_path: pathlib.Path
    ):
        """Unlike `write_json`, repeated `term` values both appear as separate lines."""
        destination = tmp_path / "out.jsonl"
        first = make_search_result(term="Porosity", definition="first")
        second = make_search_result(term="Porosity", definition="second")
        await write_jsonl([first, second], destination)

        lines = destination.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2


@pytest.mark.anyio
class TestWriteTxt:
    async def test_writes_a_numbered_human_readable_list(self, tmp_path: pathlib.Path):
        """Each record renders as a numbered block with humanized field labels."""
        destination = tmp_path / "out.txt"
        results = make_search_results(2)
        await write_txt(results, destination)

        content = destination.read_text(encoding="utf-8")
        assert "(1)" in content
        assert "(2)" in content
        assert "Definition:" in content


@pytest.mark.anyio
class TestWriteXlsx:
    openpyxl = pytest.importorskip("openpyxl")

    async def test_writes_a_humanized_header_and_rows(self, tmp_path: pathlib.Path):
        """The workbook's first row is a humanized header, followed by one row per record."""
        from slb_glossary.writers import write_xlsx

        destination = tmp_path / "out.xlsx"
        results = make_search_results(2)
        await write_xlsx(results, destination)

        workbook = self.openpyxl.load_workbook(destination)
        sheet = workbook.active
        rows = list(sheet.iter_rows(values_only=True))
        assert rows[0][0] == "Term"
        assert len(rows) == 1 + len(results)


@pytest.mark.anyio
class TestSave:
    async def test_dispatches_by_destination_extension(self, tmp_path: pathlib.Path):
        """`save` picks a writer based on `destination`'s file extension."""
        destination = tmp_path / "out.json"
        await save(make_search_results(1), destination)
        assert destination.exists()

    async def test_format_argument_overrides_destination_extension(
        self, tmp_path: pathlib.Path
    ):
        """An explicit `format=` overrides whatever the destination's extension implies."""
        destination = tmp_path / "out.data"
        await save(make_search_results(1), destination, format="json")
        data = json.loads(destination.read_text(encoding="utf-8"))
        assert data

    async def test_no_extension_defaults_to_txt(self, tmp_path: pathlib.Path):
        """A destination with no extension and no explicit `format` defaults to `txt`."""
        destination = tmp_path / "out"
        await save(make_search_results(1), destination)
        assert "(1)" in destination.read_text(encoding="utf-8")

    async def test_creates_missing_parent_directories(self, tmp_path: pathlib.Path):
        """Missing parent directories in `destination` are created automatically."""
        destination = tmp_path / "nested" / "dir" / "out.json"
        await save(make_search_results(1), destination)
        assert destination.exists()

    async def test_accepts_an_async_iterable_of_records(self, tmp_path: pathlib.Path):
        """`records` may be an async iterable; `save` collects it before writing."""

        async def _generate():
            for result in make_search_results(2):
                yield result

        destination = tmp_path / "out.jsonl"
        await save(_generate(), destination)
        lines = destination.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2

    async def test_raises_unsupported_format_error_for_unregistered_format(
        self, tmp_path: pathlib.Path
    ):
        """An unregistered format raises `UnsupportedFormatError`."""
        with pytest.raises(UnsupportedFormatError):
            await save(make_search_results(1), tmp_path / "out.yaml")

    async def test_wraps_writer_failure_in_writer_error(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ):
        """A writer that raises is wrapped in `WriterError`, chaining the original as `__cause__`."""

        async def _failing_writer(records, destination):
            raise OSError("disk full")

        monkeypatch.setitem(WRITERS, "json", _failing_writer)
        destination = tmp_path / "out.json"
        with pytest.raises(WriterError) as exc_info:
            await save(make_search_results(1), destination)
        assert isinstance(exc_info.value.__cause__, OSError)
        assert exc_info.value.destination == destination
        assert exc_info.value.format == "json"

    async def test_writer_decorator_registers_a_new_format(self, tmp_path: pathlib.Path):
        """`@writer(format)` registers a throwaway format usable by `save` immediately."""

        written: list[pathlib.Path] = []

        @writer("throwaway-test-format")
        async def _throwaway_writer(records, destination):
            written.append(destination)
            destination.write_text("ok", encoding="utf-8")

        try:
            destination = tmp_path / "out.throwaway-test-format"
            await save(make_search_results(1), destination)
            assert written == [destination]
        finally:
            del WRITERS["throwaway-test-format"]
