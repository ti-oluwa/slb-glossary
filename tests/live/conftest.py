"""
Fixtures shared by every `tests/live/` module: a `patchright.async_api.Page`
stand-in covering both DOM extraction and navigation, a `live.types.Session`
stand-in, and the `anyio_backend` override every test here needs.
"""

import dataclasses
import typing

import pytest

from slb_glossary.retries import DEFAULT_RETRY_POLICY, RetryPolicy
from slb_glossary.types import Language


@pytest.fixture
def anyio_backend(
    anyio_backend_asyncio_only: tuple[str, dict[str, typing.Any]],
) -> tuple[str, dict[str, typing.Any]]:
    """
    Every test under `tests/live/` drives fakes for `patchright`-backed code,
    which isn't trio-safe (same reasoning as `tests/local/conftest.py`).
    """
    return anyio_backend_asyncio_only


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
    """
    Stands in for `patchright.async_api.Page`, covering both what
    `live.parsers` needs (DOM extraction via `locator`/`eval_on_selector_all`)
    and what `live.api` needs (navigation lifecycle via `goto`/`close`/
    `is_closed`) - one fake either kind of test can use.

    `locators` maps a selector to the `MockLocator` `page.locator(selector)`
    should return; `eval_results` maps a selector to what
    `page.eval_on_selector_all(selector, ...)` should return (the JS string
    itself is accepted but ignored). `goto`/`close` are no-ops by default.
    """

    def __init__(self, url: str = "https://x.com/porosity") -> None:
        self.url = url
        self.locators: dict[str, MockLocator] = {}
        self.eval_results: dict[str, object] = {}
        self.eval_should_raise: set[str] = set()
        self._closed = False

    def locator(self, selector: str) -> MockLocator:
        return self.locators.get(selector, MockLocator(text=None))

    async def eval_on_selector_all(self, selector: str, script: str) -> object:
        if selector in self.eval_should_raise:
            raise RuntimeError("evaluation failed")
        return self.eval_results.get(selector, [])

    async def goto(self, url: str, *, timeout: float | None = None, wait_until: str = "") -> None:
        pass

    async def close(self) -> None:
        self._closed = True

    def is_closed(self) -> bool:
        return self._closed


@dataclasses.dataclass
class MockSession:
    """
    Stands in for `live.types.Session`, covering what both `live.api`
    (`new_page`) and `local.sync` (topic/language lookups) need - one fake
    either kind of test can use.

    `initialized` is a read-only property backed by `initialized`, matching
    the real `Session`'s own shape (rather than a plain settable field), so
    a test asserting `session.initialized is True` after `initialize()`
    reflects the same contract the real class offers.
    """

    language: Language = Language.ENGLISH
    topics: dict[str, int] = dataclasses.field(default_factory=dict)
    retry: RetryPolicy = DEFAULT_RETRY_POLICY
    initialized: bool = False

    async def initialize(self) -> None:
        self.initialized = True

    async def new_page(self) -> MockPage:
        return MockPage()
