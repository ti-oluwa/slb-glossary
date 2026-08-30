"""Fixtures shared by every `tests/local/` module: real aiosqlite databases via `tmp_path`."""

import pytest

from slb_glossary.local.connection import database


@pytest.fixture
def anyio_backend(anyio_backend_asyncio_only):
    """Every test under `tests/local/` touches real aiosqlite, which isn't trio-safe."""
    return anyio_backend_asyncio_only


@pytest.fixture
async def db(tmp_path):
    """A real, open `Database` backed by a throwaway SQLite file under `tmp_path`."""
    async with database(tmp_path / "test.db") as db:
        yield db
