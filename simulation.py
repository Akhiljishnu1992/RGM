"""The calculation engine.

Pipeline::

    clean_df, issues = validate_dataset(raw_df, config)          # validation.py
    base_df, more    = prepare_base(clean_df, overrides, config)  # elasticity + base P&L
    result           = simulate(base_df, scenario, config)        # one per scenario
    aggregate(result.ppg, node)                                   # roll up to any node

All maths is vectorised with pandas/numpy. Formulas are documented in
docs/FORMULAS.md and verified against a worked example in tests/test_worked_example.py.

Per PPG (prices per unit, held-constant trade rates a = on-invoice, b = off-invoice):

    invoiced price   = list price × (1 − a)
    net price  P     = list price × (1 − a − b)
    price ratio r    = new list price / base list price
    new units        = base units × r ** elasticity         (constant elasticity)
    NSV              = units × net price
    TO               = units × (price chosen by the TO definition)
    GP               = NSV − units × unit COGS
    GM %             = GP / NSV
    NSV price effect = (P1 − P0) × U1      NSV volume effect = (U1 − U0) × P0
    GP  price effect = (P1 − P0) × U1      GP  volume effect = (U1 − U0) × (P0 − COGS)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

import numpy as np
import pandas as pd

from .hierarchy import has_full_path, iter_nodes, node_mask, ppg_paths, resolve_most_specific
from .models import (
    HIERARCHY_COLUMNS,
    ChangeType,
    ElasticityOverride,
    Issue,
    Node,
    PriceChange,
    Scenario,
    Severity,
    SimulationConfig,
    TO_DEFINITIONS,
)

# Additive columns: safe to sum across PPGs. Ratios (GM %, average price change)
# are always recomputed from these sums, never averaged.
SUM_COLUMNS: tuple[str, ...] = (
    "base_units",
    "new_units",
    "base_gsv",
    "new_gsv",
    "base_nsv",
    "new_nsv",
    "base_to",
    "new_to",
    "base_gp",
    "new_gp",
    "new_nsv_at_base_units",  # new net price × base units: numerator of the price index
    "nsv_price_effect",
    "nsv_volume_effect",
    "gp_price_effect",
    "gp_volume_effect",
)

# Columns the scenario fills in; blanked (NaN) for excluded PPGs and invalid scenarios.
NEW_COLUMNS: tuple[str, ...] = (
    "new_list_price",
    "new_invoiced_price",
    "new_net_price",
    "new_rsp",
    "new_units",
    "new_gsv",
    "new_nsv",
    "new_to",
    "new_gp",
    "new_gm",
    "new_nsv_at_base_units",
    "nsv_price_effect",
    "nsv_volume_effect",
    "gp_price_effect",
    "gp_volume_effect",
)

METRICS: tuple[tuple[str, str], ...] = (
    ("units", "Volume (units)"),
    ("nsv", "Net Sales Value (NSV)"),
    ("to", "Turnover (TO)"),
    ("gp", "Gross Profit (GP)"),
    ("gm", "Gross Margin (GM %)"),
)


@dataclass
class ScenarioResult:
    scenario: Scenario
    ppg: pd.DataFrame  # one row per PPG, base and scenario columns side by side
    issues: list[Issue] = field(default_factory=list)
    valid: bool = True  # False -> scenario-level error; all new_* values are NaN

    @property
    def name(self) -> str:
        return self.scenario.name


def _safe_div(num, den) -> np.ndarray:
    num = np.asarray(num, dtype=float)
    den = np.asarray(den, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(den != 0, num / den, np.nan)


# --------------------------------------------------------------------------------------
# Base
# --------------------------------------------------------------------------------------
def prepare_base(
    clean_df: pd.DataFrame,
    elasticity_overrides: Iterable[ElasticityOverride] = (),
    config: Optional[SimulationConfig] = None,
) -> tuple[pd.DataFrame, list[Issue]]:
    """Resolve the elasticity for every PPG and compute the base-period P&L.

    Elasticity precedence per PPG: most specific override > value in the data >
    default (only if ``config.apply_default_elasticity``) > missing (ERROR).
    Returns ``(base_df, issues)``; issues cover elasticity only (data issues come
    from ``validate_dataset``).
    """
    config = config or SimulationConfig()
    df = clean_df.reset_index(drop=True).copy()
    # Fresh list objects so flags added here never leak back into clean_df.
    df["row_errors"] = pd.Series([list(v) for v in df["row_errors"]], index=df.index, dtype=object)
    df["row_warnings"] = pd.Series([list(v) for v in df["row_warnings"]], index=df.index, dtype=object)
    issues: list[Issue] = []

    def flag(i: int, severity: Severity, message: str) -> None:
        target = df.at[i, "row_errors"] if severity is Severity.ERROR else df.at[i, "row_warnings"]
        target.append(message)
        issues.append(Issue(severity, message, ppg_id=df.at[i, "ppg_id"] or f"row {i + 1}", field="elasticity"))

    overrides: dict[Node, float] = {}
    for override in elasticity_overrides:
        value = float(override.value)
        if not math.isfinite(value):
            issues.append(
                Issue(Severity.ERROR, f"Override at {override.node.label} is not a number; ignored.", field="elasticity")
            )
            continue
        overrides[override.node] = value

    used: list[float] = []
    sources: list[str] = []
    for i, (node, value) in enumerate(resolve_most_specific(ppg_paths(df), overrides)):
        raw = df.at[i, "elasticity"]
        if node is not None:
            elasticity, source = float(value), f"Override @ {node.level_label}: {node.name}"
        elif not pd.isna(raw):
            elasticity = float(raw)
            source = df.at[i, "elasticity_source"] or "Input data (source not given)"
        elif config.apply_default_elasticity:
            elasticity = float(config.default_elasticity)
            source = f"Default placeholder ({elasticity:+.2f})"
            flag(i, Severity.WARNING, f"Elasticity missing; default {elasticity:+.2f} applied (placeholder).")
        else:
            elasticity, source = math.nan, "MISSING"
            flag(i, Severity.ERROR, "Elasticity missing. Add it to the data, set an override, or enable the default.")

        if not math.isnan(elasticity):
            if elasticity > 0:
                flag(i, Severity.WARNING, f"Positive elasticity ({elasticity:+.2f}): volume rises when price rises. Check the source.")
            elif elasticity == 0:
                flag(i, Severity.WARNING, "Elasticity is 0: volume will not respond to price.")
            elif elasticity < config.extreme_elasticity_threshold:
                flag(i, Severity.WARNING, f"Elasticity {elasticity:+.2f} is unusually large in magnitude; check the estimate.")
        used.append(elasticity)
        sources.append(source)

    df["elasticity_used"] = pd.Series(used, index=df.index, dtype=float)
    df["elasticity_source_used"] = pd.Series(sources, index=df.index, dtype=object)

    list_price = df["base_list_price"]
    on_inv = df["on_invoice_disc_rate"]
    off_inv = df["off_invoice_trade_rate"]
    units = df["base_units"]
    df["base_invoiced_price"] = list_price * (1 - on_inv)
    df["base_net_price"] = list_price * (1 - on_inv - off_inv)
    df["base_gsv"] = units * list_price
    df["base_nsv"] = units * df["base_net_price"]
    df["base_to"] = units * df[f"base_{TO_DEFINITIONS[config.to_basis].price_field}"]
    df["base_gp"] = df["base_nsv"] - units * df["unit_cogs"]
    df["base_gm"] = _safe_div(df["base_gp"], df["base_nsv"])
    df["is_valid"] = df["row_errors"].map(len).eq(0)
    return df, issues


# --------------------------------------------------------------------------------------
# Scenario
# --------------------------------------------------------------------------------------
def simulate(base_df: pd.DataFrame, scenario: Scenario, config: Optional[SimulationConfig] = None) -> ScenarioResult:
    """Apply one scenario's price changes to every PPG and compute the new P&L."""
    config = config or SimulationConfig()
    df = base_df.copy()
    issues: list[Issue] = []
    ok = True

    def scenario_issue(severity: Severity, message: str) -> None:
        issues.append(Issue(severity, message, scenario=scenario.name))

    multiplier = scenario.elasticity_multiplier
    if not (isinstance(multiplier, (int, float)) and math.isfinite(multiplier) and multiplier > 0):
        scenario_issue(Severity.ERROR, f"Elasticity multiplier must be a positive number (got {multiplier}).")
        ok = False
        multiplier = math.nan

    changes: dict[Node, PriceChange] = {}
    for change in scenario.price_changes:
        if not math.isfinite(change.value):
            scenario_issue(Severity.ERROR, f"Price change at {change.node.label} is not a number.")
            ok = False
            continue
        if change.change_type is ChangeType.PERCENT and change.value <= -1:
            scenario_issue(
                Severity.ERROR, f"A {change.describe()} change at {change.node.label} takes the price to zero or below."
            )
            ok = False
        if change.node in changes:
            scenario_issue(Severity.WARNING, f"Two price changes at {change.node.label}; the last one is used.")
        changes[change.node] = change

    paths = ppg_paths(df)
    for node in changes:
        if not any(node.contains(path) for path in paths):
            scenario_issue(Severity.WARNING, f"Price change at {node.label} matches no PPG in the current data.")

    base_price = df["base_list_price"].to_numpy(dtype=float)
    new_price = base_price.copy()
    sources: list[str] = []
    for i, (node, change) in enumerate(resolve_most_specific(paths, changes)):
        if change is None:
            sources.append("No change")
            continue
        if change.change_type is ChangeType.PERCENT:
            new_price[i] = base_price[i] * (1 + change.value)
        else:
            new_price[i] = base_price[i] + change.value
        sources.append(f"{change.describe()} @ {node.level_label}: {node.name}")

    valid = df["is_valid"].to_numpy(dtype=bool)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = new_price / base_price
    df["price_change_source"] = pd.Series(sources, index=df.index, dtype=object)
    df["new_list_price"] = new_price
    df["price_change_pct"] = ratio - 1

    non_positive = valid & ~(new_price > 0)
    if non_positive.any():
        ids = df.loc[non_positive, "ppg_id"].tolist()
        more = "…" if len(ids) > 5 else ""
        scenario_issue(
            Severity.ERROR,
            f"New list price would be zero or negative for {len(ids)} PPG(s): {', '.join(ids[:5])}{more}. "
            "Reduce the price cut.",
        )
        ok = False
    large = valid & (new_price > 0) & (np.abs(ratio - 1) > config.large_change_threshold)
    if large.any():
        scenario_issue(
            Severity.WARNING,
            f"{int(large.sum())} PPG(s) move more than ±{config.large_change_threshold:.0%} in price; "
            "constant-elasticity estimates are least reliable far from the base price.",
        )

    elasticity = df["elasticity_used"].to_numpy(dtype=float) * multiplier
    on_inv = df["on_invoice_disc_rate"].to_numpy(dtype=float)
    off_inv = df["off_invoice_trade_rate"].to_numpy(dtype=float)
    base_units = df["base_units"].to_numpy(dtype=float)
    cogs = df["unit_cogs"].to_numpy(dtype=float)
    base_net = df["base_net_price"].to_numpy(dtype=float)

    with np.errstate(all="ignore"):
        new_units = base_units * np.power(ratio, elasticity)
        new_net = new_price * (1 - on_inv - off_inv)
        df["elasticity_effective"] = elasticity
        df["new_invoiced_price"] = new_price * (1 - on_inv)
        df["new_net_price"] = new_net
        df["new_rsp"] = df["base_rsp"].to_numpy(dtype=float) * ratio  # full pass-through
        df["new_units"] = new_units
        df["new_gsv"] = new_units * new_price
        df["new_nsv"] = new_units * new_net
        df["new_to"] = new_units * df[f"new_{TO_DEFINITIONS[config.to_basis].price_field}"].to_numpy(dtype=float)
        df["new_gp"] = df["new_nsv"].to_numpy(dtype=float) - new_units * cogs
        df["new_gm"] = _safe_div(df["new_gp"], df["new_nsv"])
        df["new_nsv_at_base_units"] = base_units * new_net
        df["nsv_price_effect"] = (new_net - base_net) * new_units
        df["nsv_volume_effect"] = (new_units - base_units) * base_net
        df["gp_price_effect"] = (new_net - base_net) * new_units
        df["gp_volume_effect"] = (new_units - base_units) * (base_net - cogs)

    blank = ~valid if ok else np.ones(len(df), dtype=bool)
    df.loc[blank, list(NEW_COLUMNS)] = np.nan
    return ScenarioResult(scenario=scenario, ppg=df, issues=issues, valid=ok)


