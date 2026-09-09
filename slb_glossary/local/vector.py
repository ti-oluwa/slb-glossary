"""
Local semantic search API. Uses cosine similarity over `model2vec`-embedded terms.

Backed by the `sqlite-vec` SQLite extension (a `vec0` virtual table).
`embed_terms` computes and stores each locally stored term's vector.
`vector_search` embeds a query the same way and ranks stored terms
against it.

Install the `semantic` extra to use anything here:

```bash
`pip install slb-glossary[semantic]`.
```
"""

import hashlib
import logging
import time
import typing
from collections.abc import Collection

from slb_glossary.constants import constants
from slb_glossary.embeddings import build_embed_text, embed, embedding_dim
from slb_glossary.errors import DatabaseError
from slb_glossary.local.connection import transaction
from slb_glossary.local.types import Database
from slb_glossary.phrasing import clean_query
from slb_glossary.types import SearchResult

logger = logging.getLogger(__name__)

__all__ = ["delete_embeddings", "embed_terms", "vector_search"]

VECTOR_TABLE = "terms_vec"
VECTOR_META_TABLE = "terms_vec_meta"
"""
Plain (non-`vec0`) table tracking what each `VECTOR_TABLE` row's vector
was actually computed from, `(rowid, content_hash, model)`. `rowid`
alone can't tell a stored vector apart from a stale one, a term's
`(term, definition, topic)` can change (e.g. a re-sync updates an
existing row via `ON CONFLICT DO UPDATE`, keeping its `rowid`) without
`rowid`'s mere presence in `VECTOR_TABLE` changing at all. This table
is what `embed_terms`'s `only_missing=True` checks against instead, to
tell an already embedded term apart from an embedded term, but for different content".
"""


async def load_extension(db: Database) -> typing.Any:
    """
    Load the `sqlite-vec` extension onto `db`'s connection.

    Idempotent and cheap to call.

    :param db: The local database to prepare.
    :return: The imported `sqlite_vec` module.
    :raises DatabaseError: If `sqlite-vec` is not installed, or the
        installed SQLite build can not load extensions.
    """
    try:
        import sqlite_vec  # type: ignore[import]
    except ImportError as exc:
        raise DatabaseError(
            "Semantic search needs the `sqlite-vec` package, which is not "
            "installed. Install it with `pip install slb-glossary[semantic]`."
        ) from exc

    try:
        await db.connection.enable_load_extension(True)
        await db.connection.load_extension(sqlite_vec.loadable_path())
    except Exception as exc:
        raise DatabaseError(
            "Could not load the `sqlite-vec` SQLite extension. The "
            "installed SQLite build may have extension loading disabled."
        ) from exc
    finally:
        await db.connection.enable_load_extension(False)
    return sqlite_vec


async def check_table_exists(db: Database) -> bool:
    """Check whether the local vector table has been created yet."""
    async with db.connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (VECTOR_TABLE,)
    ) as cursor:
        return await cursor.fetchone() is not None


async def ensure_table(db: Database) -> None:
    """
    Load `sqlite-vec` and create the local vector table (and its
    content-hash tracking table) if missing.

    Also resolves `embedding_dim()`, which loads the embedding model, so
    only call this where a term or query is actually about to be embedded.

    :param db: The local database to prepare.
    :raises DatabaseError: If `sqlite-vec` is not installed, or its
        extension can not be loaded.
    :raises EmbeddingError: If `model2vec` is not installed, or the
        embedding model's real output size does not match `constants.embedding_dim`.
    """
    await load_extension(db)
    dim = embedding_dim()
    # Keyed by `rowid`, matching `terms.rowid`, not by `url`. A page with
    # several definitions (one per topic) shares one `url` across several
    # `terms` rows, so `url` alone can not identify which row's embedding
    # this is. `vec0` (like every SQLite table) already has an implicit
    # `rowid`, so no separate PK column needed to use it as the join key.
    await db.connection.execute(
        f"CREATE VIRTUAL TABLE IF NOT EXISTS {VECTOR_TABLE} USING vec0("
        f"embedding FLOAT[{dim}] distance_metric=cosine)"
    )
    await db.connection.execute(
        f"CREATE TABLE IF NOT EXISTS {VECTOR_META_TABLE} ("
        f"rowid INTEGER PRIMARY KEY, content_hash TEXT NOT NULL, model TEXT NOT NULL)"
    )
    await db.connection.commit()


