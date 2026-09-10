"""
Shared mock classes and fixtures for `tests/local/` and `tests/live/`.
"""

import dataclasses
import hashlib
import types
import typing

import pytest

from slb_glossary.local import vector
from slb_glossary.retries import DEFAULT_RETRY_POLICY, RetryPolicy
from slb_glossary.types import Language, SearchResult

MOCK_EMBED_DIM = 4
"""Dimensionality used by `mock_embeddings`'s fake vectors."""


def text_to_unit_vector(text: str) -> typing.Any:
    """Deterministic hash-based fallback vector for `MockEmbeddings`."""
    import numpy as np

    digest = hashlib.sha256(text.encode()).digest()
    values = [b / 255.0 for b in digest[:MOCK_EMBED_DIM]]
    vector_ = np.array(values, dtype="float32")
    norm = np.linalg.norm(vector_)
    return vector_ / norm if norm else vector_


class MockEmbeddings:
    """Controller for `mock_embeddings`: registers exact vectors per text,
    falling back to a deterministic hash for anything unregistered."""

    def __init__(self) -> None:
        self._overrides: dict[str, typing.Any] = {}

    def set(self, text: str, vector_: list[float]) -> None:
        """Register an exact vector for `text`."""
        import numpy as np

        self._overrides[text] = np.array(vector_, dtype="float32")

    def embed(self, texts: list[str]) -> typing.Any:
        import numpy as np

        return np.stack([self._overrides.get(text, text_to_unit_vector(text)) for text in texts])


@pytest.fixture
def mock_embeddings(monkeypatch: pytest.MonkeyPatch) -> MockEmbeddings:
    """Fakes `local.vector.embed`/`embedding_dim`; real `sqlite-vec` k-NN still runs."""
    controller = MockEmbeddings()
    monkeypatch.setattr(vector, "embedding_dim", lambda: MOCK_EMBED_DIM)
    monkeypatch.setattr(vector, "embed", controller.embed)
    return controller


class MockLocator:
    """Stands in for `page.locator(selector).first`."""

    def __init__(self, text: str | None = None, should_timeout: bool = False) -> None:
        self._text = text
        self._should_timeout = should_timeout
        self.first = self

    async def text_content(self, timeout: float | None = None) -> str | None:
        if self._should_timeout:
            raise TimeoutError("locator never appeared")
        return self._text


class MockPage:
    """Stands in for `patchright.async_api.Page`: DOM extraction
    (`locator`/`eval_on_selector_all`) plus navigation (`goto`/`close`/`is_closed`)."""

    def __init__(self, url: str = "https://x.com/porosity", *, fail_close: bool = False) -> None:
        self.url = url
        self.locators: dict[str, MockLocator] = {}
        self.eval_results: dict[str, object] = {}
        self.eval_should_raise: set[str] = set()
        self._closed = False
        self._fail_close = fail_close
        self._close_listeners: list[typing.Callable[[MockPage], None]] = []

    def locator(self, selector: str) -> MockLocator:
        return self.locators.get(selector, MockLocator(text=None))

    async def eval_on_selector_all(self, selector: str, script: str) -> object:
        if selector in self.eval_should_raise:
            raise RuntimeError("evaluation failed")
        return self.eval_results.get(selector, [])

    async def goto(self, url: str, *, timeout: float | None = None, wait_until: str = "") -> None:
        pass

    def on(self, event: str, callback: typing.Callable[["MockPage"], None]) -> None:
        """Register `callback` for `event` - only `"close"` is meaningful here."""
        if event == "close":
            self._close_listeners.append(callback)

    async def close(self) -> None:
        if self._fail_close:
            raise RuntimeError("simulated page close failure")
        if self._closed:
            return
        self._closed = True
        for callback in self._close_listeners:
            callback(self)

    def is_closed(self) -> bool:
        return self._closed


@dataclasses.dataclass
class MockSession:
    """Stands in for `live.types.Session`, for both `live.api` and `local.sync`."""

    language: Language = Language.ENGLISH
    topics: dict[str, int] = dataclasses.field(default_factory=dict)
    retry: RetryPolicy = DEFAULT_RETRY_POLICY
    initialized: bool = False

    async def initialize(self) -> None:
        self.initialized = True

    async def new_page(self) -> MockPage:
        return MockPage()


