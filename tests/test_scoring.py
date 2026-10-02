"""`classify_name_match`'s symbol/accent/plural-insensitive tiers."""

import pytest

from slb_glossary.constants import constants
from slb_glossary.scoring import NameMatchTier, classify_name_match, token_forms

pytestmark = pytest.mark.unit


class TestClassifyNameMatch:
    @pytest.mark.parametrize(
        "query",
        ["capillary-pressure", "Capillary_Pressure", "capillary pressure?", ":capillary pressure"],
    )
    def test_symbol_variants_are_exact(self, query: str) -> None:
        match = classify_name_match(query, "Capillary pressure")
        assert match is not None
        assert match.tier is NameMatchTier.EXACT
        assert match.score == constants.exact_match_score

    def test_symbols_in_the_term_are_ignored_too(self) -> None:
        match = classify_name_match("water cut", "Water-cut")
        assert match is not None and match.tier is NameMatchTier.EXACT

    def test_spacing_only_difference_is_a_prefix_tier_match(self) -> None:
        match = classify_name_match("watercut", "Water-cut")
        assert match is not None
        assert match.tier is NameMatchTier.PREFIX
        assert match.score == constants.prefix_match_score

    def test_trailing_symbol_on_a_prefix_is_still_a_prefix(self) -> None:
        match = classify_name_match("capillary-", "Capillary pressure")
        assert match is not None and match.tier is NameMatchTier.PREFIX

    def test_plural_query_token_still_covers_the_singular_term_token(self) -> None:
        match = classify_name_match("pressures capillary", "Capillary pressure")
        assert match is not None and match.tier is NameMatchTier.ALL_TOKENS

    @pytest.mark.parametrize(("query", "term"), [("???", "Rig"), ("rig", "???"), ("", "Rig")])
    def test_nothing_to_compare_is_no_match(self, query: str, term: str) -> None:
        assert classify_name_match(query, term) is None

    def test_unrelated_names_do_not_match(self) -> None:
        assert classify_name_match("porosity", "Rig floor") is None


class TestTokenForms:
    def test_includes_singular_forms(self) -> None:
        assert {"pressures", "pressure"} <= token_forms("pressures")
        assert {"cavities", "cavity"} <= token_forms("cavities")
        assert {"boxes", "box"} <= token_forms("boxes")

    @pytest.mark.parametrize("token", ["gas", "bus", "loss", "is"])
    def test_short_or_double_s_tokens_are_left_alone(self, token: str) -> None:
        assert token_forms(token) == frozenset({token})