async def ensure_meta_table(db: Database) -> None:
    """Create `VECTOR_META_TABLE` if missing, without touching `VECTOR_TABLE` or loading the embedding model."""
    await db.connection.execute(
        f"CREATE TABLE IF NOT EXISTS {VECTOR_META_TABLE} ("
        f"rowid INTEGER PRIMARY KEY, content_hash TEXT NOT NULL, model TEXT NOT NULL)"
    )


async def clear(db: Database) -> None:
    """
    Delete every stored embedding (and its tracked content hash), if the vector table exists at all.

    :param db: The local database to clear.
    """
    try:
        if not await check_table_exists(db):
            return
        await load_extension(db)
    except DatabaseError:
        logger.warning(
            "Local vector table exists but `sqlite-vec` could not be "
            "loaded to clear it; leaving it as-is."
        )
        return

    await ensure_meta_table(db)
    async with transaction(db):
        await db.connection.execute(f"DELETE FROM {VECTOR_TABLE}")
        await db.connection.execute(f"DELETE FROM {VECTOR_META_TABLE}")


def compute_content_hash(text: str) -> str:
    """
    Hash of the exact text that would be embedded for a term.

    Hashing the constructed embed text itself, rather than the raw
    `(term, definition, topic)` fields separately, means a future change
    to `build_embed_text` (e.g. a different field representation)
    invalidates every stored vector automatically too, not just a
    change to the underlying data. Which is what we want.

    :param text: The text `build_embed_text` produced for a row.
    :return: A hex digest identifying that exact text.
    """
    return hashlib.md5(text.encode("utf-8"), usedforsecurity=False).hexdigest()


