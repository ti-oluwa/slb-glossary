"""Tests for `local.vector`: table lifecycle, `embed_terms`, `delete_embeddings`, `vector_search`."""

import builtins
import types
import typing

import pytest

from slb_glossary.constants import constants
from slb_glossary.errors import DatabaseError
from slb_glossary.local.api import upsert_results
from slb_glossary.local.types import Database
from slb_glossary.local.vector import (
    VECTOR_TABLE,
    check_table_exists,
    clear,
    delete_embeddings,
    embed_terms,
    ensure_table,
    load_extension,
    vector_search,
)
from tests.factories import make_search_result
from tests.mocks import MockEmbeddings

pytestmark = pytest.mark.unit


@pytest.mark.anyio
class TestLoadExtension:
    async def test_returns_the_sqlite_vec_module(self, db: Database) -> None:
        """Returns the imported `sqlite_vec` module on success."""
        module = await load_extension(db)
        assert module.__name__ == "sqlite_vec"

    async def test_raises_database_error_if_sqlite_vec_not_installed(
        self, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A missing `sqlite_vec` package raises `DatabaseError`."""
        real_import = builtins.__import__

        def mock_import(name: str, *args: typing.Any, **kwargs: typing.Any) -> types.ModuleType:
            if name == "sqlite_vec":
                raise ImportError("no such module")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)
        with pytest.raises(DatabaseError, match="sqlite-vec"):
            await load_extension(db)

    async def test_disables_extension_loading_again_afterward(self, db: Database) -> None:
        """`enable_load_extension(False)` runs even on success, via the `finally`."""
        # No direct getter for this pragma-like state via aiosqlite; instead,
        # confirm a *second* load still succeeds (would only matter if the
        # first left loading permanently disabled in some broken way).
        await load_extension(db)
        await load_extension(db)  # should not raise


@pytest.mark.anyio
class TestCheckTableExistsAndEnsureTable:
    async def test_table_does_not_exist_initially(self, db: Database) -> None:
        """A freshly opened database has no vector table yet."""
        assert await check_table_exists(db) is False

    async def test_ensure_table_creates_it(
        self, db: Database, mock_embeddings: MockEmbeddings
    ) -> None:
        """`ensure_table` creates the vector table."""
        await ensure_table(db)
        assert await check_table_exists(db) is True

    async def test_ensure_table_is_idempotent(
        self, db: Database, mock_embeddings: MockEmbeddings
    ) -> None:
        """Calling `ensure_table` twice doesn't raise."""
        await ensure_table(db)
        await ensure_table(db)  # should not raise


@pytest.mark.anyio
class TestClear:
    async def test_no_op_when_table_does_not_exist(self, db: Database) -> None:
        """A no-op (not an error) when the vector table was never created."""
        await clear(db)  # should not raise
        assert await check_table_exists(db) is False

    async def test_deletes_every_stored_embedding(
        self, db: Database, mock_embeddings: MockEmbeddings
    ) -> None:
        """Deletes every row from the vector table, once it exists."""
        await upsert_results(db, [make_search_result(url="https://x.com/a", term="Porosity")])
        await embed_terms(db)
        await clear(db)
        async with db.connection.execute(f"SELECT COUNT(*) AS n FROM {VECTOR_TABLE}") as cursor:
            (row,) = await cursor.fetchall()
        assert row["n"] == 0

    async def test_leaves_table_as_is_if_sqlite_vec_cannot_load(
        self, db: Database, mock_embeddings: MockEmbeddings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If the table exists but `sqlite-vec` can't be loaded, logs and returns quietly."""
        await ensure_table(db)

        async def bad_load_extension(db: Database) -> typing.NoReturn:
            raise DatabaseError("cannot load")

        monkeypatch.setattr("slb_glossary.local.vector.load_extension", bad_load_extension)
        await clear(db)  # should not raise


@pytest.mark.anyio
class TestEmbedTerms:
    async def test_embeds_every_term_by_default(
        self, db: Database, mock_embeddings: MockEmbeddings
    ) -> None:
        """With no filters, embeds every locally stored row."""
        await upsert_results(db, [make_search_result(url="https://x.com/a", term="Porosity")])
        embedded = await embed_terms(db)
        assert embedded == 1

    async def test_returns_zero_when_nothing_to_embed(
        self, db: Database, mock_embeddings: MockEmbeddings
    ) -> None:
        """Returns `0` (no error) when there are no rows to embed."""
        assert await embed_terms(db) == 0

    async def test_a_failed_batch_rolls_back_rather_than_orphaning_a_deleted_vector(
        self, db: Database, mock_embeddings: MockEmbeddings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        A batch's `DELETE` (re-embedding an already-embedded row) and its
        replacement `INSERT`s are one atomic unit: if the `INSERT` that
        restores the vector fails, the `DELETE` is rolled back too,
        rather than leaving that row's old vector deleted with nothing
        to replace it.
        """
        await upsert_results(db, [make_search_result(url="https://x.com/a", term="Porosity")])
        await embed_terms(db)  # a real vector now exists for this row

        original_executemany = db.connection.executemany
        call_count = 0

        async def flaky_executemany(sql: str, params: typing.Any) -> typing.Any:
            nonlocal call_count
            call_count += 1
            # Fail on the first `executemany` of the batch - the actual
            # vector re-insert, right after the `DELETE` already ran.
            if call_count == 1:
                raise RuntimeError("simulated failure")
            return await original_executemany(sql, params)

        monkeypatch.setattr(db.connection, "executemany", flaky_executemany)

        with pytest.raises(RuntimeError, match="simulated failure"):
            await embed_terms(db, only_missing=False)

        monkeypatch.undo()
        async with db.connection.execute(f"SELECT COUNT(*) AS n FROM {VECTOR_TABLE}") as cursor:
            row = await cursor.fetchone()

        assert row is not None
        assert row["n"] == 1  # the old vector is still there, not deleted-and-orphaned

    async def test_only_missing_skips_already_embedded_rows(
        self, db: Database, mock_embeddings: MockEmbeddings
    ) -> None:
        """`only_missing=True` (the default) skips a row that's already embedded."""
        await upsert_results(db, [make_search_result(url="https://x.com/a", term="Porosity")])
        await embed_terms(db)
        second_call = await embed_terms(db)
        assert second_call == 0

    async def test_only_missing_false_reembeds_everything(
        self, db: Database, mock_embeddings: MockEmbeddings
    ) -> None:
        """`only_missing=False` re-embeds rows even if already embedded."""
        await upsert_results(db, [make_search_result(url="https://x.com/a", term="Porosity")])
        await embed_terms(db)
        second_call = await embed_terms(db, only_missing=False)
        assert second_call == 1

    async def test_only_missing_reembeds_a_row_whose_content_changed(
        self, db: Database, mock_embeddings: MockEmbeddings
    ) -> None:
        """
        A row whose `(term, definition, topic)` changed since it was last
        embedded (e.g. a re-sync updating it in place, same rowid) is not
        skipped by `only_missing=True`, even though a vector already
        exists for that rowid.
        """
        await upsert_results(
            db,
            [
                make_search_result(
                    url="https://x.com/a", term="Porosity", definition="Old definition."
                )
            ],
        )
        await embed_terms(db)

        await upsert_results(
            db,
            [
                make_search_result(
                    url="https://x.com/a", term="Porosity", definition="New definition."
                )
            ],
        )
        second_call = await embed_terms(db)
        assert second_call == 1

    async def test_only_missing_skips_a_row_whose_content_is_unchanged(
        self, db: Database, mock_embeddings: MockEmbeddings
    ) -> None:
        """A row re-upserted with identical content is still skipped by `only_missing=True`."""
        await upsert_results(
            db, [make_search_result(url="https://x.com/a", term="Porosity", definition="Same.")]
        )
        await embed_terms(db)

        await upsert_results(
            db, [make_search_result(url="https://x.com/a", term="Porosity", definition="Same.")]
        )
        second_call = await embed_terms(db)
        assert second_call == 0

    async def test_only_missing_reembeds_everything_after_a_model_change(
        self, db: Database, mock_embeddings: MockEmbeddings
    ) -> None:
        """Changing `constants.embedding_model` invalidates every row's tracked content hash."""
        await upsert_results(db, [make_search_result(url="https://x.com/a", term="Porosity")])
        await embed_terms(db)

        original_model = constants.embedding_model
        try:
            constants.embedding_model = "a-different-model"
            second_call = await embed_terms(db)
        finally:
            constants.embedding_model = original_model
        assert second_call == 1

    async def test_urls_filter_restricts_which_rows_are_embedded(
        self, db: Database, mock_embeddings: MockEmbeddings
    ) -> None:
        """`urls` restricts embedding to rows at those URLs."""
        await upsert_results(
            db,
            [
                make_search_result(url="https://x.com/a", term="Alpha"),
                make_search_result(url="https://x.com/b", term="Bravo"),
            ],
        )
        embedded = await embed_terms(db, urls=["https://x.com/a"])
        assert embedded == 1

    async def test_topic_filter_restricts_which_rows_are_embedded(
        self, db: Database, mock_embeddings: MockEmbeddings
    ) -> None:
        """`topic` restricts embedding to rows filed under that topic."""
        await upsert_results(
            db,
            [
                make_search_result(url="https://x.com/a", term="Alpha", topic="Geology"),
                make_search_result(url="https://x.com/b", term="Bravo", topic="Drilling"),
            ],
        )
        embedded = await embed_terms(db, topic="Geology")
        assert embedded == 1

    async def test_topic_and_urls_combine_with_and(
        self, db: Database, mock_embeddings: MockEmbeddings
    ) -> None:
        """A row must match both `urls` and `topic` when both are given."""
        await upsert_results(
            db,
            [
                make_search_result(url="https://x.com/a", term="Alpha", topic="Geology"),
                make_search_result(url="https://x.com/b", term="Bravo", topic="Geology"),
            ],
        )
        embedded = await embed_terms(db, urls=["https://x.com/a"], topic="Geology")
        assert embedded == 1

    async def test_topic_fuzzy_resolves_against_stored_topics(
        self, db: Database, mock_embeddings: MockEmbeddings
    ) -> None:
        """`fuzzy=True` resolves a misspelled `topic` against stored topic names."""
        await upsert_results(
            db, [make_search_result(url="https://x.com/a", term="Alpha", topic="Geology")]
        )
        embedded = await embed_terms(db, topic="geologyy", fuzzy=True)
        assert embedded == 1

    async def test_topic_with_no_match_embeds_nothing(
        self, db: Database, mock_embeddings: MockEmbeddings
    ) -> None:
        """A `topic` matching no stored rows embeds `0`, not an error."""
        await upsert_results(
            db, [make_search_result(url="https://x.com/a", term="Alpha", topic="Geology")]
        )
        embedded = await embed_terms(db, topic="Nonexistent Topic")
        assert embedded == 0

    async def test_topic_fuzzy_with_no_close_match_embeds_nothing(
        self, db: Database, mock_embeddings: MockEmbeddings
    ) -> None:
        """`fuzzy=True` with nothing close enough stored embeds `0`, not everything."""
        await upsert_results(
            db, [make_search_result(url="https://x.com/a", term="Alpha", topic="Geology")]
        )
        embedded = await embed_terms(db, topic="Completely Unrelated", fuzzy=True)
        assert embedded == 0

    async def test_respects_batch_size(
        self, db: Database, mock_embeddings: MockEmbeddings
    ) -> None:
        """A small `batch_size` still embeds every row, across multiple internal batches."""
        await upsert_results(
            db, [make_search_result(url=f"https://x.com/{i}", term=f"T{i}") for i in range(5)]
        )
        embedded = await embed_terms(db, batch_size=2)
        assert embedded == 5


@pytest.mark.anyio
class TestDeleteEmbeddings:
    async def test_no_op_when_table_does_not_exist(self, db: Database) -> None:
        """A no-op when the vector table was never created."""
        await delete_embeddings(db)  # should not raise

    async def test_deletes_every_embedding_with_no_urls(
        self, db: Database, mock_embeddings: MockEmbeddings
    ) -> None:
        """With no `urls`, deletes every stored embedding."""
        await upsert_results(db, [make_search_result(url="https://x.com/a", term="Porosity")])
        await embed_terms(db)
        await delete_embeddings(db)
        async with db.connection.execute(f"SELECT COUNT(*) AS n FROM {VECTOR_TABLE}") as cursor:
            (row,) = await cursor.fetchall()
        assert row["n"] == 0

    async def test_urls_scoped_deletion_only_removes_matching_rows(
        self, db: Database, mock_embeddings: MockEmbeddings
    ) -> None:
        """`urls` restricts deletion to embeddings for rows at those URLs."""
        await upsert_results(
            db,
            [
                make_search_result(url="https://x.com/a", term="Alpha"),
                make_search_result(url="https://x.com/b", term="Bravo"),
            ],
        )
        await embed_terms(db)
        await delete_embeddings(db, urls=["https://x.com/a"])
        async with db.connection.execute(f"SELECT COUNT(*) AS n FROM {VECTOR_TABLE}") as cursor:
            (row,) = await cursor.fetchall()
        assert row["n"] == 1


@pytest.mark.anyio
class TestVectorSearch:
    async def test_ranks_by_cosine_similarity_best_first(
        self, db: Database, mock_embeddings: MockEmbeddings
    ) -> None:
        """Results come back ordered by cosine similarity to the query, best first."""
        await upsert_results(
            db,
            [
                make_search_result(
                    url="https://x.com/a", term="Close", definition=None, topic=None
                ),
                make_search_result(url="https://x.com/b", term="Far", definition=None, topic=None),
            ],
        )
        mock_embeddings.set("Close", [1.0, 0.0, 0.0, 0.0])
        mock_embeddings.set("Far", [0.0, 1.0, 0.0, 0.0])
        mock_embeddings.set("query", [0.9, 0.1, 0.0, 0.0])
        await embed_terms(db)

        results = await vector_search(db, "query")
        assert [r.term for r, _ in results] == ["Close", "Far"]
        assert results[0][1] > results[1][1]

    async def test_similarity_is_one_minus_distance(
        self, db: Database, mock_embeddings: MockEmbeddings
    ) -> None:
        """An identical vector's similarity comes back as (very close to) `1.0`."""
        await upsert_results(
            db,
            [make_search_result(url="https://x.com/a", term="Exact", definition=None, topic=None)],
        )
        mock_embeddings.set("Exact", [1.0, 0.0, 0.0, 0.0])
        mock_embeddings.set("query", [1.0, 0.0, 0.0, 0.0])
        await embed_terms(db)

        [(_, similarity)] = await vector_search(db, "query")
        assert similarity == pytest.approx(1.0, abs=1e-4)

    async def test_only_embedded_terms_are_considered(
        self, db: Database, mock_embeddings: MockEmbeddings
    ) -> None:
        """A term never passed through `embed_terms` is invisible to `vector_search`."""
        await upsert_results(db, [make_search_result(url="https://x.com/a", term="Unembedded")])
        results = await vector_search(db, "query")
        assert results == []

    async def test_respects_limit(self, db: Database, mock_embeddings: MockEmbeddings) -> None:
        """`limit` caps the number of results."""
        await upsert_results(
            db, [make_search_result(url=f"https://x.com/{i}", term=f"T{i}") for i in range(5)]
        )
        await embed_terms(db)
        results = await vector_search(db, "query", limit=2)
        assert len(results) == 2

    async def test_respects_topic_filter(
        self, db: Database, mock_embeddings: MockEmbeddings
    ) -> None:
        """`topic` restricts results to that topic."""
        await upsert_results(
            db,
            [
                make_search_result(url="https://x.com/a", term="Alpha", topic="Geology"),
                make_search_result(url="https://x.com/b", term="Bravo", topic="Drilling"),
            ],
        )
        await embed_terms(db)
        results = await vector_search(db, "query", topic="Geology")
        assert [r.term for r, _ in results] == ["Alpha"]

    async def test_respects_start_letter_filter(
        self, db: Database, mock_embeddings: MockEmbeddings
    ) -> None:
        """`start_letter` restricts results to terms starting with that letter."""
        await upsert_results(
            db,
            [
                make_search_result(url="https://x.com/a", term="Alpha"),
                make_search_result(url="https://x.com/b", term="Bravo"),
            ],
        )
        await embed_terms(db)
        results = await vector_search(db, "query", start_letter="A")
        assert [r.term for r, _ in results] == ["Alpha"]

    async def test_respects_language_filter(
        self, db: Database, mock_embeddings: MockEmbeddings
    ) -> None:
        """`language` restricts results to that glossary language edition."""
        await upsert_results(
            db,
            [
                make_search_result(url="https://x.com/a", term="Alpha", language="en"),
                make_search_result(url="https://x.com/b", term="Alfa", language="es"),
            ],
        )
        await embed_terms(db)
        results = await vector_search(db, "query", language="en")
        assert [r.term for r, _ in results] == ["Alpha"]

    async def test_respects_exclude(self, db: Database, mock_embeddings: MockEmbeddings) -> None:
        """`exclude` filters out matching URLs/term names."""
        await upsert_results(
            db,
            [
                make_search_result(url="https://x.com/a", term="Alpha"),
                make_search_result(url="https://x.com/b", term="Bravo"),
            ],
        )
        await embed_terms(db)
        results = await vector_search(db, "query", exclude=["Alpha"])
        assert [r.term for r, _ in results] == ["Bravo"]

    async def test_fuzzy_topic_resolves_against_stored_topics(
        self, db: Database, mock_embeddings: MockEmbeddings
    ) -> None:
        """`fuzzy=True` resolves a misspelled `topic` against stored topic names."""
        await upsert_results(
            db, [make_search_result(url="https://x.com/a", term="Alpha", topic="Geology")]
        )
        await embed_terms(db)
        results = await vector_search(db, "query", topic="geologyy", fuzzy=True)
        assert [r.term for r, _ in results] == ["Alpha"]

    async def test_min_similarity_none_applies_no_floor(
        self, db: Database, mock_embeddings: MockEmbeddings
    ) -> None:
        """`min_similarity=None` (the default) never drops a candidate, however dissimilar."""
        await upsert_results(
            db,
            [make_search_result(url="https://x.com/a", term="Far", definition=None, topic=None)],
        )
        mock_embeddings.set("Far", [0.0, 1.0, 0.0, 0.0])
        mock_embeddings.set("query", [1.0, 0.0, 0.0, 0.0])
        await embed_terms(db)

        results = await vector_search(db, "query")
        assert [r.term for r, _ in results] == ["Far"]

    async def test_min_similarity_drops_candidates_below_it(
        self, db: Database, mock_embeddings: MockEmbeddings
    ) -> None:
        """A candidate whose similarity is below `min_similarity` is dropped entirely."""
        await upsert_results(
            db,
            [
                make_search_result(
                    url="https://x.com/a", term="Close", definition=None, topic=None
                ),
                make_search_result(url="https://x.com/b", term="Far", definition=None, topic=None),
            ],
        )
        mock_embeddings.set("Close", [1.0, 0.0, 0.0, 0.0])
        mock_embeddings.set("Far", [0.0, 1.0, 0.0, 0.0])
        mock_embeddings.set("query", [1.0, 0.0, 0.0, 0.0])
        await embed_terms(db)

        results = await vector_search(db, "query", min_similarity=0.5)
        assert [r.term for r, _ in results] == ["Close"]

    async def test_min_similarity_can_empty_the_result_set(
        self, db: Database, mock_embeddings: MockEmbeddings
    ) -> None:
        """Nothing clearing `min_similarity` returns an empty list, not the nearest neighbor anyway."""
        await upsert_results(
            db,
            [make_search_result(url="https://x.com/a", term="Far", definition=None, topic=None)],
        )
        mock_embeddings.set("Far", [0.0, 1.0, 0.0, 0.0])
        mock_embeddings.set("query", [1.0, 0.0, 0.0, 0.0])
        await embed_terms(db)

        results = await vector_search(db, "query", min_similarity=0.9)
        assert results == []

    async def test_min_similarity_applied_before_limit(
        self, db: Database, mock_embeddings: MockEmbeddings
    ) -> None:
        """A dropped low-similarity candidate does not use up part of `limit`'s budget."""
        await upsert_results(
            db,
            [
                make_search_result(
                    url="https://x.com/a", term="Close1", definition=None, topic=None
                ),
                make_search_result(
                    url="https://x.com/b", term="Close2", definition=None, topic=None
                ),
                make_search_result(url="https://x.com/c", term="Far", definition=None, topic=None),
            ],
        )
        mock_embeddings.set("Close1", [1.0, 0.0, 0.0, 0.0])
        mock_embeddings.set("Close2", [0.99, 0.01, 0.0, 0.0])
        mock_embeddings.set("Far", [0.0, 1.0, 0.0, 0.0])
        mock_embeddings.set("query", [1.0, 0.0, 0.0, 0.0])
        await embed_terms(db)

        results = await vector_search(db, "query", limit=2, min_similarity=0.5)
        assert {r.term for r, _ in results} == {"Close1", "Close2"}
