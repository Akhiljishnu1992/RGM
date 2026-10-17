"""The worked example in docs/FORMULAS.md, reproduced by hand and checked against the engine.

One PPG
    list price 100.00, on-invoice discount 5 %, off-invoice trade 10 %  -> net price 85.00
    base units 1,000, unit COGS 50.00, elasticity -2.0, RSP 130.00
Scenario
    +10 % list price

Hand calculation
    new list price      = 100 × 1.10                        = 110.00
    new net price       = 110 × (1 − 0.05 − 0.10)           = 93.50
    new units           = 1,000 × 1.10 ** −2.0 = 1,000 / 1.21 = 826.4463
    base NSV            = 1,000 × 85.00                     = 85,000.00
    new NSV             = 826.4463 × 93.50                  = 77,272.73   (−9.09 %)
    base GP             = 1,000 × (85.00 − 50.00)           = 35,000.00
    new GP              = 826.4463 × (93.50 − 50.00)        = 35,950.41   (+2.72 %)
    base GM %           = 35,000 / 85,000                   = 41.18 %
    new GM %            = 43.50 / 93.50                     = 46.52 %     (+5.35 pp)
    TO, invoiced        = 1,000 × 95.00 -> 826.4463 × 104.50 = 95,000.00 -> 86,363.64
    NSV price effect    = (93.50 − 85.00) × 826.4463        = +7,024.79
    NSV volume effect   = (826.4463 − 1,000) × 85.00        = −14,752.07
    GP volume effect    = (826.4463 − 1,000) × 35.00        = −6,074.38
"""
from __future__ import annotations

import pytest

from pricing_engine import Node, SimulationConfig, TOBasis, aggregate, bridge, comparison_long

from conftest import frame, pct, run

NEW_UNITS = 1000 / 1.21  # 826.446281...


@pytest.fixture
def result():
    return run(frame(), [pct(("Cat",), 0.10)])


def test_ppg_level_values(result):
    r = result.ppg.iloc[0]
    assert result.valid
    assert r["new_list_price"] == pytest.approx(110.0)
    assert r["base_net_price"] == pytest.approx(85.0)
    assert r["new_net_price"] == pytest.approx(93.5)
    assert r["new_units"] == pytest.approx(826.446281, rel=1e-9)
    assert r["base_nsv"] == pytest.approx(85_000.00)
    assert r["new_nsv"] == pytest.approx(77_272.727273, rel=1e-9)
    assert r["base_gp"] == pytest.approx(35_000.00)
    assert r["new_gp"] == pytest.approx(35_950.413223, rel=1e-9)
    assert r["base_gm"] == pytest.approx(0.411765, abs=1e-6)
    assert r["new_gm"] == pytest.approx(0.465241, abs=1e-6)


def test_deltas_match_hand_calculation(result):
    agg = aggregate(result.ppg).iloc[0]
    assert agg["d_units"] == pytest.approx(-173.553719, rel=1e-9)
    assert agg["pct_units"] == pytest.approx(-0.173554, abs=1e-6)
    assert agg["d_nsv"] == pytest.approx(-7_727.272727, rel=1e-9)
    assert agg["pct_nsv"] == pytest.approx(-0.090909, abs=1e-6)  # = 1.1 ** (1 − 2) − 1
    assert agg["d_gp"] == pytest.approx(950.413223, rel=1e-9)
    assert agg["pct_gp"] == pytest.approx(0.027155, abs=1e-6)
    assert agg["d_gm"] * 100 == pytest.approx(5.3476, abs=1e-4)  # percentage points
    assert agg["pct_gm"] == pytest.approx(0.129870, abs=1e-6)
    assert agg["avg_price_change"] == pytest.approx(0.10)


def test_price_volume_decomposition(result):
    b = bridge(result.ppg)
    assert b["nsv_price_effect"] == pytest.approx(7_024.793388, rel=1e-9)
    assert b["nsv_volume_effect"] == pytest.approx(-14_752.066116, rel=1e-9)
    assert b["gp_price_effect"] == pytest.approx(7_024.793388, rel=1e-9)
    assert b["gp_volume_effect"] == pytest.approx(-6_074.380165, rel=1e-9)
    assert b["nsv_price_effect"] + b["nsv_volume_effect"] == pytest.approx(b["new_nsv"] - b["base_nsv"])
    assert b["gp_price_effect"] + b["gp_volume_effect"] == pytest.approx(b["new_gp"] - b["base_gp"])


@pytest.mark.parametrize(
    "basis, base_to, new_to",
    [
        (TOBasis.GSV, 100_000.00, 90_909.090909),  # units × list price
        (TOBasis.INVOICED, 95_000.00, 86_363.636364),  # units × list × (1 − on-invoice)
        (TOBasis.NSV, 85_000.00, 77_272.727273),  # same as NSV
        (TOBasis.CONSUMER_OFFTAKE, 130_000.00, 118_181.818182),  # units × RSP (RSP 130 -> 143)
    ],
)
def test_turnover_definitions(basis, base_to, new_to):
    res = run(frame(), [pct(("Cat",), 0.10)], config=SimulationConfig(to_basis=basis))
    agg = aggregate(res.ppg).iloc[0]
    assert agg["base_to"] == pytest.approx(base_to)
    assert agg["new_to"] == pytest.approx(new_to, rel=1e-9)
    assert agg["pct_to"] == pytest.approx(-0.090909, abs=1e-6)


def test_comparison_table_reports_the_same_numbers(result):
    long = comparison_long([result], Node()).set_index("metric")
    assert long.loc["nsv", "base"] == pytest.approx(85_000.0)
    assert long.loc["nsv", "value"] == pytest.approx(77_272.727273, rel=1e-9)
    assert long.loc["gp", "delta_abs"] == pytest.approx(950.413223, rel=1e-9)
    assert long.loc["gm", "delta_abs"] == pytest.approx(0.053476, abs=1e-6)
    assert long.loc["price", "value"] == pytest.approx(0.10)
