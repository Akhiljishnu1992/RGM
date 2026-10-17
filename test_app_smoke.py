"""Headless smoke test of the Streamlit UI (uses Streamlit's AppTest; no browser needed)."""
from __future__ import annotations

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from conftest import PROJECT_DIR  # noqa: E402

APP = str(PROJECT_DIR / "app.py")


def comparison_table(at):
    for element in at.dataframe:
        value = element.value
        value = getattr(value, "data", value)  # Styler -> DataFrame
        if "Metric" in value.columns:
            return value
    raise AssertionError("comparison table not found")


@pytest.fixture
def app():
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    assert not at.exception
    return at


def test_app_loads_with_example_scenarios(app):
    table = comparison_table(app)
    assert list(table["Metric"]) == [
        "Avg. net price change",
        "Volume (units)",
        "NSV (INR Cr)",
        "TO (INR Cr)",
        "GP (INR Cr)",
        "GM %",
    ]
    assert "PC +5%" in table.columns


def test_navigate_and_set_price_change(app):
    app.selectbox(key="nav_category").set_value("Home Care").run()
    app.selectbox(key="nav_sub_category").set_value("Laundry Powder").run()
    [field] = [n for n in app.number_input if n.label == "Price change (%)"]
    field.set_value(3.0).run()
    app.button(key="btn_set_price").click().run()
    assert not app.exception
    assert ("Home Care", "Laundry Powder") in app.session_state["scenarios"]["PC +5%"]["changes"]
    assert any("+3.00%" in c.value for c in app.caption)


def test_invalid_price_cut_is_reported_not_crashing(app):
    app.selectbox(key="nav_category").set_value("Home Care").run()
    [radio] = [r for r in app.radio if r.label == "Change type"]
    radio.set_value("absolute").run()
    [field] = [n for n in app.number_input if n.label.startswith("Price change (INR")]
    field.set_value(-100.0).run()
    app.button(key="btn_set_price").click().run()
    assert not app.exception
    assert any("zero or negative" in e.value for e in app.error)


def test_override_and_scenario_management(app):
    app.selectbox(key="nav_category").set_value("Home Care").run()
    app.selectbox(key="nav_sub_category").set_value("Laundry Powder").run()
    app.selectbox(key="nav_brand").set_value("Azura").run()
    [field] = [n for n in app.number_input if n.label == "Override for this node"]
    field.set_value(-1.8).run()
    app.button(key="btn_set_override").click().run()
    assert app.session_state["elasticity_overrides"] == {("Home Care", "Laundry Powder", "Azura"): -1.8}

    app.text_input(key="new_scenario_name").input("Test scenario").run()
    app.button(key="btn_add_scenario").click().run()
    assert app.selectbox(key="active_scenario").value == "Test scenario"
    app.button(key="btn_delete_scenario").click().run()
    assert "Test scenario" not in app.session_state["scenarios"]
    assert not app.exception


def test_switching_to_offtake_and_default_elasticity(app):
    app.selectbox(key="to_basis").set_value("consumer_offtake").run()
    app.checkbox(key="apply_default").check().run()
    assert not app.exception
    assert "35 included" in app.caption[0].value
