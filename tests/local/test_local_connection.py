"""`local.connection.open_db`/`close_db`/`database`: WAL mode, schema-mismatch
discard-and-recreate, metadata file creation, and cleanup on exit."""

import pathlib

import aiosqlite
import pytest

from slb_glossary.local.connection import close_db, database, open_db
from slb_glossary.local.schema import SCHEMA_VERSION
from slb_glossary.local.types import Metadata

pytestmark = pytest.mark.unit


@pytest.mark.anyio
class TestOpenDb:
    async def test_creates_db_file_and_parent_directories(self, tmp_path: pathlib.Path):
        """`open_db` creates the database file and any missing parent directories."""
        db_path = tmp_path / "nested" / "dir" / "glossary.db"
        db = await open_db(db_path)
        try:
            assert db_path.exists()
        finally:
            await close_db(db)

    async def test_uses_wal_journal_mode(self, tmp_path: pathlib.Path):
        """The opened connection runs in WAL journal mode."""
        db = await open_db(tmp_path / "t.db")
        try:
            cursor = await db.connection.execute("PRAGMA journal_mode")
            (mode,) = await cursor.fetchone()
            await cursor.close()
            assert mode.lower() == "wal"
        finally:
            await close_db(db)

    async def test_creates_metadata_file_with_defaults_if_missing(self, tmp_path: pathlib.Path):
        """A fresh database gets a `metadata.json` with default `Metadata` fields."""
        db_path = tmp_path / "t.db"
        db = await open_db(db_path)
        try:
            assert db.metadata_path.exists()
            metadata = Metadata.load(db.metadata_path)
            assert metadata == Metadata()
        finally:
            await close_db(db)

    async def test_does_not_overwrite_existing_metadata_file(self, tmp_path: pathlib.Path):
        """An existing `metadata.json` (matching schema version) is left untouched."""
        db_path = tmp_path / "t.db"
        metadata_path = tmp_path / "t.metadata.json"
        existing = Metadata(term_count=99)
        existing.save(metadata_path)

        db = await open_db(db_path, metadata_path=metadata_path)
        try:
            assert Metadata.load(db.metadata_path).term_count == 99
        finally:
            await close_db(db)

    async def test_default_metadata_path_next_to_custom_db_path(self, tmp_path: pathlib.Path):
        """With a custom `path` but no `metadata_path`, metadata defaults to `<stem>.metadata.json`."""
        db_path = tmp_path / "custom.db"
        db = await open_db(db_path)
        try:
            assert db.metadata_path == tmp_path / "custom.metadata.json"
        finally:
            await close_db(db)

    async def test_default_paths_used_when_path_is_none(
        self, tmp_data_dir: pathlib.Path
    ):
        """With no `path` given, both db and metadata paths resolve to the app's default data dir."""
        db = await open_db()
        try:
            assert db.db_path.parent == tmp_data_dir
            assert db.metadata_path.parent == tmp_data_dir
        finally:
            await close_db(db)

    async def test_discards_and_recreates_on_schema_version_mismatch(
        self, tmp_path: pathlib.Path
    ):
        """A database whose metadata reports an older schema version is discarded and rebuilt."""
        db_path = tmp_path / "t.db"
        metadata_path = tmp_path / "t.metadata.json"

        # Simulate an old-schema database: create some sidecar files and
        # metadata reporting a mismatched schema version.
        db_path.write_text("not a real sqlite file, just needs to exist", encoding="utf-8")
        Metadata(schema_version=SCHEMA_VERSION + 1, term_count=123).save(metadata_path)

        db = await open_db(db_path, metadata_path=metadata_path)
        try:
            # The corrupt/mismatched file should have been unlinked and a
            # fresh, valid database opened in its place.
            metadata = Metadata.load(db.metadata_path)
            assert metadata.schema_version == SCHEMA_VERSION
            assert metadata.term_count == 0
        finally:
            await close_db(db)

    async def test_keeps_database_on_matching_schema_version(self, tmp_path: pathlib.Path):
        """A database whose metadata matches the current schema version is opened as-is."""
        db_path = tmp_path / "t.db"
        metadata_path = tmp_path / "t.metadata.json"

        db = await open_db(db_path, metadata_path=metadata_path)
        await db.connection.execute(
            "INSERT INTO terms (url, term, definition, topic, fetched_at) "
            "VALUES ('u1', 'Porosity', 'def', '', '2024-01-01')"
        )
        await db.connection.commit()
        await close_db(db)

        reopened = await open_db(db_path, metadata_path=metadata_path)
        try:
            cursor = await reopened.connection.execute("SELECT COUNT(*) FROM terms")
            (count,) = await cursor.fetchone()
            await cursor.close()
            assert count == 1
        finally:
            await close_db(reopened)

    async def test_raises_database_error_when_fts5_unavailable(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ):
        """`open_db` propagates `initialize`'s `DatabaseError` when FTS5 is unavailable.

        Note: `open_db` has no try/except around `initialize`, so on this
        path the just-opened `aiosqlite` connection is never closed - a
        real (if minor, since the process typically exits soon after)
        resource leak. This test closes the leaked connection itself via
        `aiosqlite.connect`'s captured return value, purely so the leak
        doesn't produce noisy background-thread warnings in this test run.
        """
        from slb_glossary.errors import DatabaseError

        async def _broken_initialize(connection):
            raise DatabaseError("no FTS5")

        monkeypatch.setattr("slb_glossary.local.connection.initialize", _broken_initialize)

        opened_connections = []
        real_connect = aiosqlite.connect

        def _tracking_connect(*args, **kwargs):
            connection = real_connect(*args, **kwargs)
            opened_connections.append(connection)
            return connection

        monkeypatch.setattr(
            "slb_glossary.local.connection.aiosqlite.connect", _tracking_connect
        )
        try:
            with pytest.raises(DatabaseError):
                await open_db(tmp_path / "t.db")
        finally:
            for connection in opened_connections:
                await connection.close()


