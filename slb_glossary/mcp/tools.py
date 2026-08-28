"""Tool definitions for `slb_glossary.mcp`'s MCP application/server."""

import dataclasses
import time
import typing
from collections.abc import Awaitable, Callable

from slb_glossary import query
from slb_glossary.local import sync
from slb_glossary.mcp.config import MCPConfig, Streaming, Tool
from slb_glossary.mcp.errors import MCPError
from slb_glossary.mcp.runtime import Runtime
from slb_glossary.query import QueryResult, Source
from slb_glossary.types import SearchResult

__all__ = [
    "DEFAULT_INSTRUCTIONS",
    "CompareArgs",
    "GetTermArgs",
    "ProgressReporter",
    "RandomTermArgs",
    "RelatedTermsArgs",
    "SearchArgs",
    "SyncArgs",
    "TermsOnArgs",
    "TermsUrlsArgs",
    "ToolSpec",
    "TopicsArgs",
    "build_tool_specs",
]

ProgressReporter = Callable[[int, int | None], Awaitable[None]]
"""
`async def report(count: int, total: int | None) -> None`.

A thin callback tools use to report incremental progress, so this module doesn't
need to import FastMCP's `Context` to stream. `slb_glossary.mcp.api.MCPApp` adapts
a real `Context.report_progress` into this shape when wiring tools up.
"""


DEFAULT_INSTRUCTIONS = """\
This server is a deterministic source of truth for oil-and-gas/energy
industry terminology, backed by the SLB Energy Glossary (glossary.slb.com).
It doesn't generate or paraphrase definitions as every result is the
glossary's own published wording, looked up exactly (from a local cached
copy, the live site, or both), never invented or approximated. Call a
tool whenever a definition, spelling, or topic classification needs to be
authoritative rather than recalled from your own training - e.g. before
stating a technical term's definition, disambiguating similar-sounding
terms, or citing a source for a term used in a report/answer.

Tool selection:
- Know the exact term name already? Use `glossary_get_term`. It's the
  cheapest, most precise lookup, and the right default for "what does X
  mean" once you know X exactly.
- Free-text/keyword/partial query, unsure of the exact term, or checking
  whether a term exists at all? Use `glossary_search`.
- Want every term under one subject area (e.g. "Drilling", "Geology")?
  Use `glossary_get_terms_on`; call `glossary_get_topics` first if you
  don't already know the exact topic name.
- Want to see how several specific terms relate/compare? Use
  `glossary_compare` (several terms at once) or `glossary_related_terms`
  (terms linked from one term's own definition).
- Only need URLs, not full definitions (e.g. to list candidates before
  fetching a few)? Use `glossary_get_terms_urls`.
- Want a term at random, optionally within a topic? Use `glossary_random_term`.

Every tool accepts a `source` argument ("auto", "local", or "live") when
this server exposes that choice: "auto" tries the local cache first and
only reaches out to the live site if nothing local matches. Prefer leaving
it at "auto" unless you specifically need one or the other. A result's
"source" field in the response tells you which one actually served it.
"""


def term_lookup_to_dict(lookup: QueryResult[SearchResult | None]) -> dict[str, typing.Any]:
    return {
        "value": lookup.value.asdict() if lookup.value is not None else None,
        "source": lookup.source.value,
        "persisted": lookup.persisted,
        "score": lookup.score,
    }


def related_lookup_to_dict(lookup: QueryResult[tuple[typing.Any, ...]]) -> dict[str, typing.Any]:
    return {
        "value": [related._asdict() for related in lookup.value],
        "source": lookup.source.value,
        "persisted": lookup.persisted,
    }


