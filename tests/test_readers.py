"""
`read_rows` and format-specific async readers for CSV/JSON/XLSX.
"""

import json
import pathlib
import typing

import pytest

from slb_glossary.errors import UnsupportedFormatError
from slb_glossary.readers import READERS, read_csv_rows, read_json_rows, read_rows, reader

pytestmark = [pytest.mark.unit, pytest.mark.anyio]


@pytest.fixture
def anyio_backend(
    anyio_backend_asyncio_only: tuple[str, dict[str, typing.Any]],
) -> tuple[str, dict[str, typing.Any]]:
    """The built-in readers offload to worker threads via `asyncio.to_thread`, which isn't trio-safe."""
    return anyio_backend_asyncio_only


class TestReadCsvRows:
    async def test_reads_rows_as_dicts_keyed_by_header(self, tmp_path: pathlib.Path) -> None:
        """Each row is a dict keyed by the CSV's header row."""
        csv_path = tmp_path / "terms.csv"
        csv_path.write_text("term,definition\nPorosity,A rock property\n", encoding="utf-8")
        rows = [row async for row in read_csv_rows(csv_path)]
        assert rows == [{"term": "Porosity", "definition": "A rock property"}]

    async def test_handles_empty_file(self, tmp_path: pathlib.Path) -> None:
        """A file with only a header row yields no rows."""
        csv_path = tmp_path / "empty.csv"
        csv_path.write_text("term,definition\n", encoding="utf-8")
        assert [row async for row in read_csv_rows(csv_path)] == []


class TestReadJsonRows:
    async def test_reads_a_json_array_of_objects(self, tmp_path: pathlib.Path) -> None:
        """A top-level JSON array of objects yields each object as a row."""
        json_path = tmp_path / "terms.json"
        json_path.write_text(json.dumps([{"term": "Porosity"}, {"term": "Permeability"}]))
        rows = [row async for row in read_json_rows(json_path)]
        assert rows == [{"term": "Porosity"}, {"term": "Permeability"}]

    async def test_reads_a_json_array_nested_in_an_object(self, tmp_path: pathlib.Path) -> None:
        """A top-level object holding one array value yields that array's rows."""
        json_path = tmp_path / "terms.json"
        json_path.write_text(json.dumps({"results": [{"term": "Porosity"}]}))
        rows = [row async for row in read_json_rows(json_path)]
        assert rows == [{"term": "Porosity"}]

    async def test_rejects_non_array_top_level_json(self, tmp_path: pathlib.Path) -> None:
        """A JSON file with no array of records anywhere raises `ValueError`."""
        json_path = tmp_path / "terms.json"
        json_path.write_text(json.dumps({"term": "Porosity"}))
        with pytest.raises(ValueError, match="expected a JSON array"):
            async for _ in read_json_rows(json_path):
                pass


class TestReadXlsxRows:
    openpyxl = pytest.importorskip("openpyxl")

    async def test_reads_rows_using_first_row_as_header(self, tmp_path: pathlib.Path) -> None:
        """The first row is treated as the header and isn't yielded as data."""
        from slb_glossary.readers import read_xlsx_rows

        workbook = self.openpyxl.Workbook()
        sheet = workbook.active
        sheet.append(["term", "definition"])
        sheet.append(["Porosity", "A rock property"])
        xlsx_path = tmp_path / "terms.xlsx"
        workbook.save(xlsx_path)

        rows = [row async for row in read_xlsx_rows(xlsx_path)]
        assert rows == [{"term": "Porosity", "definition": "A rock property"}]


class TestReadRows:
    @pytest.mark.parametrize("format", ["csv", "json", "xlsx"])
    async def test_read_rows_dispatches_by_format_string(
        self, tmp_path: pathlib.Path, format: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`read_rows` dispatches to the reader registered for `format`."""
        called_with: list[pathlib.Path] = []

        async def mock_reader(path: pathlib.Path) -> typing.AsyncIterator[dict[str, typing.Any]]:
            called_with.append(path)
            yield {"term": "Porosity"}

        monkeypatch.setitem(READERS, format, mock_reader)
        path = tmp_path / f"terms.{format}"
        rows = [row async for row in read_rows(path)]
        assert rows == [{"term": "Porosity"}]
        assert called_with == [path]

    async def test_format_argument_overrides_path_extension(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An explicit `format=` overrides whatever `path`'s extension implies."""

        async def mock_reader(path: pathlib.Path) -> typing.AsyncIterator[dict[str, typing.Any]]:
            yield {"term": "Porosity"}

        monkeypatch.setitem(READERS, "csv", mock_reader)
        path = tmp_path / "terms.data"
        rows = [row async for row in read_rows(path, format="csv")]
        assert rows == [{"term": "Porosity"}]

    async def test_read_rows_raises_on_unsupported_format(self, tmp_path: pathlib.Path) -> None:
        """An unregistered format raises `UnsupportedFormatError`."""
        with pytest.raises(UnsupportedFormatError):
            async for _ in read_rows(tmp_path / "terms.yaml"):
                pass

    async def test_reader_decorator_registers_a_new_format(self) -> None:
        """`@reader(format)` registers a throwaway format and makes it available immediately."""

        @reader("throwaway-test-format")
        async def throwaway_reader(
            path: pathlib.Path,
        ) -> typing.AsyncIterator[dict[str, typing.Any]]:
            yield {"term": "Porosity"}

        try:
            assert READERS["throwaway-test-format"] is throwaway_reader
        finally:
            del READERS["throwaway-test-format"]
