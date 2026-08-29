"""Lexical (bm25 full-text) local search API."""

import logging
import time
import typing
from collections.abc import Collection

from slb_glossary.constants import constants
from slb_glossary.local.types import Database
from slb_glossary.phrasing import clean_query
from slb_glossary.types import SearchResult
from slb_glossary.utils import normalize_text

logger = logging.getLogger(__name__)

__all__ = ["lexical_search"]


FTS_COLUMN_WEIGHTS: tuple[float, float, float] = (10.0, 1.5, 3.0)
"""
bm25() column weights for `terms_fts`'s `(term, definition, topic)` columns,
in that order. 

FTS5's default is `1.0` for every column, which lets a
result whose definition happens to repeat the query outrank one whose
term name actually matches it.

Weighting `term` well above the others still doesn't fully fix this,
since bm25 also rewards a column for how often the query appears in it,
so a term whose definition just says the query word a lot can still
out-score the term actually named that. `lexical_search` sidesteps this
with an exact/prefix name-match tier computed directly in SQL, ahead of
bm25 entirely (see its docstring).
"""


def build_fts_query(query: str) -> str:
    """
    Turn free text into a safe FTS5 MATCH query.
    Quoted, prefix-matched tokens "ANDed" together.

    Quoting each token sidesteps FTS5's own query syntax (so punctuation
    in `query` can't be misread as an FTS operator), and the trailing `*`
    makes each token a prefix match, so `"poros"` finds `"porosity"`.

    :param query: Free-text search input.
    :return: An FTS5 `MATCH` query string equivalent to "every token, as
        a prefix, in any order".
    """
    tokens = query.strip().split()
    if not tokens:
        return '""'
    return " AND ".join(f'"{token}"*' for token in tokens)


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

    Ranking happens entirely in SQL, in two tiers:

    1. An exact (case/whitespace-insensitive) match against `term` scores
       `constants.exact_match_score`; and a `term` starting with `query` scores
       `constants.prefix_match_score`. Computed directly against `terms.term`, so
       this tier is never affected by how often `query` happens to appear
       elsewhere.
    2. Everything else is ordered by `bm25()`, weighted toward the `term`
       column (see `FTS_COLUMN_WEIGHTS`), and scored by normalizing that
       result set's own bm25 spread into `(0.0, constants.content_match_score_cap]`,
       worst match to best. bm25 isn't comparable across different
       queries, only within one, which is exactly what this needs it for.

    Tier 1 is always ordered ahead of tier 2, so a term named after the
    query is never outranked by an unrelated term whose definition just
    happens to mention it a lot. For example, searching "mud" surfacing
    "Drilling fluid" ahead of "Mud" itself, because "mud" is repeated
    throughout that definition, is the failure mode a purely
    bm25 or word count driven ranking is prone to. Tier 2's score is also
    capped below `constants.relevance_threshold` (see `constants.content_match_score_cap`),
    so a query that only ever matches by content, never an actual term name,
    reads as unconfident by default.

    In summary, a real name match should generally be trusted over content
    overlap alone.

    Before any of this is done, `query` is passed through
    `slb_glossary.natural_language.clean_query`, which reduces a
    plain-English question like "what is X" or "define X" down to just
    `X`. Local matching works well against actual term names and words, not
    conversational phrasing, so this is what lets a question like "what
    is porosity" find "Porosity" via the exact-match tier, the same as
    searching "porosity" directly would. Unstripped, the extra words
    would usually just make the FTS match come back empty.

    Purely lexical, and has no notion of a synonym or a paraphrase; a
    query has to share actual words with a term's name or definition to
    find it. See `slb_glossary.local.hybrid_search` for that, or
    `slb_glossary.local.search` with `mode="hybrid"`/`mode="semantic"`.

    :param db: The local database to search.
    :param query: Free-text query, matched against term, definition, and
        topic, or, for a recognized natural-language phrasing, matched
        against the term-like phrase extracted from it.
    :param topic: Restrict results to this topic, or several
        comma-separated topics (case-insensitive exact match by default).
    :param start_letter: Restrict results to terms starting with this letter.
    :param language: Restrict results to this glossary language edition
        (e.g. `"en"`/`"es"`), matched exactly against each stored result's
        `.language`. `None` (the default) doesn't filter by language.
    :param limit: Maximum number of results to return. `None` for unlimited.
    :param fuzzy: If `True`, tolerate minor misspellings/partial names in
        `topic` by resolving it against locally stored topic names first.
        Has no effect if `topic` is falsy.
    :param exclude: URLs and/or term names to leave out of the results
        entirely, e.g. ones already handled elsewhere in the same run. An
        entry is treated as a URL if it starts with `"http://"`/`"https://"`,
        and as a term name (matched case/whitespace-insensitively)
        otherwise. Filtered in SQL before `limit` is applied, so an excluded match
        doesn't use up part of `limit`'s budget the way a plain post-filter would.
        Note that a very large `exclude` (thousands of entries) does cost one
        SQL parameter each, so keep it to a reasonable, bounded size.
        `None` (the default) excludes nothing.
    :return: `(result, score)` pairs, best match first. `score` is in `[0.0, 1.0]`.
    """
    from slb_glossary.local.api import _apply_exclude, _row_to_result, resolve_topic

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
    query_norm = normalize_text(normalized_query)
    weights = ", ".join(str(weight) for weight in FTS_COLUMN_WEIGHTS)
    sql = f"""
        SELECT terms.*,
            (LOWER(terms.term) = ?) AS is_exact,
            (? != '' AND LOWER(terms.term) LIKE ? || '%') AS is_prefix,
            bm25(terms_fts, {weights}) AS bm25_score
        FROM terms
        JOIN terms_fts ON terms.rowid = terms_fts.rowid
        WHERE terms_fts MATCH ?
    """
    params: list[typing.Any] = [
        query_norm,
        query_norm,
        query_norm,
        build_fts_query(normalized_query),
    ]

    resolved_topic = await resolve_topic(db, topic, fuzzy, language=language)
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

    sql = _apply_exclude(sql, params, exclude, url_column="terms.url", term_column="terms.term")

    sql += " ORDER BY is_exact DESC, is_prefix DESC, bm25_score ASC"
    if limit:
        sql += " LIMIT ?"
        params.append(limit)

    async with db.connection.execute(sql, params) as cursor:
        rows = await cursor.fetchall()

    # Rows already come back best-first (exact, then prefix, then bm25), so no
    # further sorting needed, we only need to turn that order into `[0.0, 1.0]` scores.
    others_bm25 = [
        row["bm25_score"] for row in rows if not row["is_exact"] and not row["is_prefix"]
    ]
    worst = max(others_bm25, default=0.0)  # bm25 is negative-is-better; less negative is worse.
    best = min(others_bm25, default=0.0)
    spread = (worst - best) or 1.0

    scored: list[tuple[SearchResult, float]] = []
    for row in rows:
        if row["is_exact"]:
            score = constants.exact_match_score
        elif row["is_prefix"]:
            score = constants.prefix_match_score
        else:
            score = round(
                constants.content_match_score_cap * (worst - row["bm25_score"]) / spread, 4
            )
        scored.append((_row_to_result(row), score))

    elapsed = time.monotonic() - started_at
    logger.debug(
        "Local `lexical_search` for %r yielded %d candidate(s) in %.3fs (best score %.3f)",
        normalized_query,
        len(scored),
        elapsed,
        scored[0][1] if scored else 0.0,
    )
    return scored
