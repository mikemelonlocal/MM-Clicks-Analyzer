# config.py
"""Configuration constants for MM Click Analyzer."""

from typing import Dict

# Application metadata
APP_TITLE = "MM Click Analyzer"
APP_ICON = "📊"
APP_LAYOUT = "wide"
APP_CAPTION = "Clicks + Stats → Leads, buckets, bids, export. Light theme only."

# Brand colors
BRAND: Dict[str, str] = {
    "green": "#47B74F",
    "yellow": "#F1CB20",
    "accent": "#E9736E",
    "neutral": "#FEF8E9",
    "panel": "#FFFFFF",
    "text": "#0F172A",
}

# Performance bucket icons
BUCKET_ICONS: Dict[str, str] = {
    "Top": "🟢",
    "Average": "🟡",
    "Weak": "🔴",
    "No Quote Starts": "⚠️",
    "No efficiency": "•",
}

# Column name aliases for auto-detection
QMPID_ALIASES = ["QMPID", "QMP ID", "QMP_Id", "QMP Id"]
CLICK_KEY_ALIASES = ["click key", "Click Key", "ClickID", "Click Id", "ClickId", "Click-Key"]
SPEND_ALIASES = ["Spend", "Cost", "Media Cost", "Total Cost", "Amount", "Budget"]
CAMPAIGN_ID_ALIASES = ["Campaign IDs", "Campaign ID", "Campaign"]
QUOTE_START_ALIASES = ["Quote Starts", "Quotes", "Leads", "QS"]
PHONE_ALIASES = ["Phone Clicks", "Calls", "PhoneCalls"]
SMS_ALIASES = ["SMS Clicks", "Texts", "SMS"]

# Default values
DEFAULT_BASELINE_CPL = 25.0
DEFAULT_LOWER_MARGIN = 5.0
DEFAULT_UPPER_MARGIN = 5.0
DEFAULT_TOP_CAP = 20.0
DEFAULT_AVG_CEILING = 30.0

DEFAULT_BID_TOP = 105
DEFAULT_BID_MID = 100
DEFAULT_BID_WEAK = 90
DEFAULT_BID_ZERO = 25

DEFAULT_MIN_QS = 3
DEFAULT_MIN_CLICKS = 10

# Device multipliers
DEFAULT_MOBILE_MULT = 1.00
DEFAULT_DESKTOP_MULT = 1.00
DEFAULT_TABLET_MULT = 1.00
DEFAULT_ROUND_TO = 5
DEFAULT_CLAMP_MIN = 0
DEFAULT_CLAMP_MAX = 300

# Regex chunk size for performance
REGEX_CHUNK_SIZE = 3000

# Export warning threshold
EXPORT_WARNING_THRESHOLD = 120

# Session state keys (constants to avoid typos)
class SessionKeys:
    """Session state key constants."""
    EXCLUDE_UNKNOWN = "exclude_unknown"
    RANGE_MODE = "range_mode"
    GROUP_COLS = "group_cols"
    INCLUDE_DEVICE = "include_device"
    A_LABEL = "a_label"
    A_FMT_CURRENCY = "a_fmt_currency"
    A_COLS = "a_cols"
    USE_ZERO_A = "use_zero_A"
    FILTER_NO_A = "filter_no_a"
    EFF_LABEL = "eff_label"
    EFF_FMT_CURRENCY = "eff_fmt_currency"
    BUCKET_BASIS = "bucket_basis"
    BASELINE_CPL = "baseline_cpl"
    LINK_TO_BASELINE = "link_to_baseline"
    LOWER_MARGIN = "lower_margin"
    UPPER_MARGIN = "upper_margin"
    TOP_CAP = "top_cap"
    AVG_CEILING = "avg_ceiling"
    BID_TOP = "bid_top"
    BID_MID = "bid_mid"
    BID_WEAK = "bid_weak"
    BID_ZERO = "bid_zero"
    MOBILE_MULT = "mobile_mult"
    DESKTOP_MULT = "desktop_mult"
    TABLET_MULT = "tablet_mult"
    EXPORT_SELECTION = "export_selection"
    BID_OVERRIDES = "bid_overrides"
    PREV_POLICY_SIG = "prev_policy_sig"
    RESULTS_EDITOR_VERSION = "results_editor_version"
    CHANGE_INCLUDE_MAP = "change_include_map"
    PREV_CHANGE_SIG = "prev_change_sig"
    USE_VOLUME_GATE = "use_volume_gate"
    VOLUME_GATE_THRESHOLD = "volume_gate_click_threshold"
    MIN_B_FOR_TOP = "min_B_for_top"
