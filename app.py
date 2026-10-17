"""RGM Pricing Simulator — Streamlit UI.

All business logic lives in ``pricing_engine/`` (no Streamlit imports there), so it can
be tested on its own with ``pytest``. This file only manages state, widgets,
formatting and charts.

Run:  streamlit run app.py
"""
from __future__ import annotations

import copy
import math
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from pricing_engine import (
    HIERARCHY_COLUMNS,
    LEVEL_LABELS,
    LEVEL_PLURALS,
    TO_DEFINITIONS,
    ChangeType,
    ElasticityOverride,
    Node,
    PriceChange,
    Scenario,
    SchemaError,
    Severity,
    SimulationConfig,
    TOBasis,
    add_derived,
    aggregate,
    bridge,
    build_tree,
    children,
    comparison_long,
    display_name,
    export_node_summary,
    export_ppg_results,
    load_dataset,
    node_mask,
    node_summary,
    ppg_names,
    prepare_base,
    simulate,
    to_csv_bytes,
    validate_dataset,
)

APP_DIR = Path(__file__).resolve().parent
SAMPLE_DATA_PATH = APP_DIR / "data" / "sample_hierarchy.csv"
FORMULAS_PATH = APP_DIR / "docs" / "FORMULAS.md"
SAMPLE_SOURCE_LABEL = "Bundled sample (fictional brands, placeholder numbers)"

ALL = "__ALL__"
RESERVED_SCENARIO_NAMES = {"base", "metric"}
# PLACEHOLDER display defaults: currency label and money scale are cosmetic only.
DEFAULT_CURRENCY = "INR"
MONEY_SCALES: dict[str, tuple[float, str]] = {
    "As is": (1.0, ""),
    "Thousands": (1e3, "K"),
    "Lakhs": (1e5, "L"),
    "Millions": (1e6, "M"),
    "Crores": (1e7, "Cr"),
    "Billions": (1e9, "B"),
}
DEFAULT_SCALE = "Crores"
DEFAULTS = SimulationConfig()

POSITIVE = "#1E8449"
NEGATIVE = "#C0392B"
TOTAL = "#34495E"
PRICE_COLOR = "#2E86C1"
VOLUME_COLOR = "#E67E22"
WIDE = {"width": "stretch"}

st.set_page_config(page_title="RGM Pricing Simulator", layout="wide")


# ======================================================================================
# State
# ======================================================================================
def _example_scenarios() -> dict:
    """Illustrative starter scenarios for the bundled sample data (PLACEHOLDERS).

    They are replaced by one blank scenario as soon as you load your own data.
    Percent values are decimal fractions (0.05 = +5 %).
    """
    pc_up = {("Personal Care",): {"type": "percent", "value": 0.05}}
    return {
        "PC +5%": {"multiplier": 1.0, "changes": copy.deepcopy(pc_up)},
        "Laundry +6%, LUP held": {
            "multiplier": 1.0,
            "changes": {
                ("Home Care", "Laundry Powder"): {"type": "percent", "value": 0.06},
                ("Home Care", "Laundry Powder", "Freshgen", "FRG-LP-090"): {"type": "percent", "value": 0.0},
            },
        },
        "PC +5%, elasticity ×1.25": {"multiplier": 1.25, "changes": copy.deepcopy(pc_up)},
    }


def _load_into_state(df: pd.DataFrame, source: str, examples: bool) -> None:
    ss = st.session_state
    ss.raw_df = df
    ss.data_source = source
    ss.data_version = ss.get("data_version", -1) + 1
    ss.scenarios = _example_scenarios() if examples else {"Scenario 1": {"multiplier": 1.0, "changes": {}}}
    ss.active_scenario = next(iter(ss.scenarios))
    ss.elasticity_overrides = {}


def init_state() -> None:
    ss = st.session_state
    if "raw_df" not in ss:
        _load_into_state(load_dataset(SAMPLE_DATA_PATH), SAMPLE_SOURCE_LABEL, examples=True)
    ss.setdefault("uploader_nonce", 0)
    ss.setdefault("last_upload_id", None)
    ss.setdefault("upload_error", None)


def _toast(message: str) -> None:
    st.session_state["_toast"] = message


def scenario_objects() -> list[Scenario]:
    """Convert UI state (plain dicts keyed by path tuples) into engine objects."""
    out = []
    for name, spec in st.session_state.scenarios.items():
        changes = tuple(
            PriceChange(Node(path), ChangeType(c["type"]), float(c["value"])) for path, c in spec["changes"].items()
        )
        out.append(Scenario(name, changes, float(spec["multiplier"])))
    return out


# ======================================================================================
# Formatting
# ======================================================================================
def money_label(currency: str, scale: tuple[float, str]) -> str:
    return " ".join(part for part in (currency, scale[1]) if part) or "money"


def fmt_money(value, scale: tuple[float, str], signed: bool = False) -> str:
    if value is None or not np.isfinite(value):
        return "—"
    divisor = scale[0]
    decimals = 0 if divisor == 1 else 2
    return format(value / divisor, f"{'+' if signed else ''},.{decimals}f")


def fmt_units(value, signed: bool = False) -> str:
    if value is None or not np.isfinite(value):
        return "—"
    return format(value, f"{'+' if signed else ''},.0f")


def fmt_pct(value, signed: bool = False, decimals: int = 2) -> str:
    if value is None or not np.isfinite(value):
        return "—"
    return format(value, f"{'+' if signed else ''}.{decimals}%")


def fmt_pp(value) -> str:
    if value is None or not np.isfinite(value):
        return "—"
    return f"{value * 100:+.2f} pp"


def fmt_elasticity(value) -> str:
    return "—" if value is None or not np.isfinite(value) else f"{value:+.2f}"


def describe_change(change: dict, currency: str) -> str:
    return PriceChange(Node(), ChangeType(change["type"]), float(change["value"])).describe(currency)


