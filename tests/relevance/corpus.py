"""Representative oil-and-gas glossary corpus for the relevance benchmark. Includes related/ambiguous term clusters."""

import typing

CorpusEntry = dict[str, typing.Any]

CORPUS: list[CorpusEntry] = [
    # --- Porosity / permeability cluster -----------------------------------
    {
        "term": "Porosity",
        "definition": (
            "The percentage of pore volume, or void space, within a rock that "
            "can contain fluids. Porosity is a measure of the capacity of a "
            "rock to store fluids such as oil, gas, or water."
        ),
        "topic": "Reservoir Engineering",
    },
    {
        "term": "Effective Porosity",
        "definition": (
            "The percentage of interconnected pore volume in a rock that "
            "contributes to fluid flow, excluding isolated pores that do not "
            "connect to the rest of the pore network."
        ),
        "topic": "Reservoir Engineering",
    },
    {
        "term": "Permeability",
        "definition": (
            "A measure of the ability of a rock to transmit fluids through its "
            "connected pore spaces, typically expressed in millidarcies."
        ),
        "topic": "Reservoir Engineering",
    },
    {
        "term": "Porous Medium",
        "definition": (
            "A solid material, such as a reservoir rock, containing pores or "
            "voids through which fluids can pass."
        ),
        "topic": "Reservoir Engineering",
    },
    # --- Artificial lift cluster --------------------------------------------
    {
        "term": "Gas Lift",
        "definition": (
            "A method of artificially lifting fluids from a well by injecting "
            "gas into the production tubing to reduce the density of the fluid "
            "column and help it rise to the surface."
        ),
        "topic": "Production Engineering",
    },
    {
        "term": "Gas Lift Valve",
        "definition": (
            "A device installed in the production tubing string of a gas-lift "
            "well that controls the injection of lift gas at a specific depth."
        ),
        "topic": "Production Engineering",
    },
    {
        "term": "Artificial Lift",
        "definition": (
            "Any method used to increase the flow of fluids from a well to the "
            "surface, such as gas lift, rod pumping, or electrical submersible "
            "pumping, generally used when reservoir pressure alone is "
            "insufficient to produce fluids."
        ),
        "topic": "Production Engineering",
    },
    {
        "term": "Electrical Submersible Pump",
        "definition": (
            "An artificial lift system that uses a downhole electric motor to "
            "drive a multistage centrifugal pump, lifting fluid from the well "
            "to the surface."
        ),
        "topic": "Production Engineering",
    },
    {
        "term": "Sucker Rod Pump",
        "definition": (
            "A surface-driven artificial lift system that uses a string of "
            "rods connected to a downhole pump to lift fluid to the surface, "
            "commonly seen as a beam pumping unit or 'pumpjack'."
        ),
        "topic": "Production Engineering",
    },
    # --- Drilling / MWD-LWD cluster ------------------------------------------
    {
        "term": "Measurement While Drilling",
        "definition": (
            "The technique of measuring downhole conditions, such as wellbore "
            "trajectory, pressure, and temperature, and transmitting them to "
            "the surface while drilling is in progress, commonly abbreviated MWD."
        ),
        "grammatical_label": "Noun",
        "topic": "Drilling",
    },
    {
        "term": "Logging While Drilling",
        "definition": (
            "The technique of acquiring formation evaluation data, such as "
            "resistivity, density, and porosity logs, while the well is being "
            "drilled, commonly abbreviated LWD."
        ),
        "topic": "Drilling",
    },
    {
        "term": "Wireline Logging",
        "definition": (
            "The process of lowering measurement tools into a wellbore on an "
            "electrical cable, or wireline, after drilling has stopped, to "
            "acquire formation evaluation data."
        ),
        "topic": "Drilling",
    },
    {
        "term": "Drilling Fluid",
        "definition": (
            "A fluid, commonly called mud, circulated through a wellbore "
            "during drilling to cool and lubricate the drill bit, carry "
            "cuttings to the surface, and control formation pressure."
        ),
        "topic": "Drilling",
    },
    {
        "term": "Mud",
        "definition": "A common name for drilling fluid.",
        "topic": "Drilling",
    },
    {
        "term": "Drill Bit",
        "definition": (
            "The cutting tool attached to the bottom of the drill string that "
            "breaks up rock as it is rotated or percussed against the "
            "formation being drilled."
        ),
        "topic": "Drilling",
    },
    {
        "term": "Bottomhole Assembly",
        "definition": (
            "The lower portion of the drill string, including the drill bit, "
            "drill collars, and other downhole tools, that provides weight and "
            "directional control for drilling."
        ),
        "topic": "Drilling",
    },
    # --- Pressure cluster -----------------------------------------------------
    {
        "term": "Reservoir Pressure",
        "definition": (
            "The pressure of fluids within a reservoir's pore spaces, "
            "measured at a given point in time and depth, which drives fluid "
            "flow toward a wellbore."
        ),
        "topic": "Reservoir Engineering",
    },
    {
        "term": "Pore Pressure",
        "definition": (
            "The pressure exerted by fluids contained within the pore spaces of a rock formation."
        ),
        "topic": "Drilling",
    },
    {
        "term": "Formation Pressure",
        "definition": (
            "The pressure of fluids within the pores of a rock formation at a "
            "given depth, before any drilling-induced disturbance."
        ),
        "topic": "Drilling",
    },
    {
        "term": "Bottomhole Pressure",
        "definition": (
            "The pressure measured or calculated at the bottom of a wellbore, "
            "used to monitor well control and reservoir behavior."
        ),
        "topic": "Reservoir Engineering",
    },
    # --- Well/completion basics ------------------------------------------------
    {
        "term": "Wellbore",
        "definition": "The drilled hole that makes up a well, from surface to total depth.",
        "topic": "Drilling",
    },
    {
        "term": "Casing",
        "definition": (
            "Steel pipe installed in a wellbore to prevent collapse, isolate "
            "fluid zones, and provide a conduit for further drilling or "
            "production."
        ),
        "topic": "Well Construction",
    },
    {
        "term": "Cementing",
        "definition": (
            "The process of pumping cement slurry into the annulus between "
            "casing and the wellbore wall to provide zonal isolation and "
            "structural support."
        ),
        "topic": "Well Construction",
    },
    {
        "term": "Christmas Tree",
        "definition": (
            "An assembly of valves, spools, and fittings installed at the "
            "wellhead to control the flow of fluids into or out of a "
            "completed well."
        ),
        "topic": "Well Construction",
    },
    {
        "term": "Wellhead",
        "definition": (
            "The surface (or subsea) equipment at the top of a well that "
            "provides structural and pressure-containing support for casing "
            "strings and the well's flow-control equipment."
        ),
        "topic": "Well Construction",
    },
    {
        "term": "Hydraulic Fracturing",
        "definition": (
            "A well-stimulation technique in which fluid is pumped into a "
            "formation at pressure high enough to create fractures, "
            "increasing permeability and improving fluid flow to the wellbore."
        ),
        "topic": "Production Engineering",
    },
    {
        "term": "Perforating",
        "definition": (
            "The process of creating holes through casing and cement into the "
            "surrounding formation to establish communication between the "
            "wellbore and the reservoir."
        ),
        "topic": "Well Construction",
    },
    # --- Reservoir/geology basics ------------------------------------------------
    {
        "term": "Reservoir",
        "definition": (
            "A subsurface body of rock with sufficient porosity and "
            "permeability to store and transmit fluids, such as oil, gas, or "
            "water."
        ),
        "topic": "Reservoir Engineering",
    },
    {
        "term": "Source Rock",
        "definition": (
            "A rock rich in organic matter that, if heated sufficiently, will generate oil or gas."
        ),
        "topic": "Geology",
    },
    {
        "term": "Trap",
        "definition": (
            "A geological structure that allows significant accumulation of "
            "oil or gas in the subsurface, formed by a combination of a "
            "reservoir rock, a seal, and a geometric configuration that "
            "prevents fluids from escaping."
        ),
        "topic": "Geology",
    },
    {
        "term": "Seal",
        "definition": (
            "A relatively impermeable rock that forms a barrier or cap above "
            "or around a reservoir, preventing the escape of fluids."
        ),
        "topic": "Geology",
    },
    {
        "term": "Anticline",
        "definition": (
            "A fold in rock strata that is convex upward, often forming a "
            "structural trap for oil and gas accumulation."
        ),
        "topic": "Geology",
    },
    {
        "term": "Unconventional Reservoir",
        "definition": (
            "A reservoir with very low permeability, such as shale or tight "
            "gas sandstone, that requires stimulation, like hydraulic "
            "fracturing, to produce economically."
        ),
        "topic": "Reservoir Engineering",
    },
    # --- Surface/production facilities -------------------------------------------
    {
        "term": "Separator",
        "definition": (
            "A pressure vessel used to separate well fluids into their "
            "constituent gas, oil, and water phases."
        ),
        "topic": "Facilities Engineering",
    },
    {
        "term": "Choke",
        "definition": (
            "A device that restricts the flow of fluid through a pipe, used to "
            "control production rate and wellhead pressure."
        ),
        "topic": "Production Engineering",
    },
    {
        "term": "Flowline",
        "definition": "Surface piping that carries produced fluids from a wellhead to a facility.",
        "topic": "Facilities Engineering",
    },
    {
        "term": "Blowout Preventer",
        "definition": (
            "A large valve assembly installed at the wellhead to seal, "
            "control, and monitor the well to prevent an uncontrolled release "
            "of formation fluids, commonly abbreviated BOP."
        ),
        "topic": "Well Control",
    },
    {
        "term": "Kick",
        "definition": (
            "An unwanted influx of formation fluids into the wellbore, caused "
            "when formation pressure exceeds the pressure exerted by the "
            "drilling fluid column."
        ),
        "topic": "Well Control",
    },
    {
        "term": "Blowout",
        "definition": (
            "An uncontrolled flow of formation fluids from a well, occurring "
            "when well-control measures fail to contain a kick."
        ),
        "topic": "Well Control",
    },
    # --- Formation evaluation misc ----------------------------------------------
    {
        "term": "Resistivity Log",
        "definition": (
            "A wireline or logging-while-drilling measurement of a "
            "formation's resistance to electrical current, used to "
            "distinguish hydrocarbon-bearing zones from water-bearing zones."
        ),
        "topic": "Formation Evaluation",
    },
    {
        "term": "Gamma Ray Log",
        "definition": (
            "A log that measures the natural radioactivity of formations, "
            "commonly used to distinguish shale from non-shale (reservoir) "
            "rock."
        ),
        "topic": "Formation Evaluation",
    },
    {
        "term": "Water Saturation",
        "definition": (
            "The fraction of pore volume in a reservoir rock that is occupied "
            "by water, as opposed to oil or gas."
        ),
        "topic": "Reservoir Engineering",
    },
    {
        "term": "Net Pay",
        "definition": (
            "The thickness of a reservoir interval capable of producing "
            "hydrocarbons economically, excluding non-productive layers."
        ),
        "topic": "Reservoir Engineering",
    },
    {
        "term": "Enhanced Oil Recovery",
        "definition": (
            "Techniques, such as water flooding, gas injection, or thermal "
            "methods, used to increase the amount of oil that can be "
            "extracted from a reservoir beyond primary and secondary recovery."
        ),
        "topic": "Reservoir Engineering",
    },
    {
        "term": "Waterflooding",
        "definition": (
            "A secondary recovery method in which water is injected into a "
            "reservoir to displace oil toward producing wells and maintain "
            "reservoir pressure."
        ),
        "topic": "Reservoir Engineering",
    },
]
"""
`(term, definition, topic[, grammatical_label])` entries. Fed to
`slb_glossary.local.upsert_results` via `tests/relevance/harness.py`'s
`seed_corpus`, one synthetic `https://` URL per entry.
"""