# --------------------------------------------------------------------------------------
# Roll-ups
# --------------------------------------------------------------------------------------
def add_derived(df: pd.DataFrame) -> pd.DataFrame:
    """Add ratios and deltas computed from summed columns (works at any level)."""
    out = df.copy()
    out["base_gm"] = _safe_div(out["base_gp"], out["base_nsv"])
    out["new_gm"] = _safe_div(out["new_gp"], out["new_nsv"])
    for metric in ("units", "gsv", "nsv", "to", "gp"):
        out[f"d_{metric}"] = out[f"new_{metric}"] - out[f"base_{metric}"]
        out[f"pct_{metric}"] = _safe_div(out[f"d_{metric}"], out[f"base_{metric}"])
    out["d_gm"] = out["new_gm"] - out["base_gm"]  # fraction points (×100 = pp)
    out["pct_gm"] = _safe_div(out["new_gm"], out["base_gm"]) - 1  # relative change
    # Base-volume-weighted (Laspeyres) net price change.
    out["avg_price_change"] = _safe_div(out["new_nsv_at_base_units"], out["base_nsv"]) - 1
    return out


def aggregate(ppg_df: pd.DataFrame, node: Optional[Node] = None, group_depth: Optional[int] = None) -> pd.DataFrame:
    """Sum included PPGs under ``node``.

    ``group_depth=None`` returns one row for the node itself; a deeper value groups
    by that level (e.g. ``node.depth + 1`` for the node's children). Excluded PPGs
    are counted in ``n_ppgs`` but not in ``n_included`` or any sum.
    """
    node = node or Node()
    depth = node.depth if group_depth is None else group_depth
    if not node.depth <= depth <= len(HIERARCHY_COLUMNS):
        raise ValueError(f"group_depth must be between {node.depth} and {len(HIERARCHY_COLUMNS)}")
    in_node = ppg_df[node_mask(ppg_df, node)]
    included = in_node[in_node["is_valid"]]

    if depth == node.depth:
        sums = included[list(SUM_COLUMNS)].sum(min_count=1)
        row = {col: value for col, value in zip(HIERARCHY_COLUMNS, node.path)}
        row.update(n_ppgs=len(in_node), n_included=int(in_node["is_valid"].sum()))
        row.update({col: float(sums[col]) for col in SUM_COLUMNS})
        return add_derived(pd.DataFrame([row]))

    keys = list(HIERARCHY_COLUMNS[:depth])
    counts = in_node.groupby(keys, sort=False).agg(n_ppgs=("ppg_id", "size"), n_included=("is_valid", "sum"))
    sums = included.groupby(keys, sort=False)[list(SUM_COLUMNS)].sum(min_count=1)
    out = counts.join(sums, how="left").reset_index()
    out["n_included"] = out["n_included"].astype(int)
    return add_derived(out)