class MockSite:
    """In-memory stand-in for the live glossary, for monkeypatching `sync.py`'s
    live-layer imports. `pages_by_url` is what one page fetch for that URL
    yields; `urls_by_topic`/`urls_by_query` is what a listing surfaces;
    `visited` records every URL actually fetched."""

    def __init__(self) -> None:
        self.pages_by_url: dict[str, list[SearchResult]] = {}
        self.urls_by_topic: dict[str, list[str]] = {}
        self.urls_by_query: dict[str, list[str]] = {}
        self.visited: list[str] = []

    def add_term(self, url: str, topics: list[SearchResult], under_topics: list[str]) -> None:
        """Register a term's page and which topic listings surface its URL."""
        self.pages_by_url[url] = topics
        for topic_name in under_topics:
            self.urls_by_topic.setdefault(topic_name, []).append(url)

    async def mock_get_terms_on(
        self,
        session: MockSession,
        topic: str,
        *,
        limit: int | None = None,
        concurrency: int = 1,
        exclude: frozenset[str] | None = None,
    ) -> typing.AsyncIterator[SearchResult]:
        exclude = exclude or frozenset()
        for url in self.urls_by_topic.get(topic, []):
            if url in exclude:
                continue
            self.visited.append(url)
            for result in self.pages_by_url[url]:
                yield result

    async def mock_get_terms_urls(
        self,
        session: MockSession,
        *,
        topic: str | None = None,
        start_letter: str | None = None,
        limit: int | None = None,
        exclude: frozenset[str] | None = None,
    ) -> typing.AsyncIterator[str]:
        exclude = exclude or frozenset()
        urls = self.urls_by_topic.get(topic, []) if topic else list(self.pages_by_url)
        for url in urls:
            if url in exclude:
                continue
            yield url

    async def mock_get_results_from_urls(
        self,
        session: MockSession,
        urls: typing.AsyncIterator[str],
        *,
        topic: str | None = None,
        concurrency: int = 1,
        first_only: bool = True,
        exclude: frozenset[str] | None = None,
    ) -> typing.AsyncIterator[SearchResult]:
        exclude = exclude or frozenset()
        async for url in urls:
            if url in exclude:
                continue
            self.visited.append(url)
            results = self.pages_by_url[url]
            yield results[0]
            if not first_only:
                for result in results[1:]:
                    yield result

    async def mock_live_search(
        self,
        session: MockSession,
        query: str,
        *,
        topic: str | None = None,
        start_letter: str | None = None,
        limit: int | None = None,
        concurrency: int = 1,
        exclude: frozenset[str] | None = None,
    ) -> typing.AsyncIterator[SearchResult]:
        exclude = exclude or frozenset()
        for url in self.urls_by_query.get(query, []):
            if url in exclude:
                continue
            self.visited.append(url)
            for result in self.pages_by_url[url]:
                yield result


@pytest.fixture
def mock_site() -> MockSite:
    """A fresh `MockSite` for one test."""
    return MockSite()


class MockStaticModel:
    """Stands in for `model2vec.StaticModel`."""

    last_call_kwargs: typing.ClassVar[dict[str, typing.Any]] = {}

    def __init__(self, dim: int) -> None:
        self.dim = dim

    def encode(self, texts: list[str]) -> typing.NoReturn:
        raise NotImplementedError("not needed by these tests")

    @classmethod
    def from_pretrained(cls, model_name: str, **kwargs: typing.Any) -> "MockStaticModel":
        cls.last_call_kwargs = {"model_name": model_name, **kwargs}
        return cls(dim=kwargs.get("dim", 4))


@pytest.fixture
def mock_model2vec(monkeypatch: pytest.MonkeyPatch) -> type[MockStaticModel]:
    """Replaces the `model2vec` module `load_model` imports."""
    import sys

    mock_module = types.ModuleType("model2vec")
    mock_module.StaticModel = MockStaticModel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "model2vec", mock_module)
    return MockStaticModel
