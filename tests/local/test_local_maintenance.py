"""Tests for `local.maintenance`: `flush`, `reset`."""

import typing

import pytest

from slb_glossary.local.api import count, upsert_results
from slb_glossary.local.maintenance import flush, reset
from slb_glossary.local.types import Database, Metadata
from slb_glossary.local.vector import VECTOR_TABLE, embed_terms
from tests.factories import make_search_result
from tests.mocks import MockEmbeddings

pytestmark = pytest.mark.unit


@pytest.mark.anyio
class TestFlush:
    async def test_deletes_every_term(self, db: Database) -> None:
        """Every stored term is gone after `flush`."""
        await upsert_results(db, [make_search_result(url="https://x.com/a", term="Porosity")])
        await flush(db)
        assert await count(db) == 0

    async def test_deletes_every_embedding(
        self, db: Database, mock_embeddings: MockEmbeddings
    ) -> None:
        """Every stored embedding is gone after `flush`, not just the terms."""
        await upsert_results(db, [make_search_result(url="https://x.com/a", term="Porosity")])
        await embed_terms(db)
        await flush(db)
        async with db.connection.execute(f"SELECT COUNT(*) AS n FROM {VECTOR_TABLE}") as cursor:
            row = await cursor.fetchone()
        assert row["n"] == 0

    async def test_keeps_metadata_json_intact(self, db: Database) -> None:
        """`flush` clears term/vector data but leaves `metadata.json`'s sync history alone."""
        metadata = Metadata.load(db.metadata_path)
        metadata.last_synced_at = "2024-01-01T00:00:00+00:00"
        metadata.save(db.metadata_path)

        await upsert_results(db, [make_search_result(url="https://x.com/a", term="Porosity")])
        await flush(db)

        assert Metadata.load(db.metadata_path).last_synced_at == "2024-01-01T00:00:00+00:00"

    async def test_clearing_terms_and_vectors_is_one_atomic_unit(
        self, db: Database, mock_embeddings: MockEmbeddings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        If the `DELETE FROM terms` half fails, the vector-clearing half
        (already issued moments earlier in the same block) is rolled back
        too, rather than leaving vectors cleared with their terms still present.
        """
        await upsert_results(db, [make_search_result(url="https://x.com/a", term="Porosity")])
        await embed_terms(db)

        original_execute = db.connection.execute

        def flaky_execute(sql: str, *args: typing.Any, **kwargs: typing.Any) -> typing.Any:
            if sql.strip() == "DELETE FROM terms":
                raise RuntimeError("simulated failure")
            return original_execute(sql, *args, **kwargs)

        monkeypatch.setattr(db.connection, "execute", flaky_execute)

        with pytest.raises(RuntimeError, match="simulated failure"):
            await flush(db)

        monkeypatch.undo()
        assert await count(db) == 1  # term still present
        async with db.connection.execute(f"SELECT COUNT(*) AS n FROM {VECTOR_TABLE}") as cursor:
            row = await cursor.fetchone()
        assert row["n"] == 1  # vector still present too, not orphan-cleared


@pytest.mark.anyio
class TestReset:
    async def test_flushes_and_resets_metadata(self, db: Database) -> None:
        """`reset` does everything `flush` does, and also resets `metadata.json` to defaults."""
        metadata = Metadata.load(db.metadata_path)
        metadata.last_synced_at = "2024-01-01T00:00:00+00:00"
        metadata.save(db.metadata_path)

        await upsert_results(db, [make_search_result(url="https://x.com/a", term="Porosity")])
        await reset(db)

        assert await count(db) == 0
        assert Metadata.load(db.metadata_path).last_synced_at is None
