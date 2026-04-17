"""Tests for data_utils helpers."""

import numpy as np
import pandas as pd
import pytest

from data_utils import (
    canonical_id,
    extract_click_keys_from_campaigns,
    find_column,
    format_number,
    hash_dataframe_columns,
    make_stable_row_key,
    normalize_device,
    normalize_name,
    to_numeric_safe,
)


class TestNormalizeName:
    @pytest.mark.parametrize("given,expected", [
        ("Click Key", "clickkey"),
        ("Clicks", "click"),  # trailing 's' stripped
        ("SMS", "sms"),       # exception preserved
        ("Campaign ID", "campaignid"),
        ("  spaced  ", "spaced"),
    ])
    def test_cases(self, given, expected):
        assert normalize_name(given) == expected


class TestFindColumn:
    def test_exact_match(self):
        df = pd.DataFrame({"Click Key": [], "Other": []})
        assert find_column(df, ["ClickID", "Click Key"]) == "Click Key"

    def test_alias_via_normalization(self):
        df = pd.DataFrame({"clickid": []})
        assert find_column(df, ["Click ID"]) == "clickid"

    def test_returns_none_on_empty(self):
        assert find_column(pd.DataFrame(), ["Anything"]) is None
        assert find_column(None, ["Anything"]) is None


class TestToNumericSafe:
    def test_default_fills_nan_with_zero(self):
        s = pd.Series(["5", "", "bad", "10.5"])
        out = to_numeric_safe(s)
        assert out.isna().sum() == 0
        assert out.tolist() == [5.0, 0.0, 0.0, 10.5]

    def test_preserves_nan_when_fill_none(self):
        s = pd.Series(["5", "", "bad", "10.5"])
        out = to_numeric_safe(s, fill_na=None)
        assert out.isna().sum() == 2

    def test_strips_currency_symbols(self):
        s = pd.Series(["$1,234.56", "€42"])
        out = to_numeric_safe(s)
        assert out.tolist() == [1234.56, 42.0]

    def test_numeric_input_passthrough(self):
        s = pd.Series([1.0, np.nan, 3.0])
        out = to_numeric_safe(s)
        assert out.tolist() == [1.0, 0.0, 3.0]


class TestCanonicalId:
    @pytest.mark.parametrize("given,expected", [
        ("ABC-123", "abc123"),
        ("  abc ", "abc"),
        ("42.0", "42"),         # trailing .0 from stringified float stripped
        (None, ""),
        (42, "42"),
    ])
    def test_cases(self, given, expected):
        assert canonical_id(given) == expected


class TestNormalizeDevice:
    @pytest.mark.parametrize("given,expected", [
        ("mobile", "mobile"),
        ("iPhone", "mobile"),
        ("Desktop", "desktop"),
        ("iPad", "tablet"),
        ("Smart TV", ""),
        (None, ""),
    ])
    def test_cases(self, given, expected):
        assert normalize_device(given) == expected


class TestHashDataframeColumns:
    def test_same_data_same_hash(self):
        df1 = pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})
        df2 = pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})
        assert hash_dataframe_columns(df1, ["a", "b"]) == hash_dataframe_columns(df2, ["a", "b"])

    def test_different_data_different_hash(self):
        df1 = pd.DataFrame({"a": [1, 2]})
        df2 = pd.DataFrame({"a": [1, 3]})
        assert hash_dataframe_columns(df1, ["a"]) != hash_dataframe_columns(df2, ["a"])


class TestMakeStableRowKey:
    def test_uses_qmpid_when_present(self):
        df = pd.DataFrame({"QMPID": ["A", "B"], "Device": ["mobile", "desktop"]})
        keys = make_stable_row_key(df, "Device")
        assert keys.tolist() == ["A||mobile", "B||desktop"]

    def test_fallback_without_qmpid(self):
        df = pd.DataFrame({"Name": ["x", "y"]})
        keys = make_stable_row_key(df, None)
        assert keys.tolist() == ["x", "y"]


class TestExtractClickKeys:
    def test_finds_longest_match(self):
        series = pd.Series(["cmp_ABCDEF_foo", "cmp_ABC_bar"])
        out = extract_click_keys_from_campaigns(series, ["ABC", "ABCDEF"])
        assert out.tolist() == ["ABCDEF", "ABC"]

    def test_case_insensitive(self):
        series = pd.Series(["cmp_abc_123"])
        out = extract_click_keys_from_campaigns(series, ["ABC"])
        assert out.iloc[0].lower() == "abc"


class TestFormatNumber:
    def test_currency(self):
        assert format_number(1234.5, as_currency=True) == "$1,234.50"

    def test_plain(self):
        assert format_number(1234.5) == "1,234.50"

    def test_nan_returns_empty(self):
        assert format_number(np.nan) == ""