def fmt_metric_value(metric: str, value, scale) -> str:
    if metric == "units":
        return fmt_units(value)
    if metric == "gm":
        return fmt_pct(value)
    return fmt_money(value, scale)


def fmt_metric_delta(metric: str, value, scale) -> str:
    if metric == "units":
        return fmt_units(value, signed=True)
    if metric == "gm":
        return fmt_pp(value)
    return fmt_money(value, scale, signed=True)


def style_deltas(df: pd.DataFrame):
    """Colour signed delta strings green/red."""
    delta_cols = [c for c in df.columns if "Δ" in c]

    def colour(value):
        if not isinstance(value, str):
            return ""
        if value.startswith("+") and value.strip("+0.,% p") != "":
            return f"color: {POSITIVE}"
        if value.startswith("-") and value.strip("-0.,% p") != "":
            return f"color: {NEGATIVE}"
        return ""

    return df.style.map(colour, subset=delta_cols)


# ======================================================================================
# Callbacks (run before the script reruns, so results always reflect the latest input)
# ======================================================================================
def _scenario_name_error(name: str) -> str | None:
    if not name:
        return "Enter a scenario name."
    if name in st.session_state.scenarios:
        return f"A scenario called '{name}' already exists."
    if name.lower() in RESERVED_SCENARIO_NAMES:
        return f"'{name}' is reserved; pick another name."
    if len(name) > 60:
        return "Keep scenario names under 60 characters."
    return None


def _cb_add_scenario() -> None:
    ss = st.session_state
    name = (ss.get("new_scenario_name") or "").strip()
    error = _scenario_name_error(name)
    if error:
        _toast(error)
        return
    ss.scenarios[name] = {"multiplier": 1.0, "changes": {}}
    ss.active_scenario = name
    ss.new_scenario_name = ""
    _toast(f"Added scenario '{name}'.")


def _cb_duplicate_scenario() -> None:
    ss = st.session_state
    source = ss.active_scenario
    name, k = f"{source} (copy)", 2
    while name in ss.scenarios:
        name, k = f"{source} (copy {k})", k + 1
    ss.scenarios[name] = copy.deepcopy(ss.scenarios[source])
    ss.active_scenario = name
    _toast(f"Duplicated '{source}' as '{name}'.")


def _cb_rename_scenario() -> None:
    ss = st.session_state
    old = ss.active_scenario
    new = (ss.get("rename_scenario_name") or "").strip()
    error = _scenario_name_error(new)
    if error:
        _toast(error)
        return
    ss.scenarios = {(new if key == old else key): value for key, value in ss.scenarios.items()}
    ss.active_scenario = new
    ss.rename_scenario_name = ""
    _toast(f"Renamed '{old}' to '{new}'.")


def _cb_delete_scenario() -> None:
    ss = st.session_state
    if len(ss.scenarios) <= 1:
        return
    name = ss.active_scenario
    del ss.scenarios[name]
    ss.active_scenario = next(iter(ss.scenarios))
    _toast(f"Deleted scenario '{name}'.")


def _cb_set_multiplier(scenario: str, key: str) -> None:
    st.session_state.scenarios[scenario]["multiplier"] = float(st.session_state[key])


def _cb_set_price_change(scenario: str, path: tuple, type_key: str, pct_key: str, abs_key: str) -> None:
    ss = st.session_state
    change_type = ss.get(type_key, "percent")
    if change_type == "percent":
        value = float(ss.get(pct_key, 0.0)) / 100.0
    else:
        value = float(ss.get(abs_key, 0.0))
    change = {"type": change_type, "value": value}
    ss.scenarios[scenario]["changes"][tuple(path)] = change
    _toast(f"{scenario}: {describe_change(change, '')} set at {Node(path).label}.")


def _cb_clear_price_change(scenario: str, path: tuple) -> None:
    st.session_state.scenarios[scenario]["changes"].pop(tuple(path), None)
    _toast(f"{scenario}: price change cleared at {Node(path).label}.")


def _cb_remove_changes(scenario: str, key: str) -> None:
    ss = st.session_state
    selected = set(ss.get(key) or [])
    changes = ss.scenarios[scenario]["changes"]
    for path in list(changes):
        if Node(path).key in selected:
            del changes[path]
    ss[key] = []


def _cb_set_override(path: tuple, key: str) -> None:
    st.session_state.elasticity_overrides[tuple(path)] = float(st.session_state[key])
    _toast(f"Elasticity override set at {Node(path).label}.")


def _cb_clear_override(path: tuple) -> None:
    st.session_state.elasticity_overrides.pop(tuple(path), None)
    _toast(f"Elasticity override cleared at {Node(path).label}.")


def _cb_clear_all_overrides() -> None:
    st.session_state.elasticity_overrides = {}
    _toast("All elasticity overrides cleared.")


def _cb_reset_sample() -> None:
    ss = st.session_state
    _load_into_state(load_dataset(SAMPLE_DATA_PATH), SAMPLE_SOURCE_LABEL, examples=True)
    ss.uploader_nonce += 1
    ss.last_upload_id = None
    ss.upload_error = None
    _toast("Sample data and example scenarios restored.")


