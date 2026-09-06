"""Lexical (bm25 full-text) local search API."""

import difflib
import logging
import time
import typing
from collections.abc import Collection

from slb_glossary.constants import constants
from slb_glossary.local.types import Database
from slb_glossary.phrasing import clean_query
from slb_glossary.scoring import NameMatchTier, classify_name_match
from slb_glossary.types import SearchResult
from slb_glossary.utils import normalize_text

logger = logging.getLogger(__name__)

__all__ = ["build_fts_query", "build_fts_query_or", "lexical_search"]


FTS_COLUMN_WEIGHTS: tuple[float, float, float] = (10.0, 1.5, 3.0)
"""
bm25() column weights for `terms_fts`'s `(term, definition, topic)` columns,
in that order. 

FTS5's default is `1.0` for every column, which lets a
result whose definition happens to repeat the query outrank one whose
term name actually matches it.

Weighting `term` well above the others still does not fully fix this,
since bm25 also rewards a column for how often the query appears in it,
so a term whose definition just says the query word a lot can still
out-score the term actually named that. `lexical_search` sidesteps this
entirely by classifying every candidate's term-name match tier in
Python (`slb_glossary.scoring.classify_name_match`) ahead of bm25, so
bm25 only ever breaks ties among rows with no name-tier match at all
(see its docstring).
"""

_NAME_TIER_ORDER: tuple[NameMatchTier, ...] = (
    NameMatchTier.EXACT,
    NameMatchTier.PREFIX,
    NameMatchTier.CONTAINS,
    NameMatchTier.ALL_TOKENS,
    NameMatchTier.PARTIAL_TOKENS,
)
"""Every non-content tier, best first. A result in any of these never falls back to bm25 scoring."""


def build_fts_query(query: str) -> str:
    """
    Turn free text into a safe FTS5 MATCH query.
    Quoted, prefix-matched tokens "ANDed" together.

    Quoting each token sidesteps FTS5's own query syntax (so punctuation
    in `query` can not be misread as an FTS operator), and the trailing `*`
    makes each token a prefix match, so `"poros"` finds `"porosity"`.

    :param query: Free-text search input.
    :return: An FTS5 `MATCH` query string equivalent to "every token, as
        a prefix, in any order".
    """
    tokens = query.strip().split()
    if not tokens:
        return '""'

    return " AND ".join('"' + token.replace('"', '""') + '"*' for token in tokens)


def build_fts_query_or(query: str) -> str:
    """
    Same tokenization/quoting/prefix-matching as `build_fts_query`, but
    `OR`ed together instead of `AND`ed.

    Used as a fallback when the strict `AND` query comes back with zero
    rows: with `AND`, a single query token that doesn't prefix-match
    *anything* in the corpus (an extra word, a genuinely unmatched typo)
    zeroes out the whole query, even if every other token matches a
    term perfectly. `OR` still finds those rows; Python-side tiering
    (`_rank_candidates`) and the bm25 content-tier cap then keep a
    row that only matched by one weak token from outranking a real
    match, the same way they already do for any other bm25-only result.

    :param query: Free-text search input.
    :return: An FTS5 `MATCH` query string equivalent to "any token, as a prefix".
    """
    tokens = query.strip().split()
    if not tokens:
        return '""'

    return " OR ".join('"' + token.replace('"', '""') + '"*' for token in tokens)


async def _run_fts_query(
    db: Database,
    match_expression: str,
    *,
    topic: str | None,
    start_letter: str | None,
    language: str | None,
    exclude: Collection[str] | None,
) -> list[typing.Any]:
    """Run one FTS5 `MATCH` query (`match_expression`) with the given filters applied."""
    from slb_glossary.local.api import _apply_exclude

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

    sql = _apply_exclude(sql, params, exclude, url_column="terms.url", term_column="terms.term")

    sql += " ORDER BY bm25_score ASC LIMIT ?"
    params.append(constants.lexical_candidate_cap)

    async with db.connection.execute(sql, params) as cursor:
        return list(await cursor.fetchall())


