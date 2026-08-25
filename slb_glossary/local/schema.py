"""SQL schema and initialization for the local search database."""

import aiosqlite

from slb_glossary.errors import DatabaseError

__all__ = ["SCHEMA_VERSION", "initialize"]

SCHEMA_VERSION = 1
"""
Local database schema version. Bumped (by developer) alongside any DDL change below
that isn't purely additive, so `slb_glossary.local.types.Metadata` can eventually 
gate migrations on it.
"""

CREATE_TERMS_TABLE = """
CREATE TABLE IF NOT EXISTS terms (
    url TEXT PRIMARY KEY,
    term TEXT NOT NULL,
    definition TEXT,
    grammatical_label TEXT,
    topic TEXT,
    language TEXT NOT NULL DEFAULT 'en',
    image TEXT,
    image_caption TEXT,
    related_json TEXT,
    source TEXT NOT NULL DEFAULT 'glossary',
    fetched_at TEXT NOT NULL
)
"""

CREATE_TERMS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_terms_term ON terms(term)",
    "CREATE INDEX IF NOT EXISTS idx_terms_topic ON terms(topic)",
    "CREATE INDEX IF NOT EXISTS idx_terms_language ON terms(language)",
]

CREATE_FTS_TABLE = """
CREATE VIRTUAL TABLE IF NOT EXISTS terms_fts USING fts5(
    term,
    definition,
    topic,
    content='terms',
    content_rowid='rowid'
)
"""

# Standard FTS5 "external content" sync triggers: terms_fts stores no text
# of its own, so every write to `terms` is mirrored into it by rowid. The
# 'delete' sentinel row on UPDATE/DELETE is FTS5's own convention for
# removing an indexed row from an external-content table.
CREATE_FTS_TRIGGERS = [
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
    Create every table, index, and trigger the local database needs, if missing.

    Safe to call every time a database is opened as every statement here is
    `IF NOT EXISTS`, so this is a no-op on an already-initialized database.

    :param connection: An open `aiosqlite` connection.
    :raises DatabaseError: If the installed SQLite build lacks the FTS5
        extension, which `slb_glossary.local.api.search` requires.
    """
    await connection.execute("PRAGMA foreign_keys = ON")
    await connection.execute(CREATE_TERMS_TABLE)
    for statement in CREATE_TERMS_INDEXES:
        await connection.execute(statement)

    try:
        await connection.execute(CREATE_FTS_TABLE)
    except aiosqlite.OperationalError as exc:
        raise DatabaseError(
            "The installed SQLite build has no FTS5 extension, which "
            "`slb_glossary.local` requires for full-text search. Rebuild "
            "Python's `sqlite3` module against a SQLite build with FTS5 enabled."
        ) from exc

    for statement in CREATE_FTS_TRIGGERS:
        await connection.execute(statement)

    await connection.commit()
