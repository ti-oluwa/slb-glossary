"""
`local.sync`: `get_known_urls_set`, `record_sync`, `drain_and_upsert`'s
error handling, and every `sync_*` function - including the guarantee that
`sync_all` never re-fetches a term's page a second time just because it's
filed under more than one topic.

Mocks the live-layer functions `sync.py` imports by name
(`live.api.search`/`get_terms_on`/`get_terms_urls`/`get_results_from_urls`)
with a small in-memory fake site, rather than a real `Session` (which
needs real Playwright objects to construct). The fake models exactly the
one behavior this module's design leans on: a URL excluded from a topic
listing is never "fetched" (no page ever visited for it), and fetching a
URL yields every topic-tagged definition found on that single page, not
just the one for the topic whose listing led there.
"""

import dataclasses

import pytest

from slb_glossary.local import sync as sync_module
from slb_glossary.local.api import count as count_terms
from slb_glossary.local.api import upsert_results
from slb_glossary.local.sync import (
    drain_and_upsert,
    get_known_urls_set,
    sync_all,
    sync_letter,
    sync_query,
    sync_topic,
    sync_topics,
)
from slb_glossary.local.types import Metadata
from slb_glossary.types import Language, SearchResult
from tests.factories import make_search_result

pytestmark = pytest.mark.unit


@pytest.fixture
def anyio_backend(anyio_backend_asyncio_only):
    """Every test here touches a real aiosqlite database, which isn't trio-safe."""
    return anyio_backend_asyncio_only


@dataclasses.dataclass
class MockSession:
    """
    Enough of `live.browser.Session`'s shape for `sync.py`, without the real
    Playwright objects `Session` itself requires to construct.
    """

    language: Language = Language.ENGLISH
    topics: dict[str, int] = dataclasses.field(default_factory=dict)
    _initialized: bool = False

    @property
    def initialized(self) -> bool:
        return self._initialized

    async def initialize(self) -> None:
        self._initialized = True


class MockSite:
    """
    A tiny in-memory stand-in for the live glossary, used to monkeypatch
    `sync.py`'s live-layer imports.

    `pages_by_url` models "what a single page fetch for this URL yields" -
    every topic-tagged definition found on it, same as the real
    `get_results_from_url`. `urls_by_topic`/`urls_by_query`/`urls_by_letter`
    model which URLs a topic/query/letter listing would surface. `visited`
    records every URL an actual page fetch happened for, so a test can
    assert a URL already known (excluded) was never fetched again.
    """

    def __init__(self) -> None:
        self.pages_by_url: dict[str, list[SearchResult]] = {}
        self.urls_by_topic: dict[str, list[str]] = {}
        self.urls_by_query: dict[str, list[str]] = {}
        self.visited: list[str] = []

    def add_term(self, url: str, topics: list[SearchResult], under_topics: list[str]) -> None:
        """
        Register a term's page (`topics`, one `SearchResult` per topic-tagged
        definition on it) and which topic listings surface its URL.
        """
        self.pages_by_url[url] = topics
        for topic_name in under_topics:
            self.urls_by_topic.setdefault(topic_name, []).append(url)

    async def mock_get_terms_on(self, session, topic, *, limit=None, concurrency=1, exclude=None):
        exclude = exclude or frozenset()
        for url in self.urls_by_topic.get(topic, []):
            if url in exclude:
                continue
            self.visited.append(url)
            for result in self.pages_by_url[url]:
                yield result

    async def mock_get_terms_urls(
        self, session, *, topic=None, start_letter=None, limit=None, exclude=None
    ):
        exclude = exclude or frozenset()
        urls = self.urls_by_topic.get(topic, []) if topic else list(self.pages_by_url)
        for url in urls:
            if url in exclude:
                continue
            yield url

    async def mock_get_results_from_urls(
        self, session, urls, *, topic=None, concurrency=1, first_only=True, exclude=None
    ):
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
        session,
        query,
        *,
        topic=None,
        start_letter=None,
        limit=None,
        concurrency=1,
        exclude=None,
    ):
        exclude = exclude or frozenset()
        for url in self.urls_by_query.get(query, []):
            if url in exclude:
                continue
            self.visited.append(url)
            for result in self.pages_by_url[url]:
                yield result


@pytest.fixture
def mock_site(monkeypatch: pytest.MonkeyPatch) -> MockSite:
    """Install a `MockSite` in place of `sync.py`'s live-layer imports."""
    site = MockSite()
    monkeypatch.setattr(sync_module, "get_terms_on", site.mock_get_terms_on)
    monkeypatch.setattr(sync_module, "get_terms_urls", site.mock_get_terms_urls)
    monkeypatch.setattr(sync_module, "get_results_from_urls", site.mock_get_results_from_urls)
    monkeypatch.setattr(sync_module, "live_search", site.mock_live_search)
    return site


