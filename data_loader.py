"""Load the PPG dataset from CSV or JSON.

Expected shape: one row (record) per PPG — see README -> "Data format".
Loading only reads and tidies; it never validates or fills values. That is
``validation.validate_dataset``'s job, so every assumption is flagged in one place.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import IO, Optional, Union

import pandas as pd

from ._utils import try_parse_number
from .schema import NUMERIC_COLUMNS, normalize_columns

Source = Union[str, Path, IO[str], IO[bytes]]


def load_dataset(source: Source, filename: Optional[str] = None) -> pd.DataFrame:
    """Read a CSV or JSON file (path or file-like object) into a DataFrame.

    JSON may be a list of PPG records, or an object holding that list under
    "ppgs", "records", "data" or "rows". Column names are normalised (see schema).
    Numeric columns become floats when every non-blank cell parses; a column with a
    bad cell stays as text so the validator can point at the exact cell.
    """
    if filename:
        name = filename
    elif isinstance(source, (str, Path)):
        name = str(source)
    else:
        name = getattr(source, "name", "") or ""
    suffix = Path(name).suffix.lower()

    if suffix == ".json":
        df = _read_json(source)
    elif suffix in {".csv", ".txt", ""}:
        # dtype=str + keep_default_na=False: keep ids like "001" intact and treat
        # blanks as "" so nothing is converted behind the user's back.
        df = pd.read_csv(
            source, dtype=str, keep_default_na=False, encoding="utf-8-sig", skipinitialspace=True
        )
    else:
        raise ValueError(f"Unsupported file type '{suffix}'. Use a .csv or .json file.")

    df = normalize_columns(df)
    return _infer_numeric_columns(df)


def _read_json(source: Source) -> pd.DataFrame:
    if isinstance(source, (str, Path)):
        text = Path(source).read_text(encoding="utf-8-sig")
    else:
        raw = source.read()
        text = raw.decode("utf-8-sig") if isinstance(raw, (bytes, bytearray)) else raw
    data = json.loads(text)
    if isinstance(data, dict):
        for key in ("ppgs", "records", "data", "rows"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            raise ValueError("JSON must be a list of PPG records, or an object with a 'ppgs' list.")
    if not isinstance(data, list) or not all(isinstance(rec, dict) for rec in data):
        raise ValueError("JSON must be a list of PPG records (one object per PPG).")
    return pd.DataFrame.from_records(data)


def _infer_numeric_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for col in NUMERIC_COLUMNS:
        if col not in df.columns:
            continue
        parsed = [try_parse_number(value) for value in df[col]]
        if all(value is not None for value in parsed):
            df[col] = pd.Series(parsed, index=df.index, dtype=float)
    return df
