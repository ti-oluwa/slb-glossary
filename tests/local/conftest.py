"""Fixtures shared by every `tests/local/` module: real aiosqlite databases via `tmp_path`."""

import hashlib

import pytest

from slb_glossary.local.connection import database

MOCK_EMBED_DIM = 4
"""Dimensionality used by the `mock_embeddings` fixture's fake vectors.

Real semantic search needs `model2vec` to download and load a real
embedding model from Hugging Face, which this sandbox has no network
access to (only a fixed allowlist of package-index domains). `sqlite-vec`
itself, though, is a normal installed package with no network dependency,
so real `vec0` k-NN search still runs for real here - only the embedding
step (`slb_glossary.local.vector.embed`/`embedding_dim`) is faked, via
this fixture, keeping the actual vector-table/search SQL under real test
coverage while avoiding a live model download.
"""


def _text_to_unit_vector(text: str):
    """Deterministically hash `text` into a fixed-size unit vector (the fallback for
    any text not explicitly `.set()` on the `mock_embeddings` fixture)."""
    import numpy as np

    digest = hashlib.sha256(text.encode()).digest()
    values = [b / 255.0 for b in digest[:MOCK_EMBED_DIM]]
    vector = np.array(values, dtype="float32")
    norm = np.linalg.norm(vector)
    return vector / norm if norm else vector


class _MockEmbeddings:
    """Controller for the `mock_embeddings` fixture: register exact vectors for
    specific texts (e.g. a query and a term's embed-text), falling back to a
    deterministic hash-based vector for anything not explicitly registered."""

    def __init__(self) -> None:
        self._overrides: dict = {}

    def set(self, text: str, vector: list[float]) -> None:
        """Register an exact vector for `text`, overriding the hash-based default."""
        import numpy as np

        self._overrides[text] = np.array(vector, dtype="float32")

    def embed(self, texts):
        import numpy as np

        return np.stack(
            [self._overrides.get(text, _text_to_unit_vector(text)) for text in texts]
        )


@pytest.fixture
def mock_embeddings(monkeypatch: pytest.MonkeyPatch) -> _MockEmbeddings:
    """Fake `slb_glossary.local.vector.embed`/`embedding_dim`, avoiding a real
    (network-dependent) `model2vec` model load. See `MOCK_EMBED_DIM`'s docstring."""
    from slb_glossary.local import vector as vector_module

    controller = _MockEmbeddings()
    monkeypatch.setattr(vector_module, "embedding_dim", lambda: MOCK_EMBED_DIM)
    monkeypatch.setattr(vector_module, "embed", controller.embed)
    return controller


@pytest.fixture
def anyio_backend(anyio_backend_asyncio_only):
    """Every test under `tests/local/` touches real aiosqlite, which isn't trio-safe."""
    return anyio_backend_asyncio_only


@pytest.fixture
async def db(tmp_path):
    """A real, open `Database` backed by a throwaway SQLite file under `tmp_path`."""
    async with database(tmp_path / "test.db") as db:
        yield db
