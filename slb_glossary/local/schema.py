"""SQL schema and initialization for the local search database."""

import aiosqlite

from slb_glossary.errors import DatabaseError

__all__ = ["SCHEMA_VERSION", "get_schema_version", "initialize", "set_schema_version"]

SCHEMA_VERSION = 1
"""
Local database schema version. Bumped alongside any DDL change below
that is not purely additive.

`slb_glossary.local.open_db` compares this against a database's stored
`slb_glossary.local.types.Metadata.schema_version` and discards/recreates
it on a mismatch, since there's currently no migration path between versions.

`get_schema_version`/`set_schema_version` read/write the same version
directly on a database's own connection (via `PRAGMA user_version`),
independent of `Metadata`.
"""


async def get_schema_version(connection: aiosqlite.Connection) -> int:
    """
    Read the schema version stamped on `connection`'s own database file.

    Backed by SQLite's built-in `PRAGMA user_version` (a plain integer
    the database file itself carries, defaulting to `0` for a database
    that's never had it set), not what `slb_glossary.local.types.Metadata` holds.
    So this reflects what's actually inside the `.db` file even if its
    `metadata.json` sidecar were missing, stale, or edited by hand.

    :param connection: An open `aiosqlite` connection.
    :return: The stamped schema version, or `0` for a database that's
        never had one set (e.g. one created before this existed, or a
        brand new file `initialize` hasn't stamped yet).
    """
    cursor = await connection.execute("PRAGMA user_version")
    row = await cursor.fetchone()
    await cursor.close()
    return int(row[0]) if row is not None else 0


async def set_schema_version(connection: aiosqlite.Connection, version: int) -> None:
    """
    Stamp `version` onto `connection`'s own database file, via `PRAGMA user_version`.

    This does not commit. `initialize` folds this into its own final commit,
    and a caller doing this outside `initialize` should do the same.

    :param connection: An open `aiosqlite` connection.
    :param version: The schema version to stamp. This should always be
        an internally controlled constant (`SCHEMA_VERSION`), not
        user input. `PRAGMA` does not support bound parameters, so this
        interpolates `version` directly.
    """
    await connection.execute(f"PRAGMA user_version = {int(version)}")


TERMS_TABLE_CREATE_STATEMENT = """
CREATE TABLE IF NOT EXISTS terms (
    url TEXT NOT NULL,
    term TEXT NOT NULL,
    definition TEXT,
    grammatical_label TEXT,
    topic TEXT NOT NULL DEFAULT '',
    language TEXT NOT NULL DEFAULT 'en',
    image TEXT,
    image_caption TEXT,
    related_json TEXT,
    source TEXT NOT NULL DEFAULT 'glossary',
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (url, topic)
)
"""
"""
A glossary page can hold several definitions of the same term, one per
topic it's filed under all at the same `url`. Keying on `url` alone would let the second
definition upserted silently overwrite the first, so `url` and `topic`
together are the real identity of one definition.

`topic` is `NOT NULL DEFAULT ''` rather than nullable so two untopicked
definitions at the same `url` (a page with only one, topic-less section)
still collide correctly on upsert instead of comparing unequal as SQLite
(like standard SQL) never considers `NULL = NULL` true, so two `NULL`
topics in a composite primary key would not conflict with each other at
all and would just accumulate as duplicate rows on every re-sync. The
empty string is mapped back to `None` at the Python boundary, so 
`SearchResult.topic` still reads as `None` for a topic-less definition.
"""

TERMS_INDEXES_CREATE_STATEMENTS = [
    "CREATE INDEX IF NOT EXISTS idx_terms_term ON terms(term)",
    "CREATE INDEX IF NOT EXISTS idx_terms_topic ON terms(topic)",
    "CREATE INDEX IF NOT EXISTS idx_terms_language ON terms(language)",
]

FTS_TABLE_CREATE_STATEMENT = """
CREATE VIRTUAL TABLE IF NOT EXISTS terms_fts USING fts5(
    term,
    definition,
    topic,
    content='terms',
    content_rowid='rowid'
)
"""

# Standard FTS5 external-content sync triggers. `terms_fts` stores no text
# of its own, so every write to `terms` is mirrored into it by rowid. The
# 'delete' sentinel row on UPDATE/DELETE is FTS5's own convention for
# removing an indexed row from an external-content table.
FTS_TRIGGERS_CREATE_STATEMENTS = [
    """
    CREATE TRIGGER IF NOT EXISTS terms_ai AFTER INSERT ON terms BEGIN
        INSERT INTO terms_fts(rowid, term, definition, topic)
        VALUES (new.rowid, new.term, new.definition, new.topic);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS terms_ad AFTER DELETE ON terms BEGIN
        INSERT INTO terms_fts(terms_fts, rowid, term, definition, topic)
        VALUES ('delete', old.rowid, old.term, old.definition, old.topic);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS terms_au AFTER UPDATE ON terms BEGIN
        INSERT INTO terms_fts(terms_fts, rowid, term, definition, topic)
        VALUES ('delete', old.rowid, old.term, old.definition, old.topic);
        INSERT INTO terms_fts(rowid, term, definition, topic)
        VALUES (new.rowid, new.term, new.definition, new.topic);
    END
    """,
]


async def initialize(connection: aiosqlite.Connection) -> None:
    """
    Create every table, index, and trigger the local database needs, if missing,
    and stamp it with `SCHEMA_VERSION` (see `set_schema_version`).

    Safe to call every time a database is opened as every statement here is
    `IF NOT EXISTS`, so this is a no-op on an already-initialized database
    (re-stamping the same `SCHEMA_VERSION` every time is harmless).

    :param connection: An open `aiosqlite` connection.
    :raises DatabaseError: If the installed SQLite build lacks the FTS5
        extension, which `slb_glossary.local.api.search` requires.
    """
    await connection.execute("PRAGMA foreign_keys = ON")
    await connection.execute(TERMS_TABLE_CREATE_STATEMENT)
    for statement in TERMS_INDEXES_CREATE_STATEMENTS:
        await connection.execute(statement)

    try:
        await connection.execute(FTS_TABLE_CREATE_STATEMENT)
    except aiosqlite.OperationalError as exc:
        raise DatabaseError(
            "The installed SQLite build has no FTS5 extension, which "
            "`slb_glossary.local` requires for full-text search. Rebuild "
            "Python's `sqlite3` module against a SQLite build with FTS5 enabled."
        ) from exc

    for statement in FTS_TRIGGERS_CREATE_STATEMENTS:
        await connection.execute(statement)

    await set_schema_version(connection, SCHEMA_VERSION)
    await connection.commit()