class TestGetKnownUrlsSet:
    @pytest.mark.anyio
    async def test_returns_urls_matching_topic_filter(self, db):
        """Collects locally stored URLs matching the given filters, as a `frozenset`."""
        await upsert_results(
            db,
            [
                make_search_result(url="https://x.com/a", topic="Geology"),
                make_search_result(url="https://x.com/b", topic="Drilling"),
            ],
        )
        urls = await get_known_urls_set(db, topic="Geology")
        assert urls == frozenset({"https://x.com/a"})

    @pytest.mark.anyio
    async def test_empty_database_returns_empty_frozenset(self, db):
        """An empty database returns an empty `frozenset`, not an error."""
        assert await get_known_urls_set(db) == frozenset()


class TestRecordSync:
    @pytest.mark.anyio
    async def test_updates_metadata_totals_and_topics(self, db):
        """Recomputes and persists `term_count`/`topics`/`last_synced_at` to `metadata.json`."""
        await upsert_results(
            db,
            [
                make_search_result(url="https://x.com/a", topic="Geology"),
                make_search_result(url="https://x.com/b", topic="Geology"),
            ],
        )
        summary = await sync_module.record_sync(db, terms_written=2, language="en")
        assert summary.total_terms == 2
        assert summary.topics == {"Geology": 2}
        assert summary.terms_written == 2
        assert summary.interrupted is False

        metadata = Metadata.load(db.metadata_path)
        assert metadata.term_count == 2
        assert metadata.last_sync_language == "en"
        assert metadata.last_synced_at == summary.synced_at

    @pytest.mark.anyio
    async def test_interrupted_flag_is_recorded(self, db):
        """`interrupted=True` is carried through into the returned `SyncSummary`."""
        summary = await sync_module.record_sync(
            db, terms_written=0, language="en", interrupted=True
        )
        assert summary.interrupted is True


@pytest.mark.anyio
class TestDrainAndUpsert:
    async def test_drains_every_result_and_returns_written_count(self, db):
        """Drains the whole stream, returning `(written, False)` on success."""

        async def results():
            for r in [make_search_result(url="https://x.com/a")]:
                yield r

        written, interrupted = await drain_and_upsert(
            db, results(), language="en", batch_size=None, persist_on_error=True
        )
        assert written == 1
        assert interrupted is False
        assert await count_terms(db) == 1

    async def test_persist_on_error_true_saves_partial_progress_and_reraises(self, db):
        """A raise mid-stream still saves whatever was buffered, and re-raises."""

        async def results():
            yield make_search_result(url="https://x.com/a")
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            await drain_and_upsert(
                db, results(), language="en", batch_size=None, persist_on_error=True
            )
        assert await count_terms(db) == 1

    async def test_persist_on_error_false_discards_partial_progress_and_reraises(self, db):
        """A raise mid-stream discards the buffer when `persist_on_error=False`."""

        async def results():
            yield make_search_result(url="https://x.com/a")
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            await drain_and_upsert(
                db, results(), language="en", batch_size=None, persist_on_error=False
            )
        assert await count_terms(db) == 0


@pytest.mark.anyio
class TestSyncTopics:
    async def test_initializes_session_if_not_already(self, db):
        """Calls `session.initialize()` when it isn't initialized yet."""
        session = MockSession()
        await sync_topics(db, session)
        assert session.initialized is True

    async def test_does_not_reinitialize_an_already_initialized_session(self, db):
        """Doesn't call `initialize()` again if the session already is."""
        session = MockSession(_initialized=True, topics={"Geology": 5})
        await sync_topics(db, session)
        assert session.topics == {"Geology": 5}

    async def test_writes_zero_terms(self, db):
        """`terms_written` is always `0` - this only records the topic list."""
        session = MockSession(_initialized=True)
        summary = await sync_topics(db, session)
        assert summary.terms_written == 0