def bridge(ppg_df: pd.DataFrame, node: Optional[Node] = None) -> dict[str, float]:
    """Base -> price effect -> volume effect -> scenario, for NSV and GP."""
    row = aggregate(ppg_df, node).iloc[0]
    keys = (
        "base_nsv",
        "nsv_price_effect",
        "nsv_volume_effect",
        "new_nsv",
        "base_gp",
        "gp_price_effect",
        "gp_volume_effect",
        "new_gp",
    )
    return {key: float(row[key]) for key in keys}


def comparison_long(results: Sequence[ScenarioResult], node: Optional[Node] = None) -> pd.DataFrame:
    """Scenario-vs-base table for one node in long format (one row per scenario × metric)."""
    node = node or Node()
    rows = []
    for result in results:
        agg = aggregate(result.ppg, node).iloc[0]
        common = {
            "scenario": result.name,
            "scenario_valid": result.valid,
            "elasticity_multiplier": result.scenario.elasticity_multiplier,
            "node": node.label,
            "level": node.level_label,
            "n_ppgs": int(agg["n_ppgs"]),
            "n_included": int(agg["n_included"]),
        }
        rows.append(
            {
                **common,
                "metric": "price",
                "metric_label": "Avg. net price change (base-volume weighted)",
                "base": np.nan,
                "value": agg["avg_price_change"],
                "delta_abs": np.nan,
                "delta_pct": np.nan,
            }
        )
        for key, label in METRICS:
            rows.append(
                {
                    **common,
                    "metric": key,
                    "metric_label": label,
                    "base": agg[f"base_{key}"],
                    "value": agg[f"new_{key}"],
                    "delta_abs": agg[f"d_{key}"],
                    "delta_pct": agg[f"pct_{key}"],
                }
            )
    return pd.DataFrame(rows)