# ======================================================================================
# Sidebar
# ======================================================================================
def render_data_loader() -> None:
    ss = st.session_state
    st.header("Data")
    st.caption(f"Source: {ss.data_source}")
    upload = st.file_uploader(
        "Load your own data (CSV or JSON)",
        type=["csv", "json"],
        key=f"uploader_{ss.uploader_nonce}",
        help="One row per PPG. See the README for the columns. Loading resets scenarios and overrides.",
    )
    if upload is not None:
        upload_id = f"{upload.name}:{upload.size}:{getattr(upload, 'file_id', '')}"
        if upload_id != ss.last_upload_id:
            ss.last_upload_id = upload_id
            try:
                df = load_dataset(upload, filename=upload.name)
                validate_dataset(df)  # fail fast if the file is structurally unusable
            except (ValueError, KeyError) as exc:
                ss.upload_error = f"Could not load {upload.name}: {exc}"
            else:
                ss.upload_error = None
                _load_into_state(df, upload.name, examples=False)
                _toast(f"Loaded {len(df)} rows from {upload.name}. Scenarios and overrides were reset.")
        if ss.upload_error:
            st.error(ss.upload_error)
    left, right = st.columns(2)
    left.button("Use sample", on_click=_cb_reset_sample, key="btn_reset_sample", help="Reload the bundled sample data and example scenarios.", **WIDE)
    right.download_button(
        "Template",
        SAMPLE_DATA_PATH.read_bytes(),
        file_name="pricing_input_template.csv",
        mime="text/csv",
        key="dl_template",
        **WIDE,
    )


def render_assumptions() -> tuple[SimulationConfig, str, tuple[float, str]]:
    st.header("Assumptions")
    basis_values = [basis.value for basis in TOBasis]
    basis = TOBasis(
        st.selectbox(
            "TO (turnover) definition",
            basis_values,
            index=basis_values.index(DEFAULTS.to_basis.value),
            format_func=lambda v: TO_DEFINITIONS[TOBasis(v)].label,
            key="to_basis",
            help="Conventions differ between companies; pick yours. The default is a placeholder.",
        )
    )
    st.caption(f"{TO_DEFINITIONS[basis].formula}. {TO_DEFINITIONS[basis].description}")
    default_e = st.number_input(
        "Default elasticity (placeholder)",
        value=float(DEFAULTS.default_elasticity),
        step=0.1,
        format="%.2f",
        key="default_elasticity",
        help="Used only for PPGs with no elasticity, and only if the box below is ticked.",
    )
    if default_e > 0:
        st.warning("The default elasticity is positive: volume would rise with price.")
    apply_default = st.checkbox(
        "Apply default where elasticity is missing",
        value=DEFAULTS.apply_default_elasticity,
        key="apply_default",
        help="Off by default: PPGs without an elasticity are flagged and excluded rather than silently defaulted.",
    )
    st.divider()
    currency = st.text_input("Currency label", value=DEFAULT_CURRENCY, key="currency").strip()
    scale_name = st.selectbox(
        "Show money in", list(MONEY_SCALES), index=list(MONEY_SCALES).index(DEFAULT_SCALE), key="money_scale"
    )
    config = SimulationConfig(
        to_basis=basis, default_elasticity=float(default_e), apply_default_elasticity=bool(apply_default)
    )
    return config, currency, MONEY_SCALES[scale_name]


def render_navigator(clean_df: pd.DataFrame, tree: dict, names: dict) -> Node:
    """Cascading selectors: stop at any level to select that whole node."""
    st.header("Hierarchy")
    ss = st.session_state
    path: list[str] = []
    stopped = False
    for depth, col in enumerate(HIERARCHY_COLUMNS):
        key = f"nav_{col}"
        if stopped:
            options = [ALL]
        else:
            options = [ALL] + [child.path[-1] for child in children(clean_df, Node(tuple(path)), tree)]
        if ss.get(key) not in options:
            ss[key] = ALL
        is_ppg = col == "ppg_id"

        def label(value, plural=LEVEL_PLURALS[depth], is_ppg=is_ppg):
            if value == ALL:
                return f"All {plural if is_ppg else plural.lower()}"
            return f"{names.get(value, value)} · {value}" if is_ppg else value

        choice = st.selectbox(LEVEL_LABELS[depth], options, key=key, disabled=stopped, format_func=label)
        if choice == ALL:
            stopped = True
        else:
            path.append(choice)
    node = Node(tuple(path))
    st.caption(f"Selected **{node.level_label}**: {node.label}. Inputs apply to every PPG beneath it.")
    return node


# ======================================================================================
# Charts
# ======================================================================================
def waterfall_fig(title: str, base, price_effect, volume_effect, new, scale, money_lbl: str):
    values = [base, price_effect, volume_effect, new]
    if not all(np.isfinite(v) for v in values):
        return None
    divisor = scale[0]
    y = [v / divisor for v in values]
    d = 0 if divisor == 1 else 2
    text = [f"{y[0]:,.{d}f}", f"{y[1]:+,.{d}f}", f"{y[2]:+,.{d}f}", f"{y[3]:,.{d}f}"]
    fig = go.Figure(
        go.Waterfall(
            measure=["absolute", "relative", "relative", "total"],
            x=["Base", "Price effect", "Volume effect", "Scenario"],
            y=y,
            text=text,
            textposition="outside",
            cliponaxis=False,
            connector={"line": {"color": "#95A5A6", "width": 1}},
            increasing={"marker": {"color": POSITIVE}},
            decreasing={"marker": {"color": NEGATIVE}},
            totals={"marker": {"color": TOTAL}},
            hovertemplate="%{x}: %{text}<extra></extra>",
        )
    )
    levels = [y[0], y[0] + y[1], y[3]]
    low, high = min(levels), max(levels)
    span = (high - low) or abs(high) * 0.05 or 1.0
    lower = max(0.0, low - 1.5 * span) if low >= 0 else low - 0.5 * span
    fig.update_layout(
        title=title,
        height=360,
        margin=dict(l=10, r=10, t=50, b=10),
        showlegend=False,
        yaxis=dict(title=money_lbl, range=[lower, high + 0.6 * span]),
    )
    return fig