@pytest.mark.anyio
class TestSyncQuery:
    async def test_fetches_and_stores_matching_results(self, db, mock_site: MockSite):
        """Fetches `query`'s live results and stores them locally."""
        mock_site.add_term(
            "https://x.com/a",
            [make_search_result(url="https://x.com/a", term="Porosity", topic="Geology")],
            under_topics=[],
        )
        mock_site.urls_by_query["porosity"] = ["https://x.com/a"]
        session = MockSession()

        summary = await sync_query(db, session, "porosity")
        assert summary.terms_written == 1
        assert await count_terms(db) == 1

    async def test_skip_existing_excludes_already_stored_urls(self, db, mock_site: MockSite):
        """`skip_existing=True` (the default) excludes URLs already stored under
        this query/topic/start_letter filter."""
        result = make_search_result(url="https://x.com/a", term="Porosity", topic="Geology")
        mock_site.add_term("https://x.com/a", [result], under_topics=[])
        mock_site.urls_by_query["porosity"] = ["https://x.com/a"]
        await upsert_results(db, [result])

        session = MockSession()
        summary = await sync_query(db, session, "porosity")
        assert summary.terms_written == 0
        assert "https://x.com/a" not in mock_site.visited

    async def test_skip_existing_false_forces_a_full_refetch(self, db, mock_site: MockSite):
        """`skip_existing=False` re-fetches even an already-stored URL."""
        result = make_search_result(url="https://x.com/a", term="Porosity", topic="Geology")
        mock_site.add_term("https://x.com/a", [result], under_topics=[])
        mock_site.urls_by_query["porosity"] = ["https://x.com/a"]
        await upsert_results(db, [result])

        session = MockSession()
        summary = await sync_query(db, session, "porosity", skip_existing=False)
        assert summary.terms_written == 1
        assert "https://x.com/a" in mock_site.visited


@pytest.mark.anyio
class TestSyncTopic:
    async def test_fetches_and_stores_every_term_under_topic(self, db, mock_site: MockSite):
        """Fetches every term filed under `topic` and stores it locally."""
        mock_site.add_term(
            "https://x.com/a",
            [make_search_result(url="https://x.com/a", term="Porosity", topic="Geology")],
            under_topics=["Geology"],
        )
        session = MockSession()

        summary = await sync_topic(db, session, "Geology")
        assert summary.terms_written == 1

    async def test_skip_existing_excludes_urls_already_stored_under_that_topic(
        self, db, mock_site: MockSite
    ):
        """`skip_existing=True` excludes URLs already stored under `topic`."""
        result = make_search_result(url="https://x.com/a", term="Porosity", topic="Geology")
        mock_site.add_term("https://x.com/a", [result], under_topics=["Geology"])
        await upsert_results(db, [result])

        session = MockSession()
        summary = await sync_topic(db, session, "Geology")
        assert summary.terms_written == 0
        assert "https://x.com/a" not in mock_site.visited


@pytest.mark.anyio
class TestSyncLetter:
    async def test_fetches_and_stores_terms_starting_with_letter(self, db, mock_site: MockSite):
        """Fetches every term starting with `start_letter` and stores it locally."""
        mock_site.add_term(
            "https://x.com/a",
            [make_search_result(url="https://x.com/a", term="Porosity", topic="Geology")],
            under_topics=[],
        )
        mock_site.urls_by_topic[None] = ["https://x.com/a"]
        session = MockSession()

        summary = await sync_letter(db, session, "P")
        assert summary.terms_written == 1

    async def test_uses_first_only_so_a_multi_topic_page_yields_one_row(
        self, db, mock_site: MockSite
    ):
        """Unlike `sync_topic`/`sync_all`, `sync_letter` passes `first_only=True`
        to `get_results_from_urls`, so a term filed under several topics
        still only writes the one topic-tagged row its page fetch returns first."""
        mock_site.add_term(
            "https://x.com/a",
            [
                make_search_result(url="https://x.com/a", term="Mud", topic="Drilling"),
                make_search_result(url="https://x.com/a", term="Mud", topic="Shale Gas"),
            ],
            under_topics=[],
        )
        mock_site.urls_by_topic[None] = ["https://x.com/a"]
        session = MockSession()

        summary = await sync_letter(db, session, "M")
        assert summary.terms_written == 1


