"""Fixtures for `tests/relevance/`. (The real-database `db` fixture is in `tests/conftest.py`.)"""

import typing

import pytest


@pytest.fixture
def anyio_backend(
    anyio_backend_asyncio_only: tuple[str, dict[str, typing.Any]],
) -> tuple[str, dict[str, typing.Any]]:
    """Every test here touches real aiosqlite, which is not trio-safe (see `tests/local/conftest.py`)."""
    return anyio_backend_asyncio_only
