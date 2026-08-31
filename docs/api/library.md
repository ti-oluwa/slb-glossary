# Library API

A dense, structural reference across `slb_glossary`'s modules. For explanations and worked examples, see [Using the Library](../library/index.md), [Sessions and the Browser](../concepts/sessions.md), [Search Modes](../concepts/search-modes.md), and [The Data Model](../concepts/data-model.md). Everything below is importable either from its own submodule (`slb_glossary.live.search`) or from the top-level package (`slb_glossary.search` is actually `slb_glossary.query.search`, re-exported) — check `slb_glossary.__all__` for the full re-export list.

---

## `slb_glossary.live`

| Name | Kind | Notes |
|---|---|---|
| `session(**options)` | async context manager | Opens a `Session`. See [Sessions and the Browser](../concepts/sessions.md) for every keyword option (`language`, `browser_type`, `headless`, `timeout`, `retry`, `proxy`, `viewport`, `max_pages`, `use_stealth`, `initialize`, ...). |
| `open_session(**options)` / `close_session(session)` | async functions | The non-context-manager pair `session()` wraps. Use when you need to hold a session open across a scope `async with` can't express cleanly. |
| `session_from_config(config, **overrides)` | async context manager | Same as `session()`, but sourced from a `Config` (or a path to one). Keyword overrides win over the config's own values for that one call. |
| `search(session, query, *, limit=3, topic=None, start_letter=None, concurrency=1, ...)` | async generator | Ranked live search. `limit=None` for unlimited. `concurrency>1` trades relevance-order guarantees for speed. |
| `get_term(session, term_or_url, *, topic=None, language=None)` | coroutine → `SearchResult \| None` | One exact term or detail-page URL. |
| `get_terms_on(session, topic, *, limit=None, start_letter=None)` | async generator | Every term filed under one topic. |
| `get_terms_urls(session, *, query=None, topic=None, start_letter=None, limit=None)` | async generator → `str` | Raw URLs, no content fetched. |
| `get_topics(session)` | coroutine → `dict[str, int]` | Topic name → term count. |
| `get_random_term(session, *, topic=None, count=1)` | coroutine | Sampled by visiting a random detail page. |
| `score_result(result, query)` | function → `float` | Token-overlap relevance score used internally by `search`; exposed for custom ranking. |
| `ensure_initialized(session, auto_initialize=True)` | coroutine | Loads `session.topics`/`session.size` if not already loaded; raises `SessionNotInitializedError` if `auto_initialize=False` and it isn't. |
| `Session` | class | See [Sessions and the Browser](../concepts/sessions.md#what-opening-a-session-actually-does). Key attributes: `topics`, `size`, `pages` (the page pool), `language`. |
| `BrowserType` | `StrEnum` | `CHROMIUM` \| `FIREFOX` \| `WEBKIT`. |
| `ResourceType` | `IntFlag` | `DOCUMENT`, `STYLESHEET`, `IMAGE`, `MEDIA`, `FONT`, `SCRIPT`, `TEXTTRACK`, `XHR`, and more — combine with `|` for `block_resources`. |

## `slb_glossary.local`

| Name | Kind | Notes |
|---|---|---|
| `database(path=None, *, metadata_path=None)` | async context manager | Opens a `Database`. No path uses the OS-appropriate default (`slb local path`). |
| `open_db(path=None, **kw)` / `close_db(db)` | async functions | The non-context-manager pair. |
| `search(db, query, *, mode="lexical", scored=False, topic=None, limit=None, fuzzy=False)` | coroutine → `list[SearchResult]` (or `list[tuple[SearchResult, float]]` with `scored=True`) | See [Search Modes](../concepts/search-modes.md). |
| `lexical_search` / `vector_search` / `hybrid_search` | coroutines | The three functions `search`'s `mode` dispatches to; callable directly for lower-level control. |
| `get_term(db, term_or_url, *, topic=None, language=None)` | coroutine → `SearchResult \| None` | |
| `get_terms_on(db, topic, *, limit=None)` | coroutine → `list[SearchResult]` | |
| `get_topics(db)` | coroutine → `dict[str, int]` | |
| `count(db)` | coroutine → `int` | Total stored terms. |
| `upsert_results(db, results)` | coroutine → `int` | Insert/update by `(url, topic)`. Returns rows written. |
| `upsert_results_incrementally(db, results_iter, *, batch_size=20)` | async generator | Wraps an async iterable of `SearchResult`, writing every `batch_size` as they pass through, yielding each result onward unchanged. |
| `load_file(db, path, *, term_field="term", definition_field="definition", topic_field=..., url_field=..., source="glossary")` | coroutine → `int` | Import from CSV/JSON/XLSX. See [`local import`](../cli/sync.md#importing-your-own-data). |
| `embed_terms(db, *, urls=None, only_missing=True, batch_size=None)` | coroutine → `int` | Computes and stores embeddings via [model2vec](https://github.com/MinishLab/model2vec) (`minishlab/potion-retrieval-32M`). Needs the `semantic` extra. |
| `delete_embeddings(db, *, urls=None)` | coroutine → `int` | Remove stored embeddings, e.g. before `embed_terms` with a different model. |
| `flush(db)` / `reset(db)` | coroutines | `flush` clears stored terms only; `reset` also clears sync/metadata history. |
| `Database` | class | Obtained from `database()`/`open_db()`. |
| `Metadata` | class | Sync history / bookkeeping, loaded via `Metadata.load(path)`. |

## `slb_glossary.query`

Every function takes `db`, `session`, `source` (`Source.LOCAL`/`LIVE`/`AUTO`, default `AUTO`), and `persist` (default `False`) as shared keyword arguments — see [Combined Search with slb_glossary.query](../library/query.md) for what each actually does. All are also re-exported at the top level (`slb_glossary.search` is `slb_glossary.query.search`).

| Name | Kind | Returns |
|---|---|---|
| `search(query, ...)` | async generator | `QueryResult[SearchResult]` |
| `get_term(term_or_url, ...)` | coroutine | `QueryResult[SearchResult \| None]` |
| `compare(terms, ..., concurrency=constants.compare_concurrency)` | coroutine | `dict[str, QueryResult[SearchResult \| None]]` |
| `related_terms(term_or_url, ...)` | coroutine | `QueryResult[tuple[RelatedTerm, ...] \| None]` |
| `get_terms_on(topic, ...)` | async generator | `QueryResult[SearchResult]` |
| `get_terms_urls(...)` | async generator | `QueryResult[str]` |
| `get_topics(...)` | coroutine | `QueryResult[dict[str, int]]` |
| `get_random_term(...)` | coroutine | `QueryResult[SearchResult \| None]` |
| `resolve_source(db, session, source)` | coroutine → `Source` | Validates a requested `Source` against what `db`/`session` are actually available, raising if it can't be honored. |
| `Source` | `Enum` | `LOCAL` \| `LIVE` \| `AUTO`. |
| `QueryResult` | `dataclass` | `.value`, `.source`, `.persisted`. See [The Data Model](../concepts/data-model.md#queryresult). |

## `slb_glossary.types`

| Name | Kind | Notes |
|---|---|---|
| `SearchResult` | `NamedTuple` | Full field list in [The Data Model](../concepts/data-model.md#searchresult). |
| `RelatedTerm` | `NamedTuple` | `term`, `url`. |
| `Language` | `StrEnum` | `ENGLISH = "en"`, `SPANISH = "es"`. |
| `SearchMode` | `StrEnum` | `LEXICAL`, `SEMANTIC`, `HYBRID`. See [Search Modes](../concepts/search-modes.md). |

## `slb_glossary.config`

| Name | Kind | Notes |
|---|---|---|
| `Config` | `dataclass` (`Updatable`) | `.session` (`SessionOptions`), `.local` (`DatabaseOptions`), `.output` (`OutputOptions`). `.load()`, `.from_file(path)`, `.to_file(path, format=None)`, `.get("dotted.key")`, `.set("dotted.key", value)`. |
| `SessionOptions` | `dataclass` | Mirrors every `session()` keyword argument. |
| `DatabaseOptions` | `dataclass` | `data_dir`, `db_filename`, `prefer_local`, `sync_max_age_days`. |
| `OutputOptions` | `dataclass` | `default_format`, `show_url`, `show_topic`, `show_grammar`, `show_image`, `show_related`. |

## `slb_glossary.mcp`

| Name | Kind | Notes |
|---|---|---|
| `MCPApp(config)` | class | Wraps a `fastmcp.FastMCP` server. `.run()` / `.run_async()` / `.server()`. Assembly is lazy: no I/O until first use. |
| `MCPConfig` | `dataclass` | `.tools` (`Tool`), `.local` (`LocalAccess`), `.session` (`SessionAccess`), `.source` (`SourcePolicy`), `.server_info` (`ServerInfo`), transport/auth/rate-limit settings. |
| `Tool` | `Flag` enum | `SEARCH`, `GET_TERM`, `GET_TERMS_ON`, `GET_TERMS_URLS`, `GET_TOPICS`, `GET_RANDOM_TERM`, `RELATED_TERMS`, `COMPARE`, `SYNC`. Aliases: `"read_only"` (everything but `SYNC`), `"all"`. |
| `LocalAccess` | `dataclass` | `allow_write` (gates the `SYNC` tool and `persist=True` regardless of `tools`). |
| `SessionAccess` | `dataclass` | `enabled` — whether the server may open a live session at all. |
| `SourcePolicy` | `dataclass` | Which `Source` values a caller may request per call. |

See [Running an MCP Server](../agent/mcp-server.md) for how these compose in practice, and the CLI flags (`slb mcp serve`) that set them without writing Python.

## `slb_glossary` (top level)

| Name | Kind | Notes |
|---|---|---|
| `save(records, destination, *, format=None)` | coroutine | Writes a list or async iterable of `SearchResult`-likes to a file. Format inferred from extension unless overridden. |
| `supported_formats()` | function → `list[str]` | `["csv", "json", "xlsx"]` on a base install; `WRITERS` dict keys, sorted. |
| `writer(format)` | function → `Writer` | Look up a specific writer callable directly. |
| `WRITERS` | `dict[str, Writer]` | `Writer = Callable[[Sequence[RecordLike], pathlib.Path], Awaitable[None]]`. |
| `RetryPolicy` | `dataclass` | `attempts`, `base_delay`, `backoff_type`, `factor`, `max_delay`, `jitter`. |
| `BackoffType` | `StrEnum` | `CONSTANT`, `LINEAR`, `EXPONENTIAL`, `LOGARITHMIC`. |
| `SLBGlossaryError` | Exception | Base class for every exception this package raises. |
| `NetworkError`, `BrowserError`, `SessionNotInitializedError` (subclass of `BrowserError`), `ParsingError`, `ConfigError`, `DatabaseError`, `EmbeddingError`, `QueryError`, `LoggingError`, `UnsupportedFormatError`, `WriterError` | Exceptions | All subclass `SLBGlossaryError` (and, where it makes sense, a matching stdlib exception — `NetworkError` also subclasses `ConnectionError`, `WriterError` also subclasses `OSError`). |
| `log` | `logging.Logger` | The package's own logger, configurable via `slb_glossary.logging.configure_logging`/a custom `LogSink`. |
| `__version__` | `str` | Installed package version. |