def node_summary(ppg_df: pd.DataFrame) -> pd.DataFrame:
    """One row per node at every level (root included), in tree order.

    Sums cover included PPGs only. ``weighted_elasticity`` is the base-NSV-weighted
    average of the elasticity in use (all PPGs that have one).
    """
    df = ppg_df[has_full_path(ppg_df)].copy()
    columns = ["path", "depth", "n_ppgs", "n_included", "weighted_elasticity", *SUM_COLUMNS]
    if df.empty:
        return add_derived(pd.DataFrame(columns=columns))

    valid = df["is_valid"].to_numpy(dtype=bool)
    values = df[list(SUM_COLUMNS)].astype(float)
    values.loc[~valid, :] = np.nan
    weight_ok = df["elasticity_used"].notna() & (df["base_nsv"] > 0)
    values["_ew"] = np.where(weight_ok, df["elasticity_used"] * df["base_nsv"], 0.0)
    values["_w"] = np.where(weight_ok, df["base_nsv"], 0.0)
    values["n_ppgs"] = 1
    values["n_included"] = valid.astype(int)
    for col in HIERARCHY_COLUMNS:
        values[col] = df[col].astype(str)

    sum_cols = [*SUM_COLUMNS, "_ew", "_w", "n_ppgs", "n_included"]
    parts = []
    for depth in range(len(HIERARCHY_COLUMNS) + 1):
        keys = list(HIERARCHY_COLUMNS[:depth])
        if keys:
            part = values.groupby(keys, sort=False)[sum_cols].sum(min_count=1).reset_index()
            part["path"] = list(part[keys].itertuples(index=False, name=None))
        else:
            part = values[sum_cols].sum(min_count=1).to_frame().T.astype(float)
            part["path"] = [()]
        part["depth"] = depth
        parts.append(part)

    out = pd.concat(parts, ignore_index=True)
    out["n_ppgs"] = out["n_ppgs"].fillna(0).astype(int)
    out["n_included"] = out["n_included"].fillna(0).astype(int)
    out["weighted_elasticity"] = _safe_div(out["_ew"], out["_w"])
    order = {node.path: i for i, node in enumerate(iter_nodes(df, include_root=True))}
    out["_order"] = [order.get(tuple(path), len(order)) for path in out["path"]]
    out = out.sort_values("_order", kind="stable").reset_index(drop=True)
    out = out[columns]
    return add_derived(out)
