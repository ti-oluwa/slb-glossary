"""
Local text embedding for semantic search.

Wraps a single, package-managed `model2vec` static embedding model (see
`constants.embedding_model`), so `slb_glossary.local.embed_terms`/
`vector_search`/`hybrid_search` all embed text the same way without
every caller needing to bring their own model.

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

__all__ = ["embed", "embedding_dim"]

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

    :param texts: Text to embed. `slb_glossary.local.embed_terms` embeds
        each term's name, definition, and topic joined together; a
        search query is just embedded as-is.
    :return: A `(len(texts), constants.embedding_dim)` array of `float32` vectors.
    :raises EmbeddingError: If the `semantic` extra isn't installed, or
        the model's real output size doesn't match `constants.embedding_dim`.
    """
    model = load_model()
    return model.encode(list(texts))
