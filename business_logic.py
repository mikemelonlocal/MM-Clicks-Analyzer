# business_logic.py
"""Business logic for performance bucketing and bid recommendations."""

import hashlib
import json
from dataclasses import dataclass, field
from typing import Dict

import numpy as np
import pandas as pd
import streamlit as st


BUCKET_NAMES = ("Top", "Average", "Weak", "No Quote Starts")


@dataclass(frozen=True)
class BucketMinimum:
    """Bucket minimum requirements."""

    use_qs: bool
    min_qs: int
    use_clicks: bool
    min_clicks: int

    def passes(self, quote_starts: float, clicks: float) -> bool:
        """Check if a single row meets the minimums (kept for scalar callers)."""
        if self.use_qs and quote_starts < self.min_qs:
            return False
        if self.use_clicks and clicks < self.min_clicks:
            return False
        return True

    def passes_mask(self, qs_series: pd.Series, clicks_series: pd.Series) -> pd.Series:
        """Vectorized variant of :meth:`passes`."""
        mask = pd.Series(True, index=qs_series.index)
        if self.use_qs:
            mask &= qs_series >= self.min_qs
        if self.use_clicks:
            mask &= clicks_series >= self.min_clicks
        return mask


@dataclass
class BucketingPolicy:
    """Policy for bucketing performance and recommending bids."""

    bucket_basis: str
    top_threshold: float
    avg_ceiling: float
    bid_top: int
    bid_mid: int
    bid_weak: int
    bid_zero: int
    eff_label: str
    minimums: Dict[str, BucketMinimum]
    use_volume_gate: bool = False
    volume_gate_threshold: float = 0.0

    def _calculate_bucket_value(self, row: pd.Series) -> float:
        """Calculate the value used for bucketing a single row."""
        cpqs = row.get(self.eff_label, np.nan)
        cost_phone_qs = row.get("Cost per Phone+QS", np.nan)
        cost_sms_qs = row.get("Cost per SMS+QS", np.nan)
        cost_per_lead = row.get("Cost per Lead", np.nan)

        if self.bucket_basis == "CPQS only":
            return cpqs
        if self.bucket_basis == "Cost per Lead only":
            return cost_per_lead

        # Worst of all available metrics
        values = [v for v in (cpqs, cost_phone_qs, cost_sms_qs, cost_per_lead)
                  if pd.notna(v)]
        return max(values) if values else np.nan

    def determine_bucket(self, row: pd.Series) -> str:
        """Determine performance bucket for a single row (scalar path)."""
        quote_starts = row.get("B", 0.0) or 0.0
        clicks = row.get("Clicks", 0.0) or 0.0
        spend = row.get("A", 0.0) or 0.0

        if spend > 0 and quote_starts == 0:
            if self.minimums["No Quote Starts"].passes(quote_starts, clicks):
                bucket = "No Quote Starts"
            else:
                bucket = "Average"
        else:
            value = self._calculate_bucket_value(row)
            if pd.isna(value):
                bucket = "Average"
            elif value < self.top_threshold and self.minimums["Top"].passes(quote_starts, clicks):
                bucket = "Top"
            elif value > self.avg_ceiling and self.minimums["Weak"].passes(quote_starts, clicks):
                bucket = "Weak"
            else:
                bucket = "Average"

        if self.use_volume_gate and clicks < self.volume_gate_threshold:
            bucket = "Average"
        return bucket

    def recommend_bid(self, bucket: str) -> int:
        """Get recommended bid percentage for a bucket."""
        return {
            "Top": self.bid_top,
            "Average": self.bid_mid,
            "Weak": self.bid_weak,
            "No Quote Starts": self.bid_zero,
        }.get(bucket, self.bid_mid)

    def get_signature(self) -> str:
        """Generate a unique 12-char hash of this policy's parameters."""
        payload = {
            "bucket_basis": self.bucket_basis,
            "top_threshold": self.top_threshold,
            "avg_ceiling": self.avg_ceiling,
            "bid_top": self.bid_top,
            "bid_mid": self.bid_mid,
            "bid_weak": self.bid_weak,
            "bid_zero": self.bid_zero,
            "use_volume_gate": self.use_volume_gate,
            "volume_gate_threshold": self.volume_gate_threshold,
            "eff_label": self.eff_label,
        }
        hash_str = json.dumps(payload, sort_keys=True)
        return hashlib.sha256(hash_str.encode()).hexdigest()[:12]


