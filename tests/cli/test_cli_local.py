"""Tests for `slb-glossary local cli commands`."""

import asyncio
import json
import pathlib

import numpy as np
import pytest
from click.testing import CliRunner

from slb_glossary.cli.main import cli
from slb_glossary.local import vector
from slb_glossary.local.api import count, upsert_results
from slb_glossary.local.connection import database
from slb_glossary.local.types import Metadata
from slb_glossary.local.vector import VECTOR_TABLE, embed_terms
from slb_glossary.types import SearchResult
from tests.factories import make_search_result

pytestmark = [pytest.mark.unit, pytest.mark.cli]


@pytest.fixture(autouse=True)
def mock_embeddings(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Fake `slb_glossary.local.vector.embed`/`embedding_dim`, avoiding a real
    (network-dependent) `model2vec` model load.
    """

    def embed(texts: list[str]) -> np.ndarray:
        vectors = []
        for text in texts:
            vectors.append([1.0, 0.0, 0.0, 0.0] if "Porosity" in text else [0.0, 1.0, 0.0, 0.0])
        return np.array(vectors, dtype="float32")

    monkeypatch.setattr(vector, "embedding_dim", lambda: 4)
    monkeypatch.setattr(vector, "embed", embed)


@pytest.fixture
def db_path(tmp_path: pathlib.Path) -> pathlib.Path:
    """A throwaway db path under `tmp_path`, for `--db-path`."""
    return tmp_path / "test.db"


def seed(db_path: pathlib.Path, results: list[SearchResult], *, embed: bool = False) -> None:
    """Write `results` into the database at `db_path`, optionally embedding them, before invoking the CLI."""

    async def run() -> None:
        async with database(db_path) as db:
            await upsert_results(db, results)
            if embed:
                await embed_terms(db)

    asyncio.run(run())


async def _vector_row_count(db_path: pathlib.Path) -> int:
    async with database(db_path) as db:
        await vector.load_extension(db)
        async with db.connection.execute(f"SELECT COUNT(*) AS n FROM {VECTOR_TABLE}") as cursor:
            row = await cursor.fetchone()
        assert row is not None
        return row["n"]


def vector_row_count(db_path: pathlib.Path) -> int:
    """Number of rows currently in the vector table at `db_path`."""
    return asyncio.run(_vector_row_count(db_path))


def term_count(db_path: pathlib.Path) -> int:
    """Number of terms currently stored at `db_path`."""

    async def run() -> int:
        async with database(db_path) as db:
            return await count(db)

    return asyncio.run(run())


class TestLocalPath:
    def test_prints_the_resolved_paths(self, db_path: pathlib.Path) -> None:
        """Prints the database and metadata file paths, matching `--db-path`."""
        result = CliRunner().invoke(
            cli, ["local", "path", "--db-path", str(db_path), "--config", "none"]
        )
        assert result.exit_code == 0, result.output
        assert f"Database: {db_path}" in result.output
        assert "Metadata:" in result.output

    def test_metadata_path_follows_a_custom_override(
        self, db_path: pathlib.Path, tmp_path: pathlib.Path
    ) -> None:
        """`--metadata-path` overrides where the metadata file resolves to."""
        metadata_path = tmp_path / "custom-meta.json"
        result = CliRunner().invoke(
            cli,
            [
                "local",
                "path",
                "--db-path",
                str(db_path),
                "--metadata-path",
                str(metadata_path),
                "--config",
                "none",
            ],
        )
        assert result.exit_code == 0, result.output
        assert f"Metadata: {metadata_path}" in result.output

    def test_metadata_colocates_with_a_custom_db_path_without_its_own_override(
        self, db_path: pathlib.Path
    ) -> None:
        """
        With `--db-path` overridden but no `--metadata-path`, metadata
        follows the custom `--db-path` (as a sibling file), instead of
        resolving to the unrelated default data directory.
        """
        result = CliRunner().invoke(
            cli, ["local", "path", "--db-path", str(db_path), "--config", "none"]
        )
        assert result.exit_code == 0, result.output
        expected_metadata_path = db_path.with_name(db_path.stem + ".metadata.json")
        assert f"Metadata: {expected_metadata_path}" in result.output


class TestLocalStats:
    def test_reports_term_count_and_topics(self, db_path: pathlib.Path) -> None:
        """Reports the total term count and a topic breakdown."""
        seed(
            db_path,
            [
                make_search_result(url="https://x.com/a", term="Porosity", topic="Geology"),
                make_search_result(url="https://x.com/b", term="Permeability", topic="Geology"),
            ],
        )
        result = CliRunner().invoke(
            cli, ["local", "stats", "--db-path", str(db_path), "--config", "none"]
        )
        assert result.exit_code == 0, result.output
        assert "Terms stored locally: 2" in result.output
        assert "Geology" in result.output

    def test_json_output(self, db_path: pathlib.Path) -> None:
        """`--json` prints machine-readable stats instead of the human summary."""
        seed(db_path, [make_search_result(url="https://x.com/a", term="Porosity")])
        result = CliRunner().invoke(
            cli, ["local", "stats", "--db-path", str(db_path), "--config", "none", "--json"]
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["term_count"] == 1

    def test_empty_database(self, db_path: pathlib.Path) -> None:
        """An empty database reports zero terms and no topics, without erroring."""
        result = CliRunner().invoke(
            cli, ["local", "stats", "--db-path", str(db_path), "--config", "none"]
        )
        assert result.exit_code == 0, result.output
        assert "Terms stored locally: 0" in result.output
        assert "No topics stored locally yet." in result.output


class TestLocalSearch:
    def test_finds_a_stored_term(self, db_path: pathlib.Path) -> None:
        """A basic query finds a matching, locally stored term."""
        seed(db_path, [make_search_result(url="https://x.com/a", term="Porosity")])
        result = CliRunner().invoke(
            cli, ["local", "search", "porosity", "--db-path", str(db_path), "--config", "none"]
        )
        assert result.exit_code == 0, result.output
        assert "Porosity" in result.output

    def test_empty_query_is_rejected(self, db_path: pathlib.Path) -> None:
        """An empty/whitespace-only query is rejected with a clear error, not silently searched."""
        result = CliRunner().invoke(
            cli, ["local", "search", "   ", "--db-path", str(db_path), "--config", "none"]
        )
        assert result.exit_code != 0

    def test_no_results_found(self, db_path: pathlib.Path) -> None:
        """A query matching nothing locally reports no results, without erroring."""
        result = CliRunner().invoke(
            cli,
            ["local", "search", "zzz_no_such_term", "--db-path", str(db_path), "--config", "none"],
        )
        assert result.exit_code == 0, result.output
        assert "No local results found." in result.output

    def test_no_url_hides_the_url_column(self, db_path: pathlib.Path) -> None:
        """`--no-url` hides the URL column from the results table."""
        seed(db_path, [make_search_result(url="https://x.com/a", term="Porosity")])
        result = CliRunner().invoke(
            cli,
            [
                "local",
                "search",
                "porosity",
                "--db-path",
                str(db_path),
                "--config",
                "none",
                "--no-url",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "https://x.com/a" not in result.output

    def test_mode_semantic_with_min_similarity_filters_weak_matches(
        self, db_path: pathlib.Path
    ) -> None:
        """`--mode semantic --min-similarity` drops a result whose similarity is below the floor."""
        seed(
            db_path,
            [
                make_search_result(url="https://x.com/a", term="Porosity", definition=None),
                make_search_result(url="https://x.com/b", term="Unrelated", definition=None),
            ],
            embed=True,
        )
        result = CliRunner().invoke(
            cli,
            [
                "local",
                "search",
                "Porosity",
                "--db-path",
                str(db_path),
                "--config",
                "none",
                "--mode",
                "semantic",
                "--min-similarity",
                "0.9",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "Porosity" in result.output
        assert "Unrelated" not in result.output

    def test_topic_filter_restricts_results(self, db_path: pathlib.Path) -> None:
        """`--topic` restricts results to that topic only."""
        seed(
            db_path,
            [
                make_search_result(url="https://x.com/a", term="Porosity", topic="Geology"),
                make_search_result(url="https://x.com/b", term="Porosity2", topic="Drilling"),
            ],
        )
        result = CliRunner().invoke(
            cli,
            [
                "local",
                "search",
                "porosity",
                "--db-path",
                str(db_path),
                "--config",
                "none",
                "--topic",
                "Drilling",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "Porosity2" in result.output
        assert "https://x.com/a" not in result.output


class TestLocalGet:
    def test_finds_an_exact_term(self, db_path: pathlib.Path) -> None:
        """An exact term name is found and printed."""
        seed(db_path, [make_search_result(url="https://x.com/a", term="Porosity")])
        result = CliRunner().invoke(
            cli, ["local", "get", "Porosity", "--db-path", str(db_path), "--config", "none"]
        )
        assert result.exit_code == 0, result.output
        assert "Porosity" in result.output

    def test_finds_by_url(self, db_path: pathlib.Path) -> None:
        """A URL is also a valid lookup key."""
        seed(db_path, [make_search_result(url="https://x.com/a", term="Porosity")])
        result = CliRunner().invoke(
            cli,
            ["local", "get", "https://x.com/a", "--db-path", str(db_path), "--config", "none"],
        )
        assert result.exit_code == 0, result.output
        assert "Porosity" in result.output

    def test_empty_argument_is_rejected(self, db_path: pathlib.Path) -> None:
        """An empty/whitespace-only argument is rejected, not silently looked up."""
        result = CliRunner().invoke(
            cli, ["local", "get", "  ", "--db-path", str(db_path), "--config", "none"]
        )
        assert result.exit_code != 0

    def test_not_found_reports_clearly(self, db_path: pathlib.Path) -> None:
        """A term not stored locally reports not found, without erroring."""
        result = CliRunner().invoke(
            cli, ["local", "get", "NoSuchTerm", "--db-path", str(db_path), "--config", "none"]
        )
        assert result.exit_code == 0, result.output
        assert "was not found locally" in result.output

    def test_no_suggest_skips_similar_alternatives(self, db_path: pathlib.Path) -> None:
        """`--no-suggest` skips offering similarly-named alternatives on a miss."""
        seed(db_path, [make_search_result(url="https://x.com/a", term="Porosity")])
        result = CliRunner().invoke(
            cli,
            [
                "local",
                "get",
                "Porositi",
                "--db-path",
                str(db_path),
                "--config",
                "none",
                "--no-suggest",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "Did you mean" not in result.output


class TestLocalImport:
    def test_imports_a_csv_file(self, db_path: pathlib.Path, tmp_path: pathlib.Path) -> None:
        """A CSV file with the default column names imports into the local database."""
        csv_path = tmp_path / "terms.csv"
        csv_path.write_text("term,definition,topic\nPorosity,A measure of pore space.,Geology\n")

        result = CliRunner().invoke(
            cli, ["local", "import", str(csv_path), "--db-path", str(db_path), "--config", "none"]
        )
        assert result.exit_code == 0, result.output
        assert term_count(db_path) == 1

    def test_imports_a_json_file(self, db_path: pathlib.Path, tmp_path: pathlib.Path) -> None:
        """A JSON file with the default field names imports into the local database."""
        json_path = tmp_path / "terms.json"
        json_path.write_text(json.dumps([{"term": "Porosity", "definition": "Pore space."}]))

        result = CliRunner().invoke(
            cli, ["local", "import", str(json_path), "--db-path", str(db_path), "--config", "none"]
        )
        assert result.exit_code == 0, result.output
        assert term_count(db_path) == 1

    def test_custom_term_field(self, db_path: pathlib.Path, tmp_path: pathlib.Path) -> None:
        """`--term-field` reads the term name from a differently-named column."""
        csv_path = tmp_path / "terms.csv"
        csv_path.write_text("name\nPorosity\n")

        result = CliRunner().invoke(
            cli,
            [
                "local",
                "import",
                str(csv_path),
                "--db-path",
                str(db_path),
                "--config",
                "none",
                "--term-field",
                "name",
            ],
        )
        assert result.exit_code == 0, result.output
        assert term_count(db_path) == 1

    def test_reimporting_the_same_file_updates_rather_than_duplicates(
        self, db_path: pathlib.Path, tmp_path: pathlib.Path
    ) -> None:
        """Importing the same file twice updates the existing row rather than duplicating it."""
        csv_path = tmp_path / "terms.csv"
        csv_path.write_text("term,definition\nPorosity,First definition.\n")
        runner = CliRunner()
        runner.invoke(
            cli, ["local", "import", str(csv_path), "--db-path", str(db_path), "--config", "none"]
        )
        runner.invoke(
            cli, ["local", "import", str(csv_path), "--db-path", str(db_path), "--config", "none"]
        )
        assert term_count(db_path) == 1


class TestLocalExport:
    def test_exports_every_term_to_a_json_file(
        self, db_path: pathlib.Path, tmp_path: pathlib.Path
    ) -> None:
        """A raw (no `--query`) export writes every stored term to the given file."""
        seed(db_path, [make_search_result(url="https://x.com/a", term="Porosity")])
        out_path = tmp_path / "out.json"

        result = CliRunner().invoke(
            cli,
            [
                "local",
                "export",
                "--db-path",
                str(db_path),
                "--config",
                "none",
                "--save",
                str(out_path),
                "--quiet",
            ],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(out_path.read_text())
        assert "Porosity" in data

    def test_topic_filter_restricts_the_export(
        self, db_path: pathlib.Path, tmp_path: pathlib.Path
    ) -> None:
        """`--topic` restricts a raw export to that topic only."""
        seed(
            db_path,
            [
                make_search_result(url="https://x.com/a", term="Porosity", topic="Geology"),
                make_search_result(url="https://x.com/b", term="MWD", topic="Drilling"),
            ],
        )
        out_path = tmp_path / "out.json"
        result = CliRunner().invoke(
            cli,
            [
                "local",
                "export",
                "--db-path",
                str(db_path),
                "--config",
                "none",
                "--topic",
                "Drilling",
                "--save",
                str(out_path),
                "--quiet",
            ],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(out_path.read_text())
        assert "MWD" in data
        assert "Porosity" not in data

    def test_query_option_exports_a_ranked_subset(
        self, db_path: pathlib.Path, tmp_path: pathlib.Path
    ) -> None:
        """`--query` restricts the export to results matching that search, instead of a raw dump."""
        seed(
            db_path,
            [
                make_search_result(url="https://x.com/a", term="Porosity"),
                make_search_result(url="https://x.com/b", term="Unrelated"),
            ],
        )
        out_path = tmp_path / "out.json"
        result = CliRunner().invoke(
            cli,
            [
                "local",
                "export",
                "--db-path",
                str(db_path),
                "--config",
                "none",
                "--query",
                "porosity",
                "--save",
                str(out_path),
                "--quiet",
            ],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(out_path.read_text())
        assert "Porosity" in data
        assert "Unrelated" not in data


class TestLocalFlush:
    def test_deletes_every_term_and_embedding_with_yes(self, db_path: pathlib.Path) -> None:
        """`--yes` skips confirmation and clears both terms and embeddings."""
        seed(db_path, [make_search_result(url="https://x.com/a", term="Porosity")], embed=True)
        result = CliRunner().invoke(
            cli, ["local", "flush", "--db-path", str(db_path), "--config", "none", "--yes"]
        )
        assert result.exit_code == 0, result.output
        assert term_count(db_path) == 0
        assert vector_row_count(db_path) == 0

    def test_keeps_sync_history_intact(self, db_path: pathlib.Path) -> None:
        """`flush` clears term/vector data but leaves `metadata.json`'s sync history alone."""

        async def stamp() -> None:
            async with database(db_path) as db:
                metadata = Metadata.load(db.metadata_path)
                metadata.last_synced_at = "2024-01-01T00:00:00+00:00"
                metadata.save(db.metadata_path)

        asyncio.run(stamp())
        seed(db_path, [make_search_result(url="https://x.com/a", term="Porosity")])

        CliRunner().invoke(
            cli, ["local", "flush", "--db-path", str(db_path), "--config", "none", "--yes"]
        )

        async def check() -> str | None:
            async with database(db_path) as db:
                return Metadata.load(db.metadata_path).last_synced_at

        assert asyncio.run(check()) == "2024-01-01T00:00:00+00:00"

    def test_prompts_for_confirmation_without_yes(self, db_path: pathlib.Path) -> None:
        """Without `--yes`, a confirmation prompt is shown before flushing."""
        seed(db_path, [make_search_result(url="https://x.com/a", term="Porosity")])
        result = CliRunner().invoke(
            cli, ["local", "flush", "--db-path", str(db_path), "--config", "none"], input="y\n"
        )
        assert result.exit_code == 0, result.output
        assert term_count(db_path) == 0

    def test_aborts_if_confirmation_declined(self, db_path: pathlib.Path) -> None:
        """Declining the confirmation prompt aborts without flushing anything."""
        seed(db_path, [make_search_result(url="https://x.com/a", term="Porosity")])
        result = CliRunner().invoke(
            cli, ["local", "flush", "--db-path", str(db_path), "--config", "none"], input="n\n"
        )
        assert result.exit_code != 0
        assert term_count(db_path) == 1


class TestLocalReset:
    def test_resets_sync_history_too(self, db_path: pathlib.Path) -> None:
        """Unlike `flush`, `reset` also resets `metadata.json`'s sync history."""

        async def stamp() -> None:
            async with database(db_path) as db:
                metadata = Metadata.load(db.metadata_path)
                metadata.last_synced_at = "2024-01-01T00:00:00+00:00"
                metadata.save(db.metadata_path)

        asyncio.run(stamp())
        seed(db_path, [make_search_result(url="https://x.com/a", term="Porosity")])

        result = CliRunner().invoke(
            cli, ["local", "reset", "--db-path", str(db_path), "--config", "none", "--yes"]
        )
        assert result.exit_code == 0, result.output
        assert term_count(db_path) == 0

        async def check() -> str | None:
            async with database(db_path) as db:
                return Metadata.load(db.metadata_path).last_synced_at

        assert asyncio.run(check()) is None


class TestLocalDeleteEmbeddings:
    def test_deletes_every_embedding_with_yes(self, db_path: pathlib.Path) -> None:
        """`--yes` skips confirmation and deletes every stored embedding."""
        seed(db_path, [make_search_result(url="https://x.com/a", term="Porosity")], embed=True)
        assert vector_row_count(db_path) == 1

        result = CliRunner().invoke(
            cli,
            ["local", "delete-embeddings", "--db-path", str(db_path), "--config", "none", "--yes"],
        )
        assert result.exit_code == 0, result.output
        assert "Embeddings deleted." in result.output
        assert vector_row_count(db_path) == 0

    def test_does_not_touch_the_terms_themselves(self, db_path: pathlib.Path) -> None:
        """Terms remain in the database after their embeddings are deleted."""
        seed(db_path, [make_search_result(url="https://x.com/a", term="Porosity")], embed=True)
        CliRunner().invoke(
            cli,
            ["local", "delete-embeddings", "--db-path", str(db_path), "--config", "none", "--yes"],
        )
        assert term_count(db_path) == 1

    def test_urls_option_restricts_which_embeddings_are_deleted(
        self, db_path: pathlib.Path
    ) -> None:
        """`--urls` only deletes embeddings for the given comma-separated URLs."""
        seed(
            db_path,
            [
                make_search_result(url="https://x.com/a", term="Alpha"),
                make_search_result(url="https://x.com/b", term="Bravo"),
            ],
            embed=True,
        )
        assert vector_row_count(db_path) == 2

        result = CliRunner().invoke(
            cli,
            [
                "local",
                "delete-embeddings",
                "--db-path",
                str(db_path),
                "--config",
                "none",
                "--yes",
                "--urls",
                "https://x.com/a",
            ],
        )
        assert result.exit_code == 0, result.output
        assert vector_row_count(db_path) == 1

    def test_empty_database_is_a_no_op(self, db_path: pathlib.Path) -> None:
        """A database with no stored embeddings at all does not error."""
        result = CliRunner().invoke(
            cli,
            ["local", "delete-embeddings", "--db-path", str(db_path), "--config", "none", "--yes"],
        )
        assert result.exit_code == 0, result.output
        assert "Embeddings deleted." in result.output

    def test_prompts_for_confirmation_without_yes(self, db_path: pathlib.Path) -> None:
        """Without `--yes`, a confirmation prompt is shown before deleting."""
        seed(db_path, [make_search_result(url="https://x.com/a", term="Porosity")], embed=True)
        result = CliRunner().invoke(
            cli,
            ["local", "delete-embeddings", "--db-path", str(db_path), "--config", "none"],
            input="y\n",
        )
        assert result.exit_code == 0, result.output
        assert "Delete every stored embedding?" in result.output
        assert vector_row_count(db_path) == 0

    def test_aborts_if_confirmation_declined(self, db_path: pathlib.Path) -> None:
        """Declining the confirmation prompt aborts without deleting anything."""
        seed(db_path, [make_search_result(url="https://x.com/a", term="Porosity")], embed=True)
        result = CliRunner().invoke(
            cli,
            ["local", "delete-embeddings", "--db-path", str(db_path), "--config", "none"],
            input="n\n",
        )
        assert result.exit_code != 0
        assert vector_row_count(db_path) == 1


class TestLocalEmbed:
    def test_embeds_every_row_by_default(self, db_path: pathlib.Path) -> None:
        """With no options, embeds every locally stored row and reports the count."""
        seed(db_path, [make_search_result(url="https://x.com/a", term="Porosity")])
        result = CliRunner().invoke(
            cli, ["local", "embed", "--db-path", str(db_path), "--config", "none"]
        )
        assert result.exit_code == 0, result.output
        assert "Embedded 1 row(s)." in result.output

    def test_only_missing_skips_already_embedded_rows_by_default(
        self, db_path: pathlib.Path
    ) -> None:
        """A second run with no `--reembed` embeds nothing new."""
        seed(db_path, [make_search_result(url="https://x.com/a", term="Porosity")])
        runner = CliRunner()
        runner.invoke(cli, ["local", "embed", "--db-path", str(db_path), "--config", "none"])
        result = runner.invoke(
            cli, ["local", "embed", "--db-path", str(db_path), "--config", "none"]
        )
        assert result.exit_code == 0, result.output
        assert "Embedded 0 row(s)." in result.output

    def test_reembed_flag_recomputes_everything(self, db_path: pathlib.Path) -> None:
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

    def test_urls_option_restricts_which_rows_are_embedded(self, db_path: pathlib.Path) -> None:
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

    def test_empty_database_embeds_nothing(self, db_path: pathlib.Path) -> None:
        """An empty (never-synced/imported) database embeds `0` rows, no error."""
        result = CliRunner().invoke(
            cli, ["local", "embed", "--db-path", str(db_path), "--config", "none"]
        )
        assert result.exit_code == 0, result.output
        assert "Embedded 0 row(s)." in result.output

    def test_topic_option_restricts_which_rows_are_embedded(self, db_path: pathlib.Path) -> None:
        """`--topic` restricts embedding to rows filed under that topic."""
        seed(
            db_path,
            [
                make_search_result(url="https://x.com/a", term="Alpha", topic="Geology"),
                make_search_result(url="https://x.com/b", term="Bravo", topic="Drilling"),
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
                "--topic",
                "Geology",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "Embedded 1 row(s)." in result.output

    def test_topic_and_fuzzy_options_resolve_a_misspelled_topic(
        self, db_path: pathlib.Path
    ) -> None:
        """`--topic`/`--fuzzy` together resolve a misspelled topic against
        topics actually stored locally."""
        seed(
            db_path,
            [make_search_result(url="https://x.com/a", term="Alpha", topic="Geology")],
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
                "--topic",
                "geologyy",
                "--fuzzy",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "Embedded 1 row(s)." in result.output
