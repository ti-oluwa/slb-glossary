"""Tests for `embeddings.load_model`, `build_embed_text`, and `cosine_similarity`."""

import logging
import types
import typing

import pytest

from slb_glossary.embeddings import build_embed_text, cosine_similarity, load_model
from slb_glossary.errors import EmbeddingError
from tests.mocks import MockStaticModel

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def clear_load_model_cache() -> typing.Iterator[None]:
    """`load_model` is `functools.lru_cache`d; clear it so each test loads fresh."""
    load_model.cache_clear()
    yield
    load_model.cache_clear()


class TestLoadModel:
    def test_raises_embedding_error_if_model2vec_not_installed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A missing `model2vec` package raises `EmbeddingError`, not `ImportError`."""
        import builtins
        import sys

        monkeypatch.delitem(sys.modules, "model2vec", raising=False)
        real_import = builtins.__import__

        def mock_import(name: str, *args: typing.Any, **kwargs: typing.Any) -> types.ModuleType:
            if name == "model2vec":
                raise ImportError("no such module")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)
        with pytest.raises(EmbeddingError, match="model2vec"):
            load_model()

    def test_passes_force_download_false(
        self, mock_model2vec: type[MockStaticModel], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        `from_pretrained` is called with `force_download=False`, not left at its
        (expensive, noisy, re-download-on-every-call) `True` default.
        """
        monkeypatch.setattr("slb_glossary.constants.constants.embedding_dim", 4)
        load_model()
        assert mock_model2vec.last_call_kwargs["force_download"] is False

    def test_raises_embedding_error_on_dimension_mismatch(
        self, mock_model2vec: type[MockStaticModel], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A loaded model whose real `.dim` disagrees with `constants.embedding_dim` raises."""
        monkeypatch.setattr("slb_glossary.constants.constants.embedding_dim", 999)
        with pytest.raises(EmbeddingError, match="dimensional"):
            load_model()

    def test_caches_across_calls_within_a_process(
        self, mock_model2vec: type[MockStaticModel], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A second call within the same process reuses the cached model, not a fresh load."""
        monkeypatch.setattr("slb_glossary.constants.constants.embedding_dim", 4)
        first = load_model()
        second = load_model()
        assert first is second

    def test_quiets_third_party_loggers(
        self, mock_model2vec: type[MockStaticModel], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`httpx`/`httpcore`/`huggingface_hub`/`filelock` loggers are raised to WARNING,
        so their own INFO-level request/progress chatter doesn't bleed into our
        configured log sinks."""
        monkeypatch.setattr("slb_glossary.constants.constants.embedding_dim", 4)
        for noisy_logger_name in ("httpx", "httpcore", "huggingface_hub", "filelock"):
            logging.getLogger(noisy_logger_name).setLevel(logging.INFO)

        load_model()

        for noisy_logger_name in ("httpx", "httpcore", "huggingface_hub", "filelock"):
            assert logging.getLogger(noisy_logger_name).level == logging.WARNING


class TestBuildEmbedText:
    def test_joins_term_definition_and_topic(self) -> None:
        """Joins all three parts with `". "` when every part is given."""
        text = build_embed_text("Porosity", "A rock property", "Geology")
        assert text == "Porosity. A rock property. Geology"

    @pytest.mark.parametrize(
        ("term", "definition", "topic", "expected"),
        [
            ("Porosity", None, None, "Porosity"),
            ("Porosity", "A rock property", None, "Porosity. A rock property"),
            ("Porosity", None, "Geology", "Porosity. Geology"),
            ("Porosity", "", "", "Porosity"),
        ],
    )
    def test_skips_empty_parts(
        self, term: str, definition: str | None, topic: str | None, expected: str
    ) -> None:
        """Falsy (`None` or empty-string) `definition`/`topic` are skipped, not
        joined in as empty segments."""
        assert build_embed_text(term, definition, topic) == expected


class TestCosineSimilarity:
    def test_identical_vectors_score_one(self) -> None:
        """Two identical vectors score (very close to) `1.0`."""
        import numpy as np

        vector = np.array([1.0, 2.0, 3.0], dtype="float32")
        assert cosine_similarity(vector, vector) == pytest.approx(1.0)

    def test_orthogonal_vectors_score_zero(self) -> None:
        """Two orthogonal vectors score `0.0`."""
        import numpy as np

        a = np.array([1.0, 0.0], dtype="float32")
        b = np.array([0.0, 1.0], dtype="float32")
        assert cosine_similarity(a, b) == pytest.approx(0.0)

    def test_opposite_vectors_score_negative_one(self) -> None:
        """Two exactly opposite vectors score `-1.0`."""
        import numpy as np

        a = np.array([1.0, 0.0], dtype="float32")
        b = np.array([-1.0, 0.0], dtype="float32")
        assert cosine_similarity(a, b) == pytest.approx(-1.0)

    def test_zero_vector_scores_zero_not_a_division_error(self) -> None:
        """A zero-length vector scores `0.0` rather than raising a division error."""
        import numpy as np

        zero = np.array([0.0, 0.0], dtype="float32")
        other = np.array([1.0, 1.0], dtype="float32")
        assert cosine_similarity(zero, other) == 0
