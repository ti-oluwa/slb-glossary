"""`local.schema.initialize`: idempotent table/index/trigger/FTS creation."""

import aiosqlite
import pytest

from slb_glossary.errors import DatabaseError
from slb_glossary.local.schema import initialize

pytestmark = pytest.mark.unit


async def _table_names(connection: aiosqlite.Connection) -> set[str]:
    cursor = await connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    rows = await cursor.fetchall()
    await cursor.close()
    return {row[0] for row in rows}


async def _trigger_names(connection: aiosqlite.Connection) -> set[str]:
    cursor = await connection.execute("SELECT name FROM sqlite_master WHERE type='trigger'")
    rows = await cursor.fetchall()
    await cursor.close()
    return {row[0] for row in rows}


@pytest.mark.anyio
class TestInitialize:
    async def test_creates_terms_table(self, tmp_path):
        """Creates the `terms` table."""
        async with aiosqlite.connect(tmp_path / "t.db") as connection:
            await initialize(connection)
            assert "terms" in await _table_names(connection)

    async def test_creates_fts_table(self, tmp_path):
        """Creates the `terms_fts` FTS5 virtual table."""
        async with aiosqlite.connect(tmp_path / "t.db") as connection:
            await initialize(connection)
            assert "terms_fts" in await _table_names(connection)

    async def test_creates_sync_triggers(self, tmp_path):
        """Creates the `terms_ai`/`terms_ad`/`terms_au` FTS sync triggers."""
        async with aiosqlite.connect(tmp_path / "t.db") as connection:
            await initialize(connection)
            triggers = await _trigger_names(connection)
            assert {"terms_ai", "terms_ad", "terms_au"} <= triggers

    async def test_is_idempotent_when_run_twice(self, tmp_path):
        """Calling `initialize` a second time on the same connection is a no-op, not an error."""
        async with aiosqlite.connect(tmp_path / "t.db") as connection:
            await initialize(connection)
            await initialize(connection)  # should not raise
            assert "terms" in await _table_names(connection)

    async def test_fts_trigger_keeps_index_in_sync_on_insert(self, tmp_path):
        """Inserting into `terms` is mirrored into `terms_fts` via the AFTER INSERT trigger."""
        async with aiosqlite.connect(tmp_path / "t.db") as connection:
            await initialize(connection)
            await connection.execute(
                "INSERT INTO terms (url, term, definition, topic, fetched_at) "
                "VALUES ('u1', 'Porosity', 'A rock property', '', '2024-01-01')"
            )
            await connection.commit()
            cursor = await connection.execute(
                "SELECT term FROM terms_fts WHERE terms_fts MATCH 'porosity'"
            )
            rows = await cursor.fetchall()
            await cursor.close()
            assert [row[0] for row in rows] == ["Porosity"]

    async def test_fts_trigger_keeps_index_in_sync_on_delete(self, tmp_path):
        """Deleting a row removes its entry from `terms_fts` via the AFTER DELETE trigger."""
        async with aiosqlite.connect(tmp_path / "t.db") as connection:
            await initialize(connection)
            await connection.execute(
                "INSERT INTO terms (url, term, definition, topic, fetched_at) "
                "VALUES ('u1', 'Porosity', 'A rock property', '', '2024-01-01')"
            )
            await connection.execute("DELETE FROM terms WHERE url = 'u1'")
            await connection.commit()
            cursor = await connection.execute(
                "SELECT term FROM terms_fts WHERE terms_fts MATCH 'porosity'"
            )
            rows = await cursor.fetchall()
            await cursor.close()
            assert rows == []

    async def test_foreign_keys_pragma_is_enabled(self, tmp_path):
        """`PRAGMA foreign_keys` is turned on."""
        async with aiosqlite.connect(tmp_path / "t.db") as connection:
            await initialize(connection)
            cursor = await connection.execute("PRAGMA foreign_keys")
            (value,) = await cursor.fetchone()
            await cursor.close()
            assert value == 1

    async def test_raises_database_error_if_fts5_unavailable(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ):
        """An `OperationalError` creating the FTS5 table is wrapped in `DatabaseError`."""

        async def _broken_execute(statement, *args, **kwargs):
            if "VIRTUAL TABLE" in statement:
                raise aiosqlite.OperationalError("no such module: fts5")
            return await aiosqlite.Connection.execute(connection, statement, *args, **kwargs)

        async with aiosqlite.connect(tmp_path / "t.db") as connection:
            monkeypatch.setattr(connection, "execute", _broken_execute)
            with pytest.raises(DatabaseError, match="FTS5"):
                await initialize(connection)