@dataclasses.dataclass(slots=True, kw_only=True)
class ToolSpec:
    """One MCP tool's registration metadata, ready for `slb_glossary.mcp.api.MCPApp` to wire up."""

    name: str
    """MCP tool name, e.g. `"glossary_search"`."""

    description: str
    """
    Tool description shown to MCP clients/LLMs. Written to make the
    right tool choice as unambiguous as possible.
    """

    args_type: type
    """The frozen dataclass type describing this tool's arguments."""

    tags: frozenset[str]
    """Categorization tags (e.g. `{"read", "search"}`) forwarded to FastMCP."""

    writes: bool
    """
    Whether this tool can write to the local database. Only ever `True`
    for the sync tool. Drives extra logging/auditing in `slb_glossary.mcp.middleware`.
    """

    supports_source: bool
    """Whether `args_type` has a `source` field at all."""

    supports_streaming: bool
    """Whether this tool accepts a `stream` argument and reports progress."""

    handler: Callable[..., Awaitable[typing.Any]]
    """
    `async def handler(args, runtime, config, *, report_progress) -> dict`.

    Return type is `Any` here since it's whatever JSON-serializable `dict`
    shape that particular handler produces.
    """


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class SearchArgs:
    """Arguments for `glossary_search`."""

    query: str
    """Free-text search query, e.g. `"water saturation"`."""

    source: Source = Source.AUTO
    """Where to search: `"auto"` (local first, live fallback), `"local"`, or `"live"`."""

    topic: str | None = None
    """Restrict results to this topic, or several comma-separated topics."""

    start_letter: str | None = None
    """Restrict results to terms starting with this letter."""

    limit: int | None = 5
    """Maximum number of results to return. `None` for unlimited (use with care)."""

    persist: bool = False
    """
    If a live fetch happens, cache its results locally for next time.
    Ignored (never persists) unless the server was configured with local
    write access enabled.
    """

    fuzzy: bool = False
    """Tolerate minor misspellings/partial names in `topic` for local reads."""

    stream: bool = False
    """
    Report incremental MCP progress notifications as results are found.
    Ignored if this server's streaming default disallows overriding.
    """


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class GetTermArgs:
    """Arguments for `glossary_get_term` and `glossary_related_terms`."""

    term_or_url: str
    """An exact (case-insensitive) term name, or a glossary term detail-page URL."""

    source: Source = Source.AUTO
    """Where to look up the term: `"auto"`, `"local"`, or `"live"`."""

    persist: bool = False
    """
    If a live fetch happens, cache its result locally. Ignored unless
    the server has local write access enabled.
    """


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class TermsOnArgs:
    """Arguments for `glossary_get_terms_on`."""

    topic: str
    """
    Topic name, or several comma-separated topic names. Call
    `glossary_get_topics` first if you're unsure of the exact name.
    """

    source: Source = Source.AUTO
    """Where to read from: `"auto"`, `"local"`, or `"live"`."""

    start_letter: str | None = None
    """Restrict results to terms starting with this letter."""

    limit: int | None = 25
    """Maximum number of terms to return. `None` for unlimited (use with care)."""

    persist: bool = False
    """
    If a live fetch happens, cache its results locally. Ignored unless
    the server has local write access enabled.
    """

    fuzzy: bool = False
    """Tolerate minor misspellings/partial names in `topic` for local reads."""

    stream: bool = False
    """Report incremental MCP progress notifications as terms are found."""


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class TermsUrlsArgs:
    """Arguments for `glossary_get_terms_urls`."""

    query: str | None = None
    """Restrict to a free-text query match."""

    topic: str | None = None
    """Restrict to this topic, or several comma-separated topics."""

    start_letter: str | None = None
    """Restrict to terms starting with this letter."""

    source: Source = Source.AUTO
    """Where to read from: `"auto"`, `"local"`, or `"live"`."""

    limit: int | None = 50
    """Maximum number of URLs to return. `None` for unlimited (use with care)."""

    fuzzy: bool = False
    """Tolerate minor misspellings/partial names in `topic` for local reads."""


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class TopicsArgs:
    """Arguments for `glossary_get_topics`."""

    source: Source = Source.AUTO
    """Where to read from: `"auto"`, `"local"`, or `"live"`."""


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class RelatedTermsArgs(GetTermArgs):
    """Arguments for `glossary_related_terms`. Same shape as `GetTermArgs`."""


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class RandomTermArgs:
    """Arguments for `glossary_random_term`."""

    source: Source = Source.AUTO
    """Where to pick from: `"auto"`, `"local"`, or `"live"`."""

    topic: str | None = None
    """Restrict the pick to this topic, or several comma-separated topics."""

    persist: bool = False
    """If a live pick happens, cache it locally. Ignored unless the server
    has local write access enabled."""

    fuzzy: bool = False
    """Tolerate minor misspellings/partial names in `topic` for local picks."""


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class CompareArgs:
    """Arguments for `glossary_compare`."""

    terms: tuple[str, ...]
    """Term names (or detail-page URLs) to look up, side by side."""

    source: Source = Source.AUTO
    """Where to look up each term: `"auto"`, `"local"`, or `"live"`."""

    persist: bool = False
    """
    If a live fetch happens, cache each result locally. Ignored unless
    the server has local write access enabled.
    """


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class SyncArgs:
    """
    Arguments for `glossary_sync` - the one tool that writes to the local database.

    Only ever registered when this server was explicitly configured with
    both `Tool.SYNC` and `LocalAccess.allow_write=True`.
    """

    mode: typing.Literal["query", "topic", "letter", "all"]
    """
    What to sync: a single free-text `"query"`, everything under one
    `"topic"`, everything starting with one `"letter"`, or `"all"` (the
    entire glossary. Heavy, so use sparingly).
    """

    value: str | None = None
    """The query/topic/letter to sync. Required for every `mode` except `"all"`."""

    limit: int | None = None
    """Maximum number of terms to fetch. `None` for unlimited. Ignored for `mode="all"`."""

    concurrency: int = 1
    """Concurrent term-page fetches while syncing."""


