"""Input validation: nothing missing is silently defaulted, bad values are flagged."""
from __future__ import annotations

import io
import math

import pandas as pd
import pytest

from pricing_engine import SchemaError, Severity, SimulationConfig, TOBasis, load_dataset, validate_dataset

from conftest import frame, row


def errors_for(df, config=None):
    clean, issues = validate_dataset(df, config)
    return clean, [i for i in issues if i.severity is Severity.ERROR], [i for i in issues if i.severity is Severity.WARNING]


def test_clean_row_has_no_issues():
    clean, errors, warnings = errors_for(frame())
    assert errors == [] and warnings == []
    assert clean.loc[0, "row_errors"] == []


@pytest.mark.parametrize(
    "overrides, fragment",
    [
        ({"base_list_price": -5}, "must be > 0"),
        ({"base_list_price": 0}, "must be > 0"),
        ({"base_list_price": None}, "Missing base list price"),
        ({"base_units": -1}, "cannot be negative"),
        ({"base_units": None}, "Missing base units"),
        ({"unit_cogs": None}, "Missing unit COGS"),
        ({"unit_cogs": -1}, "cannot be negative"),
        ({"on_invoice_disc_rate": 5}, "fractions"),  # 5 meant as 5 %
        ({"on_invoice_disc_rate": 0.6, "off_invoice_trade_rate": 0.5}, "100% or more"),
        ({"base_units": "abc"}, "not a valid number"),
        ({"brand": ""}, "Missing Brand"),
        ({"base_rsp": -10}, "RSP must be > 0"),
    ],
)
def test_bad_values_are_errors(overrides, fragment):
    clean, errors, _ = errors_for(frame(row(**overrides)))
    assert any(fragment in e.message for e in errors), [e.message for e in errors]
    assert clean.loc[0, "row_errors"], "the row must carry its error"


def test_missing_values_are_never_silently_defaulted():
    clean, errors, _ = errors_for(frame(row(unit_cogs=None, base_units=None)))
    assert math.isnan(clean.loc[0, "unit_cogs"]) and math.isnan(clean.loc[0, "base_units"])
    assert len(errors) == 2


def test_blank_trade_rate_is_assumed_zero_and_flagged():
    clean, errors, warnings = errors_for(frame(row(off_invoice_trade_rate=None)))
    assert errors == []
    assert clean.loc[0, "off_invoice_trade_rate"] == 0.0
    assert any("assumed 0%" in w.message for w in warnings)


def test_cogs_above_net_price_warns():
    _, errors, warnings = errors_for(frame(row(unit_cogs=90)))  # net price 85
    assert errors == []
    assert any("negative base margin" in w.message for w in warnings)


def test_zero_units_warns():
    _, errors, warnings = errors_for(frame(row(base_units=0)))
    assert errors == [] and any("0" in w.message for w in warnings)


def test_duplicate_ppg_ids_flag_both_rows():
    clean, errors, _ = errors_for(frame(row(ppg_id="X"), row(ppg_id="X", brand="Other")))
    assert len([e for e in errors if "Duplicate" in e.message]) == 2
    assert all(clean["row_errors"].map(len) > 0)


def test_rsp_required_only_for_consumer_offtake():
    df = frame(row(base_rsp=None))
    _, errors, _ = errors_for(df, SimulationConfig(to_basis=TOBasis.GSV))
    assert errors == []
    _, errors, _ = errors_for(df, SimulationConfig(to_basis=TOBasis.CONSUMER_OFFTAKE))
    assert any("RSP" in e.message for e in errors)


def test_missing_required_column_raises():
    with pytest.raises(SchemaError, match="unit_cogs"):
        validate_dataset(frame().drop(columns=["unit_cogs"]))


def test_optional_columns_absent_are_reported_as_assumptions():
    df = frame().drop(columns=["on_invoice_disc_rate", "off_invoice_trade_rate", "ppg_name", "base_rsp"])
    clean, issues = validate_dataset(df)
    infos = [i.message for i in issues if i.severity is Severity.INFO]
    assert any("on_invoice_disc_rate" in m for m in infos)
    assert clean.loc[0, "ppg_name"] == "P1"
    assert clean.loc[0, "off_invoice_trade_rate"] == 0.0


def test_column_aliases_and_messy_headers():
    csv = (
        "Category,Sub-Category,Brand,PPG,PPG Description,Units,Base Price,COGS,Price Elasticity\n"
        "Cat,Sub,B,P9,Name,\"1,200\",10,4,-1.5\n"
    )
    raw = load_dataset(io.StringIO(csv), filename="x.csv")
    clean, issues = validate_dataset(raw)
    assert clean.loc[0, "sub_category"] == "Sub"
    assert clean.loc[0, "ppg_id"] == "P9"
    assert clean.loc[0, "base_units"] == 1200.0  # thousands separator accepted
    assert clean.loc[0, "base_list_price"] == 10.0
    assert not [i for i in issues if i.severity is Severity.ERROR]


def test_ids_keep_leading_zeros():
    csv = "category,sub_category,brand,ppg_id,base_units,base_list_price,unit_cogs,elasticity\nC,S,B,007,1,1,0.5,-1\n"
    clean, _ = validate_dataset(load_dataset(io.StringIO(csv), filename="x.csv"))
    assert clean.loc[0, "ppg_id"] == "007"


def test_blank_rows_are_ignored():
    df = pd.DataFrame([row(), {column: None for column in row()}])
    clean, issues = validate_dataset(df)
    assert len(clean) == 1
    assert any("blank row" in i.message for i in issues)
