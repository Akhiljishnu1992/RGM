"""Navigation helpers for the Category -> Sub-category -> Brand -> PPG hierarchy.

The hierarchy is derived from the dataset itself (one row per PPG), so swapping in
real data needs no code change. Order follows first appearance in the file.
"""
from __future__ import annotations

from typing import Mapping, Optional, Sequence, TypeVar

import pandas as pd

from .models import HIERARCHY_COLUMNS, Node

T = TypeVar("T")
Tree = dict  # nested {name: {child: {...}}}


def _text(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str)


def has_full_path(df: pd.DataFrame) -> pd.Series:
    """Rows whose four hierarchy fields are all filled in."""
    mask = pd.Series(True, index=df.index)
    for col in HIERARCHY_COLUMNS:
        mask &= _text(df[col]).str.strip() != ""
    return mask


def ppg_paths(df: pd.DataFrame) -> list[tuple[str, ...]]:
    """Full path tuple for every row, in row order."""
    cols = pd.DataFrame({col: _text(df[col]) for col in HIERARCHY_COLUMNS})
    return list(cols.itertuples(index=False, name=None))


def node_mask(df: pd.DataFrame, node: Node) -> pd.Series:
    """Boolean mask of the rows (PPGs) that sit under ``node``."""
    mask = pd.Series(True, index=df.index)
    for col, value in zip(HIERARCHY_COLUMNS, node.path):
        mask &= _text(df[col]) == value
    return mask


def build_tree(df: pd.DataFrame) -> Tree:
    """Nested dict of the hierarchy, preserving first-appearance order."""
    tree: Tree = {}
    for path in ppg_paths(df[has_full_path(df)]):
        level = tree
        for part in path:
            level = level.setdefault(part, {})
    return tree


def _subtree(tree: Tree, node: Node) -> Tree:
    for part in node.path:
        tree = tree.get(part)
        if tree is None:
            return {}
    return tree


def children(df: pd.DataFrame, node: Node, tree: Optional[Tree] = None) -> list[Node]:
    tree = build_tree(df) if tree is None else tree
    return [Node(node.path + (name,)) for name in _subtree(tree, node)]


def iter_nodes(df: pd.DataFrame, include_root: bool = False, tree: Optional[Tree] = None) -> list[Node]:
    """Every node in pre-order (parent before children)."""
    tree = build_tree(df) if tree is None else tree
    out: list[Node] = [Node()] if include_root else []

    def walk(sub: Tree, prefix: tuple[str, ...]) -> None:
        for name, child in sub.items():
            node = Node(prefix + (name,))
            out.append(node)
            walk(child, node.path)

    walk(tree, ())
    return out


def ppg_names(df: pd.DataFrame) -> dict[str, str]:
    return dict(zip(_text(df["ppg_id"]), _text(df["ppg_name"])))


def display_name(node: Node, names: Optional[Mapping[str, str]] = None) -> str:
    """Readable name: the PPG name for leaves, the path element otherwise."""
    if node.is_leaf and names:
        return names.get(node.path[-1], node.path[-1]) or node.path[-1]
    return node.name


def resolve_most_specific(
    paths: Sequence[tuple[str, ...]], settings: Mapping[Node, T]
) -> list[tuple[Optional[Node], Optional[T]]]:
    """For each PPG path, find the setting attached to the deepest matching node.

    Used for both price changes and elasticity overrides, so a PPG-level setting
    beats a brand-level one, which beats a sub-category-level one, and so on.
    Returns ``(node, value)`` per path, or ``(None, None)`` if nothing applies.
    """
    if not settings:
        return [(None, None)] * len(paths)
    by_path = {node.path: (node, value) for node, value in settings.items()}
    resolved: list[tuple[Optional[Node], Optional[T]]] = []
    for path in paths:
        hit: tuple[Optional[Node], Optional[T]] = (None, None)
        for depth in range(len(path), -1, -1):
            found = by_path.get(tuple(path[:depth]))
            if found is not None:
                hit = found
                break
        resolved.append(hit)
    return resolved
