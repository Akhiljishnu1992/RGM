"""Plain data structures shared by the engine and the UI.

Nothing in ``pricing_engine`` imports Streamlit, so the calculation logic can be
unit-tested, run from a notebook, or reused in a batch job without the UI.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

# --------------------------------------------------------------------------------------
# Hierarchy
# --------------------------------------------------------------------------------------
# Four levels, top to bottom. A PPG is identified by its ``ppg_id``; the readable name
# lives in ``ppg_name``.
HIERARCHY_COLUMNS: tuple[str, ...] = ("category", "sub_category", "brand", "ppg_id")
LEVEL_LABELS: tuple[str, ...] = ("Category", "Sub-category", "Brand", "PPG")
LEVEL_PLURALS: tuple[str, ...] = ("Categories", "Sub-categories", "Brands", "PPGs")
ROOT_LABEL = "All products"
PATH_SEPARATOR = " › "


@dataclass(frozen=True)
class Node:
    """A node in the hierarchy, identified by its path from the root.

    ``Node(())``                                              -> root ("All products")
    ``Node(("Personal Care",))``                              -> category
    ``Node(("Personal Care", "Bar Soap"))``                   -> sub-category
    ``Node(("Personal Care", "Bar Soap", "Lumina"))``         -> brand
    ``Node(("Personal Care", "Bar Soap", "Lumina", "LUM-BAR-075"))`` -> PPG (leaf)

    Paths (not bare names) are used so the same name can appear under different
    parents, e.g. a brand that sells in two sub-categories.
    """

    path: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        path = tuple(str(part) for part in self.path)
        if len(path) > len(HIERARCHY_COLUMNS):
            raise ValueError(
                f"A node path has at most {len(HIERARCHY_COLUMNS)} levels; got {len(path)}: {path}"
            )
        object.__setattr__(self, "path", path)

    @property
    def depth(self) -> int:
        return len(self.path)

    @property
    def is_root(self) -> bool:
        return not self.path

    @property
    def is_leaf(self) -> bool:
        return len(self.path) == len(HIERARCHY_COLUMNS)

    @property
    def level_label(self) -> str:
        return "Total" if self.is_root else LEVEL_LABELS[self.depth - 1]

    @property
    def name(self) -> str:
        return ROOT_LABEL if self.is_root else self.path[-1]

    @property
    def label(self) -> str:
        return ROOT_LABEL if self.is_root else PATH_SEPARATOR.join(self.path)

    @property
    def key(self) -> str:
        """Stable string form, handy for UI widget keys."""
        return "|".join(self.path) if self.path else "__root__"

    def contains(self, path: tuple[str, ...]) -> bool:
        """True if ``path`` (usually a PPG path) is this node or sits beneath it."""
        return tuple(path[: self.depth]) == self.path


# --------------------------------------------------------------------------------------
# Issues raised by validation and simulation
# --------------------------------------------------------------------------------------
class Severity(str, Enum):
    ERROR = "error"  # the PPG (or scenario) is excluded until fixed
    WARNING = "warning"  # calculated, but the value deserves a second look
    INFO = "info"  # a documented assumption was applied; nothing is wrong


@dataclass(frozen=True)
class Issue:
    severity: Severity
    message: str
    ppg_id: Optional[str] = None
    field: Optional[str] = None
    scenario: Optional[str] = None


# --------------------------------------------------------------------------------------
# Scenario inputs
# --------------------------------------------------------------------------------------
class ChangeType(str, Enum):
    PERCENT = "percent"  # value is a decimal fraction: 0.05 means +5 %
    ABSOLUTE = "absolute"  # value is currency per unit, added to the base LIST price


@dataclass(frozen=True)
class PriceChange:
    """A proposed list-price change at any node; it applies to every PPG beneath it.

    When changes exist at several levels, the most specific one wins for each PPG
    (PPG > Brand > Sub-category > Category > All products).
    """

    node: Node
    change_type: ChangeType
    value: float

    def describe(self, currency: str = "") -> str:
        if self.change_type is ChangeType.PERCENT:
            return f"{self.value:+.1%}"
        unit = f" {currency}" if currency else ""
        return f"{self.value:+,.2f}{unit}/unit"


@dataclass(frozen=True)
class ElasticityOverride:
    """Overrides the own-price elasticity for every PPG under ``node``.

    The most specific override wins; any override beats the value in the data.
    """

    node: Node
    value: float


@dataclass(frozen=True)
class Scenario:
    name: str
    price_changes: tuple[PriceChange, ...] = ()
    # Scales every PPG's elasticity in this scenario only (1.25 = 25 % more price
    # sensitive). 1.0 leaves elasticities as they are. Used for stress tests.
    elasticity_multiplier: float = 1.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "price_changes", tuple(self.price_changes))


# --------------------------------------------------------------------------------------
# Turnover (TO) definition — configurable because conventions differ between companies
# --------------------------------------------------------------------------------------
class TOBasis(str, Enum):
    GSV = "gsv"
    INVOICED = "invoiced"
    NSV = "nsv"
    CONSUMER_OFFTAKE = "consumer_offtake"


@dataclass(frozen=True)
class TODefinition:
    label: str
    formula: str
    # Which per-unit price TO multiplies units by. The engine looks up the columns
    # ``base_<price_field>`` and ``new_<price_field>``.
    price_field: str
    description: str


TO_DEFINITIONS: dict[TOBasis, TODefinition] = {
    TOBasis.GSV: TODefinition(
        label="Gross Sales Value (GSV)",
        formula="TO = units × list price",
        price_field="list_price",
        description="Sales at list price, before any trade investment.",
    ),
    TOBasis.INVOICED: TODefinition(
        label="Invoiced turnover (GSV − on-invoice discounts)",
        formula="TO = units × list price × (1 − on-invoice discount rate)",
        price_field="invoiced_price",
        description="What appears on the customer invoice; off-invoice trade spend is still to come.",
    ),
    TOBasis.NSV: TODefinition(
        label="Net sales (TO = NSV)",
        formula="TO = units × net price",
        price_field="net_price",
        description="Turnover reported net of all trade investment, i.e. identical to NSV.",
    ),
    TOBasis.CONSUMER_OFFTAKE: TODefinition(
        label="Consumer offtake at RSP",
        formula="TO = units × retail selling price (RSP)",
        price_field="rsp",
        description="Consumer sell-out value at shelf price. Requires base_rsp for every PPG.",
    ),
}
# To add your own convention: add a TOBasis member, a TODefinition here, and (if it
# needs a new per-unit price) derive base_/new_ columns for it in simulation.py.


# --------------------------------------------------------------------------------------
# Global settings
# --------------------------------------------------------------------------------------
# PLACEHOLDER: "Turnover" means different things in different companies. Invoiced
# turnover (GSV less on-invoice discounts) is used as the default because it sits
# between GSV and NSV and is common in FMCG reporting. Change it here or in the app.
DEFAULT_TO_BASIS = TOBasis.INVOICED

# PLACEHOLDER: a generic own-price elasticity for an FMCG PPG. Meta-analyses of
# brand-level price elasticity report averages of roughly -1.8 to -2.6, and
# category-level elasticities are usually smaller in magnitude, so -1.5 is a
# middle-of-the-road stand-in. It is only applied when the user explicitly opts in;
# otherwise a missing elasticity is flagged and the PPG is excluded.
DEFAULT_ELASTICITY = -1.5


@dataclass(frozen=True)
class SimulationConfig:
    to_basis: TOBasis = DEFAULT_TO_BASIS
    default_elasticity: float = DEFAULT_ELASTICITY
    apply_default_elasticity: bool = False  # opt-in: never default silently
    # Warn when a PPG's price moves by more than this fraction: constant-elasticity
    # curves are local approximations and get unreliable far from the base price.
    large_change_threshold: float = 0.30
    # Warn when an elasticity is more negative than this.
    extreme_elasticity_threshold: float = -5.0
