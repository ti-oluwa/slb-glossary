"""Root-level fixtures shared by every test module."""

import sys

import pytest

from slb_glossary.constants import Constant, Constants

ALL_BACKENDS = [
    pytest.param(("asyncio", {}), id="asyncio"),
    pytest.param(("asyncio", {"use_uvloop": True}), id="asyncio+uvloop"),
    pytest.param(("trio", {}), id="trio"),
]
ASYNCIO_ONLY_BACKENDS = [
    pytest.param(("asyncio", {}), id="asyncio"),
    pytest.param(
        ("asyncio", {"use_uvloop": True}),
        id="asyncio+uvloop",
        marks=pytest.mark.skipif(
            sys.platform == "win32", reason="uvloop is Unix-only"
        ),
    ),
]


@pytest.fixture(params=ALL_BACKENDS)
def anyio_backend(request: pytest.FixtureRequest) -> tuple[str, dict]:
    """Every backend. Use for tests with no asyncio-specific dependency."""
    return request.param


@pytest.fixture(params=ASYNCIO_ONLY_BACKENDS)
def anyio_backend_asyncio_only(request: pytest.FixtureRequest) -> tuple[str, dict]:
    """asyncio and asyncio+uvloop only.

    Use for anything touching aiosqlite/patchright/FastMCP, none of which
    are trio-safe (verified empirically: opening a real
    ``local.connection.database()`` under a trio ``anyio_backend`` fails
    before any test logic runs, because aiosqlite's connection thread
    hands work back via a raw ``asyncio.Future`` that trio's run loop
    can't recognize). That failure is a fact about the dependency, not
    a bug worth chasing down per test.
    """
    return request.param


@pytest.fixture(autouse=True)
def reset_constants():
    """Snapshot every `Constant` on `Constants` and reset it after each test.

    `Constants` is a process-wide singleton, so a test that overrides a
    constant (`constants.relevance_threshold = 0.9`) or leaves an env var
    set that a cached constant already read would otherwise leak into
    every later test.
    """
    constant_descriptors = [
        value for value in vars(Constants).values() if isinstance(value, Constant)
    ]
    yield
    for descriptor in constant_descriptors:
        descriptor.reset()


@pytest.fixture(autouse=True)
def no_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    """Fail loudly if a non-`live` test reaches the real network.

    Monkeypatches the lowest-level entry points real network access
    would go through (`asyncio.open_connection`, the primitive
    `connectivity.probe` itself calls, and, when available,
    `patchright.async_api.async_playwright`) to raise instead of
    connecting. Deliberately does *not* patch
    `connectivity.has_internet_connection`/`connectivity.probe`
    themselves, since `tests/test_connectivity.py` exercises those
    directly with their own, more targeted mocks. Skipped entirely for
    tests marked `live`, which are meant to reach the real site/browser.
    """
    if request.node.get_closest_marker("live") is not None:
        yield
        return

    import asyncio

    async def _forbidden_open_connection(*args: object, **kwargs: object) -> object:
        raise AssertionError("network access attempted in a non-live test")

    monkeypatch.setattr(asyncio, "open_connection", _forbidden_open_connection)

    try:
        import patchright.async_api as patchright_async_api
    except ImportError:
        yield
        return

    def _forbidden_async_playwright(*args: object, **kwargs: object) -> object:
        raise AssertionError("network access attempted in a non-live test")

    monkeypatch.setattr(
        patchright_async_api, "async_playwright", _forbidden_async_playwright
    )
    yield


@pytest.fixture
def tmp_data_dir(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """A `tmp_path` subdirectory used as the app's data/config dir for a test.

    Monkeypatches `SLB_GLOSSARY_DATA_DIR`/`SLB_GLOSSARY_CONFIG_DIR` so
    `slb_glossary.paths.default_db_path()` and friends never touch the
    real user data directory during a test run.
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("SLB_GLOSSARY_DATA_DIR", str(data_dir))
    monkeypatch.setenv("SLB_GLOSSARY_CONFIG_DIR", str(data_dir))
    return data_dir


@pytest.fixture
def freeze_time(monkeypatch: pytest.MonkeyPatch):
    """Freeze `time.monotonic` and `datetime.now(UTC)` to a fixed instant.

    Returns the frozen `datetime.datetime` (UTC) so a test can assert on
    e.g. a `Metadata.last_synced_at` timestamp it expects to have been
    stamped during the test.
    """
    import datetime
    import time

    frozen_monotonic = 1_000_000.0
    frozen_datetime = datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc)

    monkeypatch.setattr(time, "monotonic", lambda: frozen_monotonic)

    real_datetime = datetime.datetime

    class _FrozenDatetime(real_datetime):
        @classmethod
        def now(cls, tz: datetime.timezone | None = None) -> "_FrozenDatetime":
            if tz is not None:
                return frozen_datetime.astimezone(tz)  # type: ignore[return-value]
            return frozen_datetime  # type: ignore[return-value]

    monkeypatch.setattr(datetime, "datetime", _FrozenDatetime)
    return frozen_datetime
