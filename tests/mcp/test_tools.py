"""Tests for `mcp.tools`: policy-resolution helpers, result-to-dict converters, `build_tool_specs`."""

import pytest

from slb_glossary.mcp.config import (
    LocalAccess,
    MCPConfig,
    SessionAccess,
    SourcePolicy,
    Streaming,
    Tool,
)
from slb_glossary.mcp.errors import MCPError
from slb_glossary.mcp.tools import (
    build_tool_specs,
    get_effective_concurrency,
    get_effective_persist,
    get_effective_stream,
    related_lookup_to_dict,
    resolve_source,
    similar_lookup_to_dict,
    term_lookup_to_dict,
)
from slb_glossary.query import QueryResult, SimilarResult, Source
from slb_glossary.types import RelatedTerm, SearchResult
from tests.factories import make_search_result

pytestmark = pytest.mark.unit


class TestResolveSource:
    def test_expose_choice_true_uses_the_requested_source(self) -> None:
        """With `expose_choice=True` (the default), the caller's own requested source is used."""
        config = MCPConfig(source_policy=SourcePolicy(expose_choice=True))
        assert resolve_source(Source.LOCAL, config) is Source.LOCAL

    def test_expose_choice_false_ignores_the_request_and_uses_the_default(self) -> None:
        """With `expose_choice=False`, the caller's request is ignored in favor of the policy default."""
        config = MCPConfig(
            source_policy=SourcePolicy(expose_choice=False, default=Source.LOCAL),
            session=SessionAccess(enabled=False),
        )
        assert resolve_source(Source.AUTO, config) is Source.LOCAL

    def test_a_disallowed_source_raises(self) -> None:
        """A source not in `source_policy.allowed` is rejected, not silently narrowed."""
        config = MCPConfig(
            session=SessionAccess(enabled=False),
            source_policy=SourcePolicy(default=Source.LOCAL, allowed=frozenset({Source.LOCAL})),
        )
        with pytest.raises(MCPError, match="is not permitted"):
            resolve_source(Source.LIVE, config)


class TestGetEffectivePersist:
    def test_true_with_allow_write_stays_true(self) -> None:
        """A caller's `persist=True` is honored when the server allows writes."""
        config = MCPConfig(local=LocalAccess(allow_write=True))
        assert get_effective_persist(True, config) is True

    def test_true_without_allow_write_is_forced_false(self) -> None:
        """A caller's `persist=True` is overridden to `False` when the server disallows writes."""
        config = MCPConfig(local=LocalAccess(allow_write=False))
        assert get_effective_persist(True, config) is False

    def test_false_stays_false_regardless(self) -> None:
        """A caller's `persist=False` stays `False` regardless of server policy."""
        config = MCPConfig(local=LocalAccess(allow_write=True))
        assert get_effective_persist(False, config) is False


class TestGetEffectiveStream:
    def test_allow_override_true_uses_the_request(self) -> None:
        """With `allow_override=True` (the default), the caller's own `stream` request is used."""
        config = MCPConfig(streaming=Streaming(allow_override=True, default=False))
        assert get_effective_stream(True, config) is True

    def test_allow_override_false_ignores_the_request(self) -> None:
        """With `allow_override=False`, the caller's request is ignored in favor of the configured default."""
        config = MCPConfig(streaming=Streaming(allow_override=False, default=True))
        assert get_effective_stream(False, config) is True


class TestGetEffectiveConcurrency:
    def test_none_uses_the_default(self) -> None:
        """No requested concurrency at all falls back to the tool's own `default`."""
        config = MCPConfig()
        assert get_effective_concurrency(None, 5, config) == 5

    def test_a_requested_value_below_the_cap_is_honored(self) -> None:
        """A requested value under the server's cap is used as-is."""
        config = MCPConfig(session=SessionAccess(max_request_concurrency=10))
        assert get_effective_concurrency(3, 5, config) == 3

    def test_a_requested_value_above_the_cap_is_clamped(self) -> None:
        """A requested value over the server's cap is clamped down to it."""
        config = MCPConfig(session=SessionAccess(max_request_concurrency=4))
        assert get_effective_concurrency(10, 5, config) == 4

    def test_no_cap_configured_leaves_it_unclamped(self) -> None:
        """No `max_request_concurrency` configured at all leaves a high request value unclamped."""
        config = MCPConfig(session=SessionAccess(max_request_concurrency=None))
        assert get_effective_concurrency(100, 5, config) == 100

    def test_never_goes_below_one(self) -> None:
        """The result is never below `1`, even for a `default=0` with no request."""
        config = MCPConfig()
        assert get_effective_concurrency(None, 0, config) == 1