def resolve_source(requested: Source, config: MCPConfig) -> Source:
    """Narrow `requested` against `config.source_policy`, raising if it's not allowed."""
    policy = config.source_policy
    source = requested if policy.expose_choice else policy.default
    allowed = policy.allowed
    assert allowed is not None, (
        "`MCPConfig.source_policy.allowed` should always be resolved to a concrete frozenset "
        "by `MCPConfig` (post initialization) before a tool call can reach this point."
    )
    if source not in allowed:
        choices = ", ".join(sorted(item.value for item in allowed))
        raise MCPError(
            f"source={source.value!r} isn't permitted by this server's policy. Allowed: {choices}."
        )
    return source


def get_effective_persist(requested: bool, config: MCPConfig) -> bool:
    """`persist` only ever takes effect when the server was configured to allow writes."""
    return requested and config.local.allow_write


def get_effectivestream(requested: bool, config: MCPConfig) -> bool:
    streaming: Streaming = config.streaming
    if not streaming.allow_override:
        return streaming.default
    return requested


async def _handle_search(
    args: SearchArgs,
    runtime: Runtime,
    config: MCPConfig,
    *,
    report_progress: ProgressReporter,
) -> dict[str, typing.Any]:
    source = resolve_source(args.source, config)
    stream = get_effectivestream(args.stream, config)
    started_at = time.monotonic()
    async with runtime.acquire(source) as (db, session):
        results: list[dict[str, typing.Any]] = []
        count = 0
        async for lookup in query.search(
            args.query,
            db=db,
            session=session,
            source=source,
            topic=args.topic,
            start_letter=args.start_letter,
            limit=args.limit,
            persist=get_effective_persist(args.persist, config),
            fuzzy=args.fuzzy,
        ):
            result = {
                **lookup.value.asdict(),
                "source": lookup.source.value,
                "score": lookup.score,
            }
            results.append(result)
            count += 1
            if stream:
                await report_progress(count, args.limit)
    return {
        "results": results,
        "count": count,
        "source": source.value,
        "elapsed_ms": round((time.monotonic() - started_at) * 1000, 1),
    }


async def _handle_get_term(
    args: GetTermArgs,
    runtime: Runtime,
    config: MCPConfig,
    *,
    report_progress: ProgressReporter,
) -> dict[str, typing.Any]:
    source = resolve_source(args.source, config)
    async with runtime.acquire(source) as (db, session):
        lookup = await query.get_term(
            args.term_or_url,
            db=db,
            session=session,
            source=source,
            persist=get_effective_persist(args.persist, config),
        )
    return term_lookup_to_dict(lookup)


async def _handle_terms_on(
    args: TermsOnArgs,
    runtime: Runtime,
    config: MCPConfig,
    *,
    report_progress: ProgressReporter,
) -> dict[str, typing.Any]:
    source = resolve_source(args.source, config)
    stream = get_effectivestream(args.stream, config)
    started_at = time.monotonic()
    async with runtime.acquire(source) as (db, session):
        results: list[dict[str, typing.Any]] = []
        count = 0
        async for result in query.get_terms_on(
            args.topic,
            db=db,
            session=session,
            source=source,
            start_letter=args.start_letter,
            limit=args.limit,
            persist=get_effective_persist(args.persist, config),
            fuzzy=args.fuzzy,
        ):
            results.append(result.asdict())
            count += 1
            if stream:
                await report_progress(count, args.limit)
    return {
        "results": results,
        "count": count,
        "source": source.value,
        "elapsed_ms": round((time.monotonic() - started_at) * 1000, 1),
    }


async def _handle_terms_urls(
    args: TermsUrlsArgs,
    runtime: Runtime,
    config: MCPConfig,
    *,
    report_progress: ProgressReporter,
) -> dict[str, typing.Any]:
    source = resolve_source(args.source, config)
    async with runtime.acquire(source) as (db, session):
        urls = [
            url
            async for url in query.get_terms_urls(
                db=db,
                session=session,
                source=source,
                query=args.query,
                topic=args.topic,
                start_letter=args.start_letter,
                limit=args.limit,
                fuzzy=args.fuzzy,
            )
        ]
    return {"urls": urls, "count": len(urls), "source": source.value}