async def embed_terms(
    db: Database,
    *,
    urls: Collection[str] | None = None,
    topic: str | None = None,
    fuzzy: bool = False,
    only_missing: bool = True,
    batch_size: int | None = None,
) -> int:
    """
    Compute and store embeddings for locally stored terms, for
    `vector_search`/`hybrid_search`.

    A term with several stored definitions (one per topic, all sharing
    one URL, see `slb_glossary.local.upsert_results`) gets one embedding
    per definition, keyed by that row's own `rowid`, since each
    definition's text (and therefore its embedding) genuinely differs.

    :param db: The local database to read terms from and write vectors to.
    :param urls: Only (re-)embed rows at these URLs. `None` (the default)
        considers every locally stored row. A URL with several stored
        definitions embeds all of them, not just one. Combines with
        `topic` (a row must match both, if both are given).
    :param topic: Only (re-)embed rows filed under this topic, or several
        comma-separated topics. `None` (the default) does not filter by
        topic. Combines with `urls` (a row must match both, if both are
        given).
    :param fuzzy: If `True`, resolve `topic` against topics actually
        stored locally (tolerating minor misspellings/partial names),
        the same as `slb_glossary.local.search`'s own `fuzzy`. Has no
        effect if `topic` is not given.
    :param only_missing: If `True` (the default), skip a row whose
        current `(term, definition, topic)` content already matches
        what its stored vector was computed from (tracked in
        `VECTOR_META_TABLE`, not just whether a vector merely exists for
        its `rowid`), so a repeat call after a sync only pays for what's
        newly added or changed, including a changed
        `constants.embedding_model`, which invalidates every row's
        tracked content hash at once. Pass `False` to unconditionally
        re-embed everything in scope regardless.
    :param batch_size: Rows embedded per model call. `None` (the
        default) uses `constants.embed_batch_size`.
    :return: Number of rows newly embedded.
    :raises DatabaseError: If `sqlite-vec` is not installed, or its
        extension can not be loaded.
    :raises EmbeddingError: If `model2vec` is not installed, or the
        embedding model's output size does not match `constants.embedding_dim`.
    """
    await ensure_table(db)
    resolved_batch_size = batch_size if batch_size is not None else constants.embed_batch_size
    model = constants.embedding_model

    sql = (
        "SELECT terms.rowid AS rowid, terms.term, terms.definition, terms.topic, "
        f"meta.content_hash AS stored_hash, meta.model AS stored_model "
        "FROM terms "
        f"LEFT JOIN {VECTOR_META_TABLE} AS meta ON meta.rowid = terms.rowid"
    )
    params: list[typing.Any] = []
    conditions: list[str] = []
    if urls:
        placeholders = ", ".join("?" for _ in urls)
        conditions.append(f"terms.url IN ({placeholders})")
        params.extend(urls)

    if topic:
        from slb_glossary.local.api import resolve_topic

        resolved_topic = await resolve_topic(db, topic, fuzzy)
        if resolved_topic:
            topic_names = [name.strip() for name in resolved_topic.split(",") if name.strip()]
            placeholders = ", ".join("?" for _ in topic_names)
            conditions.append(f"terms.topic COLLATE NOCASE IN ({placeholders})")
            params.extend(topic_names)
        else:
            # `topic` was given but resolved to nothing (e.g. `fuzzy=True`
            # with no close-enough stored topic) so we match no rows, rather
            # than silently ignoring the filter and embedding everything.
            logger.debug("`embed_terms`: topic %r resolved to nothing; embedding no rows", topic)
            return 0

    if conditions:
        sql += " WHERE " + " AND ".join(conditions)

    async with db.connection.execute(sql, params) as cursor:
        candidates = tuple(await cursor.fetchall())

    if not candidates:
        logger.debug("`embed_terms`: nothing to embed")
        return 0

    # `only_missing` means "missing or stale", not just "no vector
    # exists at all". A row's stored content hash/model has to match
    # what's about to be embedded for it to actually be skipped.
    rows: list[typing.Any] = []
    texts: list[str] = []
    for row in candidates:
        text = build_embed_text(row["term"], row["definition"], row["topic"])
        if (
            only_missing
            and row["stored_hash"] == compute_content_hash(text)
            and row["stored_model"] == model
        ):
            continue
        rows.append(row)
        texts.append(text)

    if not rows:
        logger.debug(
            "`embed_terms`: nothing to embed (%d row(s) already up to date)", len(candidates)
        )
        return 0

    started_at = time.monotonic()
    embedded = 0
    for start in range(0, len(rows), resolved_batch_size):
        batch = rows[start : start + resolved_batch_size]
        batch_texts = texts[start : start + resolved_batch_size]
        vectors = embed(batch_texts)
        rowids = [row["rowid"] for row in batch]
        # `vec0` does not support `ON CONFLICT`/`INSERT OR REPLACE` as an
        # upsert. We need to delete first so a re-embedded row does not just
        # fail to insert on top of its old vector.
        placeholders = ", ".join("?" for _ in rowids)
        async with transaction(db):
            await db.connection.execute(
                f"DELETE FROM {VECTOR_TABLE} WHERE rowid IN ({placeholders})", rowids
            )
            await db.connection.executemany(
                f"INSERT INTO {VECTOR_TABLE}(rowid, embedding) VALUES (?, ?)",
                [
                    (row["rowid"], vector.astype("float32").tobytes())
                    for row, vector in zip(batch, vectors, strict=True)
                ],
            )
            await db.connection.executemany(
                f"INSERT INTO {VECTOR_META_TABLE}(rowid, content_hash, model) VALUES (?, ?, ?) "
                "ON CONFLICT(rowid) DO UPDATE SET content_hash=excluded.content_hash, model=excluded.model",
                [
                    (row["rowid"], compute_content_hash(text), model)
                    for row, text in zip(batch, batch_texts, strict=True)
                ],
            )
        embedded += len(batch)

    elapsed = time.monotonic() - started_at
    logger.info(
        "Embedded %d row(s) in %.3fs (avg %.3fs/row)", embedded, elapsed, elapsed / embedded
    )
    return embedded


async def delete_embeddings(db: Database, *, urls: Collection[str] | None = None) -> None:
    """
    Delete stored embeddings, optionally scoped to `urls`.

    A no-op if no embeddings have ever been stored (i.e `embed_terms` was
    never called). So this is safe to call speculatively.

    :param db: The local database to write to.
    :param urls: If given, only delete embeddings for rows at these URLs
        (every stored definition at that URL, not just one). Otherwise
        delete every stored embedding.
    """
    await load_extension(db)
    if not await check_table_exists(db):
        return
    await ensure_meta_table(db)

    if urls:
        placeholders = ", ".join("?" for _ in urls)
        async with transaction(db):
            await db.connection.execute(
                f"""
                DELETE FROM {VECTOR_TABLE} WHERE rowid IN (
                    SELECT rowid FROM terms WHERE url IN ({placeholders})
                )
                """,
                list(urls),
            )
            await db.connection.execute(
                f"""
                DELETE FROM {VECTOR_META_TABLE} WHERE rowid IN (
                    SELECT rowid FROM terms WHERE url IN ({placeholders})
                )
                """,
                list(urls),
            )
    else:
        async with transaction(db):
            await db.connection.execute(f"DELETE FROM {VECTOR_TABLE}")
            await db.connection.execute(f"DELETE FROM {VECTOR_META_TABLE}")


