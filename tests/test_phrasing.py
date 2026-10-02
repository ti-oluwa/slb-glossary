"""
`clean_query`'s natural-language stripping.
"""

import pytest

from slb_glossary.phrasing import clean_query, query_variants, trim_symbols

pytestmark = pytest.mark.unit


class TestCleanQuery:
    @pytest.mark.parametrize(
        ("query", "expected"),
        [
            ("what is porosity", "porosity"),
            ("what does porosity mean", "porosity"),
            ("what's porosity", "porosity"),
            ("what's a porosity", "porosity"),
            ("define porosity", "porosity"),
            ("definition of porosity", "porosity"),
            ("tell me about porosity", "porosity"),
            ("explain porosity", "porosity"),
            ("explain what porosity is", "porosity"),
            ("meaning of porosity", "porosity"),
        ],
    )
    def test_strips_recognized_wrapper(self, query: str, expected: str) -> None:
        """A recognized natural-language wrapper is stripped down to its term."""
        assert clean_query(query) == expected

    def test_leaves_plain_term_untouched_besides_trimming(self) -> None:
        """A plain term with no recognized wrapper passes through unchanged."""
        assert clean_query("porosity") == "porosity"

    def test_leaves_geometric_mean_untouched(self) -> None:
        """`"geometric mean"` is not mangled: it does not start with any recognized wrapper word."""
        assert clean_query("geometric mean") == "geometric mean"

    def test_explain_prefixed_query_is_still_stripped_to_its_remainder(self) -> None:
        """Any `"explain ..."` query matches the generic `explain` pattern and is
        stripped to everything after it - even `"explain and give an example"`,
        which becomes `"and give an example"`. This is the actual, verified
        behavior of the `explain` pattern (a broad catch-all with no
        exception for phrases that merely start with the word "explain"),
        not a case the module guards against."""
        assert clean_query("explain and give an example") == "and give an example"

    def test_case_insensitive_matching(self) -> None:
        """Wrapper matching is case-insensitive."""
        assert clean_query("WHAT IS Porosity") == "Porosity"
        assert clean_query("Define Porosity") == "Porosity"

    def test_strips_surrounding_whitespace_in_all_cases(self) -> None:
        """Surrounding whitespace is trimmed whether or not a wrapper matched."""
        assert clean_query("  porosity  ") == "porosity"
        assert clean_query("  what is porosity  ") == "porosity"

    def test_empty_string_returns_empty_string(self) -> None:
        """An empty (or whitespace-only) query returns an empty string."""
        assert clean_query("") == ""
        assert clean_query("   ") == ""


class TestTrimSymbols:
    @pytest.mark.parametrize(
        ("query", "expected"),
        [
            ("capillary-", "capillary"),
            (":rig", "rig"),
            ("porous?", "porous"),
            ("(porosity)", "porosity"),
            ("--  gas-oil ratio !!", "gas-oil ratio"),
            ("'permeability'", "permeability"),
        ],
    )
    def test_trims_edges_but_keeps_inner_symbols(self, query: str, expected: str) -> None:
        assert trim_symbols(query) == expected

    @pytest.mark.parametrize("query", ["???", "-", "()"])
    def test_symbol_only_query_is_not_emptied(self, query: str) -> None:
        """Nothing to keep, so it is returned (whitespace-trimmed) rather than blanked."""
        assert trim_symbols(f"  {query}  ") == query


class TestCleanQuerySymbols:
    @pytest.mark.parametrize(
        ("query", "expected"),
        [
            ("capillary-", "capillary"),
            ("what is porosity?!", "porosity"),
            ("define: water-cut!!", "water-cut"),
            ("Explain: capillary-pressure", "capillary-pressure"),
            ("what is :rig?", "rig"),
        ],
    )
    def test_symbols_around_the_term_are_dropped(self, query: str, expected: str) -> None:
        assert clean_query(query) == expected


class TestQueryVariants:
    def test_plain_query_has_a_single_variant(self) -> None:
        assert query_variants("Porosity") == ("Porosity",)

    def test_hyphenated_query_adds_its_spaced_form(self) -> None:
        assert query_variants("capillary-pressure") == (
            "capillary-pressure",
            "capillary pressure",
        )

    def test_symbol_only_query_has_no_variants(self) -> None:
        assert query_variants("???") == ()
