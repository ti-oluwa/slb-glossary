# The Data Model

Every function across the CLI, `slb_glossary.live`, `slb_glossary.local`, and `slb_glossary.query` ultimately hands you the same few types. This page documents this types so you understand them and know what fields each type holds.

---

## `SearchResult`

This is a single term lookup result, extracted from a glossary term detail page.  It is a plain `typing.NamedTuple`, so it unpacks positionally, supports attribute access, and is immutable.

| Field | Type | Notes |
|---|---|---|
| `term` | `str` | The glossary term this result defines. |
| `definition` | `str \| None` | Full definition text, or `None` if it could not be parsed. |
| `grammatical_label` | `str \| None` | Part of speech, e.g. `"Noun"`. `None` if unavailable. |
| `topic` | `str \| None` | The topic/discipline this definition is filed under. |
| `url` | `str \| None` | The glossary detail-page URL this result came from. |
| `image` | `str \| None` | URL of the term's illustrative image, if the page has one. |
| `image_caption` | `str \| None` | Caption text for `image`, if present. |
| `related` | `tuple[RelatedTerm, ...] \| None` | Terms linked from this definition's "related terms" section. |
| `language` | `str` | Glossary language edition this result was found in (`"en"`/`"es"`). Defaults to `"en"`. |

```python
result = await slb.get_term("porosity", db=db, session=session)
term, definition, grammatical_label, topic, url, *_ = result.value  # positional
print(result.value.term, result.value.definition)  # by name
print(result.value.asdict())  # as a plain dict
```

Only `term`, `definition`, `topic`, and `url` are ever filtered/matched on; `image`, `image_caption`, `related`, and `language` are mostly meta information, carried through from whichever page produced the result.

!!! note "Why so much is `Optional`"
    A `SearchResult` reflects what one specific glossary term page actually had, not a guaranteed-complete schema. Some term pages have no image; some have no related-terms. `definition` itself can be `None` if a page's structure defeated parsing, which is why it's worth checking for `None` before assuming you have text to print, especially in scripts fed mostly from the live-site results.

---

## `RelatedTerm`

An entry in a `SearchResult.related` tuple. It is a (hyper)link from within a definition's text to another term.

| Field | Type | Notes |
|---|---|---|
| `term` | `str` | Display text of the link, usually, but not always, the related term's exact name. |
| `url` | `str` | The glossary URL the link points to. |

```python
related = await slb.related_terms("water saturation", db=db, session=session)
for link in related.value:
    print(link.term, "->", link.url)
```

`related_terms` (covered in [Combined Search](../library/query.md#the-rest-of-the-module-at-a-glance)) is the shortcut for reaching just this field without also handling the rest of a `SearchResult`.

---

## `Language`

```python
from slb_glossary import Language

Language.ENGLISH  # "en"
Language.SPANISH  # "es"
```

A `Session` is bound to one language edition for its entire lifetime (`session()`'s `language` parameter); a local database can hold terms from both language editions at once, distinguished by each stored `SearchResult.language`. Passing `language` to a query function filters (for a local read) or validates against the session's own language (for a live read), see `get_term`'s `language` parameter in [Combined Search](../library/query.md).

---

## `SearchMode`

```python
from slb_glossary import SearchMode

SearchMode.LEXICAL  # "lexical" - the default
SearchMode.SEMANTIC  # "semantic"
SearchMode.HYBRID  # "hybrid"
```

Covered in full on its own page: [Search Modes](search-modes.md).

---

## `QueryResult`

This is not a data model for glossary content itself, but the wrapper the `slb_glossary.query` API functions return its results in, adding provenance:

| Field | Type | Notes |
|---|---|---|
| `value` | `T` | The actual result: a `SearchResult`, `SearchResult \| None`, a tuple of `RelatedTerm`s, etc., depending on which function returned it. |
| `source` | `Source` | `Source.LOCAL` or `Source.LIVE`, which one actually answered this call. |
| `persisted` | `bool` | Whether this result was written to the local database as part of this call. |
| `score` | `float \| None` | A relevance score in `[0.0, 1.0]` for `value` against the query it was found for, where scoring is meaningful (an exact `get_term` match scores `constants.exact_match_score`, `1.0` by default). `None` where it does not apply, e.g. a topic listing or a related-terms lookup. |

Covered in full, with examples, in [Combined Search with `slb_glossary.query`](../library/query.md#queryresult-knowing-where-an-answer-came-from).

---

## `SimilarResult`

The is the data type `get_term`/`compare` return inside a `QueryResult` instead of a bare `SearchResult` when called with `with_similar=True`. It contains an exact match, plus nearby alternatives, for a "did you mean" experience when the exact match is `None` (or just to see what else is nearby even when it is not).

| Field | Type | Notes |
|---|---|---|
| `exact` | `QueryResult[SearchResult] \| None` | What a plain (non-`with_similar`) call would have returned, wrapped in its own `QueryResult`. `None` if there was no exact match. Its `.score` is always `constants.exact_match_score`, since it's exact by definition. |
| `similar` | `tuple[QueryResult[SearchResult], ...]` | Up to `max_similar_terms` other results found along the way, best match first, each with its own `.score`. Empty if none were found, or if `max_similar_terms=0`. |

```python
lookup = await slb.get_term("porocity", db=db, session=session, with_similar=True)  # a typo
similar_result = lookup.value
if similar_result.exact is None and similar_result.similar:
    print("Did you mean:", similar_result.similar[0].value.term)
```

Covered in full, with examples, in [Combined Search with `slb_glossary.query`](../library/query.md#getting-similar-results-alongside-an-exact-match).

---

## Where these show up

- [Live Search](../library/live-search.md) and [Local Search and Cache](../library/local-search.md) both hand back bare `SearchResult`s (or lists/streams of them).
- [Combined Search with slb_glossary.query](../library/query.md) wraps the same `SearchResult`s in `QueryResult`.
- The CLI's tables and `--json` output are a formatted view of exactly these data types' fields; see [Searching and Defining Terms](../cli/searching.md).