async def _handle_topics(
    args: TopicsArgs,
    runtime: Runtime,
    config: MCPConfig,
    *,
    report_progress: ProgressReporter,
) -> dict[str, typing.Any]:
    source = resolve_source(args.source, config)
    async with runtime.acquire(source) as (db, session):
        topics = await query.get_topics(db=db, session=session, source=source)
    return {"topics": topics, "count": len(topics), "source": source.value}


async def _handle_related_terms(
    args: RelatedTermsArgs,
    runtime: Runtime,
    config: MCPConfig,
    *,
    report_progress: ProgressReporter,
) -> dict[str, typing.Any]:
    source = resolve_source(args.source, config)
    async with runtime.acquire(source) as (db, session):
        lookup = await query.related_terms(
            args.term_or_url,
            db=db,
            session=session,
            source=source,
            persist=get_effective_persist(args.persist, config),
        )
    return related_lookup_to_dict(lookup)


async def _handle_random_term(
    args: RandomTermArgs,
    runtime: Runtime,
    config: MCPConfig,
    *,
    report_progress: ProgressReporter,
) -> dict[str, typing.Any]:
    source = resolve_source(args.source, config)
    async with runtime.acquire(source) as (db, session):
        lookup = await query.get_random_term(
            db=db,
            session=session,
            source=source,
            topic=args.topic,
            persist=get_effective_persist(args.persist, config),
            fuzzy=args.fuzzy,
        )
    return term_lookup_to_dict(lookup)


async def _handle_compare(
    args: CompareArgs,
    runtime: Runtime,
    config: MCPConfig,
    *,
    report_progress: ProgressReporter,
) -> dict[str, typing.Any]:
    source = resolve_source(args.source, config)
    async with runtime.acquire(source) as (db, session):
        lookups = await query.compare(
            args.terms,
            db=db,
            session=session,
            source=source,
            persist=get_effective_persist(args.persist, config),
        )
    return {term: term_lookup_to_dict(lookup) for term, lookup in lookups.items()}


async def _handle_sync(
    args: SyncArgs,
    runtime: Runtime,
    config: MCPConfig,
    *,
    report_progress: ProgressReporter,
) -> dict[str, typing.Any]:
    if not config.local.allow_write:
        raise MCPError(
            "`glossary_sync` is unavailable: this server was not configured with local write access."
        )
    if args.mode != "all" and not args.value:
        raise ValueError(f"`value` is required for mode={args.mode!r}.")

    db = await runtime.open_local_db()
    async with runtime.acquire(Source.LIVE) as (_, session):
        assert session is not None, (
            "`runtime.acquire(Source.LIVE)` should always yield a session; `Runtime.acquire` "
            "only returns a None session when it isn't asked for one."
        )
        if args.mode == "query":
            assert args.value is not None
            summary = await sync.sync_query(
                db, session, args.value, limit=args.limit, concurrency=args.concurrency
            )
        elif args.mode == "topic":
            assert args.value is not None
            summary = await sync.sync_topic(
                db, session, args.value, limit=args.limit, concurrency=args.concurrency
            )
        elif args.mode == "letter":
            assert args.value is not None
            summary = await sync.sync_letter(
                db, session, args.value, limit=args.limit, concurrency=args.concurrency
            )
        else:
            assert args.mode == "all", f"Unexpected `SyncArgs.mode` {args.mode!r}."
            summary = await sync.sync_all(db, session, concurrency=args.concurrency)

    return dataclasses.asdict(summary)


