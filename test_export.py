"""CSV exports."""
from __future__ import annotations

import io

import pandas as pd

from pricing_engine import (
    Node,
    Scenario,
    SimulationConfig,
    export_node_summary,
    export_ppg_results,
    load_dataset,
    prepare_base,
    simulate,
    to_csv_bytes,
    validate_dataset,
)

from conftest import SAMPLE_CSV, pct


def sample_results():
    config = SimulationConfig()
    clean, _ = validate_dataset(load_dataset(SAMPLE_CSV), config)
    base, _ = prepare_base(clean, [], config)
    scenarios = [
        Scenario("A", (pct(("Personal Care",), 0.05),)),
        Scenario("B", (pct(("Home Care",), 0.03),), 1.2),
    ]
    return [simulate(base, s, config) for s in scenarios], config


def test_ppg_export_has_one_row_per_scenario_and_ppg():
    results, config = sample_results()
    out = export_ppg_results(results, config)
    assert len(out) == 2 * 36
    assert set(out["scenario"]) == {"A", "B"}
    excluded = out[out["status"] != "Included"]
    assert set(excluded["ppg_id"]) == {"CHF-SAU-1KG", "NIM-SHP-650"}
    assert excluded["new_nsv"].isna().all()
    assert (out.loc[out["scenario"] == "B", "elasticity_multiplier"] == 1.2).all()


def test_csv_round_trip():
    results, config = sample_results()
    data = to_csv_bytes(export_ppg_results(results, config))
    assert data.startswith(b"\xef\xbb\xbf")  # BOM for Excel
    back = pd.read_csv(io.BytesIO(data), encoding="utf-8-sig")
    assert "nsv_price_effect" in back.columns and len(back) == 72


def test_node_summary_export_covers_every_level():
    results, _ = sample_results()
    out = export_node_summary(results, names={"LUM-BAR-075": "Lumina Cream Bar 75g"})
    per_scenario = out[out["scenario"] == "A"]
    assert per_scenario["level"].value_counts().to_dict() == {
        "PPG": 36,
        "Brand": 12,
        "Sub-category": 6,
        "Category": 3,
        "Total": 1,
    }
    assert "Lumina Cream Bar 75g" in set(per_scenario["node_name"])
