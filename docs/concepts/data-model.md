# The Data Model

Every function across the CLI, `slb_glossary.live`, `slb_glossary.local`, and `slb_glossary.query` ultimately hands you the same few types. This page is the single place they're all documented in full, so the rest of this documentation can link back here instead of re-explaining a field list on every page.

---

## `SearchResult`

A single term definition, extracted from one glossary page. A plain `typing.NamedTuple`, so it unpacks positionally, supports attribute access, and is immutable.

| Field | Type | Notes |
|---|---|---|
| `term` | `str` | The glossary term this result defines. |
| `definition` | `str \| None` | Full definition text, or `None` if it couldn't be parsed. |
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
print(result.value.term, result.value.definition)                   # by name
print(result.value.asdict())                                        # as a plain dict
```

Only `term`, `definition`, `topic`, and `url` are ever filtered/matched on; `image`, `image_caption`, `related`, and `language` are along for the ride, carried through from whichever page produced the result.

!!! note "Why so much is `Optional`"
    A `SearchResult` reflects what one specific glossary page actually had, not a guaranteed-complete schema. Some term pages have no image; some have no related-terms section. `definition` itself can be `None` if a page's structure defeated parsing, which is why it's worth checking for `None` before assuming you have text to print, especially in scripts fed from `--live`/live-fallback results rather than a database you've already inspected.

---

## `RelatedTerm`

One entry in a `SearchResult.related` tuple: a link from within a definition's text to another term.

| Field | Type | Notes |
|---|---|---|
| `term` | `str` | Display text of the link — usually, but not always, the related term's exact name. |
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

A `Session` is bound to one language edition for its entire lifetime (`session()`'s `language` parameter); a local database can hold terms from both editions at once, distinguished by each stored `SearchResult.language`. Passing `language` to a query function filters (for a local read) or validates against the session's own language (for a live read) — see `get_term`'s `language` parameter in [Combined Search](../library/query.md).

---

## `SearchMode`

```python
from slb_glossary import SearchMode

SearchMode.LEXICAL   # "lexical" - the default
SearchMode.SEMANTIC  # "semantic"
SearchMode.HYBRID     # "hybrid"
```

Covered in full on its own page: [Search Modes](search-modes.md).

---

## `QueryResult`

Not a data model for glossary content itself, but the wrapper every `slb_glossary.query` function returns its answer in, adding provenance:

| Field | Type | Notes |
|---|---|---|
| `value` | `T` | The actual answer — a `SearchResult`, `SearchResult \| None`, a tuple of `RelatedTerm`s, etc., depending on which function returned it. |
| `source` | `Source` | `Source.LOCAL` or `Source.LIVE` — which one actually answered this call. |
| `persisted` | `bool` | Whether this result was written to the local database as part of this call. |

Covered in full, with examples, in [Combined Search with slb_glossary.query](../library/query.md#queryresult-knowing-where-an-answer-came-from).

---

## Where these show up

- [Live Search](../library/live-search.md) and [Local Search and Cache](../library/local-search.md) both hand back bare `SearchResult`s (or lists/streams of them).
- [Combined Search with slb_glossary.query](../library/query.md) wraps the same `SearchResult`s in `QueryResult`.
- The CLI's tables and `--json` output are a formatted view of exactly these fields; see [Searching and Defining Terms](../cli/searching.md).
