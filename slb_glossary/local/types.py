"""Data structures for the local search database."""

import contextlib
import dataclasses
import json
import os
import pathlib
import sys
import tempfile

import aiosqlite

if sys.version_info >= (3, 11):
    from typing import Self
else:
    from typing_extensions import Self

__all__ = ["Database", "Metadata"]


@dataclasses.dataclass(slots=True, kw_only=True)
class Database:
    """
    An open connection to the local search database.

    Obtain one with `slb_glossary.local.open_db`.

    Runs in WAL journal mode, so while open it has two sidecar files next
    to `db_path`; `<db_path>-wal` and `<db_path>-shm`. Moving or copying
    `db_path` by hand (rather than through `slb_glossary.local`) means
    moving those two along with it, and `metadata_path` too.
    """

    connection: aiosqlite.Connection
    """The open `aiosqlite` connection to the SQLite database file."""

    db_path: pathlib.Path
    """Path to the SQLite database file on disk."""

    metadata_path: pathlib.Path
    """Path to this database's `metadata.json` sync/provenance file."""


@dataclasses.dataclass(slots=True, kw_only=True)
class Metadata:
    """Sync/provenance bookkeeping for a `slb_glossary.local` database."""

    schema_version: int = 1
    """Local database schema version. See `slb_glossary.local.schema.SCHEMA_VERSION`."""

    last_synced_at: str | None = None
    """ISO-8601 UTC timestamp of the last successful sync, or `None` if never synced."""

    last_sync_language: str | None = None
    """Glossary language edition (`"en"`/`"es"`) the last sync fetched from."""

    term_count: int = 0
    """Total number of terms currently stored locally, as of the last sync."""

    topics: dict[str, int] = dataclasses.field(default_factory=dict)
    """Mapping of topic name to term count, as of the last sync."""

    @classmethod
    def load(cls, path: pathlib.Path) -> Self:
        """
        Load metadata from `path`, or return fresh defaults if it does not exist.

        :param path: Path to a `metadata.json` file.
        :return: The loaded (or default) `Metadata`.
        """
        if not path.exists():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        known_fields = {f.name for f in dataclasses.fields(cls)}
        return cls(**{key: value for key, value in data.items() if key in known_fields})

    def save(self, path: pathlib.Path) -> None:
        """
        Write this metadata to `path` as JSON, atomically.

        Written to a temporary file in the same directory first, then
        moved into place with `os.replace`, atomic on both POSIX and
        Windows. This is so a reader (or a process crash) never sees a partially
        written, truncated `path`; it's always either the previous
        complete content or the new complete content.

        :param path: Destination path. Its parent directory is created if
            it does not exist.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(dataclasses.asdict(self), indent=2) + "\n"
        fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
                tmp_file.write(content)
            os.replace(tmp_path, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.remove(tmp_path)
            raise
