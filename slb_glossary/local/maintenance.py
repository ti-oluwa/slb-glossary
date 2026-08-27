"""Flushing and resetting the local database."""

import logging

from slb_glossary.local import vector
from slb_glossary.local.types import Database, Metadata

logger = logging.getLogger(__name__)

__all__ = ["flush", "reset"]


async def flush(db: Database) -> None:
    """
    Delete every stored term and vector, keeping the schema and sync history.

    Use this to clear stale data while keeping `metadata.json`'s sync
    timestamps intact e.g. right before a fresh `slb_glossary.local.sync_all`.
    Use `reset` instead to also forget the local database's sync history.

    Also checkpoints and truncates the database's `-wal` file (see
    `slb_glossary.local.open_db`'s docstring on why it has one) as part of
    the `VACUUM`, so a freshly flushed database is left with little or
    nothing outstanding in `-wal`/`-shm`. Handy if you're about to copy
    or back up `db.db_path` right after.

    :param db: The local database to clear.
    """
    await vector.clear(db)
    await db.connection.execute("DELETE FROM terms")
    await db.connection.commit()
    await db.connection.execute("VACUUM")
    await db.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    logger.info("Flushed local glossary database at %s", db.db_path)


async def reset(db: Database) -> None:
    """
    Flush the local database and reset its `metadata.json` (sync history) to defaults.

    Includes everything `flush` does, including the `-wal`/`-shm` checkpoint/truncate.

    :param db: The local database to reset.
    """
    await flush(db)
    Metadata().save(db.metadata_path)
    logger.info("Reset local glossary database at %s", db.db_path)