def build_tool_specs(config: MCPConfig) -> list[ToolSpec]:
    """
    Build the list of `ToolSpec`s to register, given `config.resolve_tools()`.

    :param config: The server's `MCPConfig`.
    :return: One `ToolSpec` per enabled tool, in a stable, sensible order.
    """
    enabled = config.resolve_tools()
    specs: list[ToolSpec] = []

    def add(
        flag: Tool,
        *,
        name: str,
        description: str,
        args_type: type,
        tags: frozenset[str],
        handler: Callable[..., Awaitable[typing.Any]],
        writes: bool = False,
        supports_source: bool = True,
        supports_streaming: bool = False,
    ) -> None:
        if flag not in enabled:
            return
        specs.append(
            ToolSpec(
                name=name,
                description=description,
                args_type=args_type,
                tags=tags,
                writes=writes,
                supports_source=supports_source,
                supports_streaming=supports_streaming,
                handler=handler,
            )
        )

    add(
        Tool.SEARCH,
        name="glossary_search",
        description=(
            "Authoritative free-text search across the SLB Energy Glossary. Use this for "
            "keyword/partial-name queries, to check whether a term exists, or when you're not "
            "sure of a term's exact name. Returns the glossary's own published definitions "
            "(never generated), best match first. If you already know the exact term name, "
            "use glossary_get_term instead - it's cheaper and more precise."
        ),
        args_type=SearchArgs,
        tags=frozenset({"read", "search"}),
        handler=_handle_search,
        supports_streaming=True,
    )
    add(
        Tool.GET_TERM,
        name="glossary_get_term",
        description=(
            "Get the authoritative, exact definition of a single glossary term by its precise "
            "(case-insensitive) name, or by its detail-page URL. The deterministic source of "
            "truth to call before stating what a technical term means, instead of relying on "
            "your own recollection. Cheaper and more precise than glossary_search when you "
            "already know the exact term name."
        ),
        args_type=GetTermArgs,
        tags=frozenset({"read", "lookup"}),
        handler=_handle_get_term,
    )
    add(
        Tool.GET_TERMS_ON,
        name="glossary_get_terms_on",
        description=(
            "List every authoritative glossary term filed under one or more subject-area "
            "topics, e.g. 'Drilling' or 'Geology,Geophysics'. Use this to enumerate a whole "
            "category of terminology at once. Call glossary_get_topics first if you're not "
            "sure of the exact topic name."
        ),
        args_type=TermsOnArgs,
        tags=frozenset({"read", "topic"}),
        handler=_handle_terms_on,
        supports_streaming=True,
    )
    add(
        Tool.GET_TERMS_URLS,
        name="glossary_get_terms_urls",
        description=(
            "List glossary term detail-page URLs matching a query/topic/starting letter, "
            "without fetching full definitions. Lighter-weight than glossary_search - use "
            "this when you only need to enumerate or count candidates, or want a citable "
            "source URL without pulling the whole definition."
        ),
        args_type=TermsUrlsArgs,
        tags=frozenset({"read", "search"}),
        handler=_handle_terms_urls,
    )
    add(
        Tool.GET_TOPICS,
        name="glossary_get_topics",
        description=(
            "List every glossary topic (subject area) and how many terms are filed under "
            "each. The authoritative topic taxonomy this server uses. Call this first "
            "whenever you need a valid topic name for glossary_get_terms_on, rather than "
            "guessing one."
        ),
        args_type=TopicsArgs,
        tags=frozenset({"read", "topic"}),
        handler=_handle_topics,
    )
    add(
        Tool.RELATED_TERMS,
        name="glossary_related_terms",
        description=(
            "Get the authoritative list of terms the glossary itself links from within one "
            "term's own definition ('See related terms'). Use this to explore concepts "
            "adjacent to a term you already looked up, sourced from the glossary rather than inferred."
        ),
        args_type=RelatedTermsArgs,
        tags=frozenset({"read", "lookup"}),
        handler=_handle_related_terms,
    )
    add(
        Tool.RANDOM_TERM,
        name="glossary_random_term",
        description=(
            "Get one randomly chosen glossary term (with its authoritative definition), "
            "optionally restricted to a topic. Use this for exploration/discovery/quizzing, "
            "not for looking up something specific - use glossary_get_term for that."
        ),
        args_type=RandomTermArgs,
        tags=frozenset({"read", "discovery"}),
        handler=_handle_random_term,
    )
    add(
        Tool.COMPARE,
        name="glossary_compare",
        description=(
            "Look up several specific glossary terms at once, each with its authoritative "
            "definition, for side-by-side comparison. Use this instead of several separate "
            "glossary_get_term calls when the user wants to compare/contrast multiple named terms."
        ),
        args_type=CompareArgs,
        tags=frozenset({"read", "lookup"}),
        handler=_handle_compare,
    )
    add(
        Tool.SYNC,
        name="glossary_sync",
        description=(
            "Fetch terms from the live glossary and write them into this server's local "
            "cache, so future lookups can be served locally. Only available on servers "
            "explicitly configured to allow local writes. Prefer mode='query'/'topic'/'letter' "
            "over mode='all', which mirrors the entire glossary and is much heavier."
        ),
        args_type=SyncArgs,
        tags=frozenset({"write", "sync"}),
        handler=_handle_sync,
        writes=True,
        supports_source=False,
    )

    return specs