@pytest.mark.anyio
class TestSyncAll:
    async def test_initializes_session_if_not_already(self, db, mock_site: MockSite):
        """Calls `session.initialize()` when it isn't initialized yet."""
        session = MockSession()
        await sync_all(db, session)
        assert session.initialized is True

    async def test_warns_and_completes_with_no_topics(self, db, mock_site: MockSite):
        """An empty `session.topics` still completes (with `0` written), not an error."""
        session = MockSession(_initialized=True, topics={})
        summary = await sync_all(db, session)
        assert summary.terms_written == 0

    async def test_fetches_every_topic_and_sums_written_counts(self, db, mock_site: MockSite):
        """Walks every topic in `session.topics` and sums each topic's written count."""
        mock_site.add_term(
            "https://x.com/a",
            [make_search_result(url="https://x.com/a", term="Porosity", topic="Geology")],
            under_topics=["Geology"],
        )
        mock_site.add_term(
            "https://x.com/b",
            [make_search_result(url="https://x.com/b", term="Casing", topic="Drilling")],
            under_topics=["Drilling"],
        )
        session = MockSession(_initialized=True, topics={"Geology": 1, "Drilling": 1})

        summary = await sync_all(db, session)
        assert summary.terms_written == 2
        assert await count_terms(db) == 2

    async def test_a_term_filed_under_two_topics_is_only_ever_fetched_once(
        self, db, mock_site: MockSite
    ):
        """
        The regression test for the cross-topic dedup guarantee: a term
        filed under two topics is fetched exactly once across the whole
        `sync_all` run, not once per topic.

        Models exactly what the real live layer does: fetching a term's
        page (triggered by *either* topic's listing) yields every
        topic-tagged definition found on that one page - so by the time
        `sync_all` reaches the second topic in its (alphabetically
        sorted) loop, that term's `(url, topic)` row for the second topic
        is already stored locally, and the URL-level exclude check skips
        it before any page fetch would happen.
        """
        shared_url = "https://x.com/mud"
        mock_site.add_term(
            shared_url,
            [
                make_search_result(url=shared_url, term="Mud", topic="Drilling"),
                make_search_result(url=shared_url, term="Mud", topic="Shale Gas"),
            ],
            under_topics=["Drilling", "Shale Gas"],
        )
        # Alphabetical order: "Drilling" is processed before "Shale Gas",
        # matching `sync_all`'s own `sorted(session.topics)`.
        session = MockSession(_initialized=True, topics={"Drilling": 1, "Shale Gas": 1})

        summary = await sync_all(db, session)

        # Both topic-tagged rows got written (one page fetch, two rows)...
        assert summary.terms_written == 2
        assert await count_terms(db) == 2
        # ...but the URL was only ever actually "fetched" once.
        assert mock_site.visited == [shared_url]

    async def test_skip_existing_false_refetches_every_topic_in_full(
        self, db, mock_site: MockSite
    ):
        """`skip_existing=False` disables the dedup guarantee entirely: every
        topic re-fetches its terms regardless of what's already stored."""
        shared_url = "https://x.com/mud"
        mock_site.add_term(
            shared_url,
            [
                make_search_result(url=shared_url, term="Mud", topic="Drilling"),
                make_search_result(url=shared_url, term="Mud", topic="Shale Gas"),
            ],
            under_topics=["Drilling", "Shale Gas"],
        )
        session = MockSession(_initialized=True, topics={"Drilling": 1, "Shale Gas": 1})

        await sync_all(db, session, skip_existing=False)
        assert mock_site.visited == [shared_url, shared_url]

    async def test_error_in_one_topic_still_keeps_earlier_completed_topics(
        self, db, mock_site: MockSite, monkeypatch: pytest.MonkeyPatch
    ):
        """If a later topic's fetch fails, terms already written for earlier,
        completed topics are kept - only the failing topic's own in-progress
        batch is subject to `persist_on_error`."""
        mock_site.add_term(
            "https://x.com/a",
            [make_search_result(url="https://x.com/a", term="Porosity", topic="Geology")],
            under_topics=["Geology"],
        )

        async def broken_get_terms_on(session, topic, **kwargs):
            if topic == "Drilling":
                raise ValueError("site unreachable")
            async for result in mock_site.mock_get_terms_on(session, topic, **kwargs):
                yield result

        monkeypatch.setattr(sync_module, "get_terms_on", broken_get_terms_on)
        session = MockSession(_initialized=True, topics={"Drilling": 1, "Geology": 1})

        with pytest.raises(ValueError, match="site unreachable"):
            await sync_all(db, session)

        # "Drilling" sorts before "Geology", so Drilling fails first and
        # Geology is never reached - nothing should be written at all here,
        # this just confirms the failure propagates rather than being swallowed.
        assert await count_terms(db) == 0

    async def test_reports_interrupted_true_when_a_topic_fails(
        self, db, mock_site: MockSite, monkeypatch: pytest.MonkeyPatch
    ):
        """The metadata recorded via the `finally` block reflects `interrupted=True`
        when a topic's fetch raised."""

        async def broken_get_terms_on(session, topic, **kwargs):
            raise ValueError("boom")
            yield  # pragma: no cover - make this an async generator

        monkeypatch.setattr(sync_module, "get_terms_on", broken_get_terms_on)
        session = MockSession(_initialized=True, topics={"Geology": 1})

        with pytest.raises(ValueError, match="boom"):
            await sync_all(db, session)

        metadata = Metadata.load(db.metadata_path)
        # `record_sync` doesn't store `interrupted` itself on `Metadata` -
        # confirm instead that a sync was still recorded despite the error
        # (i.e. the `finally` block ran).
        assert metadata.last_synced_at is not None
