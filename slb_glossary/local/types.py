"""Data structures for the local search database."""

import dataclasses
import json
import pathlib
import sys

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

    Obtain one with `slb_glossary.local.open_db`/`database`.

    Runs in WAL journal mode, so while open it has two sidecar files next
    to `db_path`: `<db_path>-wal` and `<db_path>-shm`. Moving or copying
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
        Load metadata from `path`, or return fresh defaults if it doesn't exist.

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
        Write this metadata to `path` as JSON.

        :param path: Destination path. Its parent directory is created if
            it doesn't exist.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(dataclasses.asdict(self), indent=2) + "\n", encoding="utf-8")
