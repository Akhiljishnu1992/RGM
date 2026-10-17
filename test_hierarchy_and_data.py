"""Hierarchy navigation and the bundled sample dataset."""
from __future__ import annotations

import pandas as pd
import pytest

from pricing_engine import (
    Node,
    Severity,
    SimulationConfig,
    build_tree,
    children,
    iter_nodes,
    load_dataset,
    node_mask,
    prepare_base,
    resolve_most_specific,
    validate_dataset,
)

from conftest import SAMPLE_CSV, SAMPLE_JSON, frame, row


def test_node_properties():
    leaf = Node(("PC", "Soap", "Lumina", "L1"))
    assert leaf.is_leaf and leaf.level_label == "PPG" and leaf.depth == 4
    assert Node().is_root and Node().label == "All products"
    assert Node(("PC", "Soap")).level_label == "Sub-category"
    assert Node(("PC",)).contains(leaf.path)
    assert not Node(("HC",)).contains(leaf.path)
    with pytest.raises(ValueError):
        Node(("a", "b", "c", "d", "e"))


def test_resolve_most_specific():
    paths = [("A", "S", "B", "1"), ("A", "S", "B", "2"), ("A", "T", "C", "3"), ("Z", "Z", "Z", "9")]
    settings = {Node(("A",)): "cat", Node(("A", "S", "B")): "brand", Node(("A", "S", "B", "2")): "ppg"}
    resolved = [value for _, value in resolve_most_specific(paths, settings)]
    assert resolved == ["brand", "ppg", "cat", None]


def test_tree_follows_first_appearance_even_when_rows_are_unsorted():
    df = frame(
        row(category="A", sub_category="S1", brand="B1", ppg_id="1"),
        row(category="B", sub_category="S2", brand="B2", ppg_id="2"),
        row(category="A", sub_category="S3", brand="B3", ppg_id="3"),
    )
    nodes = [n.path for n in iter_nodes(df)]
    assert nodes.index(("A", "S3")) < nodes.index(("B",))  # A's children stay together
    assert [c.path for c in children(df, Node(("A",)))] == [("A", "S1"), ("A", "S3")]


def test_same_brand_name_under_two_parents_is_two_nodes():
    df = frame(
        row(sub_category="Soap", brand="Lumina", ppg_id="1"),
        row(sub_category="Body Wash", brand="Lumina", ppg_id="2"),
    )
    assert node_mask(df, Node(("Cat", "Soap", "Lumina"))).sum() == 1
    assert len(children(df, Node(("Cat",)))) == 2


def test_sample_dataset_shape():
    raw = load_dataset(SAMPLE_CSV)
    clean, _ = validate_dataset(raw)
    tree = build_tree(clean)
    assert len(clean) == 36
    assert len(tree) == 3
    assert sum(len(subs) for subs in tree.values()) == 6
    assert sum(len(brands) for subs in tree.values() for brands in subs.values()) == 12
    assert all(len(ppgs) == 3 for subs in tree.values() for brands in subs.values() for ppgs in brands.values())


def test_sample_dataset_has_exactly_the_documented_demo_issues():
    raw = load_dataset(SAMPLE_CSV)
    clean, data_issues = validate_dataset(raw)
    base, elasticity_issues = prepare_base(clean, [])
    issues = data_issues + elasticity_issues
    errors = {(i.ppg_id, i.field) for i in issues if i.severity is Severity.ERROR}
    warnings = {(i.ppg_id, i.field) for i in issues if i.severity is Severity.WARNING}
    assert errors == {("CHF-SAU-1KG", "unit_cogs"), ("NIM-SHP-650", "elasticity")}
    assert warnings == {("AZU-LP-4KG", "elasticity")}
    assert int(base["is_valid"].sum()) == 34


def test_json_sample_matches_csv_sample():
    csv_clean, _ = validate_dataset(load_dataset(SAMPLE_CSV))
    json_clean, _ = validate_dataset(load_dataset(SAMPLE_JSON))
    cols = ["category", "sub_category", "brand", "ppg_id", "base_units", "base_list_price", "unit_cogs", "elasticity"]
    pd.testing.assert_frame_equal(csv_clean[cols], json_clean[cols], check_dtype=False)
