"""
Text embedding, for semantic scoring/search over glossary terms, local or live.

Wraps a single, package-managed `model2vec` static embedding model (see
`constants.embedding_model`), so `slb_glossary.local`'s semantic search
and `slb_glossary.live`'s semantic result scoring embed text the same
way, without either needing its own model.

The model is downloaded once from Hugging Face and cached locally by
`model2vec` itself; nothing here makes a network call at query time.

Install the `semantic` extra to use anything in this module:
`pip install slb-glossary[semantic]`.
"""

import functools
import logging
import typing

from slb_glossary.constants import constants
from slb_glossary.errors import EmbeddingError

logger = logging.getLogger(__name__)

__all__ = ["embed", "embedding_dim", "build_embed_text", "cosine_similarity"]

if typing.TYPE_CHECKING:
    import numpy as np  # type: ignore[import]


@functools.lru_cache(maxsize=1)
def load_model() -> typing.Any:
    """Load (once per process) and cache the package's `model2vec` embedding model."""
    try:
        from model2vec import StaticModel  # type: ignore[import]
    except ImportError as exc:
        raise EmbeddingError(
            "Semantic search needs the `model2vec` package, which isn't "
            "installed. Install it with `pip install slb-glossary[semantic]`."
        ) from exc

    model_name = constants.embedding_model
    logger.info("Loading embedding model %r (downloaded once, then cached locally)", model_name)
    model = StaticModel.from_pretrained(model_name)
    if model.dim != constants.embedding_dim:
        raise EmbeddingError(
            f"Embedding model {model_name!r} produces {model.dim}-dimensional "
            f"vectors, but `constants.embedding_dim` is {constants.embedding_dim}. "
            "Set `SLB_GLOSSARY_EMBEDDING_DIM` to match, or leave both "
            "`embedding_model` and `embedding_dim` at their defaults."
        )
    return model


def embedding_dim() -> int:
    """
    Return the output size of the package's embedding model.

    Loads the model on first call, purely to confirm its real output
    size matches `constants.embedding_dim` (the local vector table is
    created with that fixed size, see `slb_glossary.local.vectors`).

    :raises EmbeddingError: If the `semantic` extra isn't installed, or
        the model's real output size doesn't match `constants.embedding_dim`.
    """
    load_model()
    return constants.embedding_dim


def embed(texts: typing.Sequence[str]) -> "np.ndarray":
    """
    Embed `texts` with the package's embedding model.

    :param texts: Text to embed. `build_embed_text` gives the standard
        text for a term (name, definition, and topic); a search query is
        just embedded as-is.
    :return: A `(len(texts), constants.embedding_dim)` array of `float32` vectors.
    :raises EmbeddingError: If the `semantic` extra isn't installed, or
        the model's real output size doesn't match `constants.embedding_dim`.
    """
    model = load_model()
    return model.encode(list(texts))


def build_embed_text(term: str, definition: str | None, topic: str | None) -> str:
    """
    Build the text a term is embedded from, for a consistent embedding across local and live results.

    :param term: The term's name.
    :param definition: The term's definition, if any.
    :param topic: The term's topic, if any.
    :return: `term`, `definition`, and `topic`, joined, skipping any that are empty.
    """
    parts = [part for part in (term, definition, topic) if part]
    return ". ".join(parts)


def cosine_similarity(a: "np.ndarray", b: "np.ndarray") -> float:
    """
    Cosine similarity between two embedding vectors.

    :param a: One embedding vector.
    :param b: Another embedding vector, the same size as `a`.
    :return: A similarity in `[-1.0, 1.0]`, in practice close to
        `[0.0, 1.0]` for real text, `1.0` being identical direction.
    """
    import numpy as np

    denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denominator == 0.0:
        return 0.0
    return float(np.dot(a, b) / denominator)