def drivers_fig(title: str, names, price, volume, net, scale, money_lbl: str):
    divisor = scale[0]
    fig = go.Figure()
    fig.add_bar(name="Price effect", x=names, y=[v / divisor for v in price], marker_color=PRICE_COLOR)
    fig.add_bar(name="Volume effect", x=names, y=[v / divisor for v in volume], marker_color=VOLUME_COLOR)
    fig.add_scatter(
        name="Net change",
        x=names,
        y=[v / divisor for v in net],
        mode="markers",
        marker=dict(symbol="diamond", size=13, color=TOTAL),
    )
    fig.add_hline(y=0, line_width=1, line_color="#7F8C8D")
    fig.update_layout(
        title=title,
        barmode="relative",
        height=360,
        margin=dict(l=10, r=10, t=50, b=10),
        yaxis_title=money_lbl,
        legend=dict(orientation="h", y=-0.15),
    )
    return fig


def breakdown_fig(labels, d_nsv, d_gp, scale, money_lbl: str):
    divisor = scale[0]
    fig = go.Figure()
    fig.add_bar(name="Δ NSV", y=labels, x=np.asarray(d_nsv, float) / divisor, orientation="h", marker_color=PRICE_COLOR)
    fig.add_bar(name="Δ GP", y=labels, x=np.asarray(d_gp, float) / divisor, orientation="h", marker_color=POSITIVE)
    fig.update_layout(
        barmode="group",
        height=max(280, 100 + 46 * len(labels)),
        margin=dict(l=10, r=10, t=45, b=10),
        xaxis_title=money_lbl,
        yaxis=dict(autorange="reversed"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
    )
    return fig


# ======================================================================================
# Simulate tab
# ======================================================================================
def render_scenario_manager(names: dict, currency: str) -> None:
    ss = st.session_state
    st.markdown("**Scenarios**")
    scenario_names = list(ss.scenarios)
    if ss.get("active_scenario") not in scenario_names:
        ss.active_scenario = scenario_names[0]
    st.selectbox("Scenario being edited", scenario_names, key="active_scenario")
    active = ss.active_scenario
    spec = ss.scenarios[active]
    mult_key = f"mult_{ss.data_version}_{active}"
    st.number_input(
        "Elasticity sensitivity multiplier",
        min_value=0.1,
        max_value=5.0,
        step=0.05,
        value=float(spec["multiplier"]),
        key=mult_key,
        on_change=_cb_set_multiplier,
        args=(active, mult_key),
        help="Scales every elasticity in this scenario only (1.25 = 25% more price-sensitive). For stress tests.",
    )
    with st.expander("Add, copy, rename or delete"):
        st.text_input("New scenario name", key="new_scenario_name", placeholder="e.g. +4% mainstream packs")
        c1, c2 = st.columns(2)
        c1.button("Add blank", on_click=_cb_add_scenario, key="btn_add_scenario", **WIDE)
        c2.button("Duplicate current", on_click=_cb_duplicate_scenario, key="btn_dup_scenario", **WIDE)
        st.text_input("Rename current to", key="rename_scenario_name")
        c3, c4 = st.columns(2)
        c3.button("Rename", on_click=_cb_rename_scenario, key="btn_rename_scenario", **WIDE)
        c4.button(
            "Delete current",
            on_click=_cb_delete_scenario,
            key="btn_delete_scenario",
            disabled=len(scenario_names) <= 1,
            **WIDE,
        )

    changes = spec["changes"]
    if not changes:
        st.caption("No price changes in this scenario yet. Pick a node in the sidebar and set one.")
        return
    rows = [
        {"Node": display_name(Node(p), names), "Level": Node(p).level_label, "Change": describe_change(c, currency)}
        for p, c in changes.items()
    ]
    st.dataframe(pd.DataFrame(rows), hide_index=True, key=f"tbl_changes_{active}", **WIDE)
    labels = {Node(p).key: f"{Node(p).level_label}: {display_name(Node(p), names)}" for p in changes}
    remove_key = f"rm_{ss.data_version}_{active}"
    st.multiselect("Remove price changes", list(labels), format_func=labels.get, key=remove_key)
    st.button("Remove selected", on_click=_cb_remove_changes, args=(active, remove_key), key="btn_remove_changes")


def render_price_input(node: Node, results, currency: str) -> None:
    ss = st.session_state
    active = ss.active_scenario
    st.markdown(f"**Price change** · *{active}*")
    existing = ss.scenarios[active]["changes"].get(node.path)
    suffix = f"{ss.data_version}_{active}_{node.key}"
    type_key, pct_key, abs_key = f"pctype_{suffix}", f"pcpct_{suffix}", f"pcabs_{suffix}"
    change_type = st.radio(
        "Change type",
        ["percent", "absolute"],
        index=0 if existing is None or existing["type"] == "percent" else 1,
        format_func=lambda v: "% of list price" if v == "percent" else f"{currency or 'Currency'} per unit",
        key=type_key,
        horizontal=True,
    )
    if change_type == "percent":
        default = existing["value"] * 100 if existing and existing["type"] == "percent" else 0.0
        st.number_input(
            "Price change (%)",
            min_value=-99.0,
            max_value=500.0,
            value=float(default),
            step=0.5,
            format="%.2f",
            key=pct_key,
            help="Applied to the list price of every PPG under this node.",
        )
    else:
        default = existing["value"] if existing and existing["type"] == "absolute" else 0.0
        st.number_input(
            f"Price change ({currency or 'currency'} per unit, on list price)",
            value=float(default),
            step=0.25,
            format="%.2f",
            key=abs_key,
            help="Added to the list price of every PPG under this node. A cut that takes any price to ≤ 0 is rejected.",
        )
    c1, c2 = st.columns(2)
    c1.button(
        "Set for this node",
        type="primary",
        on_click=_cb_set_price_change,
        args=(active, node.path, type_key, pct_key, abs_key),
        key="btn_set_price",
        **WIDE,
    )
    c2.button(
        "Clear at this node",
        on_click=_cb_clear_price_change,
        args=(active, node.path),
        disabled=existing is None,
        key="btn_clear_price",
        **WIDE,
    )
    if existing:
        st.caption(f"Set at this node: **{describe_change(existing, currency)}**")
    result = next((r for r in results if r.name == active), None)
    if result is not None and result.valid:
        agg = aggregate(result.ppg, node).iloc[0]
        if node.is_leaf:
            source = result.ppg.loc[node_mask(result.ppg, node), "price_change_source"]
            if len(source):
                st.caption(f"In effect for this PPG: {source.iloc[0]}")
        st.caption(
            f"Average net price change under this node: **{fmt_pct(agg['avg_price_change'], signed=True)}** "
            "(base-volume weighted). More specific settings at lower levels take precedence."
        )


def render_elasticity_input(node: Node, base_df: pd.DataFrame, config: SimulationConfig) -> None:
    ss = st.session_state
    st.markdown("**Elasticity** · shared by all scenarios")
    sub = base_df[node_mask(base_df, node)]
    current = math.nan
    if node.is_leaf and len(sub):
        row = sub.iloc[0]
        current = row["elasticity_used"]
        st.metric("Elasticity in use", fmt_elasticity(current))
        detail = f"Source: {row['elasticity_source_used']}"
        if node.path in ss.elasticity_overrides or row["elasticity_source_used"].startswith("Override"):
            detail += f" · value in data: {fmt_elasticity(row['elasticity'])}"
        st.caption(detail)
    elif len(sub):
        weight_ok = sub["elasticity_used"].notna() & (sub["base_nsv"] > 0)
        if weight_ok.any():
            w = sub.loc[weight_ok, "base_nsv"]
            current = float((sub.loc[weight_ok, "elasticity_used"] * w).sum() / w.sum())
        st.metric("NSV-weighted elasticity", fmt_elasticity(current))
        known = sub["elasticity_used"].dropna()
        missing = int(sub["elasticity_used"].isna().sum())
        if len(known):
            st.caption(
                f"Range {known.min():+.2f} to {known.max():+.2f} across {len(known)} PPG(s)"
                + (f"; {missing} missing." if missing else ".")
            )
    existing = ss.elasticity_overrides.get(node.path)
    if existing is not None:
        start = existing
    elif np.isfinite(current):
        start = current
    else:
        start = config.default_elasticity
    key = f"el_{ss.data_version}_{node.key}"
    value = st.number_input(
        "Override for this node",
        value=float(round(start, 2)),
        step=0.05,
        format="%.2f",
        key=key,
        help="Applies to every PPG under this node in every scenario. A more specific override wins.",
    )
    if value > 0:
        st.warning("Positive elasticity: volume would rise with price. Allowed, but check it.")
    c1, c2 = st.columns(2)
    c1.button("Set override", on_click=_cb_set_override, args=(node.path, key), key="btn_set_override", **WIDE)
    c2.button(
        "Clear override",
        on_click=_cb_clear_override,
        args=(node.path,),
        disabled=existing is None,
        key="btn_clear_override",
        **WIDE,
    )
    n_overrides = len(ss.elasticity_overrides)
    if n_overrides:
        st.caption(f"{n_overrides} override(s) active across the hierarchy.")
        st.button("Clear all overrides", on_click=_cb_clear_all_overrides, key="btn_clear_all_overrides")


def comparison_display(long: pd.DataFrame, currency: str, scale) -> pd.DataFrame:
    lbl = money_label(currency, scale)
    labels = {
        "price": "Avg. net price change",
        "units": "Volume (units)",
        "nsv": f"NSV ({lbl})",
        "to": f"TO ({lbl})",
        "gp": f"GP ({lbl})",
        "gm": "GM %",
    }
    rows = []
    for metric in ("price", "units", "nsv", "to", "gp", "gm"):
        sub = long[long["metric"] == metric]
        if sub.empty:
            continue
        row = {"Metric": labels[metric], "Base": "—" if metric == "price" else fmt_metric_value(metric, sub["base"].iloc[0], scale)}
        for r in sub.itertuples(index=False):
            name = r.scenario
            if not r.scenario_valid:
                row[name], row[f"{name} · Δ"], row[f"{name} · Δ%"] = "invalid", "", ""
            elif metric == "price":
                row[name], row[f"{name} · Δ"], row[f"{name} · Δ%"] = fmt_pct(r.value, signed=True), "", ""
            else:
                row[name] = fmt_metric_value(metric, r.value, scale)
                row[f"{name} · Δ"] = fmt_metric_delta(metric, r.delta_abs, scale)
                row[f"{name} · Δ%"] = fmt_pct(r.delta_pct, signed=True)
        rows.append(row)
    return pd.DataFrame(rows)


def render_results(node: Node, names: dict, results, config: SimulationConfig, currency: str, scale) -> None:
    ss = st.session_state
    lbl = money_label(currency, scale)
    node_name = display_name(node, names)

    for result in results:
        errors = [i.message for i in result.issues if i.severity is Severity.ERROR]
        warnings = [i.message for i in result.issues if i.severity is Severity.WARNING]
        if errors:
            st.error(f"**{result.name}** can't be calculated: " + " ".join(errors))
        if warnings:
            with st.expander(f"{result.name}: {len(warnings)} warning(s)"):
                for message in warnings:
                    st.markdown(f"- {message}")

    st.markdown(f"#### Scenario comparison · {node.level_label}: {node_name}")
    definition = TO_DEFINITIONS[config.to_basis]
    st.caption(
        f"TO = {definition.label} ({definition.formula}). Money in {lbl}. "
        "GM % deltas are percentage points (pp); Δ% is the relative change. Base is identical across scenarios."
    )
    long = comparison_long(results, node)
    st.dataframe(style_deltas(comparison_display(long, currency, scale)), hide_index=True, key="tbl_comparison", **WIDE)

    valid_results = [r for r in results if r.valid]
    if not valid_results:
        st.info("No valid scenario to chart yet.")
        return

    # ---- price vs volume drivers across scenarios ----------------------------------
    bridges = {r.name: bridge(r.ppg, node) for r in valid_results}
    chart_names = list(bridges)
    c1, c2 = st.columns(2)
    c1.plotly_chart(
        drivers_fig(
            "NSV change: price vs volume effect",
            chart_names,
            [b["nsv_price_effect"] for b in bridges.values()],
            [b["nsv_volume_effect"] for b in bridges.values()],
            [b["new_nsv"] - b["base_nsv"] for b in bridges.values()],
            scale,
            lbl,
        ),
        key="fig_drivers_nsv",
        **WIDE,
    )
    c2.plotly_chart(
        drivers_fig(
            "GP change: price vs volume effect",
            chart_names,
            [b["gp_price_effect"] for b in bridges.values()],
            [b["gp_volume_effect"] for b in bridges.values()],
            [b["new_gp"] - b["base_gp"] for b in bridges.values()],
            scale,
            lbl,
        ),
        key="fig_drivers_gp",
        **WIDE,
    )

    # ---- bridge for one scenario ----------------------------------------------------
    if ss.get("chart_scenario") not in chart_names:
        ss.chart_scenario = ss.active_scenario if ss.active_scenario in chart_names else chart_names[0]
    selected = st.selectbox("Scenario for bridge and breakdown", chart_names, key="chart_scenario")
    result = next(r for r in valid_results if r.name == selected)
    b = bridges[selected]
    c3, c4 = st.columns(2)
    nsv_fig = waterfall_fig(f"NSV bridge · {selected}", b["base_nsv"], b["nsv_price_effect"], b["nsv_volume_effect"], b["new_nsv"], scale, lbl)
    gp_fig = waterfall_fig(f"GP bridge · {selected}", b["base_gp"], b["gp_price_effect"], b["gp_volume_effect"], b["new_gp"], scale, lbl)
    if nsv_fig is not None:
        c3.plotly_chart(nsv_fig, key="fig_bridge_nsv", **WIDE)
    if gp_fig is not None:
        c4.plotly_chart(gp_fig, key="fig_bridge_gp", **WIDE)
    st.caption(
        "Price effect = (new net price − base net price) × new units. "
        "Volume effect = (new units − base units) × base net price (NSV) or × base unit margin (GP). "
        "The y-axis is zoomed and does not start at zero."
    )

    # ---- breakdown by the next level down -------------------------------------------
    if not node.is_leaf:
        child_depth = node.depth + 1
        agg = aggregate(result.ppg, node, group_depth=child_depth)
        child_nodes = [Node(tuple(str(v) for v in row)) for row in agg[list(HIERARCHY_COLUMNS[:child_depth])].itertuples(index=False, name=None)]
        agg["label"] = [display_name(n, names) for n in child_nodes]
        st.markdown(f"#### Breakdown by {LEVEL_LABELS[child_depth - 1].lower()} · {selected}")
        c5, c6 = st.columns([1, 1.4])
        c5.plotly_chart(breakdown_fig(agg["label"].tolist(), agg["d_nsv"], agg["d_gp"], scale, lbl), key="fig_breakdown", **WIDE)
        table = pd.DataFrame(
            {
                LEVEL_LABELS[child_depth - 1]: agg["label"],
                "PPGs incl./total": [f"{i}/{n}" for i, n in zip(agg["n_included"], agg["n_ppgs"])],
                "Avg. price Δ": [fmt_pct(v, signed=True) for v in agg["avg_price_change"]],
                "Units Δ%": [fmt_pct(v, signed=True) for v in agg["pct_units"]],
                f"NSV ({lbl})": [fmt_money(v, scale) for v in agg["new_nsv"]],
                "NSV Δ": [fmt_money(v, scale, signed=True) for v in agg["d_nsv"]],
                "NSV Δ%": [fmt_pct(v, signed=True) for v in agg["pct_nsv"]],
                f"GP ({lbl})": [fmt_money(v, scale) for v in agg["new_gp"]],
                "GP Δ": [fmt_money(v, scale, signed=True) for v in agg["d_gp"]],
                "GM %": [fmt_pct(v) for v in agg["new_gm"]],
                "GM Δ": [fmt_pp(v) for v in agg["d_gm"]],
            }
        )
        c6.dataframe(style_deltas(table), hide_index=True, key="tbl_breakdown", **WIDE)

    # ---- PPG detail -----------------------------------------------------------------
    with st.expander(f"PPG-level detail · {selected}", expanded=node.is_leaf):
        ppg = add_derived(result.ppg[node_mask(result.ppg, node)])
        divisor = scale[0]
        detail = pd.DataFrame(
            {
                "PPG": ppg["ppg_id"],
                "Name": ppg["ppg_name"],
                "Status": np.where(ppg["is_valid"], "Included", "Excluded"),
                "Price change": ppg["price_change_source"],
                "Price Δ %": ppg["price_change_pct"] * 100,
                "Elasticity": ppg["elasticity_effective"],
                "Elasticity source": ppg["elasticity_source_used"],
                "Base list price": ppg["base_list_price"],
                "New list price": ppg["new_list_price"],
                "Base net price": ppg["base_net_price"],
                "New net price": ppg["new_net_price"],
                "Base units": ppg["base_units"],
                "New units": ppg["new_units"],
                "Units Δ %": ppg["pct_units"] * 100,
                f"Base NSV ({lbl})": ppg["base_nsv"] / divisor,
                f"New NSV ({lbl})": ppg["new_nsv"] / divisor,
                "NSV Δ %": ppg["pct_nsv"] * 100,
                f"Base GP ({lbl})": ppg["base_gp"] / divisor,
                f"New GP ({lbl})": ppg["new_gp"] / divisor,
                "Base GM %": ppg["base_gm"] * 100,
                "New GM %": ppg["new_gm"] * 100,
                "Flags": [" ".join(e + w) for e, w in zip(ppg["row_errors"], ppg["row_warnings"])],
            }
        )
        number = st.column_config.NumberColumn
        money_format = "%.0f" if divisor == 1 else "%.2f"
        config_cols = {
            "Price Δ %": number(format="%.2f%%"),
            "Elasticity": number(format="%.2f"),
            "Base list price": number(format="%.2f"),
            "New list price": number(format="%.2f"),
            "Base net price": number(format="%.2f"),
            "New net price": number(format="%.2f"),
            "Base units": number(format="%.0f"),
            "New units": number(format="%.0f"),
            "Units Δ %": number(format="%.2f%%"),
            f"Base NSV ({lbl})": number(format=money_format),
            f"New NSV ({lbl})": number(format=money_format),
            "NSV Δ %": number(format="%.2f%%"),
            f"Base GP ({lbl})": number(format=money_format),
            f"New GP ({lbl})": number(format=money_format),
            "Base GM %": number(format="%.2f%%"),
            "New GM %": number(format="%.2f%%"),
        }
        st.dataframe(detail, hide_index=True, column_config=config_cols, key="tbl_ppg_detail", **WIDE)

    # ---- export -----------------------------------------------------------------------
    st.markdown("#### Export (CSV)")
    st.caption("Money is exported in raw currency units; percentages and rates as decimals (0.05 = 5%).")
    e1, e2, e3 = st.columns(3)
    e1.download_button(
        "PPG-level results · all scenarios",
        to_csv_bytes(export_ppg_results(results, config)),
        file_name="pricing_sim_ppg_results.csv",
        mime="text/csv",
        key="dl_ppg_results",
        **WIDE,
    )
    e2.download_button(
        "Comparison for this node",
        to_csv_bytes(long),
        file_name=f"pricing_sim_comparison_{node.key.replace('|', '_')}.csv",
        mime="text/csv",
        key="dl_comparison",
        **WIDE,
    )
    e3.download_button(
        "Every node, every level · all scenarios",
        to_csv_bytes(export_node_summary(results, names)),
        file_name="pricing_sim_node_summary.csv",
        mime="text/csv",
        key="dl_node_summary",
        **WIDE,
    )


def render_simulate(node: Node, names: dict, base_df: pd.DataFrame, results, config: SimulationConfig, currency: str, scale) -> None:
    in_node = base_df[node_mask(base_df, node)]
    n_total, n_ok = len(in_node), int(in_node["is_valid"].sum())
    st.subheader(f"{node.level_label}: {display_name(node, names)}")
    st.caption(f"{node.label} · {n_total} PPG(s)")
    if n_ok < n_total:
        st.warning(
            f"{n_total - n_ok} of {n_total} PPGs here are excluded from every scenario because of data errors. "
            "See **Data & validation** to fix them."
        )
    col_scenarios, col_price, col_elasticity = st.columns([1.15, 1, 1], gap="large")
    with col_scenarios:
        render_scenario_manager(names, currency)
    with col_price:
        render_price_input(node, results, currency)
    with col_elasticity:
        render_elasticity_input(node, base_df, config)
    st.divider()
    render_results(node, names, results, config, currency, scale)


# ======================================================================================
# Hierarchy tab
# ======================================================================================
def render_tree(base_df: pd.DataFrame, results, names: dict, currency: str, scale) -> None:
    ss = st.session_state
    st.subheader("Hierarchy tree")
    st.caption(
        "Every node with base economics, the elasticity in use (NSV-weighted for parents) and the selected "
        "scenario's effect. ◆ marks an elasticity override set at that node. Use the sidebar to select a node."
    )
    scenario_names = [r.name for r in results]
    if ss.get("tree_scenario") not in scenario_names:
        ss.tree_scenario = ss.active_scenario if ss.active_scenario in scenario_names else scenario_names[0]
    selected = st.selectbox("Show changes for scenario", scenario_names, key="tree_scenario")
    result = next(r for r in results if r.name == selected)
    summary = node_summary(result.ppg)
    leaf_info = {
        row.ppg_id: row
        for row in base_df[["ppg_id", "elasticity_used", "elasticity_source_used", "row_errors", "row_warnings"]].itertuples(index=False)
    }
    changes = ss.scenarios[selected]["changes"]
    lbl = money_label(currency, scale)
    rows = []
    for r in summary.itertuples(index=False):
        node = Node(tuple(r.path))
        indent = " " * max(node.depth - 1, 0)
        marker = "" if node.is_root else ("• " if node.is_leaf else "▸ ")
        leaf = leaf_info.get(node.path[-1]) if node.is_leaf else None
        elasticity = leaf.elasticity_used if leaf is not None else r.weighted_elasticity
        if leaf is not None:
            source = leaf.elasticity_source_used
            flags = " ".join(list(leaf.row_errors) + list(leaf.row_warnings))
        else:
            source = "NSV-weighted average"
            excluded = r.n_ppgs - r.n_included
            flags = f"{excluded} PPG(s) excluded" if excluded else ""
        override = ss.elasticity_overrides.get(node.path)
        rows.append(
            {
                "Node": f"{indent}{marker}{display_name(node, names)}",
                "Level": node.level_label,
                "PPGs incl./total": f"{r.n_included}/{r.n_ppgs}",
                f"Base NSV ({lbl})": fmt_money(r.base_nsv, scale),
                "Base GM %": fmt_pct(r.base_gm),
                "Elasticity": fmt_elasticity(elasticity),
                "Elasticity source": source,
                "Override": f"◆ {override:+.2f}" if override is not None else "",
                "Price change set here": describe_change(changes[node.path], currency) if node.path in changes else "",
                "Avg. price Δ": fmt_pct(r.avg_price_change, signed=True),
                "NSV Δ": fmt_money(r.d_nsv, scale, signed=True),
                "GP Δ": fmt_money(r.d_gp, scale, signed=True),
                "GM Δ": fmt_pp(r.d_gm),
                "Data issues": flags,
            }
        )
    st.dataframe(
        style_deltas(pd.DataFrame(rows)), hide_index=True, height=min(760, 40 + 35 * len(rows)), key="tbl_tree", **WIDE
    )
    with st.expander("Sunburst of base NSV"):
        valid = base_df[base_df["is_valid"] & (base_df["base_nsv"] > 0)]
        if len(valid):
            fig = px.sunburst(valid, path=["category", "sub_category", "brand", "ppg_name"], values="base_nsv")
            fig.update_traces(hovertemplate="%{label}<br>Base NSV: %{value:,.0f}<extra></extra>")
            fig.update_layout(height=560, margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(fig, key="fig_sunburst", **WIDE)


# ======================================================================================
# Data tab
# ======================================================================================
COLUMN_HELP = {
    "category": "Level 1 of the hierarchy.",
    "sub_category": "Level 2.",
    "brand": "Level 3.",
    "ppg_id": "Level 4: unique PPG id.",
    "ppg_name": "Readable PPG name.",
    "base_units": "Base-period volume in units (e.g. last 52 weeks).",
    "base_list_price": "Base list price per unit (the price changes apply to).",
    "on_invoice_disc_rate": "On-invoice discount as a fraction of list price (0.05 = 5%).",
    "off_invoice_trade_rate": "Off-invoice trade spend as a fraction of list price.",
    "unit_cogs": "Cost of goods sold per unit (held constant in scenarios).",
    "elasticity": "Own-price elasticity (normally negative).",
    "elasticity_source": "Where the elasticity came from.",
    "base_rsp": "Consumer retail selling price per unit (needed only for TO = consumer offtake).",
}


def render_data_editor() -> pd.DataFrame:
    ss = st.session_state
    st.subheader("Base inputs per PPG")
    st.caption(
        "One row per PPG. Edit cells directly; every tab recalculates immediately. Blank cells are flagged, "
        "never silently filled. Download the edited table to keep your changes."
    )
    column_config = {
        col: st.column_config.Column(col, help=text) for col, text in COLUMN_HELP.items() if col in ss.raw_df.columns
    }
    edited = st.data_editor(
        ss.raw_df,
        key=f"editor_{ss.data_version}",
        num_rows="dynamic",
        hide_index=True,
        column_config=column_config,
        **WIDE,
    )
    st.download_button(
        "Download edited data (CSV)", to_csv_bytes(edited), file_name="pricing_input_edited.csv", mime="text/csv", key="dl_edited"
    )
    return edited


def render_issues(issues) -> None:
    st.subheader("Validation")
    order = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2}
    counts = {severity: sum(1 for i in issues if i.severity is severity) for severity in order}
    c1, c2, c3 = st.columns(3)
    c1.metric("Errors (PPG excluded)", counts[Severity.ERROR])
    c2.metric("Warnings (calculated, check)", counts[Severity.WARNING])
    c3.metric("Assumptions applied", counts[Severity.INFO])
    if not issues:
        st.success("No issues found.")
        return
    table = pd.DataFrame(
        [
            {
                "Severity": i.severity.value.upper(),
                "PPG": i.ppg_id or "—",
                "Field": i.field or "—",
                "Message": i.message,
                "_order": order[i.severity],
            }
            for i in issues
        ]
    ).sort_values(["_order", "PPG"], kind="stable")
    shown = st.multiselect("Show", ["ERROR", "WARNING", "INFO"], default=["ERROR", "WARNING", "INFO"], key="issue_filter")
    st.dataframe(table[table["Severity"].isin(shown)].drop(columns="_order"), hide_index=True, key="tbl_issues", **WIDE)


# ======================================================================================
# Main
# ======================================================================================
def main() -> None:
    init_state()
    ss = st.session_state
    sb_data, sb_nav, sb_settings = st.sidebar.container(), st.sidebar.container(), st.sidebar.container()
    with sb_data:
        render_data_loader()
    with sb_settings:
        config, currency, scale = render_assumptions()

    st.title("RGM Pricing Simulator")
    status_line = st.empty()  # filled once the data has been validated
    if "_toast" in ss:
        st.toast(ss.pop("_toast"))
    tab_sim, tab_tree, tab_data, tab_help = st.tabs(["Simulate", "Hierarchy", "Data & validation", "Formulas"])

    # The data tab is filled first so edits flow into this run's calculations.
    with tab_data:
        working_df = render_data_editor()
    try:
        clean_df, data_issues = validate_dataset(working_df, config)
    except SchemaError as exc:
        with tab_sim:
            st.error(f"The dataset can't be used: {exc}")
        st.stop()

    names = ppg_names(clean_df)
    tree = build_tree(clean_df)
    with sb_nav:
        node = render_navigator(clean_df, tree, names)

    overrides = [ElasticityOverride(Node(path), value) for path, value in ss.elasticity_overrides.items()]
    base_df, elasticity_issues = prepare_base(clean_df, overrides, config)
    results = [simulate(base_df, scenario, config) for scenario in scenario_objects()]

    status_line.caption(
        f"{ss.data_source} · {len(base_df)} PPGs ({int(base_df['is_valid'].sum())} included) · "
        f"TO = {TO_DEFINITIONS[config.to_basis].label} · {len(results)} scenario(s)"
    )
    with tab_data:
        render_issues(data_issues + elasticity_issues)
    with tab_sim:
        render_simulate(node, names, base_df, results, config, currency, scale)
    with tab_tree:
        render_tree(base_df, results, names, currency, scale)
    with tab_help:
        if FORMULAS_PATH.exists():
            st.markdown(FORMULAS_PATH.read_text(encoding="utf-8"))
        else:
            st.info("docs/FORMULAS.md not found.")


main()
