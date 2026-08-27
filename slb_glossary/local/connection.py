"""Opening, closing, and context-managing the local search database."""

import contextlib
import logging
import pathlib
import typing

import aiosqlite

from slb_glossary.local.schema import SCHEMA_VERSION, initialize
from slb_glossary.local.types import Database, Metadata
from slb_glossary.paths import default_db_path, default_metadata_path

logger = logging.getLogger(__name__)

__all__ = ["close_db", "database", "open_db"]


def _resolve_metadata_path(
    db_path: pathlib.Path, metadata_path: str | pathlib.Path | None, db_path_was_given: bool
) -> pathlib.Path:
    """Work out where a database's `metadata.json` lives, given its own path."""
    if metadata_path is not None:
        return pathlib.Path(metadata_path)
    if not db_path_was_given:
        return default_metadata_path()
    return db_path.with_name(db_path.stem + ".metadata.json")


async def _enable_wal(connection: aiosqlite.Connection) -> str:
    """
    Switch `connection` to WAL journaling and return the mode SQLite actually applied.

    WAL keeps a database usable by readers while a write is in progress
    (readers no longer block on writers), at the cost of two sidecar files
    living next to the main database file for as long as it's in active
    use: `<db>-wal` (the write-ahead log itself) and `<db>-shm` (the
    shared-memory index into it). Both are ordinary SQLite bookkeeping
    files, not optional extras. See `open_db`'s docstring for what that
    means for backing up or moving a database.

    :param connection: An open `aiosqlite` connection, not yet used for
        any other statement.
    :return: The journal mode SQLite reports after the request, e.g.
        `"wal"`. May not always return what was asked for as some
        filesystems (network shares in particular) don't support WAL
        and SQLite silently falls back to another mode instead of erroring.
    """
    cursor = await connection.execute("PRAGMA journal_mode=WAL")
    row = await cursor.fetchone()
    await cursor.close()
    mode = str(row[0]) if row else "unknown"
    if mode.lower() != "wal":
        logger.warning(
            "Requested WAL journal mode but SQLite applied %r instead "
            "(common on filesystems, e.g. some network shares, that don't "
            "support WAL). Continuing with that mode.",
            mode,
        )
    return mode


def _discard_on_mismatch(db_path: pathlib.Path, metadata_path: pathlib.Path) -> Metadata | None:
    """
    Delete an existing database/metadata pair if its schema version doesn't
    match the current one.

    There's currently no migration path between schema versions, so a mismatch
    means the stored terms and embeddings aren't safely readable under
    the current code. The local database is a disposable cache of
    glossary content (see `slb_glossary.local`'s own module docstring),
    so we just discard it and let it get recreated fresh, not to
    fail outright. Its sync history is lost along with it; run a sync
    again afterward to repopulate it.

    :param db_path: Path to the database file.
    :param metadata_path: Path to its `metadata.json`.
    :return: The metadata that was loaded (and, if mismatched, just
        discarded), or `None` if there was no existing metadata file
        to check.
    """
    if not metadata_path.exists():
        return None

    metadata = Metadata.load(metadata_path)
    if metadata.schema_version == SCHEMA_VERSION:
        return metadata

    logger.warning(
        "Local database at %s uses schema version %d; the current version "
        "is %d, and there's no migration path between them. Discarding its "
        "stored terms and embeddings and recreating it fresh. Sync again "
        "to repopulate it.",
        db_path,
        metadata.schema_version,
        SCHEMA_VERSION,
    )
    for suffix in ("", "-wal", "-shm"):
        db_path.with_name(db_path.name + suffix).unlink(missing_ok=True)
    metadata_path.unlink(missing_ok=True)
    return None


async def open_db(
    path: str | pathlib.Path | None = None,
    *,
    metadata_path: str | pathlib.Path | None = None,
) -> Database:
    """
    Open (creating if needed) the local search database at `path`.

    The database runs in WAL journal mode, which adds
    two sidecar files next to `path` while it's in use: `<path>-wal` and
    `<path>-shm`. **If you move, copy, or back up the database file
    yourself** (outside of `slb_glossary`), move those two alongside it.

    A `.db` file copied on its own while its `-wal` still holds
    unflushed writes is missing data. Move `metadata_path` (or its
    default, `<path>.metadata.json` / `metadata.json`) along with it too;
    it's a separate file and won't follow the `.db` file automatically.

    `flush`/`reset` checkpoint and truncate the WAL file as part of what
    they do, so a freshly flushed/reset database has little or nothing
    outstanding in `-wal` to lose. But closing the database first
    (`close_db`, or exiting a `database(...)` block) is still the simplest
    way to guarantee a clean, single-file snapshot, since SQLite folds the
    WAL back into the main file and removes the sidecar files itself once
    the last connection to it closes.

    An existing database built under an older schema version is
    discarded and recreated fresh, its stored terms/embeddings gone with
    it (sync again to repopulate it). There's no currently migration path
    between schema versions.

    :param path: Path to the SQLite database file. Defaults to
        `slb_glossary.paths.default_db_path()` (the OS-appropriate user
        data directory. See `slb_glossary.paths`).
    :param metadata_path: Path to the metadata JSON file. Defaults to
        `slb_glossary.paths.default_metadata_path()` when `path` is also
        left at its default, or `<path stem>.metadata.json` next to a
        custom `path`.
    :return: An open `Database`. Close it with `close_db`, or use the
        `database` context manager instead of calling this function directly.
    :raises DatabaseError: If the installed SQLite build lacks FTS5.
    """
    resolved_db_path = pathlib.Path(path) if path is not None else default_db_path()
    resolved_db_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_metadata_path = _resolve_metadata_path(
        db_path=resolved_db_path,
        metadata_path=metadata_path,
        db_path_was_given=path is not None,
    )
    _discard_on_mismatch(resolved_db_path, resolved_metadata_path)

    connection = await aiosqlite.connect(resolved_db_path)
    connection.row_factory = aiosqlite.Row
    journal_mode = await _enable_wal(connection)
    await initialize(connection)

    if not resolved_metadata_path.exists():
        Metadata().save(resolved_metadata_path)

    logger.info(
        "Opened local glossary database at %s (journal_mode=%s)", resolved_db_path, journal_mode
    )
    return Database(
        connection=connection,
        db_path=resolved_db_path,
        metadata_path=resolved_metadata_path,
    )


async def close_db(db: Database) -> None:
    """
    Close `db`'s connection.

    Safe to call more than once; later calls are no-ops.

    If this is the last open connection to `db.db_path`,
    SQLite folds its `-wal` sidecar file back into the main database
    file and removes both `-wal`/`-shm` as part of closing.

    A database closed this way is a clean, single-file snapshot,
    safe to copy or back up as just `db_path` (plus `metadata_path`)
    with nothing else to bring along.

    :param db: The local database to close.
    """
    with contextlib.suppress(Exception):
        await db.connection.close()
    logger.info("Closed local glossary database at %s", db.db_path)


@contextlib.asynccontextmanager
async def database(
    path: str | pathlib.Path | None = None,
    *,
    metadata_path: str | pathlib.Path | None = None,
) -> typing.AsyncIterator[Database]:
    """
    Open a `Database` for the duration of an `async with` block.

    ```python
    async with database(...) as db:
        async for result in search(db, "porosity"):
            print(result)
    ```

    Arguments are the same as `open_db`. The database is always closed on
    exit, including when the block raises.
    """
    db = await open_db(path, metadata_path=metadata_path)
    try:
        yield db
    finally:
        await close_db(db)