@pytest.mark.anyio
class TestCloseDb:
    async def test_closes_the_connection(self, tmp_path: pathlib.Path):
        """After `close_db`, further use of the connection raises."""
        db = await open_db(tmp_path / "t.db")
        await close_db(db)
        with pytest.raises(ValueError, match="no active connection"):
            await db.connection.execute("SELECT 1")

    async def test_safe_to_call_twice(self, tmp_path: pathlib.Path):
        """Calling `close_db` a second time is a no-op, not an error."""
        db = await open_db(tmp_path / "t.db")
        await close_db(db)
        await close_db(db)  # should not raise

    async def test_folds_wal_back_into_main_file_on_close(self, tmp_path: pathlib.Path):
        """Once the last connection closes, the `-wal`/`-shm` sidecar files are removed."""
        db_path = tmp_path / "t.db"
        db = await open_db(db_path)
        await db.connection.execute(
            "INSERT INTO terms (url, term, definition, topic, fetched_at) "
            "VALUES ('u1', 'Porosity', 'def', '', '2024-01-01')"
        )
        await db.connection.commit()
        await close_db(db)

        assert not db_path.with_name(db_path.name + "-wal").exists()
        assert not db_path.with_name(db_path.name + "-shm").exists()


@pytest.mark.anyio
class TestDatabaseContextManager:
    async def test_yields_an_open_database(self, tmp_path: pathlib.Path):
        """The `async with database(...)` block yields a usable, open `Database`."""
        async with database(tmp_path / "t.db") as db:
            cursor = await db.connection.execute("SELECT 1")
            (value,) = await cursor.fetchone()
            await cursor.close()
            assert value == 1

    async def test_closes_on_normal_exit(self, tmp_path: pathlib.Path):
        """The database is closed once the `async with` block exits normally."""
        db_path = tmp_path / "t.db"
        async with database(db_path) as db:
            pass
        with pytest.raises(ValueError, match="no active connection"):
            await db.connection.execute("SELECT 1")

    async def test_closes_on_exception_inside_block(self, tmp_path: pathlib.Path):
        """The database is still closed if the block raises."""
        db_path = tmp_path / "t.db"
        held_db = None
        with pytest.raises(ValueError, match="boom"):
            async with database(db_path) as db:
                held_db = db
                raise ValueError("boom")
        assert held_db is not None
        with pytest.raises(ValueError, match="no active connection"):
            await held_db.connection.execute("SELECT 1")
