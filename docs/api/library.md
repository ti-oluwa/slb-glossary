# Library API

This pag contains a dense, structural reference across `slb_glossary`'s modules. For explanations and worked examples, see [Using the Library](../library/index.md), [Sessions and the Browser](../concepts/sessions.md), [Search Modes](../concepts/search-modes.md), and [The Data Model](../concepts/data-model.md). Everything below is importable either from its own submodule (`slb_glossary.live.search`) or from the top-level package (`slb_glossary.search` is actually `slb_glossary.query.search`, re-exported), check `slb_glossary.__all__` for the full re-export list.

---

## `slb_glossary.live`

| Name | Kind | Notes |
|---|---|---|
| `session(**options)` | async context manager | Opens a `Session`. See [Sessions and the Browser](../concepts/sessions.md) for every keyword option (`language`, `browser_type`, `headless`, `timeout`, `retry`, `proxy`, `viewport`, `max_pages`, `use_stealth`, `initialize`, ...). |
| `open_session(**options)` / `close_session(session)` | async functions | The non-context-manager pair `session()` wraps. Use when you need to hold a session open across a scope `async with` can not express cleanly. |
| `session_from_config(config, **overrides)` | async context manager | Same as `session()`, but sourced from a `Config` (or a path to one). Keyword overrides win over the config's own values for that one call. |
| `search(session, query, *, limit=3, topic=None, start_letter=None, concurrency=1, ...)` | async generator | Ranked live search. `limit=None` for unlimited. `concurrency>1` trades relevance-order guarantees for speed. |
| `get_results_from_url(session, url, *, topic=None, page=None, exclude=None)` | async generator | Every definition found on one term detail-page URL (a term can carry more than one). What `query.get_term` calls into for a live lookup. |
| `get_results_from_urls(session, urls, *, topic=None, concurrency=1, first_only=False, exclude=None)` | async generator | Same, for several URLs. `concurrency>1` opens that many worker pages on `session` (needs `session.max_pages` to cover it). Results arrive as they finish, not necessarily in `urls`' order when concurrent. |
| `get_terms_on(session, topic, *, limit=None, start_letter=None)` | async generator | Every term filed under one topic. |
| `get_terms_urls(session, *, query=None, topic=None, start_letter=None, limit=None)` | async generator -> `str` | Raw URLs, no content fetched. |
| `refresh_topics(session)` | coroutine -> `Session` | Reloads `session.topics`/`session.size` in place, reusing `session.retry` (the exact reload `open_session`/`session()` already does once at startup). Call this again later if the glossary's topic list may have changed mid-run. |
| `score_result(result, query)` | function -> `float` | Token-overlap relevance score used internally by `search`; exposed for custom ranking. |
| `ensure_initialized(session, auto_initialize=True)` | coroutine | Loads `session.topics`/`session.size` if not already loaded; raises `SessionNotInitializedError` if `auto_initialize=False` and it is not. |
| `Session` | class | See [Sessions and the Browser](../concepts/sessions.md#what-opening-a-session-actually-does). Key attributes: `topics`, `size`, `pages` (the page pool), `language`. |
| `BrowserType` | `StrEnum` | `CHROMIUM` \| `FIREFOX` \| `WEBKIT`. |
| `ResourceType` | `IntFlag` | `DOCUMENT`, `STYLESHEET`, `IMAGE`, `MEDIA`, `FONT`, `SCRIPT`, `TEXTTRACK`, `XHR`, and more, combine with `|` for `block_resources`. |

## `slb_glossary.local`

| Name | Kind | Notes |
|---|---|---|
| `database(path=None, *, metadata_path=None)` | async context manager | Opens a `Database`. No path uses the OS-appropriate default (`slb local path`). |
| `open_db(path=None, **kw)` / `close_db(db)` | async functions | The non-context-manager pair. |
| `search(db, query, *, mode="lexical", scored=False, topic=None, limit=None, fuzzy=False)` | coroutine -> `list[SearchResult]` (or `list[tuple[SearchResult, float]]` with `scored=True`) | See [Search Modes](../concepts/search-modes.md). |
| `lexical_search` / `vector_search` / `hybrid_search` | coroutines | The three functions `search`'s `mode` dispatches to; callable directly for lower-level control. |
| `get_term(db, term_or_url, *, topic=None, language=None, with_similar=False, similar_pool_size=constants.similar_terms_pool_size, max_similar_terms=constants.max_similar_terms)` | coroutine -> `SearchResult \| None`, or `tuple[SearchResult \| None, tuple[tuple[SearchResult, float], ...]]` if `with_similar=True` | The `with_similar` alternatives are a plain tuple here, not a `SimilarResult`, since there's no `QueryResult` wrapper at this local-only level. |
| `get_terms_on(db, topic, *, limit=None)` | coroutine -> `list[SearchResult]` | |
| `get_topics(db)` | coroutine -> `dict[str, int]` | |
| `get_random_term(db, *, topic=None, language=None, fuzzy=False, exclude=None)` | coroutine -> `SearchResult \| None` | Sampled from what's already stored, no network involved. |
| `count(db)` | coroutine -> `int` | Total stored terms. |
| `upsert_results(db, results)` | coroutine -> `int` | Insert/update by `(url, topic)`. Returns rows written. |
| `upsert_results_incrementally(db, results_iter, *, batch_size=20)` | async generator | Wraps an async iterable of `SearchResult`, writing every `batch_size` as they pass through, yielding each result onward unchanged. |
| `load_file(db, path, *, term_field="term", definition_field="definition", topic_field=..., url_field=..., source="glossary")` | coroutine -> `int` | Import from CSV/JSON/XLSX/XLSM (and YAML, with the `config` extra's PyYAML dependency present), see `slb_glossary.readers.supported_formats()`. See [`local import`](../cli/sync.md#importing-your-own-data). |
| `embed_terms(db, *, urls=None, only_missing=True, batch_size=None)` | coroutine -> `int` | Computes and stores embeddings via [model2vec](https://github.com/MinishLab/model2vec) (`minishlab/potion-retrieval-32M`). Needs the `semantic` extra. |
| `delete_embeddings(db, *, urls=None)` | coroutine -> `int` | Remove stored embeddings, e.g. before `embed_terms` with a different model. |
| `flush(db)` / `reset(db)` | coroutines | `flush` clears stored terms only; `reset` also clears sync/metadata history. |
| `Database` | class | Obtained from `database()`/`open_db()`. |
| `Metadata` | class | Sync history / bookkeeping, loaded via `Metadata.load(path)`. |

## `slb_glossary.query`

Every function takes `db`, `session`, `source` (`Source.LOCAL`/`LIVE`/`AUTO`, default `AUTO`), and `persist` (default `False`) as shared keyword arguments, see [Combined Search with slb_glossary.query](../library/query.md) for what each actually does. All are also re-exported at the top level (`slb_glossary.search` is `slb_glossary.query.search`).

| Name | Kind | Returns |
|---|---|---|
| `search(query, ...)` | async generator | `QueryResult[SearchResult]` |
| `get_term(term_or_url, ..., with_similar=False, similar_pool_size=constants.similar_terms_pool_size, max_similar_terms=constants.max_similar_terms)` | coroutine | `QueryResult[SearchResult \| None]`, or `QueryResult[SimilarResult]` if `with_similar=True` |
| `compare(terms, ..., concurrency=constants.compare_concurrency, with_similar=False)` | coroutine | `dict[str, QueryResult[SearchResult \| None]]`, or `dict[str, QueryResult[SimilarResult]]` if `with_similar=True` |
| `related_terms(term_or_url, ...)` | coroutine | `QueryResult[tuple[RelatedTerm, ...] \| None]` |
| `get_terms_on(topic, ...)` | async generator | `QueryResult[SearchResult]` |
| `get_terms_urls(...)` | async generator | `QueryResult[str]` |
| `get_topics(...)` | coroutine | `QueryResult[dict[str, int]]` |
| `get_random_term(...)` | coroutine | `QueryResult[SearchResult \| None]` |
| `resolve_source(db, session, source)` | coroutine -> `Source` | Validates a requested `Source` against what `db`/`session` are actually available, raising if it can not be honored. |
| `Source` | `Enum` | `LOCAL` \| `LIVE` \| `AUTO`. |
| `QueryResult` | `dataclass` | `.value`, `.source`, `.persisted`, `.score` (`float \| None`). See [The Data Model](../concepts/data-model.md#queryresult). |
| `SimilarResult` | `dataclass` | `.exact` (`QueryResult[SearchResult] \| None`), `.similar` (`tuple[QueryResult[SearchResult], ...]`). What `get_term`/`compare` return (wrapped in a `QueryResult`) when called with `with_similar=True`. See [The Data Model](../concepts/data-model.md#similarresult). |

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
| `MCPApp(config)` | class | Wraps a `fastmcp.FastMCP` server. `.server()` builds it (lazily, once); `.run(**transport_kwargs)` / `.run_async(**transport_kwargs)` build-then-serve. |
| `load_app(dotted_path)` | function -> `MCPApp \| FastMCP` | Uvicorn-style `"module:attr"` loader; calls a zero-arg factory if `attr` is callable. What `slb mcp serve APP_PATH` uses. |
| `MCPConfig` | `dataclass` | `.server` (`ServerInfo`), `.session` (`SessionAccess`), `.local` (`LocalAccess`), `.source_policy` (`SourcePolicy`), `.tools` (`Tool`), `.timeouts` (`Timeout`), `.auth` (`Auth`), `.rate_limit` (`RateLimit`), `.hooks` (`Hooks`), `.logging` (`Logging`), `.streaming` (`Streaming`). Every field defaults to a valid read-only, local+live, unauthenticated config. `.update(...)` changes one field without re-specifying the rest. `MCPConfig.default(language=...)` is a shortcut for the one commonly-changed, deeply-nested setting. |
| `Tool` | `Flag` enum | `SEARCH`, `GET_TERM`, `GET_TERMS_ON`, `GET_TERMS_URLS`, `GET_TOPICS`, `GET_RANDOM_TERM`, `RELATED_TERMS`, `COMPARE`, `SYNC`. Aliases: `"read_only"` (everything but `SYNC`), `"all"`. |
| `resolve_tools(config)` / `MCPConfig.resolve_tools()` | function/method -> `Tool` | The actual tool set to build: `Tool.SYNC` stripped unless `local.allow_write` is also `True`. |
| `SessionAccess` | `dataclass` | `enabled`, `mode` (`SessionMode`), `idle_timeout`, `max_concurrent`, `options` (a `slb_glossary.config.SessionOptions`). |
| `SessionMode` | `Enum` | `EAGER` (open at startup), `LAZY` (open on first use, the default), `PER_CALL` (fresh session per call, full isolation). |
| `LocalAccess` | `dataclass` | `enabled`, `allow_write` (gates `Tool.SYNC` and `persist=True` regardless of `tools`), `database` (a `slb_glossary.config.DatabaseOptions`). |
| `SourcePolicy` | `dataclass` | `allowed` (`frozenset[Source] \| None`, auto-computed from `session.enabled`/`local.enabled` if unset), `default`, `expose_choice` (hide the `source` argument from tool schemas entirely). |
| `Timeout` | `dataclass` | `default` (seconds, `None` = uncapped), `per_tool` (name -> seconds override), `.for_tool(name)`. |
| `Auth` | `dataclass` | `provider` (a FastMCP `AuthProvider`, secures the transport itself), `required_scopes`. |
| `StaticTokenVerifier(tokens)` | class | A ready-made `AuthProvider` for fixed bearer tokens -> client identity. What `--auth-token` builds under the hood. |
| `import_provider(dotted_path)` | function -> `AuthProvider` | Load a custom provider from `"module:attr"`. |
| `RateLimit` | `dataclass` | `enabled`, `algorithm` (`RateLimitAlgorithm`), `limit`, `window`, `scope` (`RateLimitScope`). |
| `RateLimitAlgorithm` | `Enum` | `TOKEN_BUCKET` (bursts allowed) \| `SLIDING_WINDOW` (the default; minute-granularity). |
| `RateLimitScope` | `Enum` | `GLOBAL` \| `CLIENT` \| `TOOL` \| `CLIENT_TOOL` (the default, most granular). |
| `Hooks` | `dataclass` | `before_tool`, `after_tool`, `on_error`, `on_startup`, `on_shutdown`: each a tuple of caller-supplied callables run around tool calls/server lifecycle. |
| `Logging` | `dataclass` | `sinks`, `level`, `logger_name`, `fmt`, `propagate`, `log_tool_calls`. Mirrors `slb_glossary.logging.configure_logging`. |
| `Streaming` | `dataclass` | `default`, `allow_override`: whether tools that report MCP progress notifications (`glossary_search`, `glossary_get_terms_on`) stream by default, and whether a caller can override that. |
| `ServerInfo` | `dataclass` | `name`, `version` (defaults to `slb_glossary.__version__`), `instructions`, `logo` (a path or URL, inlined as a data URI at build time). |

See [Running an MCP Server](../agent/mcp-server.md) for how these compose in practice, the CLI flags (`slb mcp serve`) that set a subset of them without writing Python, and [`examples/app.py`](https://github.com/ti-oluwa/slb-glossary/blob/main/examples/app.py) for a complete runnable server.

## `slb_glossary.readers`

The read-side counterpart to `writers`, used internally by `local.load_file` and available directly for your own tabular-reading needs:

| Name | Kind | Notes |
|---|---|---|
| `read_rows(path, *, format=None)` | async generator -> `dict[str, Any]` | Lazily reads `path` as `{column: value}` row dicts, choosing a reader by file extension (or `format`, to override it). Raises `UnsupportedFormatError` if nothing's registered for the resolved format. |
| `supported_formats()` | function -> `list[str]` | `["csv", "json", "xlsm", "xlsx"]` on a base install (plus `"yaml"` once the `config` extra's PyYAML is present): `READERS` dict keys, sorted. Distinct from `writers.supported_formats()`, which only covers write formats. |
| `reader(format)` | decorator | Registers a new format: `@reader("yaml")` on a function matching the `Reader` signature below teaches `read_rows` (and `local.load_file`) that format. |
| `Reader` | type alias | `Callable[[pathlib.Path], AsyncIterator[dict[str, Any]]]`: what a function decorated with `@reader(...)` must look like, an async generator. |
| `READERS` | `dict[str, Reader]` | The registry `reader(...)` writes to and `read_rows` reads from directly, if you'd rather inspect or call a specific reader yourself. |
| `read_csv_rows` / `read_json_rows` / `read_xlsx_rows` | `Reader`s | The built-in readers `read_rows` dispatches to; callable directly for lower-level control. Each offloads its actual file I/O to a worker thread internally (`asyncio.to_thread`), so reading a large file does not blocks the event loop. |

```python
import slb_glossary as slb

async for row in slb.readers.read_rows("terms.csv"):
    print(row["term"], row["definition"])
```

Registering your own format works the same way `local.load_file` picks up any format `readers` already knows about, with no separate registration step needed on the `local.load_file` side:

```python
import asyncio
import pathlib
import typing

from slb_glossary.readers import reader, iter_in_thread


def _read_tsv_rows(path: pathlib.Path) -> typing.Iterator[dict[str, typing.Any]]:
    with path.open(newline="", encoding="utf-8") as f:
        header = f.readline().rstrip("\n").split("\t")
        for line in f:
            yield dict(zip(header, line.rstrip("\n").split("\t")))


@reader("tsv")
async def read_tsv_rows(path: pathlib.Path) -> typing.AsyncIterator[dict[str, typing.Any]]:
    async for row in iter_in_thread(_read_tsv_rows(path)):
        yield row


# `local.load_file` can now read `.tsv` files too, with no changes on its end.
await slb.local.load_file(db, "terms.tsv")
```

## `slb_glossary.constants`

Every tunable numeric/string constant in the package lives on the shared `constants` instance, each one optionally overridable by an environment variable without editing any code:

```python
from slb_glossary.constants import constants

print(constants.relevance_threshold)  # 0.45, or SLB_GLOSSARY_RELEVANCE_THRESHOLD if set
print(constants.compare_concurrency)
```

A representative sample, every one follows the same `SLB_GLOSSARY_<NAME>` pattern:

| Constant | Env var | Used by |
|---|---|---|
| `relevance_threshold` | `SLB_GLOSSARY_RELEVANCE_THRESHOLD` | `query.search`'s `Source.AUTO` local/live decision. |
| `compare_concurrency` | `SLB_GLOSSARY_COMPARE_CONCURRENCY` | `query.compare`'s default concurrency. |
| `default_search_mode` | `SLB_GLOSSARY_DEFAULT_SEARCH_MODE` | `local.search`'s default `mode`. |
| `embedding_model` | `SLB_GLOSSARY_EMBEDDING_MODEL` | `local.embed_terms`'s model2vec model. |
| `embed_batch_size` | `SLB_GLOSSARY_EMBED_BATCH_SIZE` | `local.embed_terms`'s default `batch_size`. |
| `import_batch_size` / `export_batch_size` | `SLB_GLOSSARY_IMPORT_BATCH_SIZE` / `SLB_GLOSSARY_EXPORT_BATCH_SIZE` | `local.load_file` / `local export`. |
| `rrf_k`, `lexical_weight`, `semantic_weight` | `SLB_GLOSSARY_RRF_K`, `SLB_GLOSSARY_LEXICAL_WEIGHT`, `SLB_GLOSSARY_SEMANTIC_WEIGHT` | `local.hybrid_search`'s Reciprocal Rank Fusion, see [Search Modes](../concepts/search-modes.md). |
| `session_auto_initialize` | `SLB_GLOSSARY_SESSION_AUTO_INITIALIZE` | Whether `session()` initializes eagerly or lazily by default. |
| `persist_by_default` | `SLB_GLOSSARY_PERSIST_BY_DEFAULT` | `slb_glossary.query`'s `persist` default when a caller does not pass one. `False` out of the box: a silent write to your local database is a bigger surprise from a library call than from a command you just typed. |
| `cli_cache_by_default` | `SLB_GLOSSARY_CLI_CACHE_BY_DEFAULT` | The CLI's `--cache`/`--no-cache` default. `True` out of the box, matching this flag's long-standing default. Deliberately a separate constant from `persist_by_default`, not shared with it, since the two have always defaulted differently and unifying them would have to pick one default and silently change the other. |
| `similar_terms_pool_size` | `SLB_GLOSSARY_SIMILAR_POOL_SIZE` | `get_term`/`compare`'s `with_similar=True`: how many candidates are pulled before alternatives are drawn from them. |
| `max_similar_terms` | `SLB_GLOSSARY_MAX_SIMILAR_TERMS` | `get_term`/`compare`'s `with_similar=True`: how many alternatives are actually returned. |
| `exact_match_score` | `SLB_GLOSSARY_EXACT_MATCH_SCORE` | The `.score` an exact `get_term` match is given (`1.0` by default), since exact matches aren't scored by the same ranking as a search. |

The full, current list is the source of truth: every constant is a `Constant(default, env_var=...)` line on `Constants` in `slb_glossary/constants.py`, each documented in place with what it controls.

## `slb_glossary` (top level)

| Name | Kind | Notes |
|---|---|---|
| `save(records, destination, *, format=None)` | coroutine | Writes a list or async iterable of `SearchResult`-likes to a file. Format inferred from extension unless overridden. |
| `writer(format)` | function -> `Writer` | Look up a specific writer callable directly. |
| `WRITERS` | `dict[str, Writer]` | `Writer = Callable[[Sequence[RecordLike], pathlib.Path], Awaitable[None]]`. |
| `read_rows(path, *, format=None)` | async generator -> `dict[str, Any]` | The read-side counterpart to `save`. See [`slb_glossary.readers`](#slb_glossaryreaders). |
| `reader(format)` | decorator | Registers a new read format. |
| `readers` / `writers` | modules | The submodules `read_rows`/`save` and friends actually live in; `slb.writers.supported_formats()`/`slb.readers.supported_formats()` aren't re-exported at the top level, so call them through the submodule. |
| `RetryPolicy` | `dataclass` | `attempts`, `base_delay`, `backoff_type`, `factor`, `max_delay`, `jitter`. |
| `BackoffType` | `StrEnum` | `CONSTANT`, `LINEAR`, `EXPONENTIAL`, `LOGARITHMIC`. |
| `SLBGlossaryError` | Exception | Base class for every exception this package raises. |
| `NetworkError`, `BrowserError`, `SessionNotInitializedError` (subclass of `BrowserError`), `ParsingError`, `ConfigError`, `DatabaseError`, `EmbeddingError`, `QueryError`, `LoggingError`, `UnsupportedFormatError`, `WriterError` | Exceptions | All subclass `SLBGlossaryError` (and, where it makes sense, a matching stdlib exception, `NetworkError` also subclasses `ConnectionError`, `WriterError` also subclasses `OSError`). |
| `log` | `logging.Logger` | The package's own logger, configurable via `slb_glossary.logging.configure_logging`/a custom `LogSink`. |
| `__version__` | `str` | Installed package version. |
