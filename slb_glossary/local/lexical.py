"""Lexical (bm25 full-text) local search API."""

import difflib
import logging
import time
import typing
from collections.abc import Collection

from slb_glossary.constants import constants
from slb_glossary.local.types import Database
from slb_glossary.phrasing import clean_query
from slb_glossary.scoring import classify_name_match
from slb_glossary.types import SearchResult
from slb_glossary.utils import normalize_text

logger = logging.getLogger(__name__)

__all__ = ["build_fts_query", "build_fts_query_or", "lexical_search"]


FTS_COLUMN_WEIGHTS: tuple[float, float, float] = (10.0, 1.5, 3.0)
"""bm25() column weights for `terms_fts`'s `(term, definition, topic)` columns, in that order."""


def build_fts_query(query: str) -> str:
    """Turn free text into an FTS5 `MATCH` query: quoted, prefix-matched tokens ANDed together."""
    tokens = query.strip().split()
    if not tokens:
        return '""'
    return " AND ".join('"' + token.replace('"', '""') + '"*' for token in tokens)


def build_fts_query_or(query: str) -> str:
    """Same as `build_fts_query`, but `OR`ed instead of `AND`ed."""
    tokens = query.strip().split()
    if not tokens:
        return '""'
    return " OR ".join('"' + token.replace('"', '""') + '"*' for token in tokens)


async def run_fts_query(
    db: Database,
    match_expression: str,
    *,
    topic: str | None,
    start_letter: str | None,
    language: str | None,
    exclude: Collection[str] | None,
) -> list[typing.Any]:
    """Run one FTS5 `MATCH` query with the given filters applied."""
    from slb_glossary.local.api import apply_sql_exclude

    weights = ", ".join(str(weight) for weight in FTS_COLUMN_WEIGHTS)
    sql = f"""
        SELECT terms.*, bm25(terms_fts, {weights}) AS bm25_score
        FROM terms
        JOIN terms_fts ON terms.rowid = terms_fts.rowid
        WHERE terms_fts MATCH ?
    """
    params: list[typing.Any] = [match_expression]

    if topic:
        topics = [name.strip() for name in topic.split(",") if name.strip()]
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

    sql += " ORDER BY bm25_score ASC LIMIT ?"
    params.append(constants.lexical_candidate_cap)

    async with db.connection.execute(sql, params) as cursor:
        return list(await cursor.fetchall())


async def _find_candidates(
    db: Database,
    query: str,
    *,
    topic: str | None,
    start_letter: str | None,
    language: str | None,
    exclude: Collection[str] | None,
) -> list[typing.Any]:
    """
    Run the FTS5 `MATCH` query and return every matching row (up to
    `constants.lexical_candidate_cap`), with its `bm25_score`.

    Tries `build_fts_query` (AND) first; if that returns nothing and the
    query has more than one token, retries with `build_fts_query_or`
    (OR), so one unmatched token doesn't zero out an otherwise-good match.
    """
    from slb_glossary.local.api import resolve_topic

    resolved_topic = await resolve_topic(db, topic, False, language=language)
    filters = {
        "topic": resolved_topic,
        "start_letter": start_letter,
        "language": language,
        "exclude": exclude,
    }

    rows = await run_fts_query(db, build_fts_query(query), **filters)
    if rows or len(query.split()) <= 1:
        return rows
    return await run_fts_query(db, build_fts_query_or(query), **filters)


def rank_candidates(query: str, rows: list[typing.Any]) -> list[tuple[SearchResult, float]]:
    """
    Tier and score every fetched row, best match first.

    Rows with a `classify_name_match` tier are scored directly off it;
    everything else is ranked by `bm25_score`, min-max normalized against
    the other content-only rows in this result set, into
    `(0.0, constants.content_match_score_cap]`.
    """
    from slb_glossary.local.api import row_to_result

    tiered: list[tuple[SearchResult, float]] = []
    content_rows: list[tuple[typing.Any, float]] = []
    for row in rows:
        match = classify_name_match(query, row["term"])
        if match is not None:
            tiered.append((row_to_result(row), match.score))
        else:
            content_rows.append((row, row["bm25_score"]))

    if content_rows:
        bm25_scores = [score for _, score in content_rows]
        worst = max(bm25_scores)
        best = min(bm25_scores)
        spread = (worst - best) or 1.0
        for row, bm25_score in content_rows:
            score = round(constants.content_match_score_cap * (worst - bm25_score) / spread, 4)
            tiered.append((row_to_result(row), score))

    tiered.sort(key=lambda pair: pair[1], reverse=True)
    return tiered


