"""Tests for bucketing and efficiency logic."""

import numpy as np
import pandas as pd
import pytest

from business_logic import (
    BucketMinimum,
    BucketingPolicy,
    apply_policy_to_dataframe,
    calculate_efficiency_metrics,
    transform_bid_percentage,
)


def _default_policy(**overrides):
    defaults = dict(
        bucket_basis="CPQS only",
        top_threshold=20.0,
        avg_ceiling=30.0,
        bid_top=105,
        bid_mid=100,
        bid_weak=90,
        bid_zero=25,
        eff_label="CPQS",
        minimums={
            "Top": BucketMinimum(use_qs=True, min_qs=3, use_clicks=True, min_clicks=10),
            "Weak": BucketMinimum(use_qs=False, min_qs=0, use_clicks=True, min_clicks=10),
            "No Quote Starts": BucketMinimum(
                use_qs=False, min_qs=0, use_clicks=True, min_clicks=5
            ),
        },
    )
    defaults.update(overrides)
    return BucketingPolicy(**defaults)


class TestBucketMinimum:
    def test_passes_when_both_checks_off(self):
        m = BucketMinimum(use_qs=False, min_qs=0, use_clicks=False, min_clicks=0)
        assert m.passes(0, 0) is True

    def test_fails_on_qs_minimum(self):
        m = BucketMinimum(use_qs=True, min_qs=5, use_clicks=False, min_clicks=0)
        assert m.passes(4, 100) is False
        assert m.passes(5, 0) is True

    def test_fails_on_clicks_minimum(self):
        m = BucketMinimum(use_qs=False, min_qs=0, use_clicks=True, min_clicks=10)
        assert m.passes(0, 9) is False
        assert m.passes(0, 10) is True

    def test_passes_mask_matches_scalar(self):
        m = BucketMinimum(use_qs=True, min_qs=3, use_clicks=True, min_clicks=10)
        qs = pd.Series([2, 3, 3, 10])
        clicks = pd.Series([50, 5, 20, 20])
        mask = m.passes_mask(qs, clicks)
        assert list(mask) == [False, False, True, True]


class TestBucketingPolicyScalar:
    """Preserves behavior of the pre-refactor scalar path."""

    def test_top_bucket(self):
        row = pd.Series({"A": 100, "B": 10, "Clicks": 50, "CPQS": 10.0})
        assert _default_policy().determine_bucket(row) == "Top"

    def test_weak_bucket(self):
        row = pd.Series({"A": 100, "B": 5, "Clicks": 50, "CPQS": 50.0})
        assert _default_policy().determine_bucket(row) == "Weak"

    def test_average_bucket_between_thresholds(self):
        row = pd.Series({"A": 100, "B": 5, "Clicks": 50, "CPQS": 25.0})
        assert _default_policy().determine_bucket(row) == "Average"

    def test_no_quote_starts(self):
        row = pd.Series({"A": 50, "B": 0, "Clicks": 10, "CPQS": np.nan})
        assert _default_policy().determine_bucket(row) == "No Quote Starts"

    def test_no_qs_below_min_becomes_average(self):
        row = pd.Series({"A": 50, "B": 0, "Clicks": 2, "CPQS": np.nan})
        assert _default_policy().determine_bucket(row) == "Average"

    def test_top_requires_minimum_qs(self):
        # CPQS qualifies for Top but QS below minimum → Average
        row = pd.Series({"A": 100, "B": 1, "Clicks": 50, "CPQS": 10.0})
        assert _default_policy().determine_bucket(row) == "Average"

    def test_volume_gate_forces_average(self):
        policy = _default_policy(use_volume_gate=True, volume_gate_threshold=100)
        row = pd.Series({"A": 100, "B": 10, "Clicks": 50, "CPQS": 10.0})
        assert policy.determine_bucket(row) == "Average"


