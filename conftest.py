"""Shared helpers for the test-suite."""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

import pandas as pd
import pytest

from pricing_engine import (
    ChangeType,
    ElasticityOverride,
    Node,
    PriceChange,
    Scenario,
    ScenarioResult,
    SimulationConfig,
    prepare_base,
    simulate,
    validate_dataset,
)

PROJECT_DIR = Path(__file__).resolve().parents[1]
SAMPLE_CSV = PROJECT_DIR / "data" / "sample_hierarchy.csv"
SAMPLE_JSON = PROJECT_DIR / "data" / "sample_hierarchy.json"

# The worked-example PPG from docs/FORMULAS.md.
BASE_ROW = dict(
    category="Cat",
    sub_category="Sub",
    brand="BrandA",
    ppg_id="P1",
    ppg_name="Pack one",
    base_units=1000.0,
    base_list_price=100.0,
    on_invoice_disc_rate=0.05,
    off_invoice_trade_rate=0.10,
    unit_cogs=50.0,
    elasticity=-2.0,
    elasticity_source="Test model",
    base_rsp=130.0,
)


def row(**overrides) -> dict:
    return {**BASE_ROW, **overrides}


def frame(*rows: dict) -> pd.DataFrame:
    return pd.DataFrame(list(rows) or [row()])


def pct(path: tuple, value: float) -> PriceChange:
    return PriceChange(Node(path), ChangeType.PERCENT, value)


def absolute(path: tuple, value: float) -> PriceChange:
    return PriceChange(Node(path), ChangeType.ABSOLUTE, value)


def run(
    df: pd.DataFrame,
    changes: Iterable[PriceChange] = (),
    overrides: Iterable[ElasticityOverride] = (),
    config: Optional[SimulationConfig] = None,
    multiplier: float = 1.0,
    name: str = "S1",
) -> ScenarioResult:
    """validate -> prepare_base -> simulate, in one call."""
    config = config or SimulationConfig()
    clean, _ = validate_dataset(df, config)
    base, _ = prepare_base(clean, overrides, config)
    return simulate(base, Scenario(name, tuple(changes), multiplier), config)


@pytest.fixture
def sample_csv() -> Path:
    return SAMPLE_CSV
