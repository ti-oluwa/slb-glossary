"""
Benchmark query set for the relevance harness, run against `tests/relevance/corpus.py`.

Every `BenchmarkQuery.expected` is a set, not a single term, because a
handful of categories (ambiguous, conceptual) genuinely have more than
one acceptable answer; `expected` lists every term that should count as
a relevant hit for that query, not just one "correct" answer.
"""

import typing


class BenchmarkQuery(typing.NamedTuple):
    """One benchmark query: what was searched, and which term(s) count as a relevant result."""

    query: str
    """The free-text query, exactly as a user might type it."""

    expected: frozenset[str]
    """
    Term name(s) that count as a relevant result for `query`. More than
    one only for genuinely ambiguous/conceptual queries where several
    corpus terms are legitimately close.
    """

    category: str
    """Which benchmark category this belongs to (see `CATEGORIES`)."""


def _q(query: str, expected: str | typing.Iterable[str], category: str) -> BenchmarkQuery:
    """Build a `BenchmarkQuery`, accepting a single expected term or several."""
    terms = frozenset([expected]) if isinstance(expected, str) else frozenset(expected)
    return BenchmarkQuery(query=query, expected=terms, category=category)


CATEGORIES: tuple[str, ...] = (
    "exact",
    "natural_language",
    "paraphrase",
    "partial",
    "misspelling",
    "conceptual",
    "ambiguous",
    "definition_oriented",
)

DATASET: list[BenchmarkQuery] = [
    # --- Exact ---------------------------------------------------------------
    _q("porosity", "Porosity", "exact"),
    _q("gas lift", "Gas Lift", "exact"),
    _q("wireline logging", "Wireline Logging", "exact"),
    _q("permeability", "Permeability", "exact"),
    _q("blowout preventer", "Blowout Preventer", "exact"),
    _q("casing", "Casing", "exact"),
    _q("hydraulic fracturing", "Hydraulic Fracturing", "exact"),
    # --- Natural language ------------------------------------------------------
    _q("what is porosity", "Porosity", "natural_language"),
    _q("define gas lift", "Gas Lift", "natural_language"),
    _q("what does mwd mean", "Measurement While Drilling", "natural_language"),
    _q("meaning of wireline logging", "Wireline Logging", "natural_language"),
    _q("tell me about permeability", "Permeability", "natural_language"),
    _q("what is a blowout preventer", "Blowout Preventer", "natural_language"),
    _q("what's a kick", "Kick", "natural_language"),
    # --- Paraphrase --------------------------------------------------------------
    _q("ability of a rock to store fluids", "Porosity", "paraphrase"),
    _q("method of artificially lifting fluids from a well", "Gas Lift", "paraphrase"),
    _q("measurement while drilling", "Measurement While Drilling", "paraphrase"),
    _q("logging performed using a cable", "Wireline Logging", "paraphrase"),
    _q(
        "ability of a rock to transmit fluids through it",
        "Permeability",
        "paraphrase",
    ),
    _q(
        "unwanted influx of fluid into the wellbore during drilling",
        "Kick",
        "paraphrase",
    ),
    _q("common name for drilling fluid", "Mud", "paraphrase"),
    # --- Partial ---------------------------------------------------------------
    _q("gas lift valve", "Gas Lift Valve", "partial"),
    _q("reservoir press", "Reservoir Pressure", "partial"),
    _q("wireline", "Wireline Logging", "partial"),
    _q("gas lif", "Gas Lift", "partial"),
    _q("electrical submersible", "Electrical Submersible Pump", "partial"),
    _q("bottomhole", ["Bottomhole Assembly", "Bottomhole Pressure"], "partial"),
    # --- Misspelling -------------------------------------------------------------
    _q("porosoty", "Porosity", "misspelling"),
    _q("permeabilty", "Permeability", "misspelling"),
    _q("reservior", "Reservoir", "misspelling"),
    _q("wireline logg", "Wireline Logging", "misspelling"),
    _q("hydralic fracturing", "Hydraulic Fracturing", "misspelling"),
    _q("blowout preventor", "Blowout Preventer", "misspelling"),
    # --- Conceptual (exact words need not occur in the target term) --------------
    _q("percentage of pore space in a rock", "Porosity", "conceptual"),
    _q("pumpjack", "Sucker Rod Pump", "conceptual"),
    _q("steel pipe that lines a wellbore", "Casing", "conceptual"),
    _q("rock rich in organic matter that generates oil", "Source Rock", "conceptual"),
    _q(
        "well stimulation technique that pumps fluid to create fractures",
        "Hydraulic Fracturing",
        "conceptual",
    ),
    _q(
        "downhole electric motor driven pump for lifting oil",
        "Electrical Submersible Pump",
        "conceptual",
    ),
    # --- Ambiguous (several corpus terms are genuinely close) ---------------------
    _q(
        "pressure of fluid in the rock",
        ["Pore Pressure", "Formation Pressure", "Reservoir Pressure"],
        "ambiguous",
    ),
    _q(
        "measuring formation properties while drilling",
        ["Measurement While Drilling", "Logging While Drilling"],
        "ambiguous",
    ),
    _q(
        "artificial lift method",
        ["Artificial Lift", "Gas Lift", "Electrical Submersible Pump", "Sucker Rod Pump"],
        "ambiguous",
    ),
    _q(
        "log used to identify shale",
        ["Gamma Ray Log", "Resistivity Log"],
        "ambiguous",
    ),
    # --- Definition-oriented (describes the concept, doesn't name it) -------------
    _q(
        "geological structure that traps oil and gas underground",
        "Trap",
        "definition_oriented",
    ),
    _q(
        "impermeable rock layer that stops oil escaping upward",
        "Seal",
        "definition_oriented",
    ),
    _q(
        "vessel that splits well fluid into oil gas and water",
        "Separator",
        "definition_oriented",
    ),
    _q(
        "injecting water into a reservoir to push oil toward wells",
        "Waterflooding",
        "definition_oriented",
    ),
    _q(
        "fraction of reservoir pore space occupied by water",
        "Water Saturation",
        "definition_oriented",
    ),
]
"""
The benchmark query set. Deliberately not tiny or hand-picked-easy: it
spans every category `slb_glossary`'s own search spec calls for
(exact/natural-language/paraphrase/partial/misspelling/conceptual/
ambiguous/definition-oriented), against a corpus with several
genuinely-related term clusters, so a change that only helps the easy
cases shows up as a regression here, not an improvement.
"""
