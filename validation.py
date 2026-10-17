"""Input validation: schema checks, type coercion and per-PPG value checks.

Policy (also in README -> "Validation rules"):

* ERROR   - the PPG is excluded from every scenario until fixed. Missing core inputs
            (price, units, COGS) are never silently defaulted.
* WARNING - the PPG is calculated, but a value deserves a second look, or a blank
            optional value was assumed to be 0 (and that assumption is shown).
* INFO    - a documented dataset-wide assumption was applied (e.g. an optional
            column is absent).

Elasticity is checked later, in ``simulation.prepare_base``, because a user
override can supply a missing value.
"""
from __future__ import annotations

import math
from typing import Optional

import pandas as pd

from ._utils import clean_text, parse_number
from .models import HIERARCHY_COLUMNS, LEVEL_LABELS, Issue, Severity, SimulationConfig, TOBasis
from .schema import (
    FIELD_LABELS,
    NUMERIC_COLUMNS,
    RATE_COLUMNS,
    REQUIRED_COLUMNS,
    TEXT_COLUMNS,
    normalize_columns,
)

INTERNAL_COLUMNS = ("row_errors", "row_warnings")


class SchemaError(ValueError):
    """The dataset cannot be used at all (e.g. a required column is missing)."""


def validate_dataset(
    df: pd.DataFrame, config: Optional[SimulationConfig] = None
) -> tuple[pd.DataFrame, list[Issue]]:
    """Validate and clean the raw dataset.

    Returns ``(clean_df, issues)``. ``clean_df`` has one row per input row, typed
    columns, a RangeIndex, and two list columns: ``row_errors`` and ``row_warnings``.
    Raises ``SchemaError`` only for problems that make the whole file unusable.
    """
    config = config or SimulationConfig()
    if df is None or len(df.columns) == 0:
        raise SchemaError("The dataset is empty.")

    df = normalize_columns(df.copy())
    df = df.drop(columns=[c for c in INTERNAL_COLUMNS if c in df.columns])
    duplicated = sorted({c for c in df.columns if list(df.columns).count(c) > 1})
    if duplicated:
        raise SchemaError(
            "Two or more columns map to the same name after normalising headers: "
            + ", ".join(duplicated)
        )
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise SchemaError(
            "Missing required column(s): "
            + ", ".join(missing)
            + ". Required columns are: "
            + ", ".join(REQUIRED_COLUMNS)
            + "."
        )

    issues: list[Issue] = []
    df = df.reset_index(drop=True)
    blank_rows = df.apply(lambda row: all(clean_text(v) == "" for v in row), axis=1)
    if len(df) and blank_rows.any():
        issues.append(Issue(Severity.INFO, f"Ignored {int(blank_rows.sum())} completely blank row(s)."))
        df = df[~blank_rows].reset_index(drop=True)
    if df.empty:
        raise SchemaError("The dataset has no PPG rows.")

    # ---- optional columns: add them, and say what was assumed ----------------------
    if "ppg_name" not in df.columns:
        df["ppg_name"] = df["ppg_id"]
        issues.append(Issue(Severity.INFO, "No 'ppg_name' column; PPG ids are used as names."))
    if "elasticity_source" not in df.columns:
        df["elasticity_source"] = ""
        issues.append(
            Issue(Severity.INFO, "No 'elasticity_source' column; sources show as 'source not given'.")
        )
    for col in RATE_COLUMNS:
        if col not in df.columns:
            df[col] = 0.0
            issues.append(Issue(Severity.INFO, f"No '{col}' column; assumed 0 for every PPG.", field=col))
    if "base_rsp" not in df.columns:
        df["base_rsp"] = math.nan
        if config.to_basis is TOBasis.CONSUMER_OFFTAKE:
            issues.append(
                Issue(
                    Severity.ERROR,
                    "TO is defined as consumer offtake but the data has no 'base_rsp' column.",
                    field="base_rsp",
                )
            )

    # ---- text columns ---------------------------------------------------------------
    for col in TEXT_COLUMNS:
        df[col] = pd.Series([clean_text(v) for v in df[col]], index=df.index, dtype=object)
    blank_name = (df["ppg_name"] == "") & (df["ppg_id"] != "")
    df.loc[blank_name, "ppg_name"] = df.loc[blank_name, "ppg_id"]

    n_rows = len(df)
    errors: list[list[str]] = [[] for _ in range(n_rows)]
    warnings: list[list[str]] = [[] for _ in range(n_rows)]
    ids = df["ppg_id"].tolist()

    def flag(i: int, severity: Severity, field: str, message: str) -> None:
        (errors if severity is Severity.ERROR else warnings)[i].append(message)
        issues.append(Issue(severity, message, ppg_id=ids[i] or f"row {i + 1}", field=field))

    # ---- numeric columns: parse, and flag anything that is present but not a number --
    unparseable: set[tuple[int, str]] = set()
    for col in NUMERIC_COLUMNS:
        values = []
        for i, raw in enumerate(df[col].tolist()):
            try:
                values.append(parse_number(raw))
            except (TypeError, ValueError):
                values.append(math.nan)
                unparseable.add((i, col))
                flag(i, Severity.ERROR, col, f"{FIELD_LABELS[col]} '{raw}' is not a valid number.")
        df[col] = pd.Series(values, index=df.index, dtype=float)

    # ---- per-row checks -------------------------------------------------------------
    duplicate_id = (df["ppg_id"] != "") & df["ppg_id"].duplicated(keep=False)
    records = df.to_dict("records")
    for i, rec in enumerate(records):

        def parsed(col: str) -> bool:
            return (i, col) not in unparseable

        for col, level in zip(HIERARCHY_COLUMNS, LEVEL_LABELS):
            if rec[col] == "":
                flag(i, Severity.ERROR, col, f"Missing {level}{' id' if col == 'ppg_id' else ''}.")
        if duplicate_id.iat[i]:
            flag(i, Severity.ERROR, "ppg_id", f"Duplicate PPG id '{rec['ppg_id']}'; ids must be unique.")

        price = rec["base_list_price"]
        price_ok = False
        if parsed("base_list_price"):
            if math.isnan(price):
                flag(i, Severity.ERROR, "base_list_price", "Missing base list price.")
            elif price <= 0:
                flag(i, Severity.ERROR, "base_list_price", f"Base list price must be > 0 (got {price:g}).")
            else:
                price_ok = True

        units = rec["base_units"]
        if parsed("base_units"):
            if math.isnan(units):
                flag(i, Severity.ERROR, "base_units", "Missing base units.")
            elif units < 0:
                flag(i, Severity.ERROR, "base_units", f"Base units cannot be negative (got {units:g}).")
            elif units == 0:
                flag(i, Severity.WARNING, "base_units", "Base units are 0, so this PPG adds nothing.")

        cogs = rec["unit_cogs"]
        cogs_ok = False
        if parsed("unit_cogs"):
            if math.isnan(cogs):
                flag(i, Severity.ERROR, "unit_cogs", "Missing unit COGS.")
            elif cogs < 0:
                flag(i, Severity.ERROR, "unit_cogs", f"Unit COGS cannot be negative (got {cogs:g}).")
            else:
                cogs_ok = True

        rates_ok = True
        for col in RATE_COLUMNS:
            if not parsed(col):
                rates_ok = False
                continue
            rate = rec[col]
            if math.isnan(rate):
                # A blank trade rate almost always means "none"; assume 0 but say so.
                df.at[i, col] = 0.0
                rec[col] = 0.0
                flag(i, Severity.WARNING, col, f"{FIELD_LABELS[col]} is blank; assumed 0%.")
            elif not 0 <= rate < 1:
                rates_ok = False
                hint = " Enter rates as fractions of list price (0.05 = 5%)." if rate >= 1 else ""
                flag(i, Severity.ERROR, col, f"{FIELD_LABELS[col]} must be in [0, 1) (got {rate:g}).{hint}")
        if rates_ok and rec[RATE_COLUMNS[0]] + rec[RATE_COLUMNS[1]] >= 1:
            rates_ok = False
            flag(i, Severity.ERROR, "off_invoice_trade_rate", "Trade rates add up to 100% or more of list price.")

        if price_ok and cogs_ok and rates_ok:
            net_price = price * (1 - rec[RATE_COLUMNS[0]] - rec[RATE_COLUMNS[1]])
            if cogs >= net_price:
                flag(
                    i,
                    Severity.WARNING,
                    "unit_cogs",
                    f"Unit COGS ({cogs:,.2f}) is at or above net price ({net_price:,.2f}): negative base margin.",
                )

        rsp = rec["base_rsp"]
        if parsed("base_rsp"):
            if not math.isnan(rsp) and rsp <= 0:
                flag(i, Severity.ERROR, "base_rsp", f"Base RSP must be > 0 (got {rsp:g}).")
            elif math.isnan(rsp) and config.to_basis is TOBasis.CONSUMER_OFFTAKE:
                flag(
                    i,
                    Severity.ERROR,
                    "base_rsp",
                    "Missing base RSP, required because TO is defined as consumer offtake.",
                )

    df["row_errors"] = pd.Series(errors, index=df.index, dtype=object)
    df["row_warnings"] = pd.Series(warnings, index=df.index, dtype=object)
    return df, issues