class TestApplyPolicyVectorized:
    """Confirms the vectorized path matches the scalar path row-for-row."""

    @pytest.fixture
    def sample_df(self):
        return pd.DataFrame({
            "A": [100, 100, 100, 50, 50, 100, 100, 200],
            "B": [10, 5, 5, 0, 0, 1, 10, 10],
            "Clicks": [50, 50, 50, 10, 2, 50, 50, 500],
            "CPQS": [10.0, 50.0, 25.0, np.nan, np.nan, 10.0, 10.0, 10.0],
            "Cost per Phone+QS": [np.nan] * 8,
            "Cost per SMS+QS": [np.nan] * 8,
            "Cost per Lead": [np.nan] * 8,
        })

    def test_vectorized_matches_scalar(self, sample_df):
        policy = _default_policy()
        vectorized = apply_policy_to_dataframe(sample_df, policy)
        scalar_buckets = sample_df.apply(policy.determine_bucket, axis=1)
        assert list(vectorized["Perf Bucket"]) == list(scalar_buckets)

    def test_bids_assigned_from_buckets(self, sample_df):
        policy = _default_policy()
        result = apply_policy_to_dataframe(sample_df, policy)
        bid_for = {
            "Top": 105, "Average": 100, "Weak": 90, "No Quote Starts": 25,
        }
        for bucket, bid in zip(result["Perf Bucket"], result["Recommended Bid %"]):
            assert bid == bid_for[bucket]

    def test_volume_gate_applied(self, sample_df):
        policy = _default_policy(use_volume_gate=True, volume_gate_threshold=100)
        result = apply_policy_to_dataframe(sample_df, policy)
        # Only the last row has Clicks=500 ≥ 100 — the rest should be Average
        assert list(result["Perf Bucket"])[:-1] == ["Average"] * 7

    def test_handles_missing_B_column(self):
        df = pd.DataFrame({"A": [100, 0], "Clicks": [50, 50], "CPQS": [10.0, np.nan]})
        result = apply_policy_to_dataframe(df, _default_policy())
        # Row 0: A>0, no B column so B treated as 0 → no_qs_cond → No Quote Starts
        # Row 1: A==0 → value path, but CPQS=NaN → Average
        assert result["Perf Bucket"].tolist() == ["No Quote Starts", "Average"]

    def test_cost_per_lead_basis(self):
        df = pd.DataFrame({
            "A": [100, 100],
            "B": [10, 10],
            "Clicks": [50, 50],
            "CPQS": [100.0, 100.0],           # would be Weak under CPQS only
            "Cost per Lead": [10.0, 50.0],    # Top, Weak under Cost per Lead
            "Cost per Phone+QS": [np.nan, np.nan],
            "Cost per SMS+QS": [np.nan, np.nan],
        })
        policy = _default_policy(bucket_basis="Cost per Lead only")
        result = apply_policy_to_dataframe(df, policy)
        assert result["Perf Bucket"].tolist() == ["Top", "Weak"]


class TestEfficiencyMetrics:
    def test_cpqs_skips_zero_qs(self):
        df = pd.DataFrame({
            "A": [100, 100],
            "B": [10, 0],
            "C": [0, 0],
            "D": [0, 0],
        })
        result = calculate_efficiency_metrics(df, "CPQS")
        assert result["CPQS"].iloc[0] == 10.0
        assert pd.isna(result["CPQS"].iloc[1])

    def test_cost_per_lead_combines_channels(self):
        df = pd.DataFrame({
            "A": [100],
            "B": [2],
            "C": [3],
            "D": [5],
        })
        result = calculate_efficiency_metrics(df, "CPQS")
        assert result["Cost per Lead"].iloc[0] == pytest.approx(100 / 10)


class TestTransformBidPercentage:
    def test_round_and_clamp(self):
        assert transform_bid_percentage(100, 1.13, round_to=5, clamp_min=0, clamp_max=300) == 115
        assert transform_bid_percentage(100, 0.00, round_to=5, clamp_min=10, clamp_max=300) == 10
        assert transform_bid_percentage(100, 10.0, round_to=5, clamp_min=0, clamp_max=300) == 300


class TestPolicySignature:
    def test_signature_stable_across_instances(self):
        p1 = _default_policy()
        p2 = _default_policy()
        assert p1.get_signature() == p2.get_signature()

    def test_signature_changes_when_thresholds_change(self):
        p1 = _default_policy()
        p2 = _default_policy(top_threshold=15.0)
        assert p1.get_signature() != p2.get_signature()
