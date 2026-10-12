"""
Tests for `live.browser.open_session`'s argument validation.

No browser is launched: the Playwright driver is replaced by one that stops the call
right after validation, so a rejected argument and an accepted one are told apart by
which error comes out.
"""

import asyncio
import types
import typing

import pytest

from slb_glossary.config import SessionOptions
from slb_glossary.live import browser as browser_module
from slb_glossary.live import close_session, open_session
from tests.conftest import cancel_repeatedly

pytestmark = [pytest.mark.unit, pytest.mark.anyio]


class PastValidation(Exception):
    """Raised by the fake driver: the arguments were accepted."""


@pytest.fixture
def anyio_backend(
    anyio_backend_asyncio_only: tuple[str, dict[str, typing.Any]],
) -> tuple[str, dict[str, typing.Any]]:
    return anyio_backend_asyncio_only


@pytest.fixture(autouse=True)
def fake_playwright(monkeypatch: pytest.MonkeyPatch) -> None:
    class Driver:
        async def start(self) -> typing.NoReturn:
            raise PastValidation

    monkeypatch.setattr(browser_module, "async_playwright", lambda: Driver())


class TestArgumentValidation:
    @pytest.mark.parametrize(
        ("browser_type", "language"),
        [("chromium", "en"), ("firefox", "es"), ("webkit", "en")],
    )
    async def test_plain_strings_are_accepted(self, browser_type: str, language: str) -> None:
        """
        Config files and the CLI hand over strings. Testing `string in SomeEnum` raises
        `TypeError` on Python 3.10 and 3.11, which broke every live command there.
        """
        with pytest.raises(PastValidation):
            await open_session(browser_type=browser_type, language=language)

    async def test_what_session_options_produces_is_accepted(self) -> None:
        """The exact kwargs the CLI and `Runtime` pass, built from the default options."""
        with pytest.raises(PastValidation):
            await open_session(**SessionOptions().session_kwargs())

    async def test_unknown_browser_type_is_rejected_with_the_supported_ones(self) -> None:
        with pytest.raises(ValueError, match=r"Unsupported `browser_type` 'netscape'.*CHROMIUM"):
            await open_session(browser_type="netscape")

    async def test_unknown_language_is_rejected_with_the_supported_ones(self) -> None:
        with pytest.raises(ValueError, match=r"Unsupported `language` 'fr'.*ENGLISH"):
            await open_session(language="fr")


class TestCloseSession:
    def fake_session(self, calls: list[str]) -> typing.Any:
        async def step(name: str) -> None:
            await asyncio.sleep(0.02)
            calls.append(name)

        return types.SimpleNamespace(
            close=lambda: step("session"),
            browser=types.SimpleNamespace(close=lambda: step("browser")),
            playwright=types.SimpleNamespace(stop=lambda: step("driver")),
        )

    async def test_closes_everything_in_order(self) -> None:
        calls: list[str] = []
        await close_session(self.fake_session(calls))
        assert calls == ["session", "browser", "driver"]

    async def test_every_step_still_runs_when_cancelled_again_and_again(self) -> None:
        """
        Cleanup runs in a `finally`, under a scope that re-cancels every `await` (anyio, which
        FastMCP runs on). Unshielded, the first interrupted step skipped the rest and left
        the browser, and its driver process, running with nothing left to close them.
        """
        calls: list[str] = []
        session = self.fake_session(calls)

        async def body() -> None:
            try:
                await asyncio.sleep(30)
            finally:
                await close_session(session)

        task = asyncio.create_task(body())
        await asyncio.sleep(0.01)
        await cancel_repeatedly(task)
        await asyncio.sleep(0.3)  # the close carries on in the background

        assert calls == ["session", "browser", "driver"]
