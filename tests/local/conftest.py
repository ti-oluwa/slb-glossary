"""Fixtures shared by every `tests/local/` module: real aiosqlite databases via `tmp_path`."""

import hashlib
import pathlib
import typing

import pytest

from slb_glossary.local import vector
from slb_glossary.local.connection import database
from slb_glossary.local.types import Database

MOCK_EMBED_DIM = 4
"""
Dimensionality used by the `mock_embeddings` fixture's fake vectors.

Real semantic search needs `model2vec` to download and load a real
embedding model from Hugging Face, which this sandbox has no network
access to (only a fixed allowlist of package-index domains). `sqlite-vec`
itself, though, is a normal installed package with no network dependency,
so real `vec0` k-NN search still runs for real here - only the embedding
step (`slb_glossary.local.vector.embed`/`embedding_dim`) is faked, via
this fixture, keeping the actual vector-table/search SQL under real test
coverage while avoiding a live model download.
"""


def text_to_unit_vector(text: str) -> typing.Any:
    """
    Deterministically hash `text` into a fixed-size unit vector (the fallback for
    any text not explicitly `.set()` on the `mock_embeddings` fixture).

    Returns a `numpy.ndarray`, typed `Any` here so this module doesn't need
    a hard `numpy` import at module level for a type-only reference.
    """
    import numpy as np

    digest = hashlib.sha256(text.encode()).digest()
    values = [b / 255.0 for b in digest[:MOCK_EMBED_DIM]]
    vector = np.array(values, dtype="float32")
    norm = np.linalg.norm(vector)
    return vector / norm if norm else vector


class MockEmbeddings:
    """
    Controller for the `mock_embeddings` fixture: register exact vectors for
    specific texts (e.g. a query and a term's embed-text), falling back to a
    deterministic hash-based vector for anything not explicitly registered.
    """

    def __init__(self) -> None:
        self._overrides: dict[str, typing.Any] = {}

    def set(self, text: str, vector: list[float]) -> None:
        """Register an exact vector for `text`, overriding the hash-based default."""
        import numpy as np

        self._overrides[text] = np.array(vector, dtype="float32")

    def embed(self, texts: list[str]) -> typing.Any:
        import numpy as np

        return np.stack([self._overrides.get(text, text_to_unit_vector(text)) for text in texts])


@pytest.fixture
def mock_embeddings(monkeypatch: pytest.MonkeyPatch) -> MockEmbeddings:
    """
    Fake `slb_glossary.local.vector.embed`/`embedding_dim`, avoiding a real
    (network-dependent) `model2vec` model load. See `MOCK_EMBED_DIM`'s docstring.
    """
    controller = MockEmbeddings()
    monkeypatch.setattr(vector, "embedding_dim", lambda: MOCK_EMBED_DIM)
    monkeypatch.setattr(vector, "embed", controller.embed)
    return controller


@pytest.fixture
def anyio_backend(
    anyio_backend_asyncio_only: tuple[str, dict[str, typing.Any]],
) -> tuple[str, dict[str, typing.Any]]:
    """Every test under `tests/local/` touches real aiosqlite, which isn't trio-safe."""
    return anyio_backend_asyncio_only


@pytest.fixture
async def db(tmp_path: pathlib.Path) -> typing.AsyncIterator[Database]:
    """A real, open `Database` backed by a throwaway SQLite file under `tmp_path`."""
    async with database(tmp_path / "test.db") as db_:
        yield db_
