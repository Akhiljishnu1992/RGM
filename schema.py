"""Column schema for the input dataset: one row per PPG.

See README -> "Data format" for units and meaning of every column.
"""
from __future__ import annotations

import re

import pandas as pd

from .models import HIERARCHY_COLUMNS

REQUIRED_COLUMNS: tuple[str, ...] = (
    *HIERARCHY_COLUMNS,
    "base_units",
    "base_list_price",
    "unit_cogs",
    "elasticity",
)
RATE_COLUMNS: tuple[str, ...] = ("on_invoice_disc_rate", "off_invoice_trade_rate")
OPTIONAL_COLUMNS: tuple[str, ...] = ("ppg_name", "elasticity_source", *RATE_COLUMNS, "base_rsp")
NUMERIC_COLUMNS: tuple[str, ...] = (
    "base_units",
    "base_list_price",
    "unit_cogs",
    "elasticity",
    *RATE_COLUMNS,
    "base_rsp",
)
TEXT_COLUMNS: tuple[str, ...] = (*HIERARCHY_COLUMNS, "ppg_name", "elasticity_source")

FIELD_LABELS: dict[str, str] = {
    "category": "Category",
    "sub_category": "Sub-category",
    "brand": "Brand",
    "ppg_id": "PPG id",
    "ppg_name": "PPG name",
    "base_units": "Base units",
    "base_list_price": "Base list price",
    "unit_cogs": "Unit COGS",
    "elasticity": "Elasticity",
    "elasticity_source": "Elasticity source",
    "on_invoice_disc_rate": "On-invoice discount rate",
    "off_invoice_trade_rate": "Off-invoice trade rate",
    "base_rsp": "Base RSP",
}

# Common alternative headers found in real extracts, mapped to the canonical names.
# Keys are compared after lower-casing and turning spaces/hyphens/slashes into "_".
COLUMN_ALIASES: dict[str, str] = {
    "subcategory": "sub_category",
    "sub_cat": "sub_category",
    "subcat": "sub_category",
    "ppg": "ppg_id",
    "ppg_code": "ppg_id",
    "ppg_description": "ppg_name",
    "ppg_desc": "ppg_name",
    "base_price": "base_list_price",
    "list_price": "base_list_price",
    "units": "base_units",
    "base_volume": "base_units",
    "volume_units": "base_units",
    "cogs": "unit_cogs",
    "cogs_per_unit": "unit_cogs",
    "unit_cost": "unit_cogs",
    "price_elasticity": "elasticity",
    "own_price_elasticity": "elasticity",
    "rsp": "base_rsp",
    "retail_price": "base_rsp",
    "on_invoice_discount": "on_invoice_disc_rate",
    "on_invoice_rate": "on_invoice_disc_rate",
    "off_invoice_trade": "off_invoice_trade_rate",
    "off_invoice_rate": "off_invoice_trade_rate",
    "trade_rate": "off_invoice_trade_rate",
}


def canonical_column_name(name) -> str:
    key = str(name).replace("﻿", "").strip().lower()
    key = re.sub(r"[\s\-/]+", "_", key)
    key = re.sub(r"_+", "_", key).strip("_")
    return COLUMN_ALIASES.get(key, key)


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Rename columns to their canonical names (case, spacing and aliases)."""
    return df.rename(columns={col: canonical_column_name(col) for col in df.columns})