async def vector_search(
    db: Database,
    query: str,
    *,
    topic: str | None = None,
    start_letter: str | None = None,
    language: str | None = None,
    limit: int | None = 10,
    fuzzy: bool = False,
    exclude: Collection[str] | None = None,
    min_similarity: float | None = None,
) -> list[tuple[SearchResult, float]]:
    """
    Rank locally stored, embedded terms by semantic similarity to `query`.

    Purely semantic. A paraphrase or a related concept can outrank a
    result that shares no words with `query` at all, which lexical
    search can never do. It also has no equivalent of lexical search's
    exact/prefix name tier, so a term named exactly what you searched
    for is not guaranteed to rank first.

    Prefer `slb_glossary.local.hybrid_search` unless you specifically
    want ranking with no lexical signal mixed in.

    Only terms already embedded via `embed_terms` are considered. A term
    synced or imported since the last `embed_terms` call is invisible here.

    :param db: The local database to search.
    :param query: Free-text query.
    :param topic: Restrict results to this topic, or several
        comma-separated topics (case-insensitive exact match by default).
    :param start_letter: Restrict results to terms starting with this letter.
    :param language: Restrict results to this glossary language edition
        (e.g. `"en"`/`"es"`). `None` (the default) does not filter by language.
    :param limit: Maximum number of results. `None` for unlimited (every
        embedded term, ranked).
    :param fuzzy: If `True`, tolerate minor misspellings/partial names in
        `topic` by resolving it against locally stored topic names first.
        Has no effect if `topic` is falsy.
    :param exclude: URLs and/or term names to leave out of the results
        entirely.
    :param min_similarity: Drop candidates with a cosine similarity below
        this. `None` (the default) applies no floor. Pass
        `constants.semantic_similarity_floor` to exclude low-confidence
        matches instead. Applied before `limit`.
    :return: `(result, similarity)` pairs, most similar first.
        `similarity` is a cosine similarity, in `[-1.0, 1.0]` in theory
        and close to `[0.0, 1.0]` in practice for real text. This is not
        the `[0.0, 1.0]`-calibrated score `lexical_search`/`hybrid_search` return.
    :raises DatabaseError: If `sqlite-vec` is not installed, or its
        extension can not be loaded.
    :raises EmbeddingError: If `model2vec` is not installed, or the
        embedding model's output size does not match `constants.embedding_dim`.
    """
    await ensure_table(db)
    started_at = time.monotonic()

    from slb_glossary.local.api import apply_sql_exclude, resolve_topic, row_to_result

    normalized_query = clean_query(query)
    query_vector = embed([normalized_query])[0].astype("float32").tobytes()
    resolved_topic = await resolve_topic(db, topic, fuzzy, language=language)

    pool = limit if limit else constants.hybrid_candidate_pool
    k = pool * constants.hybrid_overfetch_factor

    sql = f"""
        WITH matches AS (
            SELECT rowid, distance FROM {VECTOR_TABLE}
            WHERE embedding MATCH ? AND k = ?
            ORDER BY distance
        )
        SELECT terms.*, matches.distance AS distance
        FROM matches JOIN terms ON terms.rowid = matches.rowid
        WHERE 1 = 1
    """
    params: list[typing.Any] = [query_vector, k]

    if resolved_topic:
        topics = [name.strip() for name in resolved_topic.split(",") if name.strip()]
        if topics:
            placeholders = ", ".join("?" for _ in topics)
            sql += f" AND terms.topic COLLATE NOCASE IN ({placeholders})"
            params.extend(topics)

    if start_letter:
        sql += " AND terms.term COLLATE NOCASE LIKE ?"
        params.append(f"{start_letter}%")

    if language:
        sql += " AND terms.language = ?"
        params.append(language)

    sql = apply_sql_exclude(sql, params, exclude, url_column="terms.url", term_column="terms.term")

    sql += " ORDER BY matches.distance ASC"

    async with db.connection.execute(sql, params) as cursor:
        rows = await cursor.fetchall()

    scored = [(row_to_result(row), 1.0 - row["distance"]) for row in rows]
    if min_similarity is not None:
        scored = [
            (result, similarity) for result, similarity in scored if similarity >= min_similarity
        ]
    if limit:
        scored = scored[:limit]

    elapsed = time.monotonic() - started_at
    logger.debug(
        "Local `vector_search` for %r yielded %d candidate(s) in %.3fs",
        normalized_query,
        len(scored),
        elapsed,
    )
    return scored
