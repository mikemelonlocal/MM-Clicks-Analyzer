# business_logic.py
"""Business logic for performance bucketing and bid recommendations."""

import hashlib
import json
from typing import Dict, Tuple

import numpy as np
import pandas as pd
import streamlit as st

from config import DEFAULT_BID_TOP, DEFAULT_BID_MID, DEFAULT_BID_WEAK, DEFAULT_BID_ZERO


class BucketMinimum:
    """Bucket minimum requirements."""
    
    def __init__(self, use_qs: bool, min_qs: int, use_clicks: bool, min_clicks: int):
        self.use_qs = use_qs
        self.min_qs = min_qs
        self.use_clicks = use_clicks
        self.min_clicks = min_clicks
    
    def passes(self, quote_starts: float, clicks: float) -> bool:
        """Check if row meets minimum requirements.
        
        Args:
            quote_starts: Number of quote starts
            clicks: Number of clicks
            
        Returns:
            True if minimums are met
        """
        if self.use_qs and quote_starts < self.min_qs:
            return False
        if self.use_clicks and clicks < self.min_clicks:
            return False
        return True


class BucketingPolicy:
    """Policy for bucketing performance and recommending bids."""
    
    def __init__(
        self,
        bucket_basis: str,
        top_threshold: float,
        avg_ceiling: float,
        bid_top: int,
        bid_mid: int,
        bid_weak: int,
        bid_zero: int,
        eff_label: str,
        minimums: Dict[str, BucketMinimum],
        use_volume_gate: bool = False,
        volume_gate_threshold: float = 0.0,
    ):
        """Initialize bucketing policy.
        
        Args:
            bucket_basis: Which metric(s) to use for bucketing
            top_threshold: Upper bound for "Top" bucket
            avg_ceiling: Upper bound for "Average" bucket
            bid_top: Bid % for top performers
            bid_mid: Bid % for average performers
            bid_weak: Bid % for weak performers
            bid_zero: Bid % for no quote starts
            eff_label: Name of efficiency metric
            minimums: Dict of bucket minimums by bucket name
            use_volume_gate: Whether to enforce volume gating
            volume_gate_threshold: Click threshold for volume gate
        """
        self.bucket_basis = bucket_basis
        self.top_threshold = top_threshold
        self.avg_ceiling = avg_ceiling
        self.bid_top = bid_top
        self.bid_mid = bid_mid
        self.bid_weak = bid_weak
        self.bid_zero = bid_zero
        self.eff_label = eff_label
        self.minimums = minimums
        self.use_volume_gate = use_volume_gate
        self.volume_gate_threshold = volume_gate_threshold
    
    def _calculate_bucket_value(self, row: pd.Series) -> float:
        """Calculate the value to use for bucketing based on policy.
        
        Args:
            row: DataFrame row
            
        Returns:
            Bucket value (cost metric)
        """
        cpqs = row.get(self.eff_label, np.nan)
        cost_phone_qs = row.get("Cost per Phone+QS", np.nan)
        cost_sms_qs = row.get("Cost per SMS+QS", np.nan)
        cost_per_lead = row.get("Cost per Lead", np.nan)
        
        if self.bucket_basis == "CPQS only":
            return cpqs
        
        if self.bucket_basis == "Cost per Lead only":
            return cost_per_lead
        
        # Worst of all metrics
        values = [v for v in [cpqs, cost_phone_qs, cost_sms_qs, cost_per_lead] if pd.notna(v)]
        return max(values) if values else np.nan
    
    def determine_bucket(self, row: pd.Series) -> str:
        """Determine performance bucket for a row.
        
        Args:
            row: DataFrame row with metrics
            
        Returns:
            Bucket name: "Top", "Average", "Weak", or "No Quote Starts"
        """
        quote_starts = row.get("B", 0.0) or 0.0
        clicks = row.get("Clicks", 0.0) or 0.0
        spend = row.get("A", 0.0) or 0.0
        
        # No Quote Starts bucket: has spend but no QS
        if spend > 0 and quote_starts == 0:
            if self.minimums["No Quote Starts"].passes(quote_starts, clicks):
                bucket = "No Quote Starts"
            else:
                bucket = "Average"
        else:
            # Calculate bucket value
            value = self._calculate_bucket_value(row)
            
            if pd.isna(value):
                bucket = "Average"
            elif value < self.top_threshold and self.minimums["Top"].passes(quote_starts, clicks):
                bucket = "Top"
            elif value > self.avg_ceiling and self.minimums["Weak"].passes(quote_starts, clicks):
                bucket = "Weak"
            else:
                bucket = "Average"
        
        # Volume gate: override to Average if below click threshold
        if self.use_volume_gate and clicks < self.volume_gate_threshold:
            bucket = "Average"
        
        return bucket
    
    def recommend_bid(self, bucket: str) -> int:
        """Get recommended bid percentage for a bucket.
        
        Args:
            bucket: Bucket name
            
        Returns:
            Bid percentage
        """
        bid_map = {
            "Top": self.bid_top,
            "Average": self.bid_mid,
            "Weak": self.bid_weak,
            "No Quote Starts": self.bid_zero,
        }
        return bid_map.get(bucket, self.bid_mid)
    
    def get_signature(self) -> str:
        """Generate unique signature for this policy configuration.
        
        Returns:
            12-character hash of policy parameters
        """
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