class TestTermLookupToDict:
    def test_a_found_term(self) -> None:
        """A found term serializes its `SearchResult` and provenance fields."""
        result = make_search_result(url="https://x.com/a", term="Porosity")
        lookup: QueryResult[SearchResult | None] = QueryResult(
            value=result, source=Source.LOCAL, persisted=False, score=0.9
        )
        data = term_lookup_to_dict(lookup)
        assert data["value"]["term"] == "Porosity"
        assert data["source"] == "local"
        assert data["persisted"] is False
        assert data["score"] == 0.9

    def test_a_miss_serializes_value_as_none(self) -> None:
        """A lookup that found nothing serializes `value` as `None`, not an error."""
        lookup: QueryResult[SearchResult | None] = QueryResult(
            value=None, source=Source.LIVE, persisted=False
        )
        data = term_lookup_to_dict(lookup)
        assert data["value"] is None
        assert data["source"] == "live"


class TestRelatedLookupToDict:
    def test_serializes_every_related_term(self) -> None:
        """Every related term in the tuple is serialized to its own dict."""
        related = (RelatedTerm(term="Permeability", url="https://x.com/b"),)
        lookup = QueryResult(value=related, source=Source.LOCAL, persisted=True)
        data = related_lookup_to_dict(lookup)
        assert data["value"] == [{"term": "Permeability", "url": "https://x.com/b"}]
        assert data["persisted"] is True

    def test_an_empty_tuple_serializes_to_an_empty_list(self) -> None:
        """No related terms at all serializes to an empty list, not `None` or an error."""
        lookup: QueryResult[tuple] = QueryResult(value=(), source=Source.LOCAL, persisted=False)
        data = related_lookup_to_dict(lookup)
        assert data["value"] == []


class TestSimilarLookupToDict:
    def test_an_exact_match_with_no_alternatives(self) -> None:
        """An exact match with no similar alternatives serializes `similar` as an empty list."""
        exact_result = make_search_result(url="https://x.com/a", term="Porosity")
        exact = QueryResult(value=exact_result, source=Source.LOCAL, persisted=False, score=1.0)
        similar_result = SimilarResult(exact=exact, similar=())
        lookup = QueryResult(value=similar_result, source=Source.LOCAL, persisted=False, score=1.0)

        data = similar_lookup_to_dict(lookup)
        assert data["value"]["exact"]["value"]["term"] == "Porosity"
        assert data["value"]["similar"] == []

    def test_no_exact_match_with_alternatives(self) -> None:
        """No exact match at all, but some similar alternatives, serializes `exact` as `None`."""
        alt_result = make_search_result(url="https://x.com/b", term="Permeability")
        alt = QueryResult(value=alt_result, source=Source.LIVE, persisted=False, score=0.5)
        similar_result = SimilarResult(exact=None, similar=(alt,))
        lookup = QueryResult(value=similar_result, source=Source.LIVE, persisted=False)

        data = similar_lookup_to_dict(lookup)
        assert data["value"]["exact"] is None
        assert len(data["value"]["similar"]) == 1
        assert data["value"]["similar"][0]["value"]["term"] == "Permeability"


class TestBuildToolSpecs:
    def test_read_only_config_has_no_write_capable_tool(self) -> None:
        """A read-only config's specs never include the write-capable sync tool."""
        config = MCPConfig(tools=Tool.READ_ONLY)
        specs = build_tool_specs(config)
        assert not any(spec.writes for spec in specs)

    def test_write_enabled_config_includes_the_sync_tool(self) -> None:
        """`Tool.SYNC` with `local.allow_write=True` includes exactly one write-capable spec."""
        config = MCPConfig(tools=Tool.ALL, local=LocalAccess(allow_write=True))
        specs = build_tool_specs(config)
        write_specs = [spec for spec in specs if spec.writes]
        assert len(write_specs) == 1
        assert write_specs[0].name == "glossary_sync"

    def test_search_tool_supports_streaming(self) -> None:
        """The search tool's spec is marked as supporting streaming."""
        config = MCPConfig(tools=Tool.SEARCH)
        specs = build_tool_specs(config)
        assert len(specs) == 1
        assert specs[0].supports_streaming is True

    def test_only_search_tool_gives_exactly_one_spec(self) -> None:
        """Requesting only `Tool.SEARCH` builds exactly one tool spec."""
        config = MCPConfig(tools=Tool.SEARCH)
        specs = build_tool_specs(config)
        assert [spec.name for spec in specs] == ["glossary_search"]

    def test_every_spec_has_a_non_empty_name_and_description(self) -> None:
        """Every registered tool spec, across the full set, has a real name and description."""
        config = MCPConfig(tools=Tool.ALL, local=LocalAccess(allow_write=True))
        specs = build_tool_specs(config)
        assert len(specs) > 1
        for spec in specs:
            assert spec.name
            assert spec.description
