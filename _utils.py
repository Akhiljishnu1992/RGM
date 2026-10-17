"""Small parsing helpers shared by the loader and the validator."""
from __future__ import annotations

import math
from numbers import Number
from typing import Optional

import pandas as pd

# Cell contents treated as "no value" in a numeric column.
_MISSING_TOKENS = {"", "nan", "none", "null", "na", "n/a", "#n/a", "-"}


def is_missing_number(value) -> bool:
    """True for empty cells and common 'missing' tokens in a numeric column."""
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in _MISSING_TOKENS
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def parse_number(value) -> float:
    """Parse one numeric cell.

    Returns NaN for a missing cell. Raises ValueError for anything that is present
    but is not a finite number (e.g. 'abc', '5%', 'inf'), so the caller can flag it
    instead of silently treating it as missing. Thousands separators are accepted.
    """
    if is_missing_number(value):
        return math.nan
    if isinstance(value, bool):
        raise ValueError("a boolean is not a number")
    if isinstance(value, Number):
        result = float(value)
    else:
        text = str(value).strip().replace(",", "").replace(" ", "").replace(" ", "")
        result = float(text)
    if not math.isfinite(result):
        raise ValueError("not a finite number")
    return result


def try_parse_number(value) -> Optional[float]:
    """Like ``parse_number`` but returns None instead of raising."""
    try:
        return parse_number(value)
    except (TypeError, ValueError):
        return None


def clean_text(value) -> str:
    """Normalise a text cell: blanks become '', whitespace is trimmed and ids that
    arrived as floats (1001.0) are restored to '1001'."""
    if value is None:
        return ""
    if isinstance(value, float):
        if math.isnan(value):
            return ""
        if value.is_integer():
            return str(int(value))
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()
