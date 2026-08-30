"""`read_rows` and format-specific readers for CSV/JSON/XLSX."""

import json
import pathlib

import pytest

from slb_glossary.errors import UnsupportedFormatError
from slb_glossary.readers import READERS, read_csv_rows, read_json_rows, read_rows, reader

pytestmark = pytest.mark.unit


class TestReadCsvRows:
    def test_reads_rows_as_dicts_keyed_by_header(self, tmp_path: pathlib.Path):
        """Each row is a dict keyed by the CSV's header row."""
        csv_path = tmp_path / "terms.csv"
        csv_path.write_text("term,definition\nPorosity,A rock property\n", encoding="utf-8")
        rows = list(read_csv_rows(csv_path))
        assert rows == [{"term": "Porosity", "definition": "A rock property"}]

    def test_handles_empty_file(self, tmp_path: pathlib.Path):
        """A file with only a header row yields no rows."""
        csv_path = tmp_path / "empty.csv"
        csv_path.write_text("term,definition\n", encoding="utf-8")
        assert list(read_csv_rows(csv_path)) == []


class TestReadJsonRows:
    def test_reads_a_json_array_of_objects(self, tmp_path: pathlib.Path):
        """A top-level JSON array of objects yields each object as a row."""
        json_path = tmp_path / "terms.json"
        json_path.write_text(json.dumps([{"term": "Porosity"}, {"term": "Permeability"}]))
        assert list(read_json_rows(json_path)) == [
            {"term": "Porosity"},
            {"term": "Permeability"},
        ]

    def test_reads_a_json_array_nested_in_an_object(self, tmp_path: pathlib.Path):
        """A top-level object holding one array value yields that array's rows."""
        json_path = tmp_path / "terms.json"
        json_path.write_text(json.dumps({"results": [{"term": "Porosity"}]}))
        assert list(read_json_rows(json_path)) == [{"term": "Porosity"}]

    def test_rejects_non_array_top_level_json(self, tmp_path: pathlib.Path):
        """A JSON file with no array of records anywhere raises `ValueError`."""
        json_path = tmp_path / "terms.json"
        json_path.write_text(json.dumps({"term": "Porosity"}))
        with pytest.raises(ValueError, match="expected a JSON array"):
            list(read_json_rows(json_path))


class TestReadXlsxRows:
    openpyxl = pytest.importorskip("openpyxl")

    def test_reads_rows_using_first_row_as_header(self, tmp_path: pathlib.Path):
        """The first row is treated as the header and isn't yielded as data."""
        from slb_glossary.readers import read_xlsx_rows

        workbook = self.openpyxl.Workbook()
        sheet = workbook.active
        sheet.append(["term", "definition"])
        sheet.append(["Porosity", "A rock property"])
        xlsx_path = tmp_path / "terms.xlsx"
        workbook.save(xlsx_path)

        rows = list(read_xlsx_rows(xlsx_path))
        assert rows == [{"term": "Porosity", "definition": "A rock property"}]


class TestReadRows:
    @pytest.mark.parametrize("format", ["csv", "json", "xlsx"])
    def test_read_rows_dispatches_by_format_string(
        self, tmp_path: pathlib.Path, format: str, monkeypatch: pytest.MonkeyPatch
    ):
        """`read_rows` dispatches to the reader registered for `format`."""
        called_with: list[pathlib.Path] = []

        def mock_reader(path: pathlib.Path):
            called_with.append(path)
            yield {"term": "Porosity"}

        monkeypatch.setitem(READERS, format, mock_reader)
        path = tmp_path / f"terms.{format}"
        rows = list(read_rows(path))
        assert rows == [{"term": "Porosity"}]
        assert called_with == [path]

    def test_format_argument_overrides_path_extension(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ):
        """An explicit `format=` overrides whatever `path`'s extension implies."""

        def mock_reader(path: pathlib.Path):
            yield {"term": "Porosity"}

        monkeypatch.setitem(READERS, "csv", mock_reader)
        path = tmp_path / "terms.data"
        rows = list(read_rows(path, format="csv"))
        assert rows == [{"term": "Porosity"}]

    def test_read_rows_raises_on_unsupported_format(self, tmp_path: pathlib.Path):
        """An unregistered format raises `UnsupportedFormatError`."""
        with pytest.raises(UnsupportedFormatError):
            list(read_rows(tmp_path / "terms.yaml"))

    def test_reader_decorator_registers_a_new_format(self):
        """`@reader(format)` registers a throwaway format and makes it available immediately."""

        @reader("throwaway-test-format")
        def throwaway_reader(path: pathlib.Path):
            yield {"term": "Porosity"}

        try:
            assert READERS["throwaway-test-format"] is throwaway_reader
        finally:
            del READERS["throwaway-test-format"]