async def _fetch_candidates(
    db: Database,
    normalized_query: str,
    *,
    topic: str | None,
    start_letter: str | None,
    language: str | None,
    exclude: Collection[str] | None,
) -> list[typing.Any]:
    """
    Run the FTS5 `MATCH` query and return every matching row (up to
    `constants.lexical_candidate_cap`), with its `bm25_score`.

    Tries the strict `AND`-of-prefixes query (`build_fts_query`) first;
    if that comes back with zero rows *and* the query has more than one
    token, retries with the `OR`-of-prefixes query
    (`build_fts_query_or`) instead. A single query token with no match
    anywhere in the corpus would otherwise zero out an `AND` query even
    when every other token matches a term perfectly (e.g. "gas lift
    xyz123" against a corpus with no "xyz123" anywhere); `OR` still
    finds those rows, and Python-side tiering ranks them correctly
    from there (a row that only weakly matched keeps a low, capped
    score, same as any other bm25-only match; see `_rank_candidates`).
    The stricter `AND` query is always tried first and used as-is
    whenever it finds anything, since it is the more precise of the two.

    No name-tier logic and no `LIMIT` tied to the caller's own `limit`
    here - ranking happens in Python afterwards (`_rank_candidates`),
    across every fetched row, so a result with a strong name-tier match
    is never cut off by a bm25-only pre-sort before it gets the chance
    to out-rank a bm25-favored row.

    :return: Raw `aiosqlite.Row` objects, `terms.*` plus `bm25_score`,
        in no particular guaranteed order beyond "bm25, best first",
        which only matters as this function's own internal fetch order,
        not the final ranking.
    """
    from slb_glossary.local.api import resolve_topic

    resolved_topic = await resolve_topic(db, topic, False, language=language)
    filters = {
        "topic": resolved_topic,
        "start_letter": start_letter,
        "language": language,
        "exclude": exclude,
    }

    rows = await _run_fts_query(db, build_fts_query(normalized_query), **filters)
    if rows or len(normalized_query.split()) <= 1:
        return rows

    return await _run_fts_query(db, build_fts_query_or(normalized_query), **filters)


def _rank_candidates(
    normalized_query: str, rows: list[typing.Any]
) -> list[tuple[SearchResult, float]]:
    """
    Tier and score every fetched row, best match first.

    Each row is classified by `slb_glossary.scoring.classify_name_match`
    against its own `term`. A row with a name-tier match (exact, prefix,
    contains, all-tokens, or partial-tokens) is scored directly off that
    tier. Everything else - no name overlap at all, matched only via its
    definition/topic - is ranked by `bm25_score`, normalized against the
    *other content-only rows in this same result set* into
    `(0.0, constants.content_match_score_cap]`, worst to best. bm25 is
    not comparable across different queries, only within one, which is
    exactly what this needs it for.

    :param normalized_query: The already-`clean_query`-processed query.
    :param rows: Rows from `_fetch_candidates`.
    :return: `(result, score)` pairs, best match first.
    """
    from slb_glossary.local.api import _row_to_result

    tiered: list[tuple[SearchResult, float]] = []
    content_rows: list[tuple[typing.Any, float]] = []
    for row in rows:
        match = classify_name_match(normalized_query, row["term"])
        if match is not None:
            tiered.append((_row_to_result(row), match.score))
        else:
            content_rows.append((row, row["bm25_score"]))

    if content_rows:
        bm25_scores = [score for _, score in content_rows]
        worst = max(bm25_scores)  # bm25 is negative-is-better; less negative is worse.
        best = min(bm25_scores)
        spread = (worst - best) or 1.0
        for row, bm25_score in content_rows:
            score = round(constants.content_match_score_cap * (worst - bm25_score) / spread, 4)
            tiered.append((_row_to_result(row), score))

    tiered.sort(key=lambda pair: pair[1], reverse=True)
    return tiered


