"""
`local.schema.initialize`: idempotent table/index/trigger/FTS creation.
"""

import aiosqlite
import pytest

from slb_glossary.errors import DatabaseError
from slb_glossary.local.schema import (
    SCHEMA_VERSION,
    get_schema_version,
    initialize,
    set_schema_version,
)

pytestmark = pytest.mark.unit


async def table_names(connection: aiosqlite.Connection) -> set[str]:
    cursor = await connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    rows = await cursor.fetchall()
    await cursor.close()
    return {row[0] for row in rows}


async def trigger_names(connection: aiosqlite.Connection) -> set[str]:
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
            assert "terms" in await table_names(connection)

    async def test_creates_fts_table(self, tmp_path):
        """Creates the `terms_fts` FTS5 virtual table."""
        async with aiosqlite.connect(tmp_path / "t.db") as connection:
            await initialize(connection)
            assert "terms_fts" in await table_names(connection)

    async def test_creates_sync_triggers(self, tmp_path):
        """Creates the `terms_ai`/`terms_ad`/`terms_au` FTS sync triggers."""
        async with aiosqlite.connect(tmp_path / "t.db") as connection:
            await initialize(connection)
            triggers = await trigger_names(connection)
            assert {"terms_ai", "terms_ad", "terms_au"} <= triggers

    async def test_is_idempotent_when_run_twice(self, tmp_path):
        """Calling `initialize` a second time on the same connection is a no-op, not an error."""
        async with aiosqlite.connect(tmp_path / "t.db") as connection:
            await initialize(connection)
            await initialize(connection)  # should not raise
            assert "terms" in await table_names(connection)

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

        async def broken_execute(statement, *args, **kwargs):
            if "VIRTUAL TABLE" in statement:
                raise aiosqlite.OperationalError("no such module: fts5")
            return await aiosqlite.Connection.execute(connection, statement, *args, **kwargs)

        async with aiosqlite.connect(tmp_path / "t.db") as connection:
            monkeypatch.setattr(connection, "execute", broken_execute)
            with pytest.raises(DatabaseError, match="FTS5"):
                await initialize(connection)

    async def test_stamps_current_schema_version(self, tmp_path):
        """`initialize` stamps `SCHEMA_VERSION` onto the database via `PRAGMA user_version`."""
        async with aiosqlite.connect(tmp_path / "t.db") as connection:
            await initialize(connection)
            assert await get_schema_version(connection) == SCHEMA_VERSION


@pytest.mark.anyio
class TestGetSetSchemaVersion:
    async def test_unstamped_database_reads_as_version_zero(self, tmp_path):
        """A brand new, never-`initialize`d database reads `0` (SQLite's own `user_version` default)."""
        async with aiosqlite.connect(tmp_path / "t.db") as connection:
            assert await get_schema_version(connection) == 0

    async def test_set_then_get_round_trips(self, tmp_path):
        """`set_schema_version` followed by `get_schema_version` returns what was set."""
        async with aiosqlite.connect(tmp_path / "t.db") as connection:
            await set_schema_version(connection, 7)
            assert await get_schema_version(connection) == 7

    async def test_version_persists_across_reconnects(self, tmp_path):
        """The stamped version is part of the database file itself, not connection-local state."""
        db_path = tmp_path / "t.db"
        async with aiosqlite.connect(db_path) as connection:
            await set_schema_version(connection, 3)
            await connection.commit()

        async with aiosqlite.connect(db_path) as reopened:
            assert await get_schema_version(reopened) == 3

    async def test_reads_directly_from_the_database_not_a_side_channel(self, tmp_path):
        """
        `get_schema_version` reflects `PRAGMA user_version` itself, so it's
        correct even with no `metadata.json` anywhere near it - unlike a
        version tracked only in a sidecar file, which a copied/moved `.db`
        file could easily leave behind.
        """
        db_path = tmp_path / "t.db"
        async with aiosqlite.connect(db_path) as connection:
            await initialize(connection)

        # No metadata.json was ever created; the version is still readable
        # straight from the database file that was reconnected to.
        async with aiosqlite.connect(db_path) as reopened:
            assert await get_schema_version(reopened) == SCHEMA_VERSION
