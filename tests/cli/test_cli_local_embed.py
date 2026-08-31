"""
`slb-glossary local embed`: wiring to `local.embed_terms` (`--urls`,
`--reembed`/`--only-missing`, `--batch-size`).

Monkeypatches `slb_glossary.local.vector.embed`/`embedding_dim` the same
way `tests/local/conftest.py`'s `mock_embeddings` fixture does, to avoid
a real, network-dependent `model2vec` model load. Seeding uses a plain
`asyncio.run` rather than parametrizing over anyio backends: the CLI
itself always drives its own asyncio event loop internally (via
`run_async`), so there's nothing backend-specific being tested here.
"""

import asyncio
import pathlib

import numpy as np
import pytest
from click.testing import CliRunner

from slb_glossary.cli.main import cli
from slb_glossary.local.api import upsert_results
from slb_glossary.local.connection import database
from slb_glossary.types import SearchResult
from tests.factories import make_search_result

pytestmark = [pytest.mark.unit, pytest.mark.cli]


@pytest.fixture(autouse=True)
def mock_embeddings(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Fake `slb_glossary.local.vector.embed`/`embedding_dim`, avoiding a real
    (network-dependent) `model2vec` model load.
    """
    from slb_glossary.local import vector

    def embed(texts: list[str]) -> np.ndarray:
        return np.ones((len(texts), 4), dtype="float32")

    monkeypatch.setattr(vector, "embedding_dim", lambda: 4)
    monkeypatch.setattr(vector, "embed", embed)


@pytest.fixture
def db_path(tmp_path: pathlib.Path) -> pathlib.Path:
    """A throwaway db path under `tmp_path`, for `--db-path`."""
    return tmp_path / "test.db"


def seed(db_path: pathlib.Path, results: list[SearchResult]) -> None:
    """Write `results` into the database at `db_path` before invoking the CLI."""

    async def run() -> None:
        async with database(db_path) as db:
            await upsert_results(db, results)

    asyncio.run(run())


class TestLocalEmbed:
    def test_embeds_every_row_by_default(self, db_path: pathlib.Path):
        """With no options, embeds every locally stored row and reports the count."""
        seed(db_path, [make_search_result(url="https://x.com/a", term="Porosity")])
        result = CliRunner().invoke(
            cli, ["local", "embed", "--db-path", str(db_path), "--config", "none"]
        )
        assert result.exit_code == 0, result.output
        assert "Embedded 1 row(s)." in result.output

    def test_only_missing_skips_already_embedded_rows_by_default(self, db_path: pathlib.Path):
        """A second run with no `--reembed` embeds nothing new."""
        seed(db_path, [make_search_result(url="https://x.com/a", term="Porosity")])
        runner = CliRunner()
        runner.invoke(cli, ["local", "embed", "--db-path", str(db_path), "--config", "none"])
        result = runner.invoke(
            cli, ["local", "embed", "--db-path", str(db_path), "--config", "none"]
        )
        assert result.exit_code == 0, result.output
        assert "Embedded 0 row(s)." in result.output

    def test_reembed_flag_recomputes_everything(self, db_path: pathlib.Path):
        """`--reembed` re-embeds rows even if already embedded."""
        seed(db_path, [make_search_result(url="https://x.com/a", term="Porosity")])
        runner = CliRunner()
        runner.invoke(cli, ["local", "embed", "--db-path", str(db_path), "--config", "none"])
        result = runner.invoke(
            cli,
            ["local", "embed", "--db-path", str(db_path), "--config", "none", "--reembed"],
        )
        assert result.exit_code == 0, result.output
        assert "Embedded 1 row(s)." in result.output

    def test_urls_option_restricts_which_rows_are_embedded(self, db_path: pathlib.Path):
        """`--urls` restricts embedding to the given comma-separated URLs."""
        seed(
            db_path,
            [
                make_search_result(url="https://x.com/a", term="Alpha"),
                make_search_result(url="https://x.com/b", term="Bravo"),
            ],
        )
        result = CliRunner().invoke(
            cli,
            [
                "local",
                "embed",
                "--db-path",
                str(db_path),
                "--config",
                "none",
                "--urls",
                "https://x.com/a",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "Embedded 1 row(s)." in result.output

    def test_empty_database_embeds_nothing(self, db_path: pathlib.Path):
        """An empty (never-synced/imported) database embeds `0` rows, no error."""
        result = CliRunner().invoke(
            cli, ["local", "embed", "--db-path", str(db_path), "--config", "none"]
        )
        assert result.exit_code == 0, result.output
        assert "Embedded 0 row(s)." in result.output
