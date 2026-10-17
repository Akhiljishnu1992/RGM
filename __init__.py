"""RGM pricing simulation engine — independent of any UI.

Typical use::

    from pricing_engine import *

    raw = load_dataset("data/sample_hierarchy.csv")
    config = SimulationConfig()
    clean, data_issues = validate_dataset(raw, config)
    base, elasticity_issues = prepare_base(clean, elasticity_overrides=[], config=config)
    scenario = Scenario("PC +5%", [PriceChange(Node(("Personal Care",)), ChangeType.PERCENT, 0.05)])
    result = simulate(base, scenario, config)
    aggregate(result.ppg, Node(("Personal Care",)))
"""
from .data_loader import load_dataset
from .export import export_node_summary, export_ppg_results, to_csv_bytes
from .hierarchy import (
    build_tree,
    children,
    display_name,
    has_full_path,
    iter_nodes,
    node_mask,
    ppg_names,
    ppg_paths,
    resolve_most_specific,
)
from .models import (
    DEFAULT_ELASTICITY,
    DEFAULT_TO_BASIS,
    HIERARCHY_COLUMNS,
    LEVEL_LABELS,
    LEVEL_PLURALS,
    ROOT_LABEL,
    TO_DEFINITIONS,
    ChangeType,
    ElasticityOverride,
    Issue,
    Node,
    PriceChange,
    Scenario,
    Severity,
    SimulationConfig,
    TOBasis,
    TODefinition,
)
from .schema import NUMERIC_COLUMNS, OPTIONAL_COLUMNS, REQUIRED_COLUMNS, normalize_columns
from .simulation import (
    METRICS,
    SUM_COLUMNS,
    ScenarioResult,
    add_derived,
    aggregate,
    bridge,
    comparison_long,
    node_summary,
    prepare_base,
    simulate,
)
from .validation import SchemaError, validate_dataset

__all__ = [
    "load_dataset",
    "export_node_summary",
    "export_ppg_results",
    "to_csv_bytes",
    "build_tree",
    "children",
    "display_name",
    "has_full_path",
    "iter_nodes",
    "node_mask",
    "ppg_names",
    "ppg_paths",
    "resolve_most_specific",
    "DEFAULT_ELASTICITY",
    "DEFAULT_TO_BASIS",
    "HIERARCHY_COLUMNS",
    "LEVEL_LABELS",
    "LEVEL_PLURALS",
    "ROOT_LABEL",
    "TO_DEFINITIONS",
    "ChangeType",
    "ElasticityOverride",
    "Issue",
    "Node",
    "PriceChange",
    "Scenario",
    "Severity",
    "SimulationConfig",
    "TOBasis",
    "TODefinition",
    "NUMERIC_COLUMNS",
    "OPTIONAL_COLUMNS",
    "REQUIRED_COLUMNS",
    "normalize_columns",
    "METRICS",
    "SUM_COLUMNS",
    "ScenarioResult",
    "add_derived",
    "aggregate",
    "bridge",
    "comparison_long",
    "node_summary",
    "prepare_base",
    "simulate",
    "SchemaError",
    "validate_dataset",
]
