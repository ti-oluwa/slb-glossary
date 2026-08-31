"""
`embeddings.load_model`'s cache-friendly (non-`force_download`) model loading
and third-party logger/progress-bar quieting, plus the pure `build_embed_text`
and `cosine_similarity` helpers.
"""

import logging
import typing

import pytest

from slb_glossary.embeddings import build_embed_text, cosine_similarity, load_model
from slb_glossary.errors import EmbeddingError

pytestmark = pytest.mark.unit


class FakeStaticModel:
    """
    Stand-in for `model2vec.StaticModel`, letting tests inspect exactly how
    `from_pretrained` was called and control the returned model's `.dim`.
    """

    last_call_kwargs: typing.ClassVar[dict] = {}

    def __init__(self, dim: int) -> None:
        self.dim = dim

    def encode(self, texts: list[str]):
        raise NotImplementedError("not needed by these tests")

    @classmethod
    def from_pretrained(cls, model_name: str, **kwargs) -> "FakeStaticModel":
        cls.last_call_kwargs = {"model_name": model_name, **kwargs}
        return cls(dim=kwargs.get("dim", 4))


@pytest.fixture(autouse=True)
def clear_load_model_cache():
    """`load_model` is `functools.lru_cache`d; clear it so each test loads fresh."""
    load_model.cache_clear()
    yield
    load_model.cache_clear()


@pytest.fixture
def fake_model2vec(monkeypatch: pytest.MonkeyPatch):
    """
    Replace the `model2vec` module `load_model` imports with a fake exposing
    only `StaticModel.from_pretrained`, tracking how it was called.
    """
    import sys
    import types

    fake_module = types.ModuleType("model2vec")
    fake_module.StaticModel = FakeStaticModel  # type: ignore
    monkeypatch.setitem(sys.modules, "model2vec", fake_module)
    return FakeStaticModel


class TestLoadModel:
    def test_raises_embedding_error_if_model2vec_not_installed(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """A missing `model2vec` package raises `EmbeddingError`, not `ImportError`."""
        import builtins
        import sys

        monkeypatch.delitem(sys.modules, "model2vec", raising=False)
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "model2vec":
                raise ImportError("no such module")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)
        with pytest.raises(EmbeddingError, match="model2vec"):
            load_model()

    def test_passes_force_download_false(
        self, fake_model2vec: type[FakeStaticModel], monkeypatch: pytest.MonkeyPatch
    ):
        """
        `from_pretrained` is called with `force_download=False`, not left at its
        (expensive, noisy, re-download-on-every-call) `True` default.
        """
        monkeypatch.setattr("slb_glossary.constants.constants.embedding_dim", 4)
        load_model()
        assert fake_model2vec.last_call_kwargs["force_download"] is False

    def test_raises_embedding_error_on_dimension_mismatch(
        self, fake_model2vec: type[FakeStaticModel], monkeypatch: pytest.MonkeyPatch
    ):
        """A loaded model whose real `.dim` disagrees with `constants.embedding_dim` raises."""
        monkeypatch.setattr("slb_glossary.constants.constants.embedding_dim", 999)
        with pytest.raises(EmbeddingError, match="dimensional"):
            load_model()

    def test_caches_across_calls_within_a_process(
        self, fake_model2vec: type[FakeStaticModel], monkeypatch: pytest.MonkeyPatch
    ):
        """A second call within the same process reuses the cached model, not a fresh load."""
        monkeypatch.setattr("slb_glossary.constants.constants.embedding_dim", 4)
        first = load_model()
        second = load_model()
        assert first is second

    def test_quiets_third_party_loggers(
        self, fake_model2vec: type[FakeStaticModel], monkeypatch: pytest.MonkeyPatch
    ):
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
    def test_joins_term_definition_and_topic(self):
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
    ):
        """Falsy (`None` or empty-string) `definition`/`topic` are skipped, not
        joined in as empty segments."""
        assert build_embed_text(term, definition, topic) == expected


class TestCosineSimilarity:
    def test_identical_vectors_score_one(self):
        """Two identical vectors score (very close to) `1.0`."""
        import numpy as np

        vector = np.array([1.0, 2.0, 3.0], dtype="float32")
        assert cosine_similarity(vector, vector) == pytest.approx(1.0)

    def test_orthogonal_vectors_score_zero(self):
        """Two orthogonal vectors score `0.0`."""
        import numpy as np

        a = np.array([1.0, 0.0], dtype="float32")
        b = np.array([0.0, 1.0], dtype="float32")
        assert cosine_similarity(a, b) == pytest.approx(0.0)

    def test_opposite_vectors_score_negative_one(self):
        """Two exactly opposite vectors score `-1.0`."""
        import numpy as np

        a = np.array([1.0, 0.0], dtype="float32")
        b = np.array([-1.0, 0.0], dtype="float32")
        assert cosine_similarity(a, b) == pytest.approx(-1.0)

    def test_zero_vector_scores_zero_not_a_division_error(self):
        """A zero-length vector scores `0.0` rather than raising a division error."""
        import numpy as np

        zero = np.array([0.0, 0.0], dtype="float32")
        other = np.array([1.0, 1.0], dtype="float32")
        assert cosine_similarity(zero, other) == 0