async def _fuzzy_find_candidates(
    db: Database,
    query: str,
    *,
    topic: str | None,
    start_letter: str | None,
    language: str | None,
    exclude: Collection[str] | None,
) -> list[tuple[SearchResult, float]]:
    """
    Fuzzy-match `query` against stored term names directly, for
    typos FTS5's prefix matching can't tolerate (a typo anywhere but a
    token's start).

    Bounded to the distinct term-name list, not a full-table scan.
    Every result is capped at `constants.fuzzy_match_score_cap`.
    """
    from slb_glossary.local.api import apply_sql_exclude, row_to_result

    query_norm = normalize_text(query)
    if not query_norm:
        return []

    async with db.connection.execute("SELECT DISTINCT term FROM terms") as cursor:
        term_rows = await cursor.fetchall()
    known_terms = [row["term"] for row in term_rows if row["term"]]
    if not known_terms:
        return []

    normalized_to_original: dict[str, str] = {}
    for term in known_terms:
        normalized_to_original.setdefault(normalize_text(term), term)

    close = difflib.get_close_matches(
        query_norm,
        list(normalized_to_original),
        n=constants.fuzzy_candidate_limit,
        cutoff=constants.fuzzy_match_cutoff,
    )
    if not close:
        return []

    originals = [normalized_to_original[term_norm] for term_norm in close]
    placeholders = ", ".join("?" for _ in originals)
    sql = f"SELECT terms.* FROM terms WHERE terms.term IN ({placeholders})"
    params: list[typing.Any] = list(originals)

    if topic:
        topics = [name.strip() for name in topic.split(",") if name.strip()]
        if topics:
            sql += f" AND terms.topic COLLATE NOCASE IN ({', '.join('?' for _ in topics)})"
            params.extend(topics)
    if start_letter:
        sql += " AND terms.term COLLATE NOCASE LIKE ?"
        params.append(f"{start_letter}%")
    if language:
        sql += " AND terms.language = ?"
        params.append(language)
    sql = apply_sql_exclude(sql, params, exclude, url_column="terms.url", term_column="terms.term")

    async with db.connection.execute(sql, params) as cursor:
        rows = await cursor.fetchall()

    scored: list[tuple[SearchResult, float]] = []
    for row in rows:
        term_norm = normalize_text(row["term"])
        ratio = difflib.SequenceMatcher(None, query_norm, term_norm).ratio()
        score = round(
            min(constants.fuzzy_match_score_cap, constants.fuzzy_match_score_cap * ratio), 4
        )
        scored.append((row_to_result(row), score))

    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored


async def lexical_search(
    db: Database,
    query: str,
    *,
    topic: str | None = None,
    start_letter: str | None = None,
    language: str | None = None,
    limit: int | None = 20,
    fuzzy: bool = False,
    exclude: Collection[str] | None = None,
) -> list[tuple[SearchResult, float]]:
    """
    Full-text search the local database for `query`, ranked, scored, best match first.

    Retrieval is FTS5 over `(term, definition, topic)`.
    Ranking tiers every candidate via `slb_glossary.scoring.classify_name_match`
    first (exact/prefix/contains/all-tokens/partial-tokens), then bm25 for anything
    left with no name-tier match at all, so a term named after the query is never
    outranked by an unrelated term whose definition just mentions it a lot.

    If nothing clears a confident name-tier match, We additionally fuzzy match
    `query` against stored term names, to recover from a misspelling FTS5 can't tolerate.

    This is purely lexical, so no notion of a synonym or paraphrase. See
    `slb_glossary.local.hybrid_search` for that.

    :param db: The local database to search.
    :param query: Free-text query, matched against term, definition, and
        topic, or, for a recognized natural-language phrasing, matched
        against the term-like phrase extracted from it.
    :param topic: Restrict results to this topic, or several
        comma-separated topics (case-insensitive exact match by default).
    :param start_letter: Restrict results to terms starting with this letter.
    :param language: Restrict results to this glossary language edition
        (e.g. `"en"`/`"es"`). `None` (the default) does not filter by language.
    :param limit: Maximum number of results to return. `None` for unlimited.
    :param fuzzy: If `True`, tolerate minor misspellings/partial names in
        `topic` by resolving it against locally stored topic names first.
        Has no effect if `topic` is falsy. Unrelated to the query's own
        misspelling tolerance, which is always on.
    :param exclude: URLs and/or term names to leave out of the results entirely.
    :return: `(result, score)` pairs, best match first. `score` is in `[0.0, 1.0]`.
    """
    from slb_glossary.local.api import resolve_topic

    normalized_query = clean_query(query)
    logger.debug(
        "Local `lexical_search`: query=%r (normalized=%r) topic=%r start_letter=%r "
        "language=%r limit=%r fuzzy=%r exclude=%d entr(ies)",
        query,
        normalized_query,
        topic,
        start_letter,
        language,
        limit,
        fuzzy,
        len(exclude) if exclude else 0,
    )
    started_at = time.monotonic()

    resolved_topic = await resolve_topic(db, topic, fuzzy, language=language)

    rows = await _find_candidates(
        db,
        normalized_query,
        topic=resolved_topic,
        start_letter=start_letter,
        language=language,
        exclude=exclude,
    )
    scored = rank_candidates(normalized_query, rows)

    best_score = scored[0][1] if scored else 0.0
    if best_score < constants.token_overlap_score_cap:
        fuzzy_matches = await _fuzzy_find_candidates(
            db,
            normalized_query,
            topic=resolved_topic,
            start_letter=start_letter,
            language=language,
            exclude=exclude,
        )
        if fuzzy_matches:
            existing_keys = {(result.url, result.topic or "") for result, _ in scored}
            for result, score in fuzzy_matches:
                key = (result.url, result.topic or "")
                if key not in existing_keys:
                    scored.append((result, score))
                    existing_keys.add(key)
            scored.sort(key=lambda pair: pair[1], reverse=True)

    if limit:
        scored = scored[:limit]

    elapsed = time.monotonic() - started_at
    logger.debug(
        "Local `lexical_search` for %r yielded %d candidate(s) in %.3fs (best score %.3f)",
        normalized_query,
        len(scored),
        elapsed,
        scored[0][1] if scored else 0.0,
    )
    return scored