async def _fuzzy_fallback(
    db: Database,
    normalized_query: str,
    *,
    topic: str | None,
    start_letter: str | None,
    language: str | None,
    exclude: Collection[str] | None,
) -> list[tuple[SearchResult, float]]:
    """
    Recover results for a misspelled query that FTS5's prefix matching missed entirely.

    FTS5's own prefix matching (`build_fts_query`) only helps when the
    *start* of a token is spelled correctly ("logg" still finds
    "logging"); a typo anywhere else ("porosoty", "reservior") means no
    stored token starts with what was typed, and FTS5 returns nothing
    for that token at all. This recovers from that case, deliberately
    outside the FTS5 query itself, by fuzzy-matching the query against
    stored term *names* directly.

    Bounded, not a full-table scan: `difflib` runs once, in Python,
    against the (typically short) list of distinct stored term names,
    not against every row's definition text, and only the top
    `constants.fuzzy_candidate_limit` matches are then looked up by a
    single indexed SQL query. Every recovered result is capped at
    `constants.fuzzy_match_score_cap`, below every literal-match tier,
    so a typo-recovered guess can never outrank a real match found
    elsewhere.

    :param db: The local database to search.
    :param normalized_query: The already-`clean_query`-processed query.
    :param topic: Same as `lexical_search`'s `topic`, already resolved (non-fuzzy).
    :param start_letter: Same as `lexical_search`'s `start_letter`.
    :param language: Same as `lexical_search`'s `language`.
    :param exclude: Same as `lexical_search`'s `exclude`.
    :return: `(result, score)` pairs, each scored at most
        `constants.fuzzy_match_score_cap`, best match first. Empty if no
        stored term name comes close enough (`constants.fuzzy_match_cutoff`).
    """
    from slb_glossary.local.api import _apply_exclude, _row_to_result

    query_norm = normalize_text(normalized_query)
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
    sql = _apply_exclude(sql, params, exclude, url_column="terms.url", term_column="terms.term")

    async with db.connection.execute(sql, params) as cursor:
        rows = await cursor.fetchall()

    scored: list[tuple[SearchResult, float]] = []
    for row in rows:
        term_norm = normalize_text(row["term"])
        ratio = difflib.SequenceMatcher(None, query_norm, term_norm).ratio()
        score = round(
            min(constants.fuzzy_match_score_cap, constants.fuzzy_match_score_cap * ratio), 4
        )
        scored.append((_row_to_result(row), score))

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

    Ranking happens in two stages:

    1. FTS5 retrieval (`_fetch_candidates`): every row whose term,
       definition, or topic matches `query`'s tokens as prefixes, ANDed
       together (`build_fts_query`), up to `constants.lexical_candidate_cap`.
    2. Tiered ranking in Python (`_rank_candidates`), via
       `slb_glossary.scoring.classify_name_match`, best tier first:
       exact name match, prefix, whole-phrase containment either way,
       every query token present, some query tokens present, and
       finally bm25 relevance (weighted toward the `term` column; see
       `FTS_COLUMN_WEIGHTS`) for rows with no name-tier match at all -
       normalized against this result set's own bm25 spread into
       `(0.0, constants.content_match_score_cap]`, worst match to best.
       bm25 is not comparable across different queries, only within
       one, which is exactly what this needs it for.

    Because every non-bm25 tier is classified straight from
    `terms.term` itself, a term named after the query is never
    outranked by an unrelated term whose definition just happens to
    mention it a lot. For example, searching "mud" surfaces "Mud"
    itself ahead of "Drilling fluid", even though "mud" is repeated
    throughout that definition - the failure mode a purely bm25- or
    word-count-driven ranking is prone to. The bm25 tier is also capped
    below `constants.relevance_threshold` (see
    `constants.content_match_score_cap`), so a query that only ever
    matches by content, never an actual term name, reads as unconfident
    by default.

    If FTS5 retrieval turns up no confident name-tier match (nothing at
    or above `constants.token_overlap_score_cap`), `_fuzzy_fallback`
    additionally fuzzy-matches `query` against stored term *names*
    directly, to recover from a misspelling FTS5's own prefix matching
    can't tolerate (a typo anywhere but a token's very end). Recovered
    results are capped at `constants.fuzzy_match_score_cap`, always
    below a genuine literal match, and merged in rather than replacing
    what FTS5 already found.

    Before any of this, `query` is passed through
    `slb_glossary.phrasing.clean_query`, which reduces a plain-English
    question like "what is X" or "define X" down to just `X`. Local
    matching works well against actual term names and words, not
    conversational phrasing, so this is what lets a question like "what
    is porosity" find "Porosity" via the exact-match tier, the same as
    searching "porosity" directly would. Unstripped, the extra words
    would usually just make the FTS match come back empty.

    Purely lexical, and has no notion of a synonym or a paraphrase; a
    query has to share actual words (or, via the fuzzy fallback, a
    close spelling of them) with a term's name or definition to find
    it. See `slb_glossary.local.hybrid_search` for that, or
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
        `.language`. `None` (the default) does not filter by language.
    :param limit: Maximum number of results to return. `None` for unlimited.
    :param fuzzy: If `True`, tolerate minor misspellings/partial names in
        `topic` by resolving it against locally stored topic names first.
        Has no effect if `topic` is falsy. Unrelated to the *query's*
        own misspelling tolerance, which is always on (see above).
    :param exclude: URLs and/or term names to leave out of the results
        entirely, e.g. ones already handled elsewhere in the same run. An
        entry is treated as a URL if it starts with `"http://"`/`"https://"`,
        and as a term name (matched case/whitespace-insensitively)
        otherwise. Filtered in SQL before `limit` is applied, so an excluded match
        does not use up part of `limit`'s budget the way a plain post-filter would.
        Note that a very large `exclude` (thousands of entries) does cost one
        SQL parameter each, so keep it to a reasonable, bounded size.
        `None` (the default) excludes nothing.
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

    rows = await _fetch_candidates(
        db,
        normalized_query,
        topic=resolved_topic,
        start_letter=start_letter,
        language=language,
        exclude=exclude,
    )
    scored = _rank_candidates(normalized_query, rows)

    best_score = scored[0][1] if scored else 0.0
    if best_score < constants.token_overlap_score_cap:
        fuzzy_matches = await _fuzzy_fallback(
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
