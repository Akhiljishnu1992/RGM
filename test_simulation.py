"""Calculation engine: precedence rules, price types, elasticity handling, roll-ups."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pricing_engine import (
    ElasticityOverride,
    Node,
    Scenario,
    Severity,
    SimulationConfig,
    aggregate,
    load_dataset,
    node_summary,
    prepare_base,
    simulate,
    validate_dataset,
)

from conftest import SAMPLE_CSV, absolute, frame, pct, row, run

# Three PPGs: two in BrandA, one in BrandB, all in the same sub-category.
THREE = frame(
    row(ppg_id="A1", brand="BrandA"),
    row(ppg_id="A2", brand="BrandA", base_units=500.0, base_list_price=40.0, unit_cogs=20.0),
    row(ppg_id="B1", brand="BrandB", base_units=2000.0, base_list_price=10.0, unit_cogs=4.0, elasticity=-1.0),
)


def by_id(result):
    return result.ppg.set_index("ppg_id")


def test_no_change_reproduces_base():
    res = run(THREE)
    df = res.ppg
    assert np.allclose(df["new_units"], df["base_units"])
    assert np.allclose(df["new_nsv"], df["base_nsv"])
    assert np.allclose(df["nsv_price_effect"], 0) and np.allclose(df["nsv_volume_effect"], 0)
    assert (df["price_change_source"] == "No change").all()


def test_most_specific_price_change_wins():
    changes = [
        pct(("Cat",), 0.05),  # category
        pct(("Cat", "Sub", "BrandA"), 0.10),  # brand overrides category
        pct(("Cat", "Sub", "BrandA", "A2"), 0.0),  # PPG held at 0 %
    ]
    df = by_id(run(THREE, changes))
    assert df.loc["A1", "price_change_pct"] == pytest.approx(0.10)
    assert df.loc["A2", "price_change_pct"] == pytest.approx(0.0)
    assert df.loc["B1", "price_change_pct"] == pytest.approx(0.05)
    assert df.loc["A1", "price_change_source"].endswith("Brand: BrandA")
    assert df.loc["B1", "price_change_source"].endswith("Category: Cat")


def test_order_of_changes_does_not_matter_for_precedence():
    a = by_id(run(THREE, [pct(("Cat",), 0.05), pct(("Cat", "Sub", "BrandA"), 0.10)]))
    b = by_id(run(THREE, [pct(("Cat", "Sub", "BrandA"), 0.10), pct(("Cat",), 0.05)]))
    pd.testing.assert_series_equal(a["new_units"], b["new_units"])


def test_root_level_change_applies_to_everything():
    df = run(THREE, [pct((), 0.02)]).ppg
    assert np.allclose(df["price_change_pct"], 0.02)


def test_absolute_change_is_added_to_list_price():
    df = by_id(run(THREE, [absolute(("Cat", "Sub", "BrandB"), 1.5)]))
    assert df.loc["B1", "new_list_price"] == pytest.approx(11.5)
    assert df.loc["B1", "price_change_pct"] == pytest.approx(0.15)
    # elasticity −1.0: units = 2000 × 1.15 ** −1
    assert df.loc["B1", "new_units"] == pytest.approx(2000 / 1.15)
    assert df.loc["A1", "price_change_pct"] == pytest.approx(0.0)


def test_price_cut_increases_volume():
    df = by_id(run(THREE, [pct(("Cat",), -0.10)]))
    assert df.loc["A1", "new_units"] == pytest.approx(1000 * 0.9 ** -2.0)
    assert df.loc["A1", "new_units"] > df.loc["A1", "base_units"]


def test_trade_rates_held_constant_so_net_price_moves_with_list():
    df = by_id(run(THREE, [pct(("Cat",), 0.08)]))
    ratio = df["new_net_price"] / df["base_net_price"]
    assert np.allclose(ratio, 1.08)


def test_elasticity_override_precedence():
    overrides = [
        ElasticityOverride(Node(("Cat", "Sub", "BrandA")), -1.0),
        ElasticityOverride(Node(("Cat", "Sub", "BrandA", "A1")), -3.0),
    ]
    df = by_id(run(THREE, [pct(("Cat",), 0.10)], overrides=overrides))
    assert df.loc["A1", "elasticity_used"] == -3.0  # PPG override beats brand override
    assert df.loc["A2", "elasticity_used"] == -1.0  # brand override beats data (−2.0)
    assert df.loc["B1", "elasticity_used"] == -1.0  # untouched: data value
    assert df.loc["A1", "elasticity_source_used"] == "Override @ PPG: A1"
    assert df.loc["B1", "elasticity_source_used"] == "Test model"
    assert df.loc["A1", "new_units"] == pytest.approx(1000 * 1.1 ** -3.0)


def test_elasticity_multiplier_scales_elasticity():
    df = by_id(run(THREE, [pct(("Cat",), 0.10)], multiplier=1.25))
    assert df.loc["A1", "elasticity_effective"] == pytest.approx(-2.5)
    assert df.loc["A1", "new_units"] == pytest.approx(1000 * 1.1 ** -2.5)


def test_positive_elasticity_is_calculated_with_warning():
    res = run(frame(row(elasticity=0.4)), [pct(("Cat",), 0.10)])
    r = res.ppg.iloc[0]
    assert r["is_valid"]
    assert r["new_units"] > r["base_units"]
    assert any("Positive elasticity" in w for w in r["row_warnings"])


def test_zero_base_units_gives_zero_volume_and_undefined_margin():
    res = run(frame(row(base_units=0.0)), [pct(("Cat",), 0.10)])
    r = res.ppg.iloc[0]
    assert r["new_units"] == 0
    assert np.isnan(r["new_gm"])
    agg = aggregate(res.ppg).iloc[0]
    assert np.isnan(agg["base_gm"])


def test_missing_elasticity_excluded_unless_default_enabled():
    df = frame(row(ppg_id="A1"), row(ppg_id="A2", elasticity=None))
    res = run(df, [pct(("Cat",), 0.10)])
    a2 = by_id(res).loc["A2"]
    assert not a2["is_valid"] and np.isnan(a2["new_nsv"])
    assert aggregate(res.ppg).iloc[0]["n_included"] == 1

    config = SimulationConfig(apply_default_elasticity=True, default_elasticity=-1.5)
    res = run(df, [pct(("Cat",), 0.10)], config=config)
    a2 = by_id(res).loc["A2"]
    assert a2["is_valid"]
    assert a2["elasticity_used"] == -1.5
    assert a2["elasticity_source_used"].startswith("Default placeholder")
    assert any("default" in w for w in a2["row_warnings"])


def test_override_supplies_missing_elasticity():
    df = frame(row(elasticity=None))
    res = run(df, [pct(("Cat",), 0.10)], overrides=[ElasticityOverride(Node(("Cat",)), -1.2)])
    assert res.ppg.iloc[0]["is_valid"]
    assert res.ppg.iloc[0]["elasticity_used"] == -1.2


def test_excluded_ppg_is_left_out_of_every_total():
    df = frame(row(ppg_id="A1"), row(ppg_id="A2", unit_cogs=None))
    res = run(df, [pct(("Cat",), 0.10)])
    agg = aggregate(res.ppg).iloc[0]
    only_a1 = run(frame(row(ppg_id="A1")), [pct(("Cat",), 0.10)])
    expected = aggregate(only_a1.ppg).iloc[0]
    assert agg["n_ppgs"] == 2 and agg["n_included"] == 1
    for col in ("base_nsv", "new_nsv", "base_gp", "new_gp", "base_units", "new_units"):
        assert agg[col] == pytest.approx(expected[col])


def test_negative_resulting_price_invalidates_scenario():
    res = run(THREE, [absolute(("Cat",), -15.0)])  # B1 list price 10 -> −5
    assert not res.valid
    errors = [i for i in res.issues if i.severity is Severity.ERROR]
    assert errors and "B1" in errors[0].message
    assert res.ppg["new_nsv"].isna().all()


def test_cut_of_100_percent_is_rejected():
    res = run(THREE, [pct(("Cat",), -1.0)])
    assert not res.valid


def test_non_positive_multiplier_is_rejected():
    res = run(THREE, [pct(("Cat",), 0.05)], multiplier=0.0)
    assert not res.valid


def test_change_on_unknown_node_warns():
    res = run(THREE, [pct(("Nope",), 0.05)])
    assert res.valid
    assert any("matches no PPG" in i.message for i in res.issues)


def test_large_change_warns():
    res = run(THREE, [pct(("Cat",), 0.45)])
    assert res.valid
    assert any("more than ±30%" in i.message for i in res.issues)


def test_gm_is_recomputed_from_sums_not_averaged():
    res = run(THREE, [pct(("Cat",), 0.05)])
    agg = aggregate(res.ppg).iloc[0]
    df = res.ppg
    assert agg["new_gm"] == pytest.approx(df["new_gp"].sum() / df["new_nsv"].sum())
    assert agg["new_gm"] != pytest.approx(df["new_gm"].mean())


def test_children_sum_to_parent():
    res = run(THREE, [pct(("Cat",), 0.05), pct(("Cat", "Sub", "BrandB"), 0.12)])
    parent = aggregate(res.ppg, Node(("Cat", "Sub"))).iloc[0]
    kids = aggregate(res.ppg, Node(("Cat", "Sub")), group_depth=3)
    assert list(kids["brand"]) == ["BrandA", "BrandB"]
    for col in ("base_nsv", "new_nsv", "new_gp", "nsv_price_effect", "gp_volume_effect"):
        assert kids[col].sum() == pytest.approx(parent[col])


def test_decomposition_is_exact_on_sample_data_for_random_scenarios():
    raw = load_dataset(SAMPLE_CSV)
    config = SimulationConfig(apply_default_elasticity=True)
    clean, _ = validate_dataset(raw, config)
    base, _ = prepare_base(clean, [], config)
    rng = np.random.default_rng(7)
    nodes = [Node(tuple(p)) for p in base[["category", "sub_category", "brand"]].drop_duplicates().itertuples(index=False)]
    for _ in range(20):
        picks = rng.choice(len(nodes), size=4, replace=False)
        changes = [pct(nodes[i].path, float(rng.uniform(-0.15, 0.15))) for i in picks]
        res = simulate(base, Scenario("random", tuple(changes), float(rng.uniform(0.8, 1.3))), config)
        assert res.valid
        valid = res.ppg[res.ppg["is_valid"]]
        nsv_delta = valid["new_nsv"] - valid["base_nsv"]
        gp_delta = valid["new_gp"] - valid["base_gp"]
        assert np.allclose(valid["nsv_price_effect"] + valid["nsv_volume_effect"], nsv_delta)
        assert np.allclose(valid["gp_price_effect"] + valid["gp_volume_effect"], gp_delta)
        total = aggregate(res.ppg).iloc[0]
        assert total["nsv_price_effect"] + total["nsv_volume_effect"] == pytest.approx(total["d_nsv"])
        assert total["gp_price_effect"] + total["gp_volume_effect"] == pytest.approx(total["d_gp"])


def test_node_summary_is_consistent_with_aggregate():
    raw = load_dataset(SAMPLE_CSV)
    clean, _ = validate_dataset(raw)
    base, _ = prepare_base(clean, [])
    res = simulate(base, Scenario("s", (pct(("Home Care",), 0.04),)))
    summary = node_summary(res.ppg)
    assert summary.iloc[0]["path"] == ()  # root first
    assert len(summary) == 1 + 3 + 6 + 12 + 36
    by_path = {tuple(p): r for p, r in zip(summary["path"], summary.itertuples(index=False))}
    for path in [(), ("Home Care",), ("Home Care", "Dishwash"), ("Home Care", "Dishwash", "Clearo")]:
        agg = aggregate(res.ppg, Node(path)).iloc[0]
        node_row = by_path[path]
        assert node_row.new_nsv == pytest.approx(agg["new_nsv"])
        assert node_row.n_included == agg["n_included"]
        assert node_row.d_gp == pytest.approx(agg["d_gp"])
    # tree order: every node appears after its parent
    seen = set()
    for path in summary["path"]:
        path = tuple(path)
        if path:
            assert path[:-1] in seen
        seen.add(path)