def _bucket_value_vectorized(df: pd.DataFrame, policy: BucketingPolicy) -> pd.Series:
    """Vectorized equivalent of :meth:`BucketingPolicy._calculate_bucket_value`."""
    def col_or_nan(name: str) -> pd.Series:
        if name in df.columns:
            return pd.to_numeric(df[name], errors="coerce")
        return pd.Series(np.nan, index=df.index)

    cpqs = col_or_nan(policy.eff_label)
    cpl = col_or_nan("Cost per Lead")

    if policy.bucket_basis == "CPQS only":
        return cpqs
    if policy.bucket_basis == "Cost per Lead only":
        return cpl

    # Worst (max) across all available metrics, ignoring NaN.
    cost_phone = col_or_nan("Cost per Phone+QS")
    cost_sms = col_or_nan("Cost per SMS+QS")
    return pd.concat([cpqs, cost_phone, cost_sms, cpl], axis=1).max(
        axis=1, skipna=True
    )


def apply_policy_to_dataframe(df: pd.DataFrame, policy: BucketingPolicy) -> pd.DataFrame:
    """Apply a bucketing policy to a DataFrame (vectorized).

    Produces the same ``Perf Bucket`` and ``Recommended Bid %`` columns as
    the per-row scalar path, but uses numpy masks instead of
    ``DataFrame.apply(axis=1)`` — O(n) Python calls collapse to a handful
    of vectorized pandas operations.
    """
    result = df.copy()

    def series_or_zero(name: str) -> pd.Series:
        if name in result.columns:
            return pd.to_numeric(result[name], errors="coerce").fillna(0.0)
        return pd.Series(0.0, index=result.index)

    B = series_or_zero("B")
    clicks = series_or_zero("Clicks")
    A = series_or_zero("A")

    value = _bucket_value_vectorized(result, policy)

    top_min = policy.minimums["Top"]
    weak_min = policy.minimums["Weak"]
    nqs_min = policy.minimums["No Quote Starts"]

    top_passes = top_min.passes_mask(B, clicks)
    weak_passes = weak_min.passes_mask(B, clicks)
    nqs_passes = nqs_min.passes_mask(B, clicks)

    no_qs_cond = (A > 0) & (B == 0)
    value_valid = ~no_qs_cond & value.notna()

    bucket = pd.Series("Average", index=result.index, dtype=object)

    # No-QS path takes priority over the value-based branch.
    bucket[no_qs_cond & nqs_passes] = "No Quote Starts"

    # Apply Weak first so a row satisfying both Weak and Top (only possible
    # with an inverted threshold config) ends up as Top — matches the old
    # ordering where Top was checked first.
    bucket[value_valid & (value > policy.avg_ceiling) & weak_passes] = "Weak"
    bucket[value_valid & (value < policy.top_threshold) & top_passes] = "Top"

    # Volume gate override is always last.
    if policy.use_volume_gate:
        bucket[clicks < policy.volume_gate_threshold] = "Average"

    result["Perf Bucket"] = bucket
    bid_map = {
        "Top": policy.bid_top,
        "Average": policy.bid_mid,
        "Weak": policy.bid_weak,
        "No Quote Starts": policy.bid_zero,
    }
    result["Recommended Bid %"] = bucket.map(bid_map).fillna(policy.bid_mid).astype(int)
    return result


def transform_bid_percentage(
    base_pct: int,
    multiplier: float,
    round_to: int,
    clamp_min: int,
    clamp_max: int,
) -> int:
    """Transform a bid percentage with multiplier, rounding, and clamping."""
    value = base_pct * multiplier
    value = int(round(value / round_to) * round_to)
    return max(clamp_min, min(clamp_max, value))


@st.cache_data(show_spinner=False)
def calculate_efficiency_metrics(df: pd.DataFrame, eff_label: str) -> pd.DataFrame:
    """Calculate efficiency metrics from A, B, C, D columns.

    Cached on the (df, eff_label) pair so identical frames reuse the
    previous computation across Streamlit reruns.
    """
    result = df.copy()

    result[eff_label] = np.where(
        result["B"] > 0,
        result["A"] / result["B"],
        np.nan,
    )
    result["Cost per Phone+QS"] = np.where(
        (result["C"] + result["B"]) > 0,
        result["A"] / (result["C"] + result["B"]),
        np.nan,
    )
    result["Cost per SMS+QS"] = np.where(
        (result["D"] + result["B"]) > 0,
        result["A"] / (result["D"] + result["B"]),
        np.nan,
    )
    result["Cost per Lead"] = np.where(
        (result["B"] + result["C"] + result["D"]) > 0,
        result["A"] / (result["B"] + result["C"] + result["D"]),
        np.nan,
    )
    return result
