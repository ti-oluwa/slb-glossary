"""
`has_internet_connection`'s probing, caching, and all-targets-failed behavior.

Uses `anyio_backend_asyncio_only`: `probe()` uses raw
`asyncio.open_connection`/`asyncio.wait_for`.
"""

import asyncio
import time

import pytest

from slb_glossary import connectivity
from slb_glossary.constants import constants

pytestmark = pytest.mark.unit


@pytest.fixture
def anyio_backend(anyio_backend_asyncio_only):
    return anyio_backend_asyncio_only


@pytest.fixture(autouse=True)
def clear_connectivity_cache():
    """`connectivity._CACHE` is a module-level global; reset it around every test."""
    connectivity._CACHE = None
    yield
    connectivity._CACHE = None


@pytest.mark.anyio
class TestProbe:
    async def test_returns_true_on_successful_connect(self, monkeypatch: pytest.MonkeyPatch):
        """`probe` returns `True` when `asyncio.open_connection` succeeds."""

        class FakeWriter:
            def close(self):
                pass

            async def wait_closed(self):
                pass

        async def mock_open_connection(host, port):
            return object(), FakeWriter()

        monkeypatch.setattr(asyncio, "open_connection", mock_open_connection)
        assert await connectivity.probe("1.1.1.1", 53, timeout=1.0) is True

    async def test_returns_false_on_connection_error(self, monkeypatch: pytest.MonkeyPatch):
        """`probe` returns `False` when the connection attempt raises."""

        async def mock_open_connection(host, port):
            raise ConnectionRefusedError

        monkeypatch.setattr(asyncio, "open_connection", mock_open_connection)
        assert await connectivity.probe("1.1.1.1", 53, timeout=1.0) is False

    async def test_returns_false_on_timeout(self, monkeypatch: pytest.MonkeyPatch):
        """`probe` returns `False` when the connection attempt times out."""

        async def hanging_open_connection(host, port):
            await asyncio.sleep(10)
            raise AssertionError("should have timed out first")

        monkeypatch.setattr(asyncio, "open_connection", hanging_open_connection)
        assert await connectivity.probe("1.1.1.1", 53, timeout=0.01) is False


@pytest.mark.anyio
class TestHasInternetConnection:
    async def test_true_if_any_probe_target_succeeds(self, monkeypatch: pytest.MonkeyPatch):
        """`True` as soon as any one probe target succeeds, without waiting for the rest sequentially."""

        async def mock_probe(host, port, timeout):
            if host == connectivity.PROBE_TARGETS[0][0]:
                return True
            await asyncio.sleep(0.2)
            return False

        monkeypatch.setattr(connectivity, "probe", mock_probe)
        started = time.monotonic()
        result = await connectivity.has_internet_connection(use_cache=False)
        elapsed = time.monotonic() - started
        assert result is True
        # Targets run concurrently: elapsed should be close to the slower
        # (failing) target's delay, not the sum of every target's delay.
        assert elapsed < 0.2 * len(connectivity.PROBE_TARGETS)

    async def test_false_if_every_probe_target_fails(self, monkeypatch: pytest.MonkeyPatch):
        """`False` only once every probe target has failed."""

        async def mock_probe(host, port, timeout):
            return False

        monkeypatch.setattr(connectivity, "probe", mock_probe)
        assert await connectivity.has_internet_connection(use_cache=False) is False

    async def test_uses_cache_within_ttl(self, monkeypatch: pytest.MonkeyPatch):
        """Two quick calls within the cache TTL only probe once."""
        calls = 0

        async def mock_probe(host, port, timeout):
            nonlocal calls
            calls += 1
            return True

        monkeypatch.setattr(connectivity, "probe", mock_probe)
        await connectivity.has_internet_connection(use_cache=True)
        await connectivity.has_internet_connection(use_cache=True)
        assert calls == len(connectivity.PROBE_TARGETS)

    async def test_use_cache_false_forces_a_fresh_probe(self, monkeypatch: pytest.MonkeyPatch):
        """`use_cache=False` always probes again, ignoring any cached result."""
        calls = 0

        async def mock_probe(host, port, timeout):
            nonlocal calls
            calls += 1
            return True

        monkeypatch.setattr(connectivity, "probe", mock_probe)
        await connectivity.has_internet_connection(use_cache=True)
        await connectivity.has_internet_connection(use_cache=False)
        assert calls == len(connectivity.PROBE_TARGETS) * 2

    async def test_cache_expires_after_ttl(self, monkeypatch: pytest.MonkeyPatch):
        """A cached result older than `constants.internet_check_cache_ttl` triggers a fresh probe."""
        calls = 0

        async def mock_probe(host, port, timeout):
            nonlocal calls
            calls += 1
            return True

        monkeypatch.setattr(connectivity, "probe", mock_probe)
        constants.internet_check_cache_ttl = 5.0

        fake_now = 1000.0
        monkeypatch.setattr(time, "monotonic", lambda: fake_now)
        await connectivity.has_internet_connection(use_cache=True)
        assert calls == len(connectivity.PROBE_TARGETS)

        fake_now += 10.0  # past the 5s TTL
        await connectivity.has_internet_connection(use_cache=True)
        assert calls == len(connectivity.PROBE_TARGETS) * 2
