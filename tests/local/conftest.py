"""Fixtures shared by `tests/local/`: real aiosqlite databases via `tmp_path`."""

import pathlib
import typing

import pytest

from slb_glossary.local.connection import database
from slb_glossary.local.types import Database


@pytest.fixture
def anyio_backend(
    anyio_backend_asyncio_only: tuple[str, dict[str, typing.Any]],
) -> tuple[str, dict[str, typing.Any]]:
    """`tests/local/` opens real aiosqlite databases, which aren't trio-safe."""
    return anyio_backend_asyncio_only


@pytest.fixture
async def db(tmp_path: pathlib.Path) -> typing.AsyncIterator[Database]:
    """A real, open `Database` backed by a throwaway SQLite file."""
    async with database(tmp_path / "test.db") as db_:
        yield db_