def apply_policy_to_dataframe(df: pd.DataFrame, policy: BucketingPolicy) -> pd.DataFrame:
    """Apply bucketing policy to entire dataframe.
    
    Args:
        df: DataFrame with metrics
        policy: Bucketing policy to apply
        
    Returns:
        DataFrame with added "Perf Bucket" and "Recommended Bid %" columns
    """
    result = df.copy()
    
    result["Perf Bucket"] = result.apply(policy.determine_bucket, axis=1)
    result["Recommended Bid %"] = result["Perf Bucket"].apply(policy.recommend_bid)
    
    return result


def transform_bid_percentage(
    base_pct: int,
    multiplier: float,
    round_to: int,
    clamp_min: int,
    clamp_max: int,
) -> int:
    """Transform bid percentage with multiplier, rounding, and clamping.
    
    Args:
        base_pct: Base bid percentage
        multiplier: Multiplier to apply
        round_to: Round to nearest N
        clamp_min: Minimum allowed value
        clamp_max: Maximum allowed value
        
    Returns:
        Transformed bid percentage
    """
    value = base_pct * multiplier
    value = int(round(value / round_to) * round_to)
    return max(clamp_min, min(clamp_max, value))


def calculate_efficiency_metrics(df: pd.DataFrame, eff_label: str) -> pd.DataFrame:
    """Calculate efficiency metrics from A and B columns.
    
    Args:
        df: DataFrame with A (spend) and B (quote starts), C (phone), D (sms)
        eff_label: Label for primary efficiency metric
        
    Returns:
        DataFrame with added efficiency columns
    """
    result = df.copy()
    
    # Primary efficiency (A / B)
    result[eff_label] = np.where(
        result["B"] > 0,
        result["A"] / result["B"],
        np.nan
    )
    
    # Cost per Phone + QS
    result["Cost per Phone+QS"] = np.where(
        (result["C"] + result["B"]) > 0,
        result["A"] / (result["C"] + result["B"]),
        np.nan
    )
    
    # Cost per SMS + QS
    result["Cost per SMS+QS"] = np.where(
        (result["D"] + result["B"]) > 0,
        result["A"] / (result["D"] + result["B"]),
        np.nan
    )
    
    # Cost per Lead (all channels)
    result["Cost per Lead"] = np.where(
        (result["B"] + result["C"] + result["D"]) > 0,
        result["A"] / (result["B"] + result["C"] + result["D"]),
        np.nan
    )
    
    return result
