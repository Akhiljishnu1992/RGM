"""Tidy exports of simulation results (CSV-ready DataFrames).

Money columns are exported in raw currency units (not the display scale chosen in
the UI). Rates and percentages are decimal fractions (0.05 = 5 %); GM deltas
(``d_gm``) are fraction points (×100 = percentage points).
"""
from __future__ import annotations

from typing import Mapping, Optional, Sequence

import numpy as np
import pandas as pd

from .hierarchy import display_name
from .models import Node, SimulationConfig, TO_DEFINITIONS
from .simulation import ScenarioResult, add_derived, node_summary

PPG_EXPORT_COLUMNS: tuple[str, ...] = (
    "scenario",
    "scenario_valid",
    "to_definition",
    "elasticity_multiplier",
    "category",
    "sub_category",
    "brand",
    "ppg_id",
    "ppg_name",
    "status",
    "data_errors",
    "data_warnings",
    "price_change_source",
    "price_change_pct",
    "elasticity_input",
    "elasticity_used",
    "elasticity_source_used",
    "elasticity_effective",
    "base_list_price",
    "new_list_price",
    "on_invoice_disc_rate",
    "off_invoice_trade_rate",
    "base_net_price",
    "new_net_price",
    "base_rsp",
    "new_rsp",
    "unit_cogs",
    "base_units",
    "new_units",
    "d_units",
    "pct_units",
    "base_nsv",
    "new_nsv",
    "d_nsv",
    "pct_nsv",
    "base_to",
    "new_to",
    "d_to",
    "pct_to",
    "base_gp",
    "new_gp",
    "d_gp",
    "pct_gp",
    "base_gm",
    "new_gm",
    "d_gm",
    "pct_gm",
    "nsv_price_effect",
    "nsv_volume_effect",
    "gp_price_effect",
    "gp_volume_effect",
)

NODE_EXPORT_COLUMNS: tuple[str, ...] = (
    "scenario",
    "scenario_valid",
    "level",
    "node",
    "node_name",
    "n_ppgs",
    "n_included",
    "weighted_elasticity",
    "avg_price_change",
    "base_units",
    "new_units",
    "d_units",
    "pct_units",
    "base_nsv",
    "new_nsv",
    "d_nsv",
    "pct_nsv",
    "base_to",
    "new_to",
    "d_to",
    "pct_to",
    "base_gp",
    "new_gp",
    "d_gp",
    "pct_gp",
    "base_gm",
    "new_gm",
    "d_gm",
    "pct_gm",
    "nsv_price_effect",
    "nsv_volume_effect",
    "gp_price_effect",
    "gp_volume_effect",
)


def export_ppg_results(results: Sequence[ScenarioResult], config: Optional[SimulationConfig] = None) -> pd.DataFrame:
    """One row per scenario × PPG, with inputs, assumptions, results and deltas."""
    config = config or SimulationConfig()
    frames = []
    for result in results:
        df = add_derived(result.ppg)
        df = df.rename(columns={"elasticity": "elasticity_input"})
        df.insert(0, "scenario", result.name)
        df["scenario_valid"] = result.valid
        df["to_definition"] = TO_DEFINITIONS[config.to_basis].label
        df["elasticity_multiplier"] = result.scenario.elasticity_multiplier
        status = np.where(df["is_valid"], "Included", "Excluded (data errors)")
        if not result.valid:
            status = np.full(len(df), "Scenario invalid")
        df["status"] = status
        df["data_errors"] = df["row_errors"].map("; ".join)
        df["data_warnings"] = df["row_warnings"].map("; ".join)
        frames.append(df[list(PPG_EXPORT_COLUMNS)])
    if not frames:
        return pd.DataFrame(columns=list(PPG_EXPORT_COLUMNS))
    return pd.concat(frames, ignore_index=True)


def export_node_summary(results: Sequence[ScenarioResult], names: Optional[Mapping[str, str]] = None) -> pd.DataFrame:
    """One row per scenario × node, for every level of the hierarchy."""
    frames = []
    for result in results:
        summary = node_summary(result.ppg)
        nodes = [Node(tuple(path)) for path in summary["path"]]
        summary.insert(0, "scenario", result.name)
        summary["scenario_valid"] = result.valid
        summary["level"] = [n.level_label for n in nodes]
        summary["node"] = [n.label for n in nodes]
        summary["node_name"] = [display_name(n, names) for n in nodes]
        frames.append(summary[list(NODE_EXPORT_COLUMNS)])
    if not frames:
        return pd.DataFrame(columns=list(NODE_EXPORT_COLUMNS))
    return pd.concat(frames, ignore_index=True)


def to_csv_bytes(df: pd.DataFrame) -> bytes:
    """CSV with a UTF-8 BOM so Excel shows symbols such as › and ₹ correctly."""
    return df.to_csv(index=False).encode("utf-8-sig")
