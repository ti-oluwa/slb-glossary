"""Fixtures shared by `tests/local/`. (The real-database `db` fixture is in `tests/conftest.py`.)"""

import typing

import pytest


@pytest.fixture
def anyio_backend(
    anyio_backend_asyncio_only: tuple[str, dict[str, typing.Any]],
) -> tuple[str, dict[str, typing.Any]]:
    """`tests/local/` opens real aiosqlite databases, which aren't trio-safe."""
    return anyio_backend_asyncio_only
