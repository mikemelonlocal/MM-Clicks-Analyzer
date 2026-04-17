# data_utils.py
"""Data processing utilities for MM Click Analyzer."""

import re
import hashlib
from typing import Optional, List

import pandas as pd
import numpy as np


def normalize_name(name: str) -> str:
    """Normalize column/string name for matching.
    
    Args:
        name: String to normalize
        
    Returns:
        Normalized lowercase string with no spaces/special chars
    """
    s = re.sub(r"[\s_]+", "", str(name).lower())
    s = re.sub(r"[^\w]", "", s)
    # Remove trailing 's' except for special cases
    if s.endswith("s") and s not in ("sms",):
        s = s[:-1]
    return s


def find_column(df: pd.DataFrame, aliases: List[str]) -> Optional[str]:
    """Find a column in dataframe using list of aliases.
    
    Args:
        df: DataFrame to search
        aliases: List of possible column names
        
    Returns:
        Actual column name if found, None otherwise
    """
    if df is None or df.empty:
        return None
    
    # Create normalized name mapping
    name_map = {normalize_name(c): c for c in df.columns}
    
    # Try exact matches first
    for alias in aliases:
        key = normalize_name(alias)
        if key in name_map:
            return name_map[key]
    
    # Try substring matches
    for col in df.columns:
        normalized_col = normalize_name(col)
        for alias in aliases:
            if normalize_name(alias) in normalized_col:
                return col
    
    return None


def to_numeric_safe(
    series: pd.Series,
    fill_na: Optional[float] = 0.0,
) -> pd.Series:
    """Convert series to numeric, handling currency symbols and errors.

    For counts (clicks, quote starts) filling NaN with 0 is usually right
    — a missing count really is "none". For spend/cost, "missing" and
    "zero" are distinct: treating a missing-spend row as free traffic
    will push it into the Top bucket with no cost signal. Pass
    ``fill_na=None`` there so NaN propagates and downstream code can
    decide whether to drop, ignore, or surface it.

    Args:
        series: Input series
        fill_na: Value to substitute for NaN. Use ``None`` to preserve NaN.

    Returns:
        Numeric series (NaN-filled when ``fill_na`` is not None).
    """
    if pd.api.types.is_numeric_dtype(series):
        numeric = series
    else:
        # Strip everything but digits, decimal point, and minus sign.
        cleaned = series.astype(str).str.replace(r"[^0-9.\-]", "", regex=True)
        numeric = pd.to_numeric(cleaned, errors="coerce")

    if fill_na is None:
        return numeric
    return numeric.fillna(fill_na)


def canonical_id(value: str) -> str:
    """Create canonical ID for matching (lowercase, alphanumeric only).
    
    Args:
        value: String to canonicalize
        
    Returns:
        Canonical ID string
    """
    s = str(value or "").strip()
    
    # Remove .0 suffix from stringified numbers
    if s.endswith(".0"):
        s = s[:-2]
    
    s = s.lower()
    s = re.sub(r"[^a-z0-9]", "", s)
    return s


def normalize_device(value: str) -> str:
    """Normalize device type strings to standard values.
    
    Args:
        value: Device string
        
    Returns:
        One of: "mobile", "desktop", "tablet", or empty string
    """
    v = str(value or "").strip().lower()
    flat = re.sub(r"[^a-z0-9]", "", v)
    
    if flat in {"mobile", "m", "iphone", "android", "phone"}:
        return "mobile"
    if flat in {"desktop", "pc", "computer", "mac", "windows"}:
        return "desktop"
    if flat in {"tablet", "tab", "ipad"}:
        return "tablet"
    
    return ""


def hash_dataframe_columns(df: pd.DataFrame, columns: List[str]) -> str:
    """Create hash of specific dataframe columns for change detection.
    
    Args:
        df: DataFrame
        columns: Columns to include in hash
        
    Returns:
        16-character hash string
    """
    if df is None or df.empty:
        return ""
    
    # Create snapshot of column values
    snapshot = df[columns].astype(str).agg("|".join, axis=1).tolist()
    blob = "\n".join(snapshot)
    
    # Hash the column names + data
    hash_input = "|".join(columns) + "||" + blob
    return hashlib.sha256(hash_input.encode()).hexdigest()[:16]


def make_stable_row_key(df: pd.DataFrame, device_col: Optional[str]) -> pd.Series:
    """Create stable row key for tracking changes across refreshes.
    
    Args:
        df: DataFrame
        device_col: Optional device column name
        
    Returns:
        Series of row keys
    """
    if "QMPID" in df.columns:
        qmp_ids = df["QMPID"].astype(str).str.strip()
        
        if device_col and device_col in df.columns:
            devices = df[device_col].map(normalize_device).fillna("")
        else:
            devices = pd.Series([""] * len(df), index=df.index)
        
        return qmp_ids + "||" + devices
    
    # Fallback: use first column
    first_col = df.columns[0]
    return df[first_col].astype(str).str.strip()


def extract_click_keys_from_campaigns(
    campaign_series: pd.Series,
    click_keys: List[str],
    chunk_size: int = 3000
) -> pd.Series:
    """Extract click keys from campaign ID strings using regex.
    
    Processes in chunks for performance with large key lists.
    
    Args:
        campaign_series: Series of campaign ID strings
        click_keys: List of click keys to find
        chunk_size: Number of keys to process per regex
        
    Returns:
        Series with matched click keys
    """
    series = campaign_series.fillna("").astype(str)
    result = pd.Series(index=series.index, dtype="object")
    
    # Sort keys by length (longest first) for better matching
    sorted_keys = sorted(set(click_keys), key=len, reverse=True)
    
    for i in range(0, len(sorted_keys), chunk_size):
        chunk = sorted_keys[i:i + chunk_size]
        
        # Build regex pattern with escaped keys
        pattern = "(" + "|".join(re.escape(k) for k in chunk) + ")"
        
        # Extract matches (case-insensitive)
        matched = series.str.extract(pattern, flags=re.IGNORECASE, expand=False)
        
        # Fill result where we found matches and don't have a value yet
        needs_fill = result.isna() & matched.notna()
        if needs_fill.any():
            result.loc[needs_fill] = matched.loc[needs_fill]
    
    return result


def format_number(value: float, as_currency: bool = False) -> str:
    """Format number for display.
    
    Args:
        value: Number to format
        as_currency: Whether to format as currency
        
    Returns:
        Formatted string
    """
    if pd.isna(value):
        return ""
    
    if as_currency:
        return f"${value:,.2f}"
    
    return f"{value:,.2f}"
