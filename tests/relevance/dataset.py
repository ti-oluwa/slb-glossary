"""Benchmark query set for the relevance harness, run against `tests/relevance/corpus.py`."""

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


def build_query(query: str, expected: str | typing.Iterable[str], category: str) -> BenchmarkQuery:
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
    build_query("porosity", "Porosity", "exact"),
    build_query("gas lift", "Gas Lift", "exact"),
    build_query("wireline logging", "Wireline Logging", "exact"),
    build_query("permeability", "Permeability", "exact"),
    build_query("blowout preventer", "Blowout Preventer", "exact"),
    build_query("casing", "Casing", "exact"),
    build_query("hydraulic fracturing", "Hydraulic Fracturing", "exact"),
    # --- Natural language ------------------------------------------------------
    build_query("what is porosity", "Porosity", "natural_language"),
    build_query("define gas lift", "Gas Lift", "natural_language"),
    build_query("what does mwd mean", "Measurement While Drilling", "natural_language"),
    build_query("meaning of wireline logging", "Wireline Logging", "natural_language"),
    build_query("tell me about permeability", "Permeability", "natural_language"),
    build_query("what is a blowout preventer", "Blowout Preventer", "natural_language"),
    build_query("what's a kick", "Kick", "natural_language"),
    # --- Paraphrase --------------------------------------------------------------
    build_query("ability of a rock to store fluids", "Porosity", "paraphrase"),
    build_query("method of artificially lifting fluids from a well", "Gas Lift", "paraphrase"),
    build_query("measurement while drilling", "Measurement While Drilling", "paraphrase"),
    build_query("logging performed using a cable", "Wireline Logging", "paraphrase"),
    build_query(
        "ability of a rock to transmit fluids through it",
        "Permeability",
        "paraphrase",
    ),
    build_query(
        "unwanted influx of fluid into the wellbore during drilling",
        "Kick",
        "paraphrase",
    ),
    build_query("common name for drilling fluid", "Mud", "paraphrase"),
    # --- Partial ---------------------------------------------------------------
    build_query("gas lift valve", "Gas Lift Valve", "partial"),
    build_query("reservoir press", "Reservoir Pressure", "partial"),
    build_query("wireline", "Wireline Logging", "partial"),
    build_query("gas lif", "Gas Lift", "partial"),
    build_query("electrical submersible", "Electrical Submersible Pump", "partial"),
    build_query("bottomhole", ["Bottomhole Assembly", "Bottomhole Pressure"], "partial"),
    # --- Misspelling -------------------------------------------------------------
    build_query("porosoty", "Porosity", "misspelling"),
    build_query("permeabilty", "Permeability", "misspelling"),
    build_query("reservior", "Reservoir", "misspelling"),
    build_query("wireline logg", "Wireline Logging", "misspelling"),
    build_query("hydralic fracturing", "Hydraulic Fracturing", "misspelling"),
    build_query("blowout preventor", "Blowout Preventer", "misspelling"),
    # --- Conceptual (exact words need not occur in the target term) --------------
    build_query("percentage of pore space in a rock", "Porosity", "conceptual"),
    build_query("pumpjack", "Sucker Rod Pump", "conceptual"),
    build_query("steel pipe that lines a wellbore", "Casing", "conceptual"),
    build_query("rock rich in organic matter that generates oil", "Source Rock", "conceptual"),
    build_query(
        "well stimulation technique that pumps fluid to create fractures",
        "Hydraulic Fracturing",
        "conceptual",
    ),
    build_query(
        "downhole electric motor driven pump for lifting oil",
        "Electrical Submersible Pump",
        "conceptual",
    ),
    # --- Ambiguous (several corpus terms are genuinely close) ---------------------
    build_query(
        "pressure of fluid in the rock",
        ["Pore Pressure", "Formation Pressure", "Reservoir Pressure"],
        "ambiguous",
    ),
    build_query(
        "measuring formation properties while drilling",
        ["Measurement While Drilling", "Logging While Drilling"],
        "ambiguous",
    ),
    build_query(
        "artificial lift method",
        ["Artificial Lift", "Gas Lift", "Electrical Submersible Pump", "Sucker Rod Pump"],
        "ambiguous",
    ),
    build_query(
        "log used to identify shale",
        ["Gamma Ray Log", "Resistivity Log"],
        "ambiguous",
    ),
    # --- Definition-oriented (describes the concept, doesn't name it) -------------
    build_query(
        "geological structure that traps oil and gas underground",
        "Trap",
        "definition_oriented",
    ),
    build_query(
        "impermeable rock layer that stops oil escaping upward",
        "Seal",
        "definition_oriented",
    ),
    build_query(
        "vessel that splits well fluid into oil gas and water",
        "Separator",
        "definition_oriented",
    ),
    build_query(
        "injecting water into a reservoir to push oil toward wells",
        "Waterflooding",
        "definition_oriented",
    ),
    build_query(
        "fraction of reservoir pore space occupied by water",
        "Water Saturation",
        "definition_oriented",
    ),
]
"""Every category in `slb_glossary`'s search spec, against a corpus with several related term clusters."""
