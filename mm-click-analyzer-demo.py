from __future__ import annotations
# lead_source_analyzer.py
# -*- coding: utf-8 -*-

import io
import json
import re
import sys
import hashlib
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd
import streamlit as st
import altair as alt

from st_aggrid import (
    AgGrid,
    GridOptionsBuilder,
    GridUpdateMode,
    ColumnsAutoSizeMode,
    DataReturnMode,
)

# -----------------------------
# Page / brand (light only)
# -----------------------------
APP_DIR = Path.cwd()
ASSETS = APP_DIR / "assets"
ASSETS.mkdir(exist_ok=True)

st.set_page_config(page_title="MM Click Analyzer", page_icon="📊", layout="wide")

# Initialize sub-segmentation variables (will be set properly after file load)
subseg_timestamp_available = False
subseg_geo_col = None

st.markdown(""")
<style>
@import url('https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700&display=swap');

/* Base Typography */
html, body, [class*="css"] {
    font-family: "Poppins", sans-serif;
}

/* Melon Local Colors */
.melon-cactus { background-color: #47B74F; color: white; }
.melon-pine { background-color: #114E38; color: white; }
.melon-mojave { background-color: #CFBA97; color: #644414; }
.melon-alpine { background-color: #FEF8E9; color: #644414; }

/* Light theme default */
[data-testid="stAppViewContainer"] { background: #FEF8E9; }
section[data-testid="stSidebar"] { background: #EDDFDB; }

h1, h2, h3, h4 { 
    color: #114E38; 
    font-weight: 600; 
}

/* All buttons - Cactus green */
button, .stButton > button, .stDownloadButton > button, [data-testid="stFileUploader"] button {
    background-color: #47B74F !important;
    color: white !important;
    border-radius: 6px !important;
    font-weight: 500 !important;
}

button:hover, .stButton > button:hover {
    background-color: #368E40 !important;
}

[data-testid="stMetricValue"] { 
    color: #47B74F !important; 
    font-weight: 700; 
}

/* ========== DARK MODE ========== */
@media (prefers-color-scheme: dark) {
    /* Expander headers - Mojave background */
    .streamlit-expanderHeader {
        background-color: #CFBA97 !important;
        color: #114E38 !important;
    }
    
    .streamlit-expanderHeader:hover {
        background-color: #C0AA87 !important;
    }
    
    /* White backgrounds for readability in dark mode */
    .streamlit-expanderContent {
        background-color: white !important;
    }
    
    .streamlit-expanderContent * {
        color: #171717 !important;
    }
    
    /* Tables - white backgrounds in dark mode */
    .stDataFrame {
        background-color: white !important;
    }
    
    .stDataFrame table {
        background-color: white !important;
    }
    
    .stDataFrame tbody tr td {
        background-color: white !important;
        color: #171717 !important;
    }
    
    /* File uploader - white in dark mode */
    [data-testid="stFileUploader"] {
        background-color: white !important;
    }
    
    [data-testid="stFileUploader"] * {
        color: #171717 !important;
    }
    
    /* Main content text - force dark on light */
    .main * {
        color: #171717 !important;
    }
    
    /* Keep headers Cactus green in dark mode */
    h1, h2, h3, h4 {
        color: #47B74F !important;
    }
}
</style>
""", unsafe_allow_html=True)

def info_badge(text: str, label: str="ℹ️ Info"):
    st.markdown(f'<span class="badge-info" title="{text}">{label}</span>', unsafe_allow_html=True)

# Keep Altair simple (avoid theme callable bugs on v4)
alt.themes.enable("none")

# -----------------------------
# Small helpers / session
# -----------------------------
def ss_default(key, value):
    if key not in st.session_state:
        st.session_state[key] = value
    return st.session_state[key]

def normalize(name: str) -> str:
    s = re.sub(r"[\s_]+", "", str(name).lower())
    s = re.sub(r"[^\w]", "", s)
    if s.endswith("s") and s not in ("sms"):
        s = s[:-1]
    return s

def find_column(df: pd.DataFrame, aliases) -> str | None:
    if df is None or df.empty:
        return None
    nm = {normalize(c): c for c in df.columns}
    for a in aliases:
        key = normalize(a)
        if key in nm:
            return nm[key]
    for c in df.columns:
        nc = normalize(c)
        if any(normalize(a) in nc for a in aliases):
            return c
    return None

def to_num(s: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(s):
        return s.fillna(0.0)
    return pd.to_numeric(s.astype(str).str.replace(r"[^0-9.\-]", "", regex=True), errors="coerce").fillna(0.0)

def canon_id(x: str) -> str:
    s = str(x or "").strip()
    if s.endswith(".0"):
        s = s[:-2]
    s = s.lower()
    s = re.sub(r"[^a-z0-9]", "", s)
    return s

def normalize_device(val: str) -> str:
    v = (str(val or "")).strip().lower()
    flat = re.sub(r"[^a-z0-9]", "", v)
    if flat in {"mobile","m","iphone","android","phone"}: return "mobile"
    if flat in {"desktop","pc","computer","mac","windows"}: return "desktop"
    if flat in {"tablet","tab","ipad"}: return "tablet"
    return ""

def hash_df_cols(df: pd.DataFrame, cols: list[str]) -> str:
    if df is None or df.empty: return ""
    snap = df[cols].astype(str).agg("|".join, axis=1).tolist()
    blob = "\n".join(snap)
    return hashlib.sha256(("|".join(cols) + "||" + blob).encode()).hexdigest()[:16]

def make_row_key(df: pd.DataFrame, device_col: str | None) -> pd.Series:
    """Internal stable key (not shown)."""
    if "QMPID" in df.columns:
        q = df["QMPID"].astype(str).str.strip()
        d = df[device_col].map(normalize_device).fillna("") if device_col and device_col in df.columns else ""
        return q + "||" + (d if isinstance(d, pd.Series) else pd.Series([d]*len(df), index=df.index))
    # fallback — use first grouping col
    first = df.columns[0]
    return df[first].astype(str).str.strip()

# -----------------------------
# Sidebar (presets only)
# -----------------------------
with st.sidebar:
    st.header("Settings")
    st.caption("Save or load app settings (presets).")
    if st.button("💾 Save preset", use_container_width=True):
        payload = json.dumps({k:v for k,v in st.session_state.items() if not k.startswith("_")}, indent=2)
        st.download_button(
            "⬇️ Download preset",
            data=payload,
            file_name="mm_lead_analyzer_preset.json",
            mime="application/json",
            use_container_width=True
        )
    up = st.file_uploader("Load preset", type=["json"], key="preset_up")
    if up is not None:
        try:
            data = json.load(up)
            for k,v in data.items():
                if isinstance(k,str) and not k.startswith("_"):
                    st.session_state[k] = v
            st.session_state["preset_loaded"] = True
            st.session_state["economics_expanded"] = False  # Collapse economics when preset loaded
            st.rerun()
        except Exception as e:
            st.error(f"Failed to load preset: {e}")
    
    st.markdown("---")
    st.subheader("🕐 Sub-Segmentation")
    st.caption("Analyze performance by time and geography")
    
    enable_subseg = st.checkbox(
        "Enable time/geo sub-segmentation",
        value=ss_default("enable_subseg", False),
        help="Break down each source's CPQS by day/hour/geography to find scheduling opportunities"
    )
    
    subseg_dimensions = []
    subseg_min_clicks = 5
    
    if enable_subseg:
        subseg_timestamp_available = st.session_state.get("subseg_timestamp_available", False)
        subseg_geo_columns = st.session_state.get("subseg_geo_columns", {})
        
        if not subseg_timestamp_available:
            st.warning("⚠️ Timestamp column not found or unparseable — time sub-segmentation unavailable.")
        
        # Build available dimensions
        available_dims = []
        if subseg_timestamp_available:
            available_dims.extend(["Day of Week", "Daypart", "Hour of Day"])
        
        # Add each geography type as a separate option
        for geo_type in subseg_geo_columns.keys():
            available_dims.append(f"Geography: {geo_type}")
        
        if available_dims:
            subseg_dimensions = st.multiselect(
                "Sub-dimensions to analyze",
                options=available_dims,
                default=[],
                key="subseg_dimensions"
            )
            
            subseg_min_clicks = st.number_input(
                "Min clicks per sub-segment cell",
                min_value=1,
                max_value=100,
                value=ss_default("subseg_min_clicks", 5),
                step=1,
                key="subseg_min_clicks",
                help="Cells below this threshold are greyed out"
            )

st.title("📊 MM Click Analyzer")
st.caption("Clicks + Stats → Leads, buckets, bids, export. Light theme only.")

# Mode Toggle
col_mode1, col_mode2, col_mode3 = st.columns([1, 2, 1])
with col_mode2:
    mode = st.radio(
        "Interface Mode",
        ["🚀 Simple Mode", "🔧 Advanced Mode"],
        index=0 if ss_default("simple_mode", True) else 1,
        horizontal=True,
        help="Simple: Quick workflow with smart defaults. Advanced: Full control over all settings."
    )
    simple_mode = (mode == "🚀 Simple Mode")
    st.session_state["simple_mode"] = simple_mode
    
    if simple_mode:
        st.info("💡 **Simple Mode:** Upload files → See results → Download modifiers. Advanced settings hidden.")
    
    # Debug toggle (Advanced mode only)
    if not simple_mode:
        show_debug = st.checkbox("Show debug messages", value=ss_default("show_debug", False), key="show_debug")
    else:
        show_debug = False

st.markdown("---")

# -----------------------------
# 1) Upload Files
# -----------------------------
with st.expander("1) Upload Files", expanded=True):
    # Initialize file lists at the top level
    click_files = []
    stats_files = []
    modifier_files = []
    
    # QMP API Integration
    st.markdown("### 🔗 Option 1: Pull from QMP API")
    
    use_api = st.checkbox("Use QMP API to auto-download click reports", value=False, key="use_qmp_api")
    
    if use_api:
        col1, col2 = st.columns(2)
        
        with col1:
            # Try to load saved credentials
            import os
            import json
            creds_file = os.path.expanduser("~/.mm_click_analyzer_qmp_creds")
            saved_client_id = ""
            saved_client_secret = ""
            
            if os.path.exists(creds_file):
                try:
                    with open(creds_file, 'r') as f:
                        creds = json.load(f)
                        saved_client_id = creds.get('client_id', '')
                        saved_client_secret = creds.get('client_secret', '')
                except:
                    pass
            
            client_id = st.text_input(
                "QMP Client ID",
                value=saved_client_id,
                help="Your QMP API Client ID",
                key="qmp_client_id"
            )
            
            client_secret = st.text_input(
                "QMP Client Secret",
                type="password",
                value=saved_client_secret,
                help="Your QMP API Client Secret (saved locally)",
            )
            
            # Save/Clear options
            col_save, col_clear = st.columns(2)
            with col_save:
                if st.button("💾 Save Credentials", help="Save for future sessions"):
                    try:
                        with open(creds_file, 'w') as f:
                            json.dump({'client_id': client_id, 'client_secret': client_secret}, f)
                        st.success("✅ Credentials saved!")
                    except Exception as e:
                        st.error(f"❌ Could not save: {e}")
            
            with col_clear:
                if st.button("🗑️ Clear Saved Creds", help="Remove saved credentials"):
                    try:
                        if os.path.exists(creds_file):
                            os.remove(creds_file)
                        st.success("✅ Credentials cleared! Refresh the page to see changes.")
                    except Exception as e:
                        st.error(f"❌ Could not clear: {e}")
            
            # Product selection
            products_to_pull = st.multiselect(
                "Select Products to Pull",
                options=["Auto Insurance", "Home Insurance"],
                default=["Auto Insurance", "Home Insurance"],
                help="Which products to download from QMP"
            )
        
        with col2:
            st.markdown("**Date Range**")
            # Default: Last 31 complete days (excluding today) - matches QMP default
            from datetime import datetime, timedelta
            default_end = datetime.now().date() - timedelta(days=1)  # Yesterday
            default_start = default_end - timedelta(days=30)  # 31 days total (including both ends)
            
            date_end = st.date_input("End Date", value=default_end, help="Last day to include (yesterday by default)", format="MM/DD/YYYY")
            date_start = st.date_input("Start Date", value=default_start, help="First day to include", format="MM/DD/YYYY")
        
        if st.button("📥 Pull Data from QMP", disabled=not (client_id and client_secret and products_to_pull)):
            import requests
            from requests.auth import HTTPBasicAuth
            import io
            
            click_files = []
            
            # Hardcoded report IDs
            REPORT_IDS = {
                "Auto Insurance": "84870",
                "Home Insurance": "84880"
            }
            
            # Helper function to get OAuth2 access token
            def get_access_token():
                try:
                    # Create Basic Auth header
                    credentials = f"{client_id}:{client_secret}"
                    encoded_credentials = base64.b64encode(credentials.encode()).decode()
                    
                    # Attempt OAuth2 token endpoint (common pattern)
                    # QMP might use a token endpoint - trying common patterns
                    token_url = "https://reporting.qmp.ai/oauth/token"
                    headers = {
                        "Authorization": f"Basic {encoded_credentials}",
                        "Content-Type": "application/x-www-form-urlencoded"
                    }
                    data = {
                        "grant_type": "client_credentials"
                    }
                    
                    response = requests.post(token_url, headers=headers, data=data, timeout=10)
                    
                    if response.status_code == 200:
                        return response.json().get('access_token')
                    else:
                        # If OAuth endpoint doesn't exist, return Basic Auth string
                        return encoded_credentials
                except:
                    # Fallback to Basic Auth
                    return base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
            
            # Step 1: Get OAuth2 access token
            def get_access_token():
                token_url = 'https://reporting.qmp.ai/oauth/generatetoken?grant_type=client_credentials'
                try:
                    response = requests.post(
                        token_url, 
                        auth=HTTPBasicAuth(client_id, client_secret),
                        timeout=10
                    )
                    if response.status_code == 200:
                        response_data = response.json()
                        return response_data.get('access_token')
                    else:
                        st.error(f"❌ Failed to get access token: {response.status_code}")
                        if response.text:
                            st.write(f"Response: {response.text[:200]}")
                        return None
                except Exception as e:
                    st.error(f"❌ Error getting access token: {e}")
                    return None
            
            with st.spinner("Getting access token from QMP..."):
                access_token = get_access_token()
            
            if not access_token:
                st.error("❌ Could not authenticate with QMP. Check your Client ID and Secret.")
                st.stop()
            
            st.success("✅ Successfully authenticated with QMP!")
            
            # Step 2: Helper function to fetch report using access token
            def fetch_qmp_report(report_id, product_name):
                url = f"https://reporting.qmp.ai/api/client/download/{report_id}"
                
                # Build URL with date params
                if date_start and date_end:
                    url = f"{url}?startDate={date_start.strftime('%Y-%m-%d')}&endDate={date_end.strftime('%Y-%m-%d')}"
                
                headers = {
                    'Authorization': f'Bearer {access_token}'
                }
                
                try:
                    with st.spinner(f"Fetching {product_name} report (all agents)... This may take up to 3 minutes for large date ranges."):
                        response = requests.get(url, headers=headers, timeout=180)  # 3 minute timeout for all-agent pulls
                        
                        if response.status_code == 200:
                            # Success!
                            content_type = response.headers.get('content-type', '')
                            
                            # QMP returns JSON
                            try:
                                data = response.json()
                                
                                # Extract records from the nested structure
                                # QMP API returns: {"data": {"records": [{"0": val, "1": val, ...}]}}
                                if isinstance(data, dict) and 'data' in data:
                                    if isinstance(data['data'], dict) and 'records' in data['data']:
                                        # New structure: data.records array with numbered columns
                                        df = pd.DataFrame(data['data']['records'])
                                    elif isinstance(data['data'], list):
                                        # Old structure: data array directly
                                        df = pd.DataFrame(data['data'])
                                    else:
                                        df = pd.DataFrame([data['data']])
                                elif isinstance(data, list):
                                    df = pd.DataFrame(data)
                                else:
                                    df = pd.DataFrame([data])
                                
                                # === COLUMN MAPPING: QMP API uses numbered columns (0, 1, 2, ...) ===
                                # Based on actual API response analysis, here's the mapping:
                                # Column 12: "2026-02-22 10:16:46" = Click Date Timestamp
                                # Column 13: 502648142 = Click ID/Key
                                # Column 16: 1 = Clicks
                                # Column 17: 3.90 = Cost
                                # Column 10: "Safari" = Browser/Device
                                # Column 3: Campaign name
                                # Column 18: Agent name
                                
                                # Check if columns are numbered (0, 1, 2, ...) vs named
                                cols_are_numbered = all(str(col).isdigit() for col in df.columns)
                                
                                if cols_are_numbered:
                                    st.info("🔢 API returned numbered columns - applying position-based mapping")
                                    
                                    # Detect product type by looking at column count and sample data
                                    num_cols = len(df.columns)
                                    product_type = None
                                    
                                    # Check if we can detect product from data
                                    # Auto Insurance typically has 115 columns, Home has 91 columns
                                    if num_cols > 100:
                                        product_type = "Auto Insurance"
                                        st.caption(f"📊 Detected: **Auto Insurance** (based on {num_cols} columns)")
                                    elif num_cols >= 85 and num_cols <= 95:
                                        product_type = "Home Insurance"
                                        st.caption(f"📊 Detected: **Home Insurance** (based on {num_cols} columns)")
                                    else:
                                        st.warning(f"⚠️ Unknown product type ({num_cols} columns) - using Auto Insurance mapping as default")
                                        product_type = "Auto Insurance"
                                    
                                    # AUTO INSURANCE MAPPING (115 columns) - COMPLETE
                                    numbered_column_mapping_auto = {
                                        '0': 'AB Test Variant Name',
                                        '1': 'Accidents At Fault',
                                        '2': 'Address Present',
                                        '3': 'Advertiser Account Name',
                                        '4': 'Age',
                                        '5': 'Age Group',
                                        '6': 'Annual Mileage',
                                        '7': 'Auto Home Bundle',
                                        '8': 'Bankruptcy ?',
                                        '9': 'Bodily Injury Limits',
                                        '10': 'Browser Name',
                                        '11': 'Cellular Connection',
                                        '12': 'Click Date Timestamp',
                                        '13': 'Click ID',
                                        '14': 'Click key',
                                        '15': 'Click Tracking URL',
                                        '16': 'Clicks',
                                        '17': 'Clicks Spend($)',
                                        '18': 'Client Name',
                                        '19': 'Collision Deductable',
                                        '20': 'Comprehensive Deductable',
                                        '21': 'Conversion P1',
                                        '22': 'Conversion P2',
                                        '23': 'Conversion P3',
                                        '24': 'Conversion P4',
                                        '25': 'Cost($)',
                                        '26': 'CPA Spend($)',
                                        '27': 'Created Date',
                                        '28': 'Creative',
                                        '29': 'Creative ID',
                                        '30': 'Credit Rating',
                                        '31': 'Currently Insured',
                                        '32': 'Data Pass Available',
                                        '33': 'Datapass Attempted',
                                        '34': 'Day Of Week',
                                        '35': 'Days until Insurance Expires',
                                        '36': 'Device OS',
                                        '37': 'Device Type',
                                        '38': 'DMA',
                                        '39': 'DPClicks',
                                        '40': 'Driver Count',
                                        '41': 'DUI',
                                        '42': 'Education',
                                        '43': 'Email Present',
                                        '44': 'Exchange Name',
                                        '45': 'Gender',
                                        '46': 'Home Owner',
                                        '47': 'Hour Of Day',
                                        '48': 'Household Income',
                                        '49': 'Impr.',
                                        '50': 'Incident Count',
                                        '51': 'Insurance Carrier',
                                        '52': 'Insured Time Frame',
                                        '53': 'Inventory Type',
                                        '54': 'IP Address',
                                        '55': 'Length Of Time With Current Insurer',
                                        '56': 'License Status',
                                        '57': 'License Suspension',
                                        '58': 'Lifetime Value',
                                        '59': 'Marital Status',
                                        '60': 'Media Channel',
                                        '61': 'Military Affiliation',
                                        '62': 'Month Of Year',
                                        '63': 'Months At Current Home',
                                        '64': 'Name Present',
                                        '65': 'Occupation',
                                        '66': 'One Way Distance To Work',
                                        '67': 'Phone Present',
                                        '68': 'Placement Name',
                                        '69': 'Policy Premium($)',
                                        '70': 'Policy Start Date',
                                        '71': 'Policy Type',
                                        '72': 'Product',
                                        '73': 'ProxyCreditRating',
                                        '74': 'ProxyCreditScore ',
                                        '75': 'Publisher Company',
                                        '76': 'QMPID',
                                        '77': 'QS O&O Media',
                                        '78': 'Quality Attribute 1',
                                        '79': 'Quality Attribute 2',
                                        '80': 'Quality Attribute 3',
                                        '81': 'Quality Attribute 4',
                                        '82': 'Quality Value 1',
                                        '83': 'Quality Value 2',
                                        '84': 'Quality Value 3',
                                        '85': 'Quality Value 4',
                                        '86': 'Quote',
                                        '87': 'Rank',
                                        '88': 'Referring Domain',
                                        '89': 'Region',
                                        '90': 'Residence Type',
                                        '91': 'Sales',
                                        '92': 'Search Date Timestamp',
                                        '93': 'Segment',
                                        '94': 'SR-22',
                                        '95': 'State',
                                        '96': 'State Code',
                                        '97': 'Supplier',
                                        '98': 'Telematics',
                                        '99': 'Ticket Count',
                                        '100': 'Vehicle Count',
                                        '101': 'Vehicle Daily Mileage',
                                        '102': 'Vehicle Make',
                                        '103': 'Vehicle Make Present',
                                        '104': 'Vehicle Model',
                                        '105': 'Vehicle Model Present',
                                        '106': 'Vehicle Ownership',
                                        '107': 'Vehicle Sub Model',
                                        '108': 'Vehicle Usage',
                                        '109': 'Vehicle Vin Present',
                                        '110': 'Vehicle Year',
                                        '111': 'Vehicle Year Present',
                                        '112': 'Year',
                                        '113': 'Years Licensed For',
                                        '114': 'Zip Code',
                                    }
                                    
                                    # HOME INSURANCE MAPPING (91 columns) - COMPLETE
                                    numbered_column_mapping_home = {
                                        '0': 'AB Test Variant Name',
                                        '1': 'Advertiser Account Name',
                                        '2': 'Age',
                                        '3': 'Age Group',
                                        '4': 'Auto Home Bundle',
                                        '5': 'Browser Name',
                                        '6': 'Building Age',
                                        '7': 'Cellular Connection',
                                        '8': 'Click Date Timestamp',
                                        '9': 'Click ID',
                                        '10': 'Click key',
                                        '11': 'Click Tracking URL',
                                        '12': 'Clicks',
                                        '13': 'Clicks Spend($)',
                                        '14': 'Client Name',
                                        '15': 'Conversion P1',
                                        '16': 'Conversion P2',
                                        '17': 'Conversion P3',
                                        '18': 'Conversion P4',
                                        '19': 'Cost($)',
                                        '20': 'CPA Spend($)',
                                        '21': 'Created Date',
                                        '22': 'Creative',
                                        '23': 'Creative ID',
                                        '24': 'Credit Rating',
                                        '25': 'Currently Insured',
                                        '26': 'Data Pass Available',
                                        '27': 'Datapass Attempted',
                                        '28': 'Day Of Week',
                                        '29': 'Device OS',
                                        '30': 'Device Type',
                                        '31': 'DMA',
                                        '32': 'DPClicks',
                                        '33': 'Education',
                                        '34': 'Exchange Name',
                                        '35': 'Foundation Type',
                                        '36': 'Garage Type',
                                        '37': 'Gender',
                                        '38': 'Heating Type',
                                        '39': 'Home Add On Insurance',
                                        '40': 'Home Alarm',
                                        '41': 'Home Any Recent Claims',
                                        '42': 'Home Deductible ',
                                        '43': 'Home Features',
                                        '44': 'Home Recent Claim Count',
                                        '45': 'Home Recent Claim Reason',
                                        '46': 'Hour Of Day',
                                        '47': 'Impr.',
                                        '48': 'Insurance Carrier',
                                        '49': 'Insurance Coverage Amount ($)',
                                        '50': 'IP Address',
                                        '51': 'Is Primary Residence',
                                        '52': 'Lifetime Value',
                                        '53': 'Marital Status',
                                        '54': 'Media Channel',
                                        '55': 'Military Affiliation',
                                        '56': 'Month Of Year',
                                        '57': 'Occupation',
                                        '58': 'Placement Name',
                                        '59': 'Policy Premium($)',
                                        '60': 'Policy Type',
                                        '61': 'Product',
                                        '62': 'ProxyCreditRating',
                                        '63': 'ProxyCreditScore ',
                                        '64': 'Publisher Company',
                                        '65': 'QMPID',
                                        '66': 'QS O&O Media',
                                        '67': 'Quality Attribute 1',
                                        '68': 'Quality Attribute 2',
                                        '69': 'Quality Attribute 3',
                                        '70': 'Quality Attribute 4',
                                        '71': 'Quality Value 1',
                                        '72': 'Quality Value 2',
                                        '73': 'Quality Value 3',
                                        '74': 'Quality Value 4',
                                        '75': 'Quote',
                                        '76': 'Rank',
                                        '77': 'Referring Domain',
                                        '78': 'Region',
                                        '79': 'Residence Type',
                                        '80': 'Roof Age',
                                        '81': 'Roof Type',
                                        '82': 'Sales',
                                        '83': 'Search Date Timestamp',
                                        '84': 'Segment',
                                        '85': 'Square Footage',
                                        '86': 'State',
                                        '87': 'State Code',
                                        '88': 'Supplier',
                                        '89': 'Year',
                                        '90': 'Zip Code',
                                    }
                                    
                                    # Select appropriate mapping
                                    if product_type == "Home Insurance":
                                        numbered_column_mapping = numbered_column_mapping_home
                                    else:
                                        numbered_column_mapping = numbered_column_mapping_auto
                                    
                                    df.rename(columns=numbered_column_mapping, inplace=True)
                                    st.success(f"✅ Applied complete column mapping: {len(numbered_column_mapping)} columns renamed")

                                    # Column mapping is hardcoded and verified - no need to show message
                                
                                else:
                                    st.info("📝 API returned named columns - applying name-based mapping")
                                    
                                    # Original name-based mapping for if they ever fix their API
                                    column_mapping = {
                                        # Date/Time columns
                                        'date': 'Click Date Timestamp',
                                        'Date': 'Click Date Timestamp',
                                        'click_date': 'Click Date Timestamp',
                                        'Click Date': 'Click Date Timestamp',
                                        'timestamp': 'Click Date Timestamp',
                                        'Timestamp': 'Click Date Timestamp',
                                        'date_time': 'Click Date Timestamp',
                                        
                                        # Device columns
                                        'device': 'Device Type',
                                        'Device': 'Device Type',
                                        'device_type': 'Device Type',
                                        'browser': 'Device Type',
                                        'Browser': 'Device Type',
                                        
                                        # Publisher columns
                                        'publisher': 'Publisher ID Category',
                                        'Publisher': 'Publisher ID Category',
                                        'publisher_id': 'Publisher ID Category',
                                        'Publisher ID': 'Publisher ID Category',
                                        'agent': 'Publisher ID Category',
                                        'Agent': 'Publisher ID Category',
                                        
                                        # Click identifier columns
                                        'click_id': 'click key',
                                        'Click ID': 'click key',
                                        'clickid': 'click key',
                                        'ClickID': 'click key',
                                        'click_key': 'click key',
                                        'Click Key': 'click key',
                                        
                                        # Campaign columns
                                        'campaign': 'Campaign',
                                        'Campaign': 'Campaign',
                                        'campaign_name': 'Campaign',
                                        
                                        # Cost columns
                                        'cost': 'Cost',
                                        'Cost': 'Cost',
                                        'spend': 'Cost',
                                        'Spend': 'Cost',
                                        
                                        # Clicks columns
                                        'clicks': 'Clicks',
                                        'Clicks': 'Clicks',
                                        'click': 'Clicks',
                                        
                                        # QMP ID columns
                                        'qmpid': 'QMPID',
                                        'qmp_id': 'QMPID',
                                        'QMPID': 'QMPID',
                                    }
                                    
                                    df.rename(columns=column_mapping, inplace=True)
                                    st.success("✅ Name-based column mapping applied")
                                
                                # CRITICAL: Ensure "Click Date Timestamp" exists
                                # Fallback detection for unmapped date columns
                                if 'Click Date Timestamp' not in df.columns:
                                    st.warning("⚠️ 'Click Date Timestamp' not found after mapping - attempting fallback detection")
                                    
                                    # For numbered columns, check column values for timestamps
                                    date_candidates = []
                                    for col in df.columns:
                                        # Check if column contains timestamp-like values
                                        sample_values = df[col].dropna().head(5).astype(str)
                                        if any('-' in str(v) and ':' in str(v) for v in sample_values):
                                            date_candidates.append(col)
                                    
                                    # Also check column names for date keywords
                                    name_candidates = [col for col in df.columns if any(
                                        keyword in str(col).lower() 
                                        for keyword in ['date', 'time', 'timestamp', 'day']
                                    )]
                                    
                                    all_candidates = date_candidates + [c for c in name_candidates if c not in date_candidates]
                                    
                                    if all_candidates:
                                        st.info(f"📅 Found potential date column: '{all_candidates[0]}'")
                                        df.rename(columns={all_candidates[0]: 'Click Date Timestamp'}, inplace=True)
                                        st.success("✅ Fallback mapping applied")
                                    else:
                                        st.error(f"❌ No date column found in API response.")
                                        st.error(f"Available columns: {list(df.columns)}")
                                        st.error("Sample values:")
                                        st.dataframe(df.head(2))
                                        st.error("Cannot proceed without a date column. The numbered column mapping may need updating.")
                                        return None
                                
                                # Column mapping is complete - continue processing
                                
                                # Convert to CSV with proper escaping
                                csv_buffer = io.StringIO()
                                df.to_csv(csv_buffer, index=False, quoting=1, escapechar='\\')  # quoting=1 = QUOTE_ALL
                                csv_buffer.seek(0)
                                
                                file_obj = io.BytesIO(csv_buffer.getvalue().encode('utf-8'))
                                file_obj.name = f"qmp_{product_name.lower().replace(' ', '_')}_report.csv"
                                file_obj.seek(0)
                                click_files.append(file_obj)
                                return True  # Success indicator
                                
                            except json.JSONDecodeError:
                                st.error(f"❌ Could not parse JSON response for {product_name}")
                                return False
                            except Exception as e:
                                st.error(f"❌ Error processing {product_name} data: {e}")
                                st.write(f"Response preview: {response.text[:500]}")
                                return False
                        else:
                            st.error(f"❌ Failed to fetch {product_name}: HTTP {response.status_code}")
                            if response.text:
                                st.write(f"Response: {response.text[:200]}")
                            return False
                            
                except Exception as e:
                    st.error(f"❌ Error fetching {product_name}: {e}")
                    return False
            
            # Fetch selected reports
            for product in products_to_pull:
                report_id = REPORT_IDS[product]
                success = fetch_qmp_report(report_id, product)
                # File is already appended inside fetch_qmp_report if successful
            
            if click_files:
                st.success(f"✅ Successfully pulled {len(click_files)} report(s) from QMP! ({', '.join(products_to_pull)})")
                # Store in session state for processing
                st.session_state['qmp_api_files'] = click_files
            else:
                st.error("❌ Failed to fetch any reports. Check your settings and try again.")
        
        # Use API files if available (from previous button click stored in session state)
        if 'qmp_api_files' in st.session_state and st.session_state['qmp_api_files']:
            click_files = st.session_state['qmp_api_files']
            st.info(f"📊 Using {len(click_files)} report(s) from QMP API")
        
        st.markdown("---")
    
    # Manual file upload (only show if not using API)
    if not use_api:
        st.markdown("### 📁 Option 2: Upload Files Manually")
        st.markdown("**Upload all your files** - the app will automatically detect clicks, stats, and modifier files")
        
        all_files = st.file_uploader(
            "Drag and drop all files here",
            type=["csv", "xlsx", "xls"],
            accept_multiple_files=True,
            key="smart_uploader",
            help="Upload click reports, stats reports, and modifier files - they'll be sorted automatically"
        )
        
        # Auto-detect file types from manual uploads
        if all_files:
            for file in all_files:
                file.seek(0)  # Reset file pointer
                
                # Detect file type based on contents
                if file.name.endswith('.csv'):
                    # Read first few rows to detect type
                    try:
                        df_sample = pd.read_csv(file, nrows=5)
                        file.seek(0)  # Reset for later reading
                        
                        columns_lower = [c.lower() for c in df_sample.columns]
                        
                        # Stats file: has Campaign ID and conversion columns
                        if any('campaign' in c and 'id' in c for c in columns_lower) and \
                           any('quote' in c or 'phone' in c or 'sms' in c for c in columns_lower):
                            stats_files.append(file)
                        # Click file: has Click ID or Click key
                        elif any('click' in c for c in columns_lower) and \
                             (any('publisher' in c for c in columns_lower) or any('device' in c for c in columns_lower)):
                            click_files.append(file)
                        else:
                            # Default to click file if unclear
                            click_files.append(file)
                    except Exception as e:
                        st.warning(f"⚠️ Could not read {file.name}: {e}")
                        
                elif file.name.endswith(('.xlsx', '.xls')):
                    # Check if it's a modifier file
                    try:
                        xl = pd.ExcelFile(file)
                        file.seek(0)
                        
                        # Modifier file: has 'source-modifiers' sheet or modifier columns
                        if 'source-modifiers' in xl.sheet_names:
                            modifier_files.append(file)
                        else:
                            # Check first sheet for modifier columns
                            df_sample = pd.read_excel(file, nrows=5)
                            file.seek(0)
                            columns_lower = [c.lower() for c in df_sample.columns]
                            
                            if any('modifier' in c for c in columns_lower):
                                modifier_files.append(file)
                            else:
                                # Might be a click or stats file in Excel format
                                if any('campaign' in c and 'id' in c for c in columns_lower):
                                    stats_files.append(file)
                                else:
                                    click_files.append(file)
                    except Exception as e:
                        st.warning(f"⚠️ Could not read {file.name}: {e}")
            
            # Show detection results - compact inline format
            col1, col2 = st.columns([3, 1])
            with col1:
                st.success(f"✅ **Auto-detected:** {len(click_files)} click, {len(stats_files)} stats, {len(modifier_files)} modifier file(s)")
            with col2:
                if st.button("🗑️ Clear All", help="Remove all files"):
                    st.session_state.pop("smart_uploader", None)
                    st.session_state.pop("modifier_product_matches", None)
                    st.session_state.pop("cached_modifier_fingerprint", None)
                    st.rerun()
            
            # Handle modifier cache clearing
            if modifier_files:
                current_fingerprint = sorted([(f.name, f.size) for f in modifier_files])
                cached_fingerprint = st.session_state.get("cached_modifier_fingerprint", [])
                
                if current_fingerprint != cached_fingerprint:
                    st.session_state.pop("modifier_product_matches", None)
                    st.session_state["cached_modifier_fingerprint"] = current_fingerprint
                    st.info("🔄 Detected new/updated modifier files - cache cleared")
            else:
                if "modifier_product_matches" in st.session_state:
                    st.session_state.pop("modifier_product_matches", None)
                    st.session_state.pop("cached_modifier_fingerprint", None)

if not click_files:
    st.info("Upload at least one **Click Report CSV** to begin.")
    st.stop()

# Read click files (combine if multiple)
click_dfs = []
for i, click_file in enumerate(click_files):
    try:
        df = pd.read_csv(click_file)
        click_dfs.append(df)
    except Exception:
        click_file.seek(0)
        df = pd.read_csv(click_file, encoding="utf-8", engine="python")
        click_dfs.append(df)

if not click_dfs:
    st.error("Could not read any click files.")
    st.stop()

# ============================================================================
# MULTI-PRODUCT DETECTION AND PROCESSING
# ============================================================================
# Check if files have different column structures (different products)
# If yes: process each file separately through the entire pipeline
# If no: combine files and process together
# ============================================================================

column_counts = [len(df.columns) for df in click_dfs]
unique_column_counts = set(column_counts)

if len(unique_column_counts) > 1:
    # Different column structures = different products
    st.info(f"📦 **Detected {len(click_dfs)} products with different structures** ({', '.join(map(str, unique_column_counts))} columns)\n\n"
           f"Each product will be processed separately: Auto Insurance ({max(column_counts)} cols), Home Insurance ({min(column_counts)} cols)")
    
    process_separately = True
    files_to_process = click_dfs  # Process each file individually
else:
    # Same structure = combine and process together
    process_separately = False
    click_raw = pd.concat(click_dfs, ignore_index=True)
    st.success(f"✅ Combined {len(click_files)} click file(s) → {len(click_raw):,} total clicks")
    files_to_process = [click_raw]  # Process as one combined file

# ============================================================================
# CENTRALIZED AGENT SELECTION (applies to all products)
# ============================================================================
st.markdown("---")
st.markdown("### 👤 Agent Selection")

# Combine all data to detect agents
combined_data = pd.concat(click_dfs, ignore_index=True) if len(click_dfs) > 1 else click_dfs[0]
agent_col = find_column(combined_data, ["Publisher ID Category", "Client Name", "Agent", "Publisher"])

global_agent_filter = "all"  # Default: analyze all agents
selected_agent_name = "All Agents"

if agent_col and agent_col in combined_data.columns:
    available_agents = sorted(combined_data[agent_col].dropna().unique())
    
    if len(available_agents) > 1:
        # Show agent counts
        agent_counts = combined_data[agent_col].value_counts()
        
        with st.expander("📊 Agents in current data (click to expand)", expanded=False):
            st.caption(f"**{len(available_agents)} agents found:**")
            for agent in available_agents[:20]:  # Show first 20
                count = agent_counts.get(agent, 0)
                st.caption(f"• {agent}: {count:,} clicks")
            if len(available_agents) > 20:
                st.caption(f"... and {len(available_agents) - 20} more agents")
        
        # Agent filter mode
        agent_filter_mode = st.radio(
            "Agent analysis mode",
            ["Analyze specific agent", "Analyze all agents together"],
            help="This filter applies to all products. Choose one agent to focus your analysis.",
            key="global_agent_filter_mode"
        )
        
        if agent_filter_mode == "Analyze specific agent":
            # Agent selection dropdown
            selected_agent_name = st.selectbox(
                "Select agent to analyze",
                available_agents,
                help="Only this agent's data will be analyzed across all products",
                key="global_selected_agent_widget"
            )
            global_agent_filter = "specific"
            st.success(f"✅ Filtering all products to: **{selected_agent_name}**")
        else:
            st.info(f"ℹ️ **Analyzing all {len(available_agents)} agents together** (across all products)")
    else:
        # Single agent detected
        selected_agent_name = available_agents[0]
        st.success(f"✅ Single agent detected: **{selected_agent_name}**")
else:
    st.warning("⚠️ Could not detect agent column in data")

# Store globally for use in product loop (use different key names to avoid widget conflicts)
st.session_state['selected_agent_for_analysis'] = selected_agent_name
st.session_state['agent_column_name'] = agent_col
st.markdown("---")

# Now loop through files
for file_idx, click_raw in enumerate(files_to_process):
    # Create unique suffix for widget keys - ALWAYS use file_idx to ensure unique keys
    product_suffix = f"_prod{file_idx}"
    
    if process_separately:
        # Show product header
        product_name = "Auto Insurance" if len(click_raw.columns) > 100 else "Home Insurance"
        st.markdown("---")
        st.markdown(f"# 📦 {product_name}")
        st.success(f"Processing {len(click_raw):,} clicks")
    
    # Continue with the rest of the pipeline for this file
    # All sections below will run for this product

    # Identify columns
    qmpid_col = find_column(click_raw, ["QMPID","QMP ID","QMP_Id","QMP Id","click key","Click key","clickkey"])
    click_key_col = find_column(click_raw, ["click key","Click Key","ClickID","Click Id","ClickId","Click-Key"])
    if "Click Date Timestamp" not in click_raw.columns:
        st.error("Click file must contain 'Click Date Timestamp'.")
        st.stop()
    if not click_key_col:
        st.error("Click file must contain a click identifier column (e.g., 'click key').")
        st.stop()

    # 2) Click Key Filter
    with st.expander("2) Click Key Filter", expanded=False):
        info_badge("Cosmetically filter out clicks missing a click key.")
        exclude_unknown = ss_default(f"exclude_unknown{product_suffix}", True)
        exclude_unknown = st.checkbox("Exclude rows with missing/unknown click key", value=exclude_unknown, key=f"exclude_unknown{product_suffix}")
    unknown_mask = click_raw[click_key_col].isna() | (click_raw[click_key_col].astype(str).str.strip()=="")
    click_work = click_raw.loc[~unknown_mask].copy() if st.session_state.get(f"exclude_unknown{product_suffix}", True) else click_raw.copy()

    # 3) Date Filter
    with st.expander("3) Date Filter", expanded=False):
        info_badge("Defaults to earliest→latest date present in the click file. You can choose a custom range.")
        dts = pd.to_datetime(click_work["Click Date Timestamp"], errors="coerce", infer_datetime_format=True)
        min_d, max_d = dts.min(), dts.max()
        if pd.isna(min_d) or pd.isna(max_d):
            today = pd.Timestamp.now().normalize()
            default_start, default_end = today - pd.Timedelta(days=29), today
        else:
            default_start, default_end = pd.Timestamp(min_d).normalize(), pd.Timestamp(max_d).normalize()

        mode = st.radio("Range mode", ["Full range in file (default)", "Custom range"],
                        horizontal=True, key=f"range_mode{product_suffix}", index=0, )
        def _mk(start, end):
            s = pd.Timestamp(start)
            e = pd.Timestamp(end)+pd.Timedelta(days=1)-pd.Timedelta(milliseconds=1)
            return s, e

        if st.session_state.get(f"range_mode{product_suffix}", "Full range") == "Custom range":
            start_ts, end_ts = _mk(default_start.date(), default_end.date())
            dr = st.date_input("Choose custom date range", (default_start.date(), default_end.date()), key=f"date_input_1{product_suffix}")
            if isinstance(dr, (list,tuple)) and len(dr)==2:
                start_ts, end_ts = _mk(dr[0], dr[1])
            elif dr:
                start_ts, end_ts = _mk(dr, dr)
        else:
            start_ts, end_ts = _mk(default_start.date(), default_end.date())
        st.caption(f"Active date range: {start_ts.date()} → {end_ts.date()}")

    date_mask = pd.to_datetime(click_work["Click Date Timestamp"], errors="coerce").between(start_ts, end_ts)
    click_work = click_work.loc[date_mask].copy()
    if click_work.empty:
        st.error("No click rows in the selected date range.")
        st.stop()

    # -----------------------------
    # 3.5) Product Filter (Auto vs Home)
    # Save original click_work before product filtering - needed for stats matching later
    click_work_unfiltered = click_work.copy()
    # -----------------------------
    product_col = find_column(click_work, ["Product", "product", "Product Type", "Insurance Product"])

    if product_col and product_col in click_work.columns:
        with st.expander("📦 Product Filter (Auto vs Home)", expanded=True):
            st.info("🎯 **QMP Setup:** Auto and Home campaigns export with the same filename (`SourceModifier.xlsx`). "
                    "The app uses the **Product column in your click data** to determine which campaign you're analyzing.")
        
            # Detect available products
            available_products = click_work[product_col].dropna().unique().tolist()
            product_counts = click_work[product_col].value_counts()
        
            st.markdown("**Products detected in your click data:**")
            for prod in available_products:
                count = product_counts.get(prod, 0)
                st.caption(f"• {prod}: {count} clicks")
        
            # Product filter options
            if len(available_products) > 1:
                st.info("📦 **Multiple products detected!** This click file contains both Auto and Home insurance clicks. "
                       "Choose your analysis mode below - both work for QMP export.")
            
                filter_mode = st.radio(
                    "Analysis mode",
                    ["Analyze specific product (Recommended)", "Analyze all products together"],
                    help="Specific product: More focused analysis. All together: Auto-splits QMP exports by product.",
                    key=f"product_filter_mode{product_suffix}"
                )
            
            
                if filter_mode == "Analyze specific product (Recommended)":
                    # Sort products to prioritize Auto Insurance first
                    sorted_products = sorted(available_products, key=lambda x: 0 if x == "Auto Insurance" else 1 if x == "Home Insurance" else 2)
                
                    selected_product = st.selectbox(
                        "Select product to analyze",
                        sorted_products,
                        help="Choose Auto or Home - only sources from this product will be included",
                        key=f"selected_product{product_suffix}"
                    )
                
                    # Clear unmatched stats cache if product changed
                    if f'last_analyzed_product{product_suffix}' in st.session_state and st.session_state.get(f'last_analyzed_product{product_suffix}') != selected_product:
                        st.session_state.pop(f'unmatched_qs{product_suffix}', None)
                        st.session_state.pop(f'unmatched_phone{product_suffix}', None)
                        st.session_state.pop(f'unmatched_sms{product_suffix}', None)
                    st.session_state[f"last_analyzed_product{product_suffix}"] = selected_product
                
                    # Filter to selected product
                    product_mask = click_work[product_col] == selected_product
                    click_work = click_work[product_mask].copy()
                
                    st.success(f"✅ Analyzing **{selected_product}** only ({len(click_work)} clicks)")
                
                    # Apply global agent filter
                    if st.session_state.get('selected_agent_for_analysis') not in ['All Agents', None]:
                        agent_col = st.session_state.get('agent_column_name')
                        selected_agent = st.session_state.get('selected_agent_for_analysis')
                        if agent_col and agent_col in click_work.columns:
                            click_work = click_work[click_work[agent_col] == selected_agent].copy()
                            st.session_state[f"selected_agent_name{product_suffix}"] = selected_agent
                        else:
                            st.session_state[f"selected_agent_name{product_suffix}"] = "All Agents"
                    else:
                        st.session_state[f"selected_agent_name{product_suffix}"] = "All Agents"
                
                    # Auto-set economics based on product
                    if selected_product == "Auto Insurance":
                        if "econ_product_type" not in st.session_state or st.session_state.get(f"econ_product_type{product_suffix}", None) != "Auto":
                            st.session_state[f"econ_product_type{product_suffix}"] = "Auto"
                            st.session_state[f"econ_annual_premium{product_suffix}"] = 1282.0
                            st.info("💡 Auto-configured economics for Auto Insurance ($1,282 premium)")
                    elif selected_product == "Home Insurance":
                        if "econ_product_type" not in st.session_state or st.session_state.get(f"econ_product_type{product_suffix}", None) != "Home":
                            st.session_state[f"econ_product_type{product_suffix}"] = "Home"
                            st.session_state[f"econ_annual_premium{product_suffix}"] = 2424.0
                            st.info("💡 Auto-configured economics for Home Insurance ($2,424 premium)")
                else:
                    st.info("ℹ️ **Analyzing all products together**\n\n"
                           "QMP Export will **auto-split** by product - the app will create separate export files "
                           "for Auto and Home by matching QMP IDs to the original click data. "
                           "Each product gets its own optimized modifier file ready for QMP import.")
                    st.caption(f"Total clicks: {len(click_work)}")
                
                    # Apply global agent filter
                    if st.session_state.get('selected_agent_for_analysis') not in ['All Agents', None]:
                        agent_col = st.session_state.get('agent_column_name')
                        selected_agent = st.session_state.get('selected_agent_for_analysis')
                        if agent_col and agent_col in click_work.columns:
                            click_work = click_work[click_work[agent_col] == selected_agent].copy()
                            st.session_state[f"selected_agent_name{product_suffix}"] = selected_agent
                        else:
                            st.session_state[f"selected_agent_name{product_suffix}"] = "All Agents"
                    else:
                        st.session_state[f"selected_agent_name{product_suffix}"] = "All Agents"
            else:
                # Single product - auto-detect and configure
                detected_product = available_products[0]
                st.success(f"✅ Single product detected: **{detected_product}**")
            
                # Store detected product for export labeling
                st.session_state.detected_product = detected_product
            
                # Apply global agent filter
                if st.session_state.get('selected_agent_for_analysis') not in ['All Agents', None]:
                    agent_col = st.session_state.get('agent_column_name')
                    selected_agent = st.session_state.get('selected_agent_for_analysis')
                    if agent_col and agent_col in click_work.columns:
                        click_work = click_work[click_work[agent_col] == selected_agent].copy()
                        st.session_state[f"selected_agent_name{product_suffix}"] = selected_agent
                    else:
                        st.session_state[f"selected_agent_name{product_suffix}"] = "All Agents"
                else:
                    st.session_state[f"selected_agent_name{product_suffix}"] = "All Agents"
            
                # Auto-set economics for single product
                if detected_product == "Auto Insurance":
                    if "econ_product_type" not in st.session_state or st.session_state.get(f"econ_product_type{product_suffix}", None) != "Auto":
                        st.session_state[f"econ_product_type{product_suffix}"] = "Auto"
                        st.session_state[f"econ_annual_premium{product_suffix}"] = 1282.0
                        st.caption("💡 Economics auto-configured for Auto Insurance")
                elif detected_product == "Home Insurance":
                    if "econ_product_type" not in st.session_state or st.session_state.get(f"econ_product_type{product_suffix}", None) != "Home":
                        st.session_state[f"econ_product_type{product_suffix}"] = "Home"
                        st.session_state[f"econ_annual_premium{product_suffix}"] = 2424.0
                        st.caption("💡 Economics auto-configured for Home Insurance")

    # Download processed click data (after column mapping)
    st.markdown("---")
    st.markdown("### 💾 Download Processed Data")
    
    # Create CSV of processed data
    csv_buffer = io.StringIO()
    click_work.to_csv(csv_buffer, index=False)
    csv_data = csv_buffer.getvalue()
    
    # Determine filename based on product and agent
    download_filename = "processed_clicks"
    if product_col and product_col in click_work.columns:
        products_in_data = click_work[product_col].unique()
        if len(products_in_data) == 1:
            product_name = str(products_in_data[0]).replace(" Insurance", "").replace(" ", "_")
            download_filename = f"processed_clicks_{product_name}"
    
    # Add agent name if applicable
    agent_name = st.session_state.get(f"selected_agent_name{product_suffix}", "")
    if agent_name and agent_name not in ["All Agents", "Unknown", ""]:
        agent_clean = agent_name.replace(" - State Farm Agent", "").replace(" ", "_")
        download_filename = f"{download_filename}_{agent_clean}"
    
    download_filename = f"{download_filename}.csv"
    
    st.download_button(
        "⬇️ Download Processed Click Data (with mapped columns)",
        data=csv_data,
        file_name=download_filename,
        mime="text/csv",
        help="Download the QMP API data after column mapping has been applied. This file has named columns instead of numeric ones.",
        key=f"download_processed_clicks{product_suffix}"
    )
    
    st.caption(f"📊 {len(click_work):,} rows × {len(click_work.columns)} columns | Includes: {', '.join(list(click_work.columns)[:5])}...")
    st.markdown("---")

    if click_work.empty:
        st.error("No clicks remaining after product filter.")
        st.stop()

    # -----------------------------
    # Detect Source Modifier Product Match (Multiple Files)
    # -----------------------------
    modifier_product_matches = {}  # Maps product name to modifier DataFrame
    if modifier_files and product_col:
        st.markdown("**📋 Modifier File Detection:**")
    
        for modifier_file in modifier_files:
            try:
                modifier_df = pd.read_excel(modifier_file, sheet_name='source-modifiers')
            
                # Get QMP IDs from modifier file - keep as strings for consistency
                modifier_qmpids = set(modifier_df['QMP ID'].dropna().astype(str).str.strip())
            
                # Try to match against each product in the data
                best_match_product = None
                best_match_pct = 0
                best_overlap = set()
            
                # Get unique products and their QMP IDs from click data
                if qmpid_col and product_col in click_work.columns:
                    for product in click_work[product_col].unique():
                        product_clicks = click_work[click_work[product_col] == product]
                        # QMPID can be hash strings or numbers - keep as strings for comparison
                        product_qmpids = set(product_clicks[qmpid_col].dropna().astype(str).str.strip())
                    
                        # Calculate overlap
                        overlap = modifier_qmpids & product_qmpids
                        overlap_pct = (len(overlap) / len(product_qmpids) * 100) if product_qmpids else 0
                    
                        if overlap_pct > best_match_pct:
                            best_match_pct = overlap_pct
                            best_match_product = product
                            best_overlap = overlap
            
                # Store if good match found
                if best_match_product and best_match_pct > 50:
                    modifier_product_matches[best_match_product] = modifier_df
                    st.success(f"✅ `{modifier_file.name}` → **{best_match_product}** ({len(best_overlap)} sources matched, {best_match_pct:.0f}%)")
                else:
                    st.warning(f"⚠️ `{modifier_file.name}` → No clear product match ({best_match_pct:.0f}% overlap)")
                
            except Exception as e:
                st.warning(f"Could not read `{modifier_file.name}`: {str(e)}")
    
        # Store all matched modifiers
        st.session_state.modifier_product_matches = modifier_product_matches

    # -----------------------------
    # Parse timestamp for sub-segmentation
    # -----------------------------
    try:
        # Try parsing timestamp
        click_work["subseg_timestamp"] = pd.to_datetime(click_work["Click Date Timestamp"], errors="coerce")
    
        # Check if any timestamps were successfully parsed
        valid_timestamps = click_work["subseg_timestamp"].notna().sum()
        total_rows = len(click_work)
    
        if valid_timestamps > 0:
            st.session_state.subseg_timestamp_available = True
        
            # Extract time dimensions
            click_work["subseg_dow"] = click_work["subseg_timestamp"].dt.dayofweek  # 0=Mon, 6=Sun
            click_work["subseg_hour"] = click_work["subseg_timestamp"].dt.hour
        
            # Daypart blocks
            def get_daypart(hour):
                if pd.isna(hour):
                    return None
                if 0 <= hour <= 5:
                    return "Night (0-5)"
                elif 6 <= hour <= 11:
                    return "Morning (6-11)"
                elif 12 <= hour <= 17:
                    return "Afternoon (12-17)"
                else:
                    return "Evening (18-23)"
        
            click_work["subseg_daypart"] = click_work["subseg_hour"].apply(get_daypart)
        
            # Day of week labels
            dow_labels = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun"}
            click_work["subseg_dow_label"] = click_work["subseg_dow"].map(dow_labels)
        
            # Show parsing success rate in sidebar if sub-seg is enabled
            if st.session_state.get("enable_subseg", False):
                parse_rate = (valid_timestamps / total_rows * 100)
                if parse_rate < 100:
                    st.sidebar.caption(f"⚠️ Timestamp parsed: {valid_timestamps}/{total_rows} rows ({parse_rate:.1f}%)")
        else:
            # No valid timestamps
            st.session_state.subseg_timestamp_available = False
        
    except Exception as e:
        st.session_state.subseg_timestamp_available = False
        # Only show error if sub-seg is enabled
        if st.session_state.get("enable_subseg", False):
            st.sidebar.error(f"Timestamp parsing error: {str(e)}")

    # Auto-detect ALL geography columns for sub-segmentation
    # Look for: State, Region, Zip Code, DMA, etc.
    geo_column_map = {}
    geo_search_terms = {
        "State": ["state", "state code"],
        "Region": ["region"],
        "Zip Code": ["zip", "zipcode", "zip code", "postal"],
        "DMA": ["dma", "designated market"],
        "City": ["city"],
        "County": ["county"]
    }

    for geo_type, search_terms in geo_search_terms.items():
        for term in search_terms:
            matches = [c for c in click_work.columns if term in c.lower()]
            if matches:
                # Take first match for this geo type
                geo_column_map[geo_type] = matches[0]
                # Add subseg column
                click_work[f"subseg_geo_{geo_type.lower().replace(' ', '_')}"] = click_work[matches[0]].astype(str).str.strip()
                break

    if geo_column_map:
        st.session_state.subseg_geo_columns = geo_column_map
        if st.session_state.get("enable_subseg", False):
            geo_list = ", ".join([f"'{col}' ({geo_type})" for geo_type, col in geo_column_map.items()])
            st.info(f"📍 Found geography columns for sub-segmentation: {geo_list}")
    else:
        st.session_state.subseg_geo_columns = {}

    # 4) Column Mapping / Metric A
    with st.expander("4) Column Mapping (from click file)", expanded=(not simple_mode)):
        info_badge("Choose your grouping columns (include QMPID for export) and map Metric A (e.g., Spend).")
        cols = click_work.columns.tolist()
        # Filter out subseg_ columns from grouping options
        cols = [c for c in cols if not c.startswith("subseg_")]
    
        # Build robust defaults: Publisher Company, QMPID, Media Channel (when present)
        pc = next((c for c in ["Publisher Company","Company","Company Name","Publisher ID Category"] if c in cols), None)
        mc = next((c for c in ["Media Channel","Channel"] if c in cols), None)
        pref_groups = [pc, qmpid_col, mc]
        default_groups = [c for c in pref_groups if c] or cols[:1]
    
        if show_debug:
            st.caption(f"🔍 Debug: Detected defaults → Publisher Company/Name: {pc}, QMPID: {qmpid_col}, Media Channel: {mc}")
            st.caption(f"🔍 Debug: default_groups = {default_groups}")
    
        # Clean any saved defaults that might include subseg columns
        saved_groups = ss_default(f"group_cols{product_suffix}", default_groups)
        if isinstance(saved_groups, list):
            saved_groups = [c for c in saved_groups if not c.startswith("subseg_") and c in cols]
        if not saved_groups:
            saved_groups = default_groups
    
        # Show reset button if saved differs from detected defaults
        if saved_groups != default_groups:
            col_a, col_b = st.columns([3, 1])
            with col_b:
                if st.button("🔄 Reset to Defaults", help="Reset grouping columns to detected defaults", key=f"button_1{product_suffix}"):
                    st.session_state[f"group_cols{product_suffix}"] = default_groups
                    st.rerun()
    
        group_cols = st.multiselect("Grouping Columns (up to 10)", options=cols,
                                        default=saved_groups,
                                        max_selections=10, key=f"group_cols{product_suffix}", )
        if not group_cols:
            st.error("Please choose at least one grouping column.")
            st.stop()

        # "Include Device Type in grouping"
        has_device = "Device Type" in cols
        include_device = st.checkbox("Include Device Type in grouping", value=False, key=f"include_device{product_suffix}")  # Default to False
        if include_device and has_device and "Device Type" not in group_cols:
            group_cols.append("Device Type")
        elif (not include_device) and "Device Type" in group_cols:
            group_cols = [c for c in group_cols if c != "Device Type"]
    
        st.markdown("**Metric A (from click file)**")
        a_label = st.selectbox("Metric A (numerator) label", ["Spend","Cost","Budget","Media Cost","Revenue","Custom…"], index=0, key=f"selectbox_3{product_suffix}")
        if a_label == "Custom…":
            a_label = st.text_input("Custom label for Metric A", value=ss_default(f"a_label{product_suffix}","Spend"), key=f"text_input_1{product_suffix}")
        st.session_state[f"a_label{product_suffix}"] = a_label
        a_fmt_currency = st.checkbox(f'Format Metric A (“{a_label}”) as currency', value=ss_default(f"a_fmt_currency{product_suffix}", True), key=f"a_fmt_currency{product_suffix}")

        A_ALIASES = ["Spend","Cost","Media Cost","Total Cost","Amount","Budget"]
        guessed = []
        nm = {normalize(c): c for c in cols}
        for a in A_ALIASES:
            key = normalize(a)
            if key in nm and nm[key] not in guessed:
                guessed.append(nm[key])
        for c in cols:
            nc = normalize(c)
            if any(normalize(a) in nc for a in A_ALIASES) and c not in guessed:
                guessed.append(c)
        a_cols = st.multiselect(f"Columns for {a_label} — summed", options=cols, default=[c for c in ["Cost($)"] if c in cols], key=f"a_cols{product_suffix}")

        use_zero_A = st.checkbox(f"No columns selected for **{a_label}**. Treat **{a_label}** as $0",
                                 value=ss_default(f"use_zero_A{product_suffix}", len(a_cols)==0), key=f"use_zero_A{product_suffix}")
        filter_no_a = st.checkbox(f"Exclude rows with no {a_label} (≤ 0 or NaN) before aggregation",
                                  value=ss_default(f"filter_no_a{product_suffix}", False), key=f"filter_no_a{product_suffix}", )

    # 5) Click → Stats Match (outcomes only)
    # Initialize unmatched stats (will be recalculated if stats exist)
    # Removed from here - now initialized inside the stats expander to ensure proper resetting

    with st.expander("5) Click → Stats Match (outcomes only)", expanded=(not simple_mode)):
        info_badge("Find the click key as a substring within Campaign IDs in the stats file; roll up QS/Phone/SMS by key.")
    
        # Initialize unmatched stats for this run
        st.session_state[f"unmatched_qs{product_suffix}"] = 0
        st.session_state[f"unmatched_phone{product_suffix}"] = 0
        st.session_state[f"unmatched_sms{product_suffix}"] = 0
    
        if not stats_files:
            st.warning("Upload STATS CSV to compute Quote Starts / Phone / SMS. Until then, B/C/D=0.")
            clicks_joined = pd.DataFrame(columns=["click_key","Quote Starts","Phone Clicks","SMS Clicks"])
        else:
            # Combine multiple stats files
            stats_dfs = []
            file_qs_breakdown = []
            for i, stats_file in enumerate(stats_files):
                try:
                    df = pd.read_csv(stats_file)
                    stats_dfs.append(df)
                except Exception:
                    stats_file.seek(0)
                    df = pd.read_csv(stats_file, encoding="utf-8", engine="python")
                    stats_dfs.append(df)
            
                # Track QS per file
                file_qs_breakdown.append({
                    'filename': stats_file.name,
                    'rows': len(df),
                    'file_index': i
                })
        
            stats_raw = pd.concat(stats_dfs, ignore_index=True)
        
            st.markdown("**🔍 Stats File Diagnostics:**")
            st.caption(f"📁 Combined {len(stats_files)} stats file(s) → {len(stats_raw):,} rows")
        
            # Show breakdown by file
            for file_info in file_qs_breakdown:
                st.caption(f"  • {file_info['filename']}: {file_info['rows']} rows")
        
            # Find columns BEFORE filtering
            sc_campaign = find_column(stats_raw, ["Campaign IDs","Campaign ID","Campaign"])
        
            # FILTER TO MELON MAX ONLY (Campaign IDs containing "QS") - silently
            if sc_campaign:
                stats_raw = stats_raw[stats_raw[sc_campaign].astype(str).str.contains("QS", case=False, na=False)].copy()
        
            sc_qs  = find_column(stats_raw, ["Quote Starts","Quotes","Leads","QS"])
            sc_ph  = find_column(stats_raw, ["Phone Clicks","Calls","PhoneCalls"])
            sc_sms = find_column(stats_raw, ["SMS Clicks","Texts","SMS"])
            sc_campaign = sc_campaign or st.selectbox("Pick column for Campaign IDs", options=stats_raw.columns, key=f"pick_camp{product_suffix}")
            sc_qs = sc_qs or st.selectbox("Pick column for Quote Starts", options=stats_raw.columns, key=f"pick_qs{product_suffix}")
            sc_ph = sc_ph or st.selectbox("Pick column for Phone Clicks", options=stats_raw.columns, key=f"pick_ph{product_suffix}")
            sc_sms = sc_sms or st.selectbox("Pick column for SMS Clicks", options=stats_raw.columns, key=f"pick_sms{product_suffix}")
        
            # DIAGNOSTIC: Show raw QS totals
            raw_qs_total = to_num(stats_raw[sc_qs]).sum()
            st.caption(f"📊 Raw Quote Starts before matching: **{int(raw_qs_total)}** (expected: 53)")

            stats_campaign = stats_raw[sc_campaign].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
            # Use UNFILTERED click_work to match ALL clicks to stats, not just filtered product
            click_keys = click_work_unfiltered[click_key_col].astype(str).str.strip().dropna().unique().tolist()
        
            st.caption(f"🔑 Found {len(click_keys)} unique click keys to match")

            def extract_key_regex_substring(series: pd.Series, keys: list[str], chunk_size=3000) -> pd.Series:
                s = series.fillna("").astype(str)
                out = pd.Series(index=s.index, dtype="object")
                for i in range(0, len(keys), chunk_size):
                    chunk = keys[i:i+chunk_size]
                    patt = "(" + "|".join(re.escape(k) for k in chunk) + ")"
                    matched = s.str.extract(patt, flags=re.IGNORECASE, expand=False)
                    fill = out.isna() & matched.notna()
                    if fill.any():
                        out.loc[fill] = matched.loc[fill]
                return out

            matched_key = extract_key_regex_substring(stats_campaign, sorted(set(click_keys), key=len, reverse=True))
        
            # DIAGNOSTIC: How many matched
            matched_count = matched_key.notna().sum()
            st.caption(f"✅ Matched {matched_count} / {len(stats_raw)} stats rows to click keys")
        
            stats_with_key = pd.DataFrame({
                "matched_key": matched_key,
                "Quote Starts": to_num(stats_raw[sc_qs]),
                "Phone Clicks": to_num(stats_raw[sc_ph]),
                "SMS Clicks": to_num(stats_raw[sc_sms]),
            })
        
            # DIAGNOSTIC: QS in matched rows
            matched_qs = stats_with_key[stats_with_key["matched_key"].notna()]["Quote Starts"].sum()
            unmatched_qs = stats_with_key[stats_with_key["matched_key"].isna()]["Quote Starts"].sum()
            st.caption(f"📊 QS in MATCHED rows: **{int(matched_qs)}** | QS in UNMATCHED rows: {int(unmatched_qs)}")
        
            # Show sample unmatched Campaign IDs if there are many unmatched
            if unmatched_qs > 0:
                unmatched_campaigns = stats_raw[matched_key.isna()][sc_campaign].head(10).tolist()
                unmatched_qs_values = stats_raw[matched_key.isna()][sc_qs].head(10).tolist()
            
                st.warning(f"⚠️ **{int(unmatched_qs)} Quote Starts in UNMATCHED rows** (Campaign IDs don't contain any click keys)")
            
                st.markdown("**Missing Click Keys Analysis:**")
            
                missing_data = []
                for camp_id, qs_val in zip(unmatched_campaigns, unmatched_qs_values):
                    # Try to determine product from Campaign ID suffix
                    product = "Unknown"
                    camp_upper = str(camp_id).upper()
                    # Auto: MLQSAM, MLQSAD, MLQSAT or ends with AM/AD/AT
                    if any(x in camp_upper for x in ["MLQSAM", "MLQSAD", "MLQSAT"]) or \
                       any(camp_upper.endswith(x) for x in ["AM", "AD", "AT"]):
                        product = "Auto Insurance"
                    # Home: MLQSHM, MLQSHD, MLQSHT or ends with HM/HD/HT
                    elif any(x in camp_upper for x in ["MLQSHM", "MLQSHD", "MLQSHT"]) or \
                         any(camp_upper.endswith(x) for x in ["HM", "HD", "HT"]):
                        product = "Home Insurance"
                
                    # Extract potential click key (remove all known suffixes)
                    click_key_candidate = str(camp_id)
                    for suffix in ["MLQSAM", "MLQSAD", "MLQSAT", "MLQSHM", "MLQSHD", "MLQSHT", 
                                  "mlqsam", "mlqsad", "mlqsat", "mlqshm", "mlqshd", "mlqsht"]:
                        click_key_candidate = click_key_candidate.replace(suffix, "")
                
                    missing_data.append({
                        'Campaign ID': camp_id,
                        'Missing Click Key': click_key_candidate,
                        'Product': product,
                        'Quote Starts': int(qs_val) if pd.notna(qs_val) else 0
                    })
            
                if missing_data:
                    missing_df = pd.DataFrame(missing_data)
                    st.dataframe(missing_df, use_container_width=True)
                    st.caption(f"💡 These clicks exist in stats but are missing from your click files. They may be from a different date range or were filtered out during export.")
        
            stats_roll = stats_with_key.groupby("matched_key", dropna=False)[["Quote Starts","Phone Clicks","SMS Clicks"]].sum().reset_index()

            # Use UNFILTERED clicks to create the join table with all stats
            clicks_view = click_work_unfiltered[[click_key_col]].copy()
            clicks_view["__k"] = clicks_view[click_key_col].astype(str).str.strip().str.lower()
            stats_roll["__k"] = stats_roll["matched_key"].fillna("").astype(str).str.lower()

            clicks_joined = clicks_view.merge(
                stats_roll[["__k","Quote Starts","Phone Clicks","SMS Clicks"]],
                on="__k", how="left"
            ).drop(columns="__k").rename(columns={click_key_col:"click_key"}).fillna({"Quote Starts":0,"Phone Clicks":0,"SMS Clicks":0})
        
            # Show matching stats
            total_qs = clicks_joined["Quote Starts"].sum()
            total_phone = clicks_joined["Phone Clicks"].sum()
            total_sms = clicks_joined["SMS Clicks"].sum()
            matched_clicks = (clicks_joined["Quote Starts"] > 0).sum()
        
            st.info(f"📊 **Stats Matching Results:**\n\n"
                   f"- **{matched_clicks:,} / {len(clicks_joined):,} clicks** matched to stats data ({matched_clicks/len(clicks_joined)*100:.1f}%)\n"
                   f"- **Total Quote Starts:** {int(total_qs):,}\n"
                   f"- **Total Phone Clicks:** {int(total_phone):,}\n"
                   f"- **Total SMS Clicks:** {int(total_sms):,}")
        
            if matched_clicks == 0:
                st.warning("⚠️ **No clicks matched to stats!** Check that:\n"
                          "1. Click keys appear in Campaign IDs column\n"
                          "2. Stats file has data for this date range\n"
                          "3. Column mappings are correct")
        
            # Track unmatched stats for later (to add to totals)
            # Need to do this HERE while matched_key, stats_raw, etc. are in scope
            unmatched_qs_to_add = 0
            unmatched_phone_to_add = 0
            unmatched_sms_to_add = 0
        
            # DEBUG: Show what unmatched campaigns we have
            if matched_key.isna().any():
                unmatched_campaigns_debug = stats_raw[matched_key.isna()][sc_campaign].tolist()
                unmatched_qs_debug = stats_raw[matched_key.isna()][sc_qs].tolist()
                st.caption(f"🐛 DEBUG: Found {len(unmatched_campaigns_debug)} unmatched campaign IDs: {unmatched_campaigns_debug[:5]}")
                st.caption(f"🐛 DEBUG: Their QS values: {unmatched_qs_debug[:5]}")
        
            # If analyzing specific product, only count unmatched stats from that product
            if product_col and st.session_state.get("product_filter_mode") == "Analyze specific product (Recommended)":
                selected_product = st.session_state.get("selected_product", "")
            
                st.caption(f"🐛 DEBUG: Filtering unmatched for product: {selected_product}")
            
                # Get the original campaign IDs for unmatched rows
                unmatched_campaigns = stats_raw[matched_key.isna()][sc_campaign]
            
                # Filter by product based on campaign ID
                # Auto suffixes: AM (mobile), AD (desktop), AT (tablet)
                # Home suffixes: HM (mobile), HD (desktop), HT (tablet)
                if "Auto" in selected_product:
                    campaign_str = unmatched_campaigns.astype(str).str.upper()
                    product_mask = (
                        campaign_str.str.contains("MLQSAM", na=False) |
                        campaign_str.str.contains("MLQSAD", na=False) |
                        campaign_str.str.contains("MLQSAT", na=False) |
                        campaign_str.str.endswith("AM", na=False) |
                        campaign_str.str.endswith("AD", na=False) |
                        campaign_str.str.endswith("AT", na=False)
                    )
                    st.caption(f"🐛 DEBUG: Auto mask matched {product_mask.sum()} out of {len(product_mask)} unmatched campaigns")
                elif "Home" in selected_product:
                    campaign_str = unmatched_campaigns.astype(str).str.upper()
                    product_mask = (
                        campaign_str.str.contains("MLQSHM", na=False) |
                        campaign_str.str.contains("MLQSHD", na=False) |
                        campaign_str.str.contains("MLQSHT", na=False) |
                        campaign_str.str.endswith("HM", na=False) |
                        campaign_str.str.endswith("HD", na=False) |
                        campaign_str.str.endswith("HT", na=False)
                    )
                    st.caption(f"🐛 DEBUG: Home mask matched {product_mask.sum()} out of {len(product_mask)} unmatched campaigns")
                else:
                    # Unknown product - count all
                    product_mask = pd.Series([True] * len(unmatched_campaigns), index=unmatched_campaigns.index)
            
                # Sum only the unmatched stats from this product
                unmatched_for_product = stats_raw[matched_key.isna() & product_mask]
                if len(unmatched_for_product) > 0:
                    unmatched_qs_to_add = int(unmatched_for_product[sc_qs].sum())
                    unmatched_phone_to_add = int(unmatched_for_product[sc_ph].sum()) if sc_ph else 0
                    unmatched_sms_to_add = int(unmatched_for_product[sc_sms].sum()) if sc_sms else 0
                    st.caption(f"🐛 DEBUG: Unmatched QS for {selected_product}: {unmatched_qs_to_add}")
            else:
                # Analyzing all products together - count all unmatched
                unmatched_mask = matched_key.isna()
                if unmatched_mask.any():
                    unmatched_qs_to_add = int(stats_raw[unmatched_mask][sc_qs].sum())
                    unmatched_phone_to_add = int(stats_raw[unmatched_mask][sc_ph].sum()) if sc_ph else 0
                    unmatched_sms_to_add = int(stats_raw[unmatched_mask][sc_sms].sum()) if sc_sms else 0
        
            if unmatched_qs_to_add > 0 or unmatched_phone_to_add > 0 or unmatched_sms_to_add > 0:
                product_note = ""
                if product_col and st.session_state.get("product_filter_mode") == "Analyze specific product (Recommended)":
                    product_note = f" (from {selected_product} campaigns only)"
            
                st.info(f"ℹ️ **Unmatched Stats (not in results table){product_note}:** {unmatched_qs_to_add} QS, {unmatched_phone_to_add} Phone, {unmatched_sms_to_add} SMS\n\n"
                       f"These outcomes exist in stats but their click keys weren't found in click files. "
                       f"They're included in totals but excluded from source-level analysis.")
        
            # Store for use later when displaying totals
            st.session_state[f"unmatched_qs{product_suffix}"] = unmatched_qs_to_add
            st.session_state[f"unmatched_phone{product_suffix}"] = unmatched_phone_to_add
            st.session_state[f"unmatched_sms{product_suffix}"] = unmatched_sms_to_add

    # 6) Build working and aggregate (A,B,C,D)
    work = pd.DataFrame({g: click_work[g].astype(str).str.strip() for g in group_cols})
    if qmpid_col and qmpid_col in click_work.columns:
        work["QMPID"] = click_work[qmpid_col].astype(str).str.strip()

    if st.session_state.get(f"use_zero_A{product_suffix}", True):
        work["A"] = 0.0
    else:
        if not a_cols:
            st.error(f"Select at least one column for **{a_label}**, or check the $0 option.")
            st.stop()
        work["A"] = sum((to_num(click_work[c]) for c in a_cols))

    if st.session_state.get(f"filter_no_a{product_suffix}", True):
        before = len(work)
        work = work.loc[work["A"] > 0].copy()
        st.caption(f"Filtered out {before-len(work)} rows with no {a_label} (≤ 0).")

    if not stats_files or clicks_joined.empty:
        work["B"] = 0.0; work["C"] = 0.0; work["D"] = 0.0
    else:
        # CRITICAL: Merge stats using the FULL click_work (after product filter was applied)
        # The click_key was generated from the original data before filtering,
        # so we need to merge on the current click_work which may be product-filtered
        cd = click_work.copy()
        cd["__k"] = cd[click_key_col].astype(str).str.strip().str.lower()
        sr = clicks_joined.copy()
        sr["__k"] = sr["click_key"].astype(str).str.strip().str.lower()
        merged = cd[["__k"]].merge(sr[["__k","Quote Starts","Phone Clicks","SMS Clicks"]], on="__k", how="left")
        work["B"] = merged["Quote Starts"].fillna(0.0).values
        work["C"] = merged["Phone Clicks"].fillna(0.0).values
        work["D"] = merged["SMS Clicks"].fillna(0.0).values

    if "Clicks" in click_work.columns:
        work["Clicks"] = to_num(click_work["Clicks"])

    # Get unmatched stats from session state (calculated earlier in stats expander)
    unmatched_qs = st.session_state.get(f'unmatched_qs{product_suffix}', 0)
    unmatched_phone = st.session_state.get(f'unmatched_phone{product_suffix}', 0)
    unmatched_sms = st.session_state.get(f'unmatched_sms{product_suffix}', 0)

    group_keys = list(dict.fromkeys(group_cols + (["QMPID"] if "QMPID" in work.columns else [])))
    agg_kwargs = dict(A=("A","sum"), B=("B","sum"), C=("C","sum"), D=("D","sum"))
    if "Clicks" in work.columns:
        agg_kwargs["Clicks"] = ("Clicks","sum")
    agg = work.groupby(group_keys, as_index=False, dropna=False).agg(**agg_kwargs)

    # Efficiency metrics
    if simple_mode:
        eff_label = "CPQS"
        eff_fmt_currency = True
    else:
        eff_label = st.selectbox("Efficiency metric name (A ÷ B)", ["CPQS","CPA","CPL","Cost per Unit","Custom…"], index=0, key=f"selectbox_4{product_suffix}")
        if eff_label == "Custom…":
            eff_label = st.text_input("Custom label", value=ss_default(f"eff_label{product_suffix}","CPQS"), key=f"text_input_2{product_suffix}")
        eff_fmt_currency = st.checkbox(f'Format Efficiency ("{eff_label}") as currency', value=ss_default(f"eff_fmt_currency{product_suffix}", True), key=f"eff_fmt_currency{product_suffix}")
    st.session_state[f"eff_label{product_suffix}"]=eff_label

    agg[eff_label] = np.where(agg["B"]>0, agg["A"]/agg["B"], np.nan)
    agg["Cost per Phone+QS"] = np.where((agg["C"]+agg["B"])>0, agg["A"]/(agg["C"]+agg["B"]), np.nan)
    agg["Cost per SMS+QS"]   = np.where((agg["D"]+agg["B"])>0, agg["A"]/(agg["D"]+agg["B"]), np.nan)
    agg["Cost per Lead"]     = np.where((agg["B"]+agg["C"]+agg["D"])>0, agg["A"]/(agg["B"]+agg["C"]+agg["D"]), np.nan)

    # Product loop is now at file level - each file is already one product
    grouped = agg.copy()
    unmatched_qs_current = unmatched_qs
    unmatched_phone_current = unmatched_phone
    unmatched_sms_current = unmatched_sms

    # 7) Bidding Policy
    with st.expander("6) 🎯 Bidding Policy (fixed dollar bands)", expanded=False):
        info_badge("Default: Top < $20 • Average ≤ $30 • Weak > $30. Link to baseline optional. Set bid % per bucket.")
    
        # Economics-Driven Target CPQS Calculator
        economics_expanded = ss_default(f"economics_expanded{product_suffix}", False)  # Always collapsed by default
    
        # Compute target CPQS first for persistent display
        product_type = ss_default(f"econ_product_type{product_suffix}", "Auto")
        annual_premium = ss_default(f"econ_annual_premium{product_suffix}", 1282.0 if product_type == "Auto" else 2424.0 if product_type == "Home" else 1853.0)
        commission_rate = ss_default(f"econ_commission_rate{product_suffix}", 10.0)
        close_rate = ss_default(f"econ_close_rate{product_suffix}", 10.0)
        renewal_multiplier = ss_default(f"econ_renewal_multiplier{product_suffix}", 1.0)
    
        commission_per_policy = annual_premium * (commission_rate / 100)
        ltv_per_policy = commission_per_policy * renewal_multiplier
        target_cpqs = ltv_per_policy * (close_rate / 100)
    
        # Persistent callout showing target even when collapsed
        # Show product being analyzed if product column was detected
        if product_col and st.session_state.get("product_filter_mode") == "Analyze specific product (Recommended)":
            analyzed_product = st.session_state.get("selected_product", "Unknown")
            st.markdown(f"**💰 Target CPQS:** ${target_cpqs:.2f} | **📦 Analyzing:** {analyzed_product}")
        else:
            st.markdown(f"**💰 Target CPQS:** ${target_cpqs:.2f} (from agent economics)")
    
        with st.expander("💰 Agent Economics (Target CPQS)", expanded=economics_expanded):
            st.markdown("*Automatically compute your target Cost Per Quote Start from your business economics*")
        
            # Product Type
            col1, col2 = st.columns([1, 2])
            with col1:
                new_product_type = st.selectbox(
                    "Product Type",
                    ["Auto", "Home", "Both (Auto + Home)"],
                    index=["Auto", "Home", "Both (Auto + Home)"].index(product_type) if product_type in ["Auto", "Home", "Both (Auto + Home)"] else 0,
                    key=f"econ_product_type{product_suffix}"
                )
        
            # Handle product type change
            if new_product_type != product_type:
                product_type = new_product_type
                # Check if premium has been customized
                default_premiums = {"Auto": 1282.0, "Home": 2424.0, "Both (Auto + Home)": 1853.0}
                if f"econ_premium_customized{product_suffix}" not in st.session_state or not st.session_state.get(f"econ_premium_customized{product_suffix}", False):
                    st.session_state[f"econ_annual_premium{product_suffix}"] = default_premiums[product_type]
                else:
                    # Prompt user
                    if st.button(f"Update annual premium to national default for {product_type} (${default_premiums[product_type]:,.0f})?", key=f"button_2{product_suffix}"):
                        st.session_state[f"econ_annual_premium{product_suffix}"] = default_premiums[product_type]
                        st.session_state[f"econ_premium_customized{product_suffix}"] = False
                        st.rerun()
        
            # Mix ratio for "Both"
            if product_type == "Both (Auto + Home)":
                auto_mix = st.slider(
                    "% Auto vs. Home",
                    0, 100, ss_default(f"econ_auto_mix{product_suffix}", 50),
                    help="Percentage of your book that is Auto business",
                    key=f"econ_auto_mix{product_suffix}"
                )
                default_annual_premium = (1282.0 * auto_mix / 100) + (2424.0 * (100 - auto_mix) / 100)
                if f"econ_premium_customized{product_suffix}" not in st.session_state or not st.session_state.get(f"econ_premium_customized{product_suffix}", False):
                    st.session_state[f"econ_annual_premium{product_suffix}"] = default_annual_premium
        
            # Annual Premium
            with col2:
                prev_premium = annual_premium
                annual_premium = st.number_input(
                    "Annual Premium ($)",
                    min_value=0.0,
                    max_value=50000.0,
                    value=float(annual_premium),
                    step=50.0,
                    help="State-specific average annual premium. Override the national default if needed.",
                    key=f"econ_annual_premium{product_suffix}"
                )
                # Track if user has customized
                if annual_premium != prev_premium and annual_premium != (1282.0 if product_type == "Auto" else 2424.0 if product_type == "Home" else 1853.0):
                    st.session_state[f"econ_premium_customized{product_suffix}"] = True
        
            # Commission Rate and Close Rate
            col3, col4 = st.columns(2)
            with col3:
                commission_rate = st.number_input(
                    "Commission Rate (%)",
                    min_value=0.0,
                    max_value=100.0,
                    value=float(commission_rate),
                    step=0.5,
                    key=f"econ_commission_rate{product_suffix}")
        
            with col4:
                close_rate = st.number_input(
                    "Estimated Close Rate (%)",
                    min_value=0.0,
                    max_value=100.0,
                    value=float(close_rate),
                    step=0.5,
                    help="Expected quote-to-bind rate. Most agents clear 10%, but adjust if you know your historical close rate.",
                    key=f"econ_close_rate{product_suffix}"
                )
        
            # Renewal Multiplier
            renewal_multiplier = st.number_input(
                "Renewal Multiplier",
                min_value=0.0,
                max_value=10.0,
                value=float(renewal_multiplier),
                step=0.5,
                help="Use 1 to break even on acquisition. Increase this if you factor in renewal income (e.g., 2 = two years of commission).",
                key=f"econ_renewal_multiplier{product_suffix}"
            )
        
            # Computed Values
            st.markdown("---")
            st.markdown("**📊 Computed Economics**")
        
            col5, col6, col7 = st.columns(3)
            with col5:
                st.metric("Commission per Policy", f"${commission_per_policy:.2f}")
            with col6:
                st.metric("LTV per Policy", f"${ltv_per_policy:.2f}")
            with col7:
                st.metric("**Target CPQS**", f"**${target_cpqs:.2f}**")
        
            st.caption("💡 Performance bands below are automatically derived from this Target CPQS (80%/120% split)")
    
        st.markdown("---")
    
        # Economics-Driven Bidding Policy
        st.markdown("### 🎯 Bidding Policy (Continuous Scaling)")
    
        # Auto-compute from economics by default
        use_custom_policy = st.checkbox(
            "⚙️ Customize bid scaling parameters",
            value=ss_default(f"use_custom_policy{product_suffix}", False),
            help="By default, bid adjustments scale smoothly based on performance vs. your Target CPQS. Enable this to customize the scaling formula.",
            key=f"use_custom_policy{product_suffix}"
        )
    
        if use_custom_policy:
            # Custom mode - show controls
            st.markdown("**Bid Scaling Formula**")
            st.latex(r"\text{Bid \%} = \text{Base} + \frac{\text{Target CPQS} - \text{CPQS}}{\text{Target CPQS}} \times \text{Max Adjustment}")
        
            col1, col2 = st.columns(2)
            with col1:
                base_bid = st.number_input(
                    "Base bid %",
                    0, 300,
                    ss_default(f"base_bid{product_suffix}", 100),
                    5,
                    help="Starting point for all sources (typically 100%)",
                    key=f"base_bid{product_suffix}"
                )
            with col2:
                max_adjustment = st.number_input(
                    "Max adjustment %",
                    0, 100,
                    ss_default(f"max_adjustment{product_suffix}", 20),
                    5,
                    help="Maximum bid adjustment in either direction. Example: 20% means sources can range from 80% to 120%",
                    key=f"max_adjustment{product_suffix}"
                )
        
            bid_zero = st.number_input(
                "No Quote Starts bid %",
                0, 300,
                ss_default(f"bid_zero{product_suffix}", 25),
                5,
                key=f"bid_zero{product_suffix}",
                help="Fixed bid for sources with spend but zero Quote Starts"
            )
        
            # Show examples
            st.markdown("**Example Bid Adjustments (Asymmetric):**")
            st.caption("Better than target: ±" + str(max_adjustment // 2) + "% | Worse than target: ±" + str(max_adjustment) + "%")
            target_for_examples = target_cpqs
            examples = [
                (target_for_examples * 0.5, "50% better than target"),
                (target_for_examples * 0.8, "20% better than target"),
                (target_for_examples, "At target"),
                (target_for_examples * 1.2, "20% worse than target"),
                (target_for_examples * 1.5, "50% worse than target"),
            ]
        
            for cpqs, label in examples:
                performance_ratio = (target_for_examples - cpqs) / target_for_examples
                if performance_ratio > 0:
                    adjustment = performance_ratio * (max_adjustment / 2)
                else:
                    adjustment = performance_ratio * max_adjustment
                bid_calc = base_bid + adjustment
                bid_calc = max(0, min(300, bid_calc))
                st.caption(f"• ${cpqs:.2f} ({label}) → **{bid_calc:.0f}%**")
        
        else:
            # Auto mode - use defaults
            base_bid = 100
            max_adjustment = 20
            bid_zero = 25
        
            # Save to session state
            st.session_state[f"base_bid{product_suffix}"] = base_bid
            st.session_state[f"max_adjustment{product_suffix}"] = max_adjustment
            st.session_state[f"bid_zero{product_suffix}"] = bid_zero
        
            # Display formula and examples
            st.markdown(f""")
    **Continuous Bid Scaling (Auto-derived from Target CPQS: ${target_cpqs:.2f})**
    
    **Range:** 0-105% with smooth acceleration/deceleration
    - Most aggressive changes in the middle (40-100%)
    - Gentle approach to limits (diminishing returns at 0% and 105%)
    """)
        
            st.markdown("**Example Adjustments:**")
        
            # Calculate actual bids using the sigmoid formula
            import math
            examples = [
                (target_cpqs * 0.25, "75% better than target", "🟢"),
                (target_cpqs * 0.5, "50% better than target", "🟢"),
                (target_cpqs * 0.8, "20% better than target", "🟢"),
                (target_cpqs, "At target", "🟡"),
                (target_cpqs * 1.5, "50% worse than target", "🟡"),
                (target_cpqs * 2.0, "100% worse than target", "🔴"),
                (target_cpqs * 3.0, "200% worse than target", "🔴"),
            ]
        
            for cpqs, label, emoji in examples:
                performance_ratio = (target_cpqs - cpqs) / target_cpqs
                scaled_ratio = performance_ratio * 1.5
                tanh_value = math.tanh(scaled_ratio)
                bid_calc = 52.5 * (tanh_value + 1)
                bid_calc = max(0, min(105, bid_calc))
                st.caption(f"{emoji} ${cpqs:.2f} ({label}) → **{bid_calc:.0f}%**")
        
            st.caption("💡 Smooth S-curve prevents overreaction at extremes while being decisive in the middle range")
    
    
    def calculate_bid(row):
        """Calculate continuous bid adjustment based on performance vs target
    
        Range: 0-105% with diminishing returns at limits
        - Great performers approach 105% gradually (not aggressive at top)
        - Poor performers approach 0% gradually (not aggressive at bottom)
        - Most aggressive changes happen in the middle range (40-100%)
        """
        qs = row.get("B", 0.0) or 0.0
        spend = row.get("A", 0.0) or 0.0
    
        # Special case: No Quote Starts
        if spend > 0 and qs == 0:
            return bid_zero
    
        # Use raw CPQS for bid decisions
        eff_label = st.session_state.get(f"eff_label{product_suffix}", "CPQS")
        cpqs = row.get(eff_label, np.nan)
    
        # If no valid CPQS, return base bid
        if pd.isna(cpqs) or cpqs <= 0:
            return base_bid
    
        # Performance ratio: positive = better than target, negative = worse
        performance_ratio = (target_cpqs - cpqs) / target_cpqs
    
        # Map performance to 0-105% range with smooth sigmoid curve
        # Key points:
        # - At target (ratio=0) → 100%
        # - 50% better (ratio=0.5) → ~103%
        # - 75% better (ratio=0.75) → ~105%
        # - 50% worse (ratio=-0.5) → ~70%
        # - 100% worse (ratio=-1.0) → ~40%
        # - 200% worse (ratio=-2.0) → ~10%
    
        # Use a compressed sigmoid to create diminishing returns at extremes
        # Formula: 52.5 * tanh(performance_ratio * 1.5) + 52.5
        # This gives us a 0-105 range with smooth acceleration/deceleration
        import math
    
        # Scale the ratio for desired sensitivity
        scaled_ratio = performance_ratio * 1.5
    
        # Apply tanh for smooth S-curve (-1 to +1)
        tanh_value = math.tanh(scaled_ratio)
    
        # Map to 0-105% range
        # tanh(-inf) → -1 maps to 0%
        # tanh(0) → 0 maps to 52.5%
        # tanh(+inf) → +1 maps to 105%
        bid_pct = 52.5 * (tanh_value + 1)
    
        # Ensure we stay within bounds
        bid_pct = max(0, min(105, bid_pct))
    
        return bid_pct
    
    # grouped is now set in the product loop above
    # grouped = agg.copy()  # REMOVED - now set per-product in loop
    grouped["Target Bid %"] = grouped.apply(lambda r: int(calculate_bid(r)), axis=1)
    
    
    # -----------------------------
    # Policy signature for editor versioning
    # -----------------------------
    def policy_signature() -> str:
        payload = {
            "target_cpqs": target_cpqs,
            "base_bid": base_bid,
            "max_adjustment": max_adjustment,
            "bid_zero": bid_zero,
            "eff_label": eff_label
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:12]
    
    sig = policy_signature()
    if ss_default(f"prev_policy_sig{product_suffix}","") != sig:
        st.session_state[f"prev_policy_sig{product_suffix}"] = sig
        st.session_state[f"results_editor_version{product_suffix}"] = ss_default(f"results_editor_version{product_suffix}", 1) + 1
    
    # -----------------------------
    # Summary metrics row
    # -----------------------------
    total_spend = grouped["A"].sum()
    total_qs = grouped["B"].sum()
    
    # Add unmatched stats to totals (they exist but aren't in results table)
    total_qs_with_unmatched = total_qs + unmatched_qs_current
    cpl_value = (total_spend / total_qs_with_unmatched) if total_qs_with_unmatched else None
    
    m1,m2,m3,m4,m5 = st.columns(5)
    m1.metric("Rows (Results)", f"{len(grouped):,}")
    m2.metric("Total " + a_label, f"${total_spend:,.0f}")
    m3.metric("Total QS", f"{int(total_qs_with_unmatched):,}", 
              delta=f"+{unmatched_qs_current} unmatched" if unmatched_qs_current > 0 else None,
              delta_color="off")
    m4.metric("CPL", f"${cpl_value:,.0f}" if cpl_value is not None else "N/A")
    m5.metric("Active Range", f"{str(start_ts.date())} → {str(end_ts.date())}")
    
    # -----------------------------
    # Results (AgGrid)
    # -----------------------------
    with st.expander("8) 📋 Results", expanded=True):
        info_badge("Main table. Edit Recommended Bid % per row; toggle Include for export. Header filters enabled.")
        # Device column detection (optional)
        device_col = None
        for c in grouped.columns:
            if normalize(c) in {"device","devicetype","platform"}:
                device_col = c; break
    
        # internal stable key (not displayed)
        grouped["__row_key"] = make_row_key(grouped, device_col)
    
        # Add current modifiers if available
        # NEW APPROACH: Look up each row's QMP ID across ALL modifier files
        # This allows modifiers to work even when analyzing "all products together"
    
        # Initialize debug log
        debug_log = []
    
        modifier_matches = st.session_state.get("modifier_product_matches", {})
    
        if show_debug:
            msg = f"🔍 Debug 1: Found {len(modifier_matches)} modifier file(s) in session state"
            st.info(msg)
            debug_log.append(msg)
            msg = f"🔍 Debug 2: Modifier files for products: {list(modifier_matches.keys())}"
            st.info(msg)
            debug_log.append(msg)
            msg = f"🔍 Debug 3: QMP ID column = '{qmpid_col}', exists in grouped = {qmpid_col in grouped.columns if qmpid_col else False}"
            st.info(msg)
            debug_log.append(msg)
    
        if modifier_matches and qmpid_col and qmpid_col in grouped.columns:
            st.success(f"✅ Starting modifier application from {len(modifier_matches)} matched file(s)")
        
            # Build a master QMP ID → Current Modifier mapping from ALL modifier files
            qmpid_to_modifier = {}
            for product_name, modifier_df in modifier_matches.items():
                msg = f"🔍 Debug 4: Processing modifier file for '{product_name}' with {len(modifier_df)} rows"
                if show_debug: 
                    st.info(msg)
                    debug_log.append(msg)
            
                # Check column names in modifier file
                msg = f"🔍 Debug 5: Modifier file columns: {list(modifier_df.columns)}"
                if show_debug: 
                    st.info(msg)
                    debug_log.append(msg)
            
                if 'QMP ID' not in modifier_df.columns or 'Mobile modifier' not in modifier_df.columns:
                    st.error(f"❌ Modifier file for '{product_name}' missing required columns!")
                    continue
            
                for idx, row in modifier_df.iterrows():
                    try:
                        qmp_id = str(row['QMP ID']).strip()  # QMPID can be hash or number - keep as string
                    
                        # Read all three device modifiers
                        mobile_mod = row.get('Mobile modifier')
                        desktop_mod = row.get('Desktop modifier')
                        tablet_mod = row.get('Tablet modifier')
                    
                        # If modifier is NaN/blank, treat as 100%
                        mobile_mod = 100 if pd.isna(mobile_mod) else int(mobile_mod)
                        desktop_mod = 100 if pd.isna(desktop_mod) else int(desktop_mod)
                        tablet_mod = 100 if pd.isna(tablet_mod) else int(tablet_mod)
                    
                        # Debug specific QMP IDs
                        if show_debug and qmp_id in [14365950, 204676890, 16296900]:
                            msg = f"🔍 Debug 5.5: Reading QMP {qmp_id} from '{product_name}': Mobile={mobile_mod}, Desktop={desktop_mod}, Tablet={tablet_mod}"
                            st.info(msg)
                            debug_log.append(msg)
                    
                        # Store device-specific modifiers as dict
                        qmpid_to_modifier[qmp_id] = {
                            'Mobile': mobile_mod,
                            'Desktop': desktop_mod,
                            'Tablet': tablet_mod
                        }
                    except Exception as e:
                        st.warning(f"⚠️ Failed to parse row {idx}: QMP ID={row.get('QMP ID')}, Error={e}")
                        continue
        
            msg = f"🔍 Debug 6: Built modifier map with {len(qmpid_to_modifier)} QMP IDs"
            if show_debug: 
                st.info(msg)
                debug_log.append(msg)
            if len(qmpid_to_modifier) > 0:
                msg = f"🔍 Debug 7: Sample mappings: {dict(list(qmpid_to_modifier.items())[:3])}"
                if show_debug: 
                    st.info(msg)
                    debug_log.append(msg)
        
            # Debug: Check what we're actually mapping
            msg = f"🔍 Debug 7.5: Mapping column '{qmpid_col}' from grouped"
            if show_debug: 
                st.info(msg)
                debug_log.append(msg)
            msg = f"🔍 Debug 7.6: Sample QMP IDs in grouped['{qmpid_col}']: {grouped[qmpid_col].head(5).tolist()}"
            if show_debug: 
                st.info(msg)
                debug_log.append(msg)
            msg = f"🔍 Debug 7.7: Are these QMP IDs in the map? {[qmp in qmpid_to_modifier for qmp in grouped[qmpid_col].head(5)]}"
            if show_debug: 
                st.info(msg)
                debug_log.append(msg)
        
            # Check specific QMP IDs
            if show_debug:
                test_ids = [14365950, 204676890, 16296900]
                for tid in test_ids:
                    if tid in qmpid_to_modifier:
                        msg = f"🔍 Debug 7.8: QMP ID {tid} IS in map with modifier = {qmpid_to_modifier[tid]}"
                        st.info(msg)
                        debug_log.append(msg)
                    else:
                        msg = f"🔍 Debug 7.8: QMP ID {tid} NOT in map"
                        st.info(msg)
                        debug_log.append(msg)
        
            # CRITICAL: Convert QMP IDs to int for matching (grouped may have strings, map has ints)
            # Skip rows with non-numeric QMP IDs (like "[Unknown]" for unmatched stats)
            def safe_qmpid_convert(val):
                """Convert QMPID to string (can be hash or number)"""
                try:
                    return str(val).strip()
                except (ValueError, TypeError):
                    return None
        
            grouped_qmpids = grouped[qmpid_col].apply(safe_qmpid_convert)
        
            msg = f"🔍 Debug 7.9: After conversion, sample QMP IDs: {grouped_qmpids.head(5).tolist()}"
            if show_debug: 
                st.info(msg)
                debug_log.append(msg)
        
            # Map current modifiers to each row by QMP ID AND device type
            def get_device_modifier(row):
                qmp_id = safe_qmpid_convert(row[qmpid_col])
                if qmp_id is None or qmp_id not in qmpid_to_modifier:
                    return 100  # Default
            
                # Get device-specific modifier
                device_mods = qmpid_to_modifier[qmp_id]
            
                # Detect device from row - check if device column exists in grouped AND in this row
                if device_col and device_col in grouped.columns:
                    try:
                        device = str(row[device_col]).strip().lower()
                        # Normalize device names
                        if 'mobile' in device or 'phone' in device:
                            return device_mods['Mobile']
                        elif 'desktop' in device or 'computer' in device:
                            return device_mods['Desktop']
                        elif 'tablet' in device:
                            return device_mods['Tablet']
                    except (KeyError, AttributeError):
                        pass  # Fall through to default
            
                # If no device column or unrecognized device, use Mobile as default
                return device_mods['Mobile']
        
            grouped["Current Modifier %"] = grouped.apply(get_device_modifier, axis=1)
        
            msg = f"🔍 Debug 7.10: After mapping, sample Current Modifier %: {grouped['Current Modifier %'].head(5).tolist()}"
            if show_debug: 
                st.info(msg)
                debug_log.append(msg)
        
            # Debug: Show device column detection and sample values
            if show_debug:
                msg = f"🔍 Debug 7.11: Device column detected: '{device_col}' (exists in grouped: {device_col in grouped.columns if device_col else False})"
                st.info(msg)
                debug_log.append(msg)
                if device_col and device_col in grouped.columns:
                    msg = f"🔍 Debug 7.12: Sample device values: {grouped[device_col].head(5).tolist()}"
                    st.info(msg)
                    debug_log.append(msg)
                    msg = f"🔍 Debug 7.13: Sample QMP IDs with devices: {list(zip(grouped[qmpid_col].head(5), grouped[device_col].head(5)))}"
                    st.info(msg)
                    debug_log.append(msg)
        
            matched_count = (grouped["Current Modifier %"] != 100).sum()
            msg = f"🔍 Debug 8: Matched {matched_count} of {len(grouped)} rows to modifiers"
            if show_debug: 
                st.info(msg)
                debug_log.append(msg)
        
            if matched_count > 0:
                # DYNAMIC INCREMENTAL STRATEGY:
                # - More aggressive when gap is large, less aggressive when close
                # - Decreasing: 75% of gap, dynamic cap based on gap size
                # - Increasing: 50% of gap, dynamic cap based on gap size
            
                grouped["Gap"] = grouped["Target Bid %"] - grouped["Current Modifier %"]
            
                def calculate_recommended(row):
                    current = row["Current Modifier %"]
                    target = row["Target Bid %"]
                    gap = row["Gap"]
                
                    if gap < 0:
                        # Need to DECREASE (cut waste) - go 75% of the way
                        step = gap * 0.75
                    
                        # Dynamic cap based on gap size (more aggressive when gap is large)
                        abs_gap = abs(gap)
                        if abs_gap >= 100:
                            max_decrease = -30
                        elif abs_gap >= 50:
                            max_decrease = -20
                        elif abs_gap >= 20:
                            max_decrease = -15
                        elif abs_gap >= 10:
                            max_decrease = -10
                        else:
                            max_decrease = -5
                    
                        step = max(max_decrease, step)
                    
                    else:
                        # Need to INCREASE (raise bid) - go 50% of the way
                        step = gap * 0.50
                    
                        # Dynamic cap based on gap size (more aggressive when gap is large)
                        if gap >= 50:
                            max_increase = 20
                        elif gap >= 20:
                            max_increase = 15
                        elif gap >= 10:
                            max_increase = 10
                        else:
                            max_increase = 5
                    
                        step = min(max_increase, step)
                
                    # Calculate recommended
                    recommended = current + step
                
                    # Ensure within 0-105% bounds
                    recommended = max(0, min(105, recommended))
                
                    return int(round(recommended))
            
                grouped["Recommended Bid %"] = grouped.apply(calculate_recommended, axis=1)
            
                # Show the change being recommended
                grouped["Change"] = grouped["Recommended Bid %"] - grouped["Current Modifier %"]
            
                has_modifiers = True
                st.success(f"✅ Applied dynamic incremental modifiers to {matched_count} sources")
            else:
                st.warning("⚠️ No QMP IDs matched between data and modifier files - using target bids")
                grouped["Recommended Bid %"] = grouped["Target Bid %"]
                has_modifiers = False
        else:
            # No modifiers: Assume current is 100%, recommend the target
            debug_log.append("No modifier matching performed")
            grouped["Recommended Bid %"] = grouped["Target Bid %"]
            has_modifiers = False
        
            if not modifier_matches:
                st.info("ℹ️ No modifier files matched - recommendations assume current modifiers are 100%")
            elif not qmpid_col:
                st.warning("⚠️ QMP ID column not found - cannot apply modifiers")
            elif qmpid_col not in grouped.columns:
                st.warning(f"⚠️ QMP ID column '{qmpid_col}' not in results - cannot apply modifiers")
    
        # Prepare display
        display = grouped.rename(columns={
            "A": a_label,
            "B": "Quote Starts (from stats)",
            "C": "Phone Clicks (from stats)",
            "D": "SMS Clicks (from stats)",
        }).copy()
    
        # ========================================
        # INTELLIGENCE LAYER: Statistical Confidence, Auto-Pause, Health Score
        # ========================================
    
        # 1) DATA QUALITY / STATISTICAL CONFIDENCE
        # Flag sources with insufficient sample size
        if "Clicks" in display.columns:
            clicks_col = "Clicks"
        else:
            clicks_col = None
    
        def calculate_confidence(row):
            clicks = row.get(clicks_col, 0) if clicks_col else 0
            qs = row.get("Quote Starts (from stats)", 0) or 0
        
            # Sample size thresholds
            if clicks >= 30 or qs >= 10:
                return "🟢 High"
            elif clicks >= 15 or qs >= 5:
                return "🟡 Medium"
            elif clicks >= 5 or qs >= 2:
                return "🟠 Low"
            else:
                return "🔴 Insufficient"
    
        display["Data Quality"] = display.apply(calculate_confidence, axis=1)
    
        # 2) AUTO-PAUSE RECOMMENDATIONS
        # Flag sources that should be paused based on spend and performance
        def calculate_action(row):
            spend = row.get(a_label, 0) or 0
            qs = row.get("Quote Starts (from stats)", 0) or 0
            cpqs = row.get(eff_label, np.nan)
        
            # Rule 1: High spend, zero conversions
            if spend >= 100 and qs == 0:
                return "⛔ PAUSE (No QS)"
        
            # Rule 2: Spend over threshold with terrible CPQS
            if spend >= 50 and not np.isnan(cpqs) and target_cpqs > 0:
                if cpqs > target_cpqs * 3:
                    return "⛔ PAUSE (3x+ Target)"
        
            # Rule 3: Recommend investigation
            if spend >= 50 and not np.isnan(cpqs) and target_cpqs > 0:
                if cpqs > target_cpqs * 2:
                    return "⚠️ Review (2x+ Target)"
        
            # No action needed
            return "✅ OK"
    
        display["Action"] = display.apply(calculate_action, axis=1)
    
        # 3) SOURCE HEALTH SCORE (A/B/C/D/F)
        # Composite score: Efficiency + Volume + Data Quality
        def calculate_health_score(row):
            spend = row.get(a_label, 0) or 0
            qs = row.get("Quote Starts (from stats)", 0) or 0
            cpqs = row.get(eff_label, np.nan)
            clicks = row.get(clicks_col, 0) if clicks_col else 0
        
            score = 0
        
            # Efficiency component (0-50 points)
            if not np.isnan(cpqs) and target_cpqs > 0:
                ratio = cpqs / target_cpqs
                if ratio <= 0.7:
                    score += 50  # Excellent
                elif ratio <= 1.0:
                    score += 40  # Good
                elif ratio <= 1.5:
                    score += 25  # Fair
                elif ratio <= 2.0:
                    score += 10  # Poor
                else:
                    score += 0   # Very Poor
        
            # Volume component (0-30 points)
            if qs >= 5:
                score += 30  # High volume
            elif qs >= 2:
                score += 20  # Medium volume
            elif qs >= 1:
                score += 10  # Low volume
            else:
                score += 0   # No conversions
        
            # Data quality component (0-20 points)
            if clicks >= 30 or qs >= 10:
                score += 20  # High confidence
            elif clicks >= 15 or qs >= 5:
                score += 15  # Medium confidence
            elif clicks >= 5 or qs >= 2:
                score += 10  # Low confidence
            else:
                score += 0   # Insufficient data
        
            # Convert to letter grade
            if score >= 80:
                return "A"
            elif score >= 65:
                return "B"
            elif score >= 50:
                return "C"
            elif score >= 35:
                return "D"
            else:
                return "F"
    
        display["Health"] = display.apply(calculate_health_score, axis=1)
    
        # Summary stats for intelligence features
        pause_count = (display["Action"].str.contains("PAUSE", na=False)).sum()
        review_count = (display["Action"].str.contains("Review", na=False)).sum()
        health_breakdown = display["Health"].value_counts().to_dict()
    
        if pause_count > 0 or review_count > 0:
            st.warning(f"⚠️ **Action Required:** {pause_count} sources recommended for pause, {review_count} for review")
    
        health_summary = " | ".join([f"{grade}: {count}" for grade, count in sorted(health_breakdown.items())])
        st.info(f"📊 **Source Health Distribution:** {health_summary}")
    
        # Position Clicks next to Spend if present
        if "Clicks" in display.columns and a_label in display.columns:
            cols = list(display.columns)
            if "Clicks" in cols:
                cols.remove("Clicks")
                cols.insert(cols.index(a_label) + 1, "Clicks")
                display = display[cols]
    
        # Formatting
        def fmt_num(v, money=False):
            if pd.isna(v): return ""
            return f"${v:,.2f}" if money else f"{v:,.2f}"
    
        if a_label in display.columns and a_fmt_currency:
            display[a_label] = display[a_label].apply(lambda v: fmt_num(v, True))
        if eff_label in display.columns and eff_fmt_currency:
            display[eff_label] = display[eff_label].apply(lambda v: fmt_num(v, True))
    
        for nm in ["Quote Starts (from stats)","Phone Clicks (from stats)","SMS Clicks (from stats)","Clicks"]:
            if nm in display.columns:
                try: display[nm] = display[nm].fillna(0).astype(int)
                except: pass
        for nm in ["Cost per Phone+QS","Cost per SMS+QS","Cost per Lead"]:
            if nm in display.columns:
                display[nm] = display[nm].apply(lambda v: fmt_num(v, True))
    
        # Include checkbox
        sel_map = ss_default(f"export_selection{product_suffix}", {})
        for rk in grouped["__row_key"]:
            sel_map.setdefault(str(rk), True)
        display["Include"] = [sel_map.get(str(rk), True) for rk in grouped["__row_key"]]
    
        # Order: Include, Status, Intelligence columns first, then others (no __row_key)
        intelligence_cols = ["Action", "Health", "Data Quality"]
        excluded_cols = {"Include", "Status", "__row_key"} | set(intelligence_cols)
        other_cols = [c for c in display.columns if c not in excluded_cols]
    
        cols_order = ["Include"]
        if "Status" in display.columns:
            cols_order.append("Status")
        cols_order.extend([c for c in intelligence_cols if c in display.columns])
        cols_order.extend(other_cols)
    
        display = display[cols_order]
    
        # AgGrid options
        gb = GridOptionsBuilder.from_dataframe(display)
        gb.configure_default_column(filter=True, sortable=True, resizable=True)
        gb.configure_column("Include", headerCheckboxSelection=True, checkboxSelection=True, pinned="left", width=100)
        gb.configure_column("Status", pinned="left", width=90)
    
        # Intelligence columns (pinned for visibility)
        gb.configure_column("Action", pinned="left", width=150)
        gb.configure_column("Health", width=80)
        gb.configure_column("Data Quality", width=120)
    
        # Set compact widths for numeric columns
        gb.configure_column("Target Bid %", width=100)
        gb.configure_column("Current Modifier %", width=130)
        gb.configure_column("Recommended Bid %", width=140)
        gb.configure_column("Gap", width=80)
        gb.configure_column("Change", width=90)
    
        # Dynamic efficiency column (CPQS, CPA, CPL, etc.)
        if eff_label in display.columns:
            gb.configure_column(eff_label, width=90)
    
        # Other cost columns
        gb.configure_column("Cost per Lead", width=110)
        gb.configure_column("Cost per Phone+QS", width=140)
        gb.configure_column("Cost per SMS+QS", width=130)
    
        # Enable cell selection and copying
        gb.configure_selection(selection_mode='multiple', use_checkbox=False)
        gb.configure_grid_options(
            enableRangeSelection=True,
            enableCellTextSelection=True,
            ensureDomOrder=True,
            clipboardDelimiter='\t'
        )
    
        gb.configure_side_bar()  # column tool panel
        go = gb.build()
    
        grid = AgGrid(
            display,
            gridOptions=go,
            update_mode=GridUpdateMode.VALUE_CHANGED,
            fit_columns_on_grid_load=False,
            allow_unsafe_jscode=True,
            enable_enterprise_modules=False,
            columns_auto_size_mode=ColumnsAutoSizeMode.FIT_CONTENTS
        )
    
        # Persist Include & Edited Recommended Bid %
        new_df = grid["data"]
        # Reattach hidden key to align changes
        new_df["__row_key"] = grouped["__row_key"].values
    
        # Include persistence
        if "Include" in new_df.columns:
            for rk, inc in zip(new_df["__row_key"], new_df["Include"]):
                sel_map[str(rk)] = bool(inc)
            st.session_state[f"export_selection{product_suffix}"] = sel_map
    
        # Recommended persistence (compare vs policy)
        if "Recommended Bid %" in new_df.columns:
            policy_map = grouped.set_index("__row_key")["Recommended Bid %"].to_dict()
            overrides = ss_default(f"bid_overrides{product_suffix}", {})
            for rk, val in zip(new_df["__row_key"], pd.to_numeric(new_df["Recommended Bid %"], errors="coerce").fillna(0).astype(int)):
                if val != int(policy_map.get(rk, val)):
                    overrides[str(rk)] = int(val)
                else:
                    overrides.pop(str(rk), None)
            st.session_state[f"bid_overrides{product_suffix}"] = overrides
    
        # CSV download for Results (user-facing columns only)
        results_export = display.copy()
        # Remove Include checkbox column from export
        if "Include" in results_export.columns:
            results_export = results_export.drop(columns=["Include"])
        csv_res = results_export.to_csv(index=False)
        st.download_button("⬇️ Download Results CSV", data=csv_res, file_name="mm_results.csv", mime="text/csv", key=f"download_button_1{product_suffix}")
    
        # Debug Log Export (actual debug messages)
        st.markdown("---")
        st.markdown("**🐛 Debug Log Export**")
        if 'debug_log' in locals() and len(debug_log) > 0:
            # Create a dataframe with timestamp and message
            debug_df = pd.DataFrame({
                'Timestamp': [pd.Timestamp.now()] * len(debug_log),
                'Message': debug_log
            })
            csv_debug_log = debug_df.to_csv(index=False)
            st.download_button(
                "⬇️ Download Debug Log", 
                data=csv_debug_log, 
                file_name="mm_debug_log.csv", 
                mime="text/csv",
                help="Debug messages showing modifier file processing and QMP ID matching",
                key=f"download_debug_log{product_suffix}"
            )
            st.caption(f"📋 {len(debug_log)} debug messages captured")
        else:
            st.caption("Enable 'Show debug messages' in Advanced Mode to generate debug log")
    
        # ========================================
        # STRATEGIC INSIGHTS & RECOMMENDATIONS
        # ========================================
        st.markdown("---")
        st.markdown("### 🧠 Strategic Insights & Recommendations")
        st.caption("AI-powered analysis across all dimensions to identify optimization opportunities")
    
        # Configuration
        if not simple_mode:  # Advanced Mode
            with st.expander("⚙️ Insights Configuration"):
                st.markdown("**Sample Size Requirements:**")
                col1, col2 = st.columns(2)
                with col1:
                    insights_min_clicks = st.number_input("Minimum clicks per segment", value=10, min_value=3, help="Segments below this threshold are too noisy to analyze", key=f"number_input_8{product_suffix}")
                    insights_min_qs = st.number_input("Minimum QS per segment", value=3, min_value=1, help="Segments below this threshold lack conversion data", key=f"number_input_9{product_suffix}")
                with col2:
                    insights_max_display = st.number_input("Max insights to display", value=20, min_value=5, max_value=100, help="Top N insights by impact", key=f"number_input_10{product_suffix}")
                    variance_threshold = st.slider(
                        "Variance threshold for drilling (%)",
                        min_value=10, max_value=50, value=20, step=5,
                        help="When drilling deeper, only continue if sub-segments differ by this % from parent"
                    ,
            key=f"slider_ml2574{product_suffix}")
            
                st.markdown("---")
                st.markdown("**Performance Deviation Thresholds:**")
                st.caption("Higher thresholds = stricter criteria (fewer insights). Lower = more sensitive (more insights).")
            
                col1, col2 = st.columns(2)
            
                with col1:
                    st.markdown("**🚨 Problem Detection** (CPQS worse than target)")
                    problem_high = st.slider(
                        "High confidence (30+ clicks, 10+ QS)",
                        min_value=1.2, max_value=3.0, value=1.5, step=0.1,
                        format="%.1fx",
                        help="Flag segments performing this many times worse than target",
                        key=f"problem_high{product_suffix}"
                    )
                    problem_medium = st.slider(
                        "Medium confidence (15-29 clicks, 5-9 QS)",
                        min_value=1.5, max_value=4.0, value=2.0, step=0.1,
                        format="%.1fx",
                        key=f"problem_medium{product_suffix}")
                    problem_low = st.slider(
                        "Low confidence (10-14 clicks, 3-4 QS)",
                        min_value=2.0, max_value=5.0, value=3.0, step=0.1,
                        format="%.1fx",
                        key=f"problem_low{product_suffix}")
            
                with col2:
                    st.markdown("**✅ Opportunity Detection** (CPQS better than target)")
                    opp_high = st.slider(
                        "High confidence (30+ clicks, 10+ QS)",
                        min_value=0.5, max_value=0.9, value=0.7, step=0.05,
                        format="%.2fx",
                        help="Flag segments performing this fraction of target (e.g., 0.7 = 30% better)",
                        key=f"opp_high{product_suffix}"
                    )
                    opp_medium = st.slider(
                        "Medium confidence (15-29 clicks, 5-9 QS)",
                        min_value=0.4, max_value=0.8, value=0.6, step=0.05,
                        format="%.2fx",
                        key=f"opp_medium{product_suffix}")
                    opp_low = st.slider(
                        "Low confidence (10-14 clicks, 3-4 QS)",
                        min_value=0.3, max_value=0.7, value=0.5, step=0.05,
                        format="%.2fx",
                        key=f"opp_low{product_suffix}")
        else:  # Simple Mode
            insights_min_clicks = 10
            insights_min_qs = 3
            insights_max_display = 20
            variance_threshold = 20
            # Default thresholds
            problem_high = 1.5
            problem_medium = 2.0
            problem_low = 3.0
            opp_high = 0.7
            opp_medium = 0.6
            opp_low = 0.5
    
        # Only run if we have stats data (B column exists in grouped after matching)
        if 'B' in grouped.columns and grouped['B'].sum() > 0:
        
            # Helper function to calculate confidence-based thresholds
            def get_deviation_threshold(clicks, qs, direction="problem"):
                """Returns the CPQS multiplier threshold based on sample size"""
                # Confidence tier
                if clicks >= 30 and qs >= 10:
                    confidence = "high"
                elif clicks >= 15 and qs >= 5:
                    confidence = "medium"
                else:
                    confidence = "low"
            
                # Thresholds - use configured values
                if direction == "problem":
                    thresholds = {
                        "high": problem_high,
                        "medium": problem_medium,
                        "low": problem_low
                    }
                else:  # opportunity
                    thresholds = {
                        "high": opp_high,
                        "medium": opp_medium,
                        "low": opp_low
                    }
            
                return thresholds[confidence], confidence
        
            # Analyze all dimensions
            insights = []
        
            # Get target CPQS from economics
            target_cpqs = st.session_state.get("econ_target_cpqs", 12.82)
        
            # Find all potential dimensions to analyze from click_work
            exclude_patterns = ['click', 'id', 'key', 'timestamp', 'url', 'ip', 'spend', 'cost', 'cpa', 
                               'conversion', 'impr', 'rank', 'quality', '__row', 'a', 'b', 'c', 'd']
        
            potential_dims = []
            for col in click_work.columns:
                col_lower = col.lower()
                # Skip if matches exclude pattern
                if any(pattern in col_lower for pattern in exclude_patterns):
                    continue
                # Skip if too many unique values (likely an ID or freeform text)
                if click_work[col].nunique() > 50:
                    continue
                # Skip if only 1 unique value (no variance)
                if click_work[col].nunique() <= 1:
                    continue
                potential_dims.append(col)
        
            if len(potential_dims) == 0:
                st.info("ℹ️ No suitable dimensions found for analysis")
            else:
                st.info(f"🔍 Analyzing {len(potential_dims)} dimensions: {', '.join(potential_dims[:10])}{'...' if len(potential_dims) > 10 else ''}")
            
                # Analyze each dimension
                for dim in potential_dims:
                    # Check if dimension exists in click_work
                    if dim not in click_work.columns:
                        continue
                
                    # Group by this dimension
                    try:
                        dim_grouped = click_work.groupby(dim, as_index=False).agg({
                            'Clicks': 'sum',
                            'B': 'sum',  # QS
                            'A': 'sum'   # Spend
                        })
                    except KeyError:
                        # A or B might not exist if stats not matched
                        continue
                
                    # Calculate CPQS for each segment
                    dim_grouped['CPQS'] = dim_grouped['A'] / dim_grouped['B']
                    dim_grouped['CPQS'] = dim_grouped['CPQS'].replace([np.inf, -np.inf], np.nan)
                
                    # Debug: Show sample of this dimension's analysis
                    if show_debug and dim in potential_dims[:3]:  # Show first 3 dimensions
                        st.caption(f"🔍 Dimension '{dim}' has {len(dim_grouped)} segments")
                        st.dataframe(dim_grouped.head(5))
                
                    # Analyze each segment
                    segments_analyzed = 0
                    segments_below_min = 0
                    segments_no_deviation = 0
                
                    for idx, row in dim_grouped.iterrows():
                        segment_value = row[dim]
                        clicks = row['Clicks'] if 'Clicks' in row else 0
                        qs = row['B']
                        spend = row['A']
                        cpqs = row['CPQS']
                    
                        # Skip if below minimums
                        if clicks < insights_min_clicks or qs < insights_min_qs:
                            segments_below_min += 1
                            continue
                    
                        # Skip if CPQS is NaN
                        if pd.isna(cpqs):
                            continue
                    
                        segments_analyzed += 1
                    
                        # Check if this is a problem or opportunity
                        problem_threshold, confidence = get_deviation_threshold(clicks, qs, "problem")
                        opportunity_threshold, _ = get_deviation_threshold(clicks, qs, "opportunity")
                    
                        insight_type = None
                        deviation_multiplier = cpqs / target_cpqs
                    
                        if deviation_multiplier >= problem_threshold:
                            insight_type = "WASTE"
                            action = "⛔ PAUSE or reduce to 0%"
                            impact = (cpqs - target_cpqs) * qs  # Potential monthly savings
                        elif deviation_multiplier <= opportunity_threshold:
                            insight_type = "SCALE"
                            action = "✅ Increase bid to 125%+ to capture more volume"
                            impact = (target_cpqs - cpqs) * qs * 2  # Potential growth value (2x current volume)
                        else:
                            segments_no_deviation += 1
                            continue  # Not significant enough
                    
                        insights.append({
                            'Type': insight_type,
                            'Dimension Path': dim,
                            'Segment': str(segment_value),
                            'CPQS': cpqs,
                            'Target CPQS': target_cpqs,
                            'Deviation': f"{deviation_multiplier:.1f}x",
                            'Clicks': int(clicks),
                            'QS': int(qs),
                            'Spend': spend,
                            'Confidence': confidence.title(),
                            'Action': action,
                            'Impact Score': abs(impact),
                            'Monthly Impact': f"${abs(impact):,.0f}"
                        })
                
                    # Debug: Show why no insights for this dimension
                    if show_debug and segments_analyzed > 0 and dim in potential_dims[:3]:
                        st.caption(f"  → {segments_analyzed} segments met minimums, {segments_no_deviation} didn't meet deviation thresholds, {segments_below_min} below sample minimums")
            
                # ========================================
                # STAGE 2: RECURSIVE DRILLING
                # ========================================
                # Now drill down on any significant segments found
            
                def drill_segment(parent_filters, parent_cpqs, parent_clicks, parent_qs, depth=1, max_depth=10):
                    """
                    Recursively drill into a segment by trying additional dimensions.
                
                    parent_filters: dict of {dimension: value} defining this segment
                    parent_cpqs: CPQS of parent segment
                    parent_clicks, parent_qs: Volume of parent segment
                    depth: Current recursion depth
                    max_depth: Safety limit to prevent infinite recursion
                    """
                    if depth >= max_depth:
                        return
                
                    # Get data for this segment by applying all parent filters
                    segment_data = click_work.copy()
                    for dim, val in parent_filters.items():
                        segment_data = segment_data[segment_data[dim] == val]
                
                    if len(segment_data) == 0:
                        return
                
                    # Try adding each remaining dimension
                    used_dims = set(parent_filters.keys())
                    remaining_dims = [d for d in potential_dims if d not in used_dims and d in segment_data.columns]
                
                    for next_dim in remaining_dims:
                        try:
                            # Group by this additional dimension
                            drill_grouped = segment_data.groupby(next_dim, as_index=False).agg({
                                'Clicks': 'sum',
                                'B': 'sum',
                                'A': 'sum'
                            })
                        except KeyError:
                            continue
                    
                        drill_grouped['CPQS'] = drill_grouped['A'] / drill_grouped['B']
                        drill_grouped['CPQS'] = drill_grouped['CPQS'].replace([np.inf, -np.inf], np.nan)
                    
                        # Check each sub-segment
                        for idx, row in drill_grouped.iterrows():
                            sub_value = row[next_dim]
                            sub_clicks = row['Clicks'] if 'Clicks' in row else 0
                            sub_qs = row['B']
                            sub_spend = row['A']
                            sub_cpqs = row['CPQS']
                        
                            # Must meet minimums
                            if sub_clicks < insights_min_clicks or sub_qs < insights_min_qs:
                                continue
                        
                            if pd.isna(sub_cpqs):
                                continue
                        
                            # Must show meaningful variance from parent (variance_threshold %)
                            variance_pct = abs((sub_cpqs - parent_cpqs) / parent_cpqs) * 100
                            if variance_pct < variance_threshold:
                                continue
                        
                            # Check if this sub-segment meets deviation thresholds
                            problem_threshold, confidence = get_deviation_threshold(sub_clicks, sub_qs, "problem")
                            opportunity_threshold, _ = get_deviation_threshold(sub_clicks, sub_qs, "opportunity")
                        
                            deviation_multiplier = sub_cpqs / target_cpqs
                            insight_type = None
                        
                            if deviation_multiplier >= problem_threshold:
                                insight_type = "WASTE"
                                action = "⛔ PAUSE or reduce to 0%"
                                impact = (sub_cpqs - target_cpqs) * sub_qs
                            elif deviation_multiplier <= opportunity_threshold:
                                insight_type = "SCALE"
                                action = "✅ Increase bid to 125%+ to capture more volume"
                                impact = (target_cpqs - sub_cpqs) * sub_qs * 2
                            else:
                                continue
                        
                            # Build dimension path string
                            path_parts = [f"{k}={v}" for k, v in parent_filters.items()]
                            path_parts.append(f"{next_dim}={sub_value}")
                            dim_path = " × ".join(path_parts)
                        
                            # Add this multi-dimensional insight
                            insights.append({
                                'Type': insight_type,
                                'Dimension Path': dim_path,
                                'Segment': str(sub_value),
                                'CPQS': sub_cpqs,
                                'Target CPQS': target_cpqs,
                                'Deviation': f"{deviation_multiplier:.1f}x",
                                'Clicks': int(sub_clicks),
                                'QS': int(sub_qs),
                                'Spend': sub_spend,
                                'Confidence': confidence.title(),
                                'Action': action,
                                'Impact Score': abs(impact),
                                'Monthly Impact': f"${abs(impact):,.0f}",
                                'Depth': depth
                            })
                        
                            # Recursively drill deeper from this sub-segment
                            new_filters = parent_filters.copy()
                            new_filters[next_dim] = sub_value
                            drill_segment(new_filters, sub_cpqs, sub_clicks, sub_qs, depth + 1, max_depth)
            
                # Trigger drilling for each significant single-dimension insight
                single_dim_insights = [i for i in insights if 'Depth' not in i]
                if len(single_dim_insights) > 0:
                    st.info(f"🔍 Stage 2: Drilling deeper into {len(single_dim_insights)} significant segments...")
                    for insight in single_dim_insights:
                        parent_filters = {insight['Dimension Path']: insight['Segment']}
                        drill_segment(
                            parent_filters,
                            insight['CPQS'],
                            insight['Clicks'],
                            insight['QS'],
                            depth=1,
                            max_depth=10
                        )
        
            # Sort by impact score
            insights_df = pd.DataFrame(insights)
        
            if len(insights_df) > 0:
                # ========================================
                # STAGE 3: IMPACT SCORING & GROUPING
                # ========================================
            
                # Add actionability score (combines impact with confidence and ease)
                def calculate_actionability(row):
                    impact = row['Impact Score']
                    confidence_map = {'High': 1.0, 'Medium': 0.7, 'Low': 0.4}
                    confidence_factor = confidence_map.get(row['Confidence'], 0.5)
                
                    # Favor simpler segments (fewer dimensions = easier to act on)
                    depth = row.get('Depth', 0)
                    simplicity_factor = 1.0 / (1 + depth * 0.2)  # Single dim = 1.0, 2-dim = 0.83, 3-dim = 0.71
                
                    # Volume factor (enough data to make it worthwhile)
                    clicks = row['Clicks']
                    volume_factor = min(1.0, clicks / 50)  # Caps at 50 clicks
                
                    return impact * confidence_factor * simplicity_factor * volume_factor
            
                insights_df['Actionability Score'] = insights_df.apply(calculate_actionability, axis=1)
            
                # Group similar insights
                # For example: "DMA=Houston" and "DMA=Dallas" are both DMA insights
                def get_insight_category(dim_path):
                    if '×' in dim_path:
                        # Multi-dimensional - use first dimension as category
                        return dim_path.split('×')[0].split('=')[0].strip()
                    else:
                        # Single dimension
                        return dim_path.split('=')[0].strip() if '=' in dim_path else dim_path
            
                insights_df['Category'] = insights_df['Dimension Path'].apply(get_insight_category)
            
                # Sort by actionability (not just raw impact)
                insights_df = insights_df.sort_values('Actionability Score', ascending=False).head(insights_max_display)
            
                # Display insights by category
                waste_insights = insights_df[insights_df['Type'] == 'WASTE']
                scale_insights = insights_df[insights_df['Type'] == 'SCALE']
            
                if len(waste_insights) > 0:
                    st.markdown("#### 🚨 HIGH-IMPACT WASTE")
                    total_waste = waste_insights['Impact Score'].sum()
                    st.warning(f"💰 **${total_waste:,.0f}/month** potential savings identified across {len(waste_insights)} segments")
                
                    # Group by category for better organization
                    categories = waste_insights.groupby('Category')
                
                    for category, cat_insights in categories:
                        if len(categories) > 1:
                            st.markdown(f"**{category} Insights:**")
                    
                        for idx, insight in cat_insights.head(5).iterrows():  # Top 5 per category
                            with st.container():
                                # Simplicity indicator
                                depth = insight.get('Depth', 0)
                                simplicity_badge = "🎯 Simple" if depth == 0 else "🔍 Detailed" if depth <= 2 else "🔬 Deep Dive"
                            
                                st.markdown(f""")
    **{insight['Dimension Path']}** = `{insight['Segment']}`  
    📊 Performance: **${insight['CPQS']:.2f} CPQS** ({insight['Deviation']} vs ${insight['Target CPQS']:.2f} target)  
    📈 Volume: {insight['Clicks']} clicks, {insight['QS']} QS, ${insight['Spend']:.2f} spend  
    🎯 Action: {insight['Action']}  
    💰 Impact: ~{insight['Monthly Impact']} potential monthly savings  
    ✓ {simplicity_badge} • {insight['Confidence']} confidence
    """)
                                st.markdown("---")
            
                if len(scale_insights) > 0:
                    st.markdown("#### ✅ SCALE OPPORTUNITIES")
                    total_opportunity = scale_insights['Impact Score'].sum()
                    st.success(f"📈 **${total_opportunity:,.0f}/month** growth potential identified across {len(scale_insights)} high-performing segments")
                
                    # Group by category
                    categories = scale_insights.groupby('Category')
                
                    for category, cat_insights in categories:
                        if len(categories) > 1:
                            st.markdown(f"**{category} Insights:**")
                    
                        for idx, insight in cat_insights.head(5).iterrows():  # Top 5 per category
                            with st.container():
                                # Simplicity indicator
                                depth = insight.get('Depth', 0)
                                simplicity_badge = "🎯 Simple" if depth == 0 else "🔍 Detailed" if depth <= 2 else "🔬 Deep Dive"
                            
                                efficiency = abs(1 - insight['CPQS']/insight['Target CPQS'])*100
                            
                                st.markdown(f""")
    **{insight['Dimension Path']}** = `{insight['Segment']}`  
    📊 Performance: **${insight['CPQS']:.2f} CPQS** ({insight['Deviation']} vs ${insight['Target CPQS']:.2f} target) - {efficiency:.0f}% better!  
    📈 Volume: {insight['Clicks']} clicks, {insight['QS']} QS  
    🎯 Action: {insight['Action']}  
    💰 Impact: ~{insight['Monthly Impact']} growth potential  
    ✓ {simplicity_badge} • {insight['Confidence']} confidence
    """)
                                st.markdown("---")
            
                # Export insights with enhanced columns
                st.markdown("**📥 Export Insights**")
                export_cols = ['Type', 'Category', 'Dimension Path', 'Segment', 'CPQS', 'Target CPQS', 'Deviation', 
                              'Clicks', 'QS', 'Spend', 'Confidence', 'Action', 'Monthly Impact']
                export_cols_available = [col for col in export_cols if col in insights_df.columns]
                insights_csv = insights_df[export_cols_available].to_csv(index=False)
                st.download_button(
                    "⬇️ Download Strategic Insights CSV",
                    data=insights_csv,
                    file_name="strategic_insights.csv",
                    mime="text/csv",
                    key=f"download_insights{product_suffix}"
                )
            
            else:
                st.info("ℹ️ No significant insights found with current thresholds. Try adjusting minimum sample sizes or wait for more data.")
        else:
            st.info("ℹ️ Strategic Insights require stats file data. Upload a stats file to enable dimensional analysis.")
    
        # QMP Source Modifier Export
        st.markdown("---")
        st.markdown("### 📤 QMP Source Modifier Export")
    
        # Check if we have QMPID column
        qmpid_export_col = None
        for possible_col in ["QMP ID", "QMPID", "QMP", "QID"]:
            if possible_col in new_df.columns:
                qmpid_export_col = possible_col
                break
    
        if qmpid_export_col:
            # Filter to included rows only, exclude Unknown/Unmatched Stats
            export_df = new_df[
                (new_df["Include"] == True) & 
                (new_df[qmpid_export_col].astype(str) != "[Unknown]")
            ].copy()
        
            # Also filter out rows where Publisher Company is [Unmatched Stats]
            if any("Publisher" in col or "Company" in col for col in export_df.columns):
                for col in export_df.columns:
                    if "Publisher" in col or "Company" in col:
                        export_df = export_df[export_df[col].astype(str) != "[Unmatched Stats]"].copy()
                        break
        
            if not export_df.empty:
                from datetime import datetime
                date_suffix = datetime.now().strftime("%Y%m%d")
            
                # Check if analyzing all products together (combined mode)
                if product_col and st.session_state.get("product_filter_mode") == "Analyze all products together":
                    # AUTO-SPLIT MODE: Create separate exports for each product based on QMP ID matching
                    st.info("🚀 **Auto-Split Export Mode**\n\n"
                           "You're analyzing all products together. The app will automatically create separate export files "
                           "for each product (Auto/Home) by matching QMP IDs to the original click data.")
                
                    # Get original product mappings from click data
                    product_qmpid_map = {}
                    if qmpid_col in click_work.columns and product_col in click_work.columns:
                        for _, row in click_work[[qmpid_col, product_col]].drop_duplicates().iterrows():
                            qmpid = str(row[qmpid_col]).strip()
                            product = str(row[product_col]).strip()
                            if qmpid and product:
                                product_qmpid_map[qmpid] = product
                
                    # Split exports by product
                    products_found = {}
                    for _, row in export_df.iterrows():
                        qmpid_str = str(row[qmpid_export_col]).strip()
                        product = product_qmpid_map.get(qmpid_str, "Unknown")
                        if product not in products_found:
                            products_found[product] = []
                        products_found[product].append(row)
                
                    # Create export for each product
                    for product, rows in products_found.items():
                        if product == "Unknown":
                            continue
                        
                        product_df = pd.DataFrame(rows)
                    
                        # Create QMP import format with device-aware recommendations
                        # Strategy: Update only device columns that have recommendations; preserve others from original file
                    
                        # Get original modifiers from input file
                        modifier_matches = st.session_state.get("modifier_product_matches", {})
                        original_modifiers = {}  # {qmp_id: {'Mobile': X, 'Desktop': Y, 'Tablet': Z}}
                    
                        if modifier_matches:
                            for product_name, modifier_df in modifier_matches.items():
                                if 'QMP ID' in modifier_df.columns:
                                    for _, row in modifier_df.iterrows():
                                        try:
                                            qmp_id = int(row['QMP ID'])
                                            original_modifiers[qmp_id] = {
                                                'Mobile': int(row['Mobile modifier']) if not pd.isna(row.get('Mobile modifier')) else 100,
                                                'Desktop': int(row['Desktop modifier']) if not pd.isna(row.get('Desktop modifier')) else 100,
                                                'Tablet': int(row['Tablet modifier']) if not pd.isna(row.get('Tablet modifier')) else 100
                                            }
                                        except:
                                            pass
                    
                        # Check if Device Type is in the export data
                        device_col_in_export = None
                        for col in product_df.columns:
                            if normalize(col) in {"device", "devicetype", "platform"}:
                                device_col_in_export = col
                                break
                    
                        # Build recommendations by QMP ID and device
                        qmp_recommendations = {}  # {qmp_id: {'Mobile': X, 'Desktop': Y, 'Tablet': Z}}
                    
                        for _, row in product_df.iterrows():
                            qmp_id = str(row[qmpid_export_col]).strip()  # QMPID can be hash string or number
                            recommended = int(row["Recommended Bid %"])
                        
                            if qmp_id not in qmp_recommendations:
                                # Start with original values (or 100 as default)
                                qmp_recommendations[qmp_id] = original_modifiers.get(qmp_id, {'Mobile': 100, 'Desktop': 100, 'Tablet': 100}).copy()
                        
                            # Update the specific device column if device type is present
                            if device_col_in_export and device_col_in_export in row.index:
                                device = str(row[device_col_in_export]).strip()
                                if 'mobile' in device.lower() or 'phone' in device.lower():
                                    qmp_recommendations[qmp_id]['Mobile'] = recommended
                                elif 'desktop' in device.lower() or 'computer' in device.lower():
                                    qmp_recommendations[qmp_id]['Desktop'] = recommended
                                elif 'tablet' in device.lower():
                                    qmp_recommendations[qmp_id]['Tablet'] = recommended
                            else:
                                # No device column - update Mobile only (default behavior)
                                qmp_recommendations[qmp_id]['Mobile'] = recommended
                    
                        # Create final export DataFrame
                        qmp_export_rows = []
                        for qmp_id, mods in qmp_recommendations.items():
                            qmp_export_rows.append({
                                "QMP ID": qmp_id,
                                "Mobile modifier": mods['Mobile'],
                                "Desktop modifier": mods['Desktop'],
                                "Tablet modifier": mods['Tablet']
                            })
                    
                        qmp_export = pd.DataFrame(qmp_export_rows)
                    
                        # Determine product label for filename
                        product_label = ""
                        product_display = product
                        if "Auto" in product:
                            product_label = "_AUTO"
                            product_display = "Auto Insurance"
                        elif "Home" in product:
                            product_label = "_HOME"
                            product_display = "Home Insurance"
                    
                        st.markdown(f"#### {product_display}")
                        st.markdown(f"**{len(qmp_export)} sources**")
                    
                        # Add Publisher Company for display (not export)
                        # Map QMP IDs back to Publisher Company from grouped results
                        publisher_map = {}
                        publisher_col_name = None
                        for col in grouped.columns:
                            if "Publisher" in col or "Company" in col:
                                publisher_col_name = col
                                break
                    
                        if publisher_col_name and qmpid_col in grouped.columns:
                            for _, row in grouped[[qmpid_col, publisher_col_name]].iterrows():
                                qmpid_str = str(row[qmpid_col]).strip()
                                publisher_map[qmpid_str] = row[publisher_col_name]
                    
                        # Create display table with Publisher Company
                        display_df = qmp_export.copy()
                        if publisher_map:
                            display_df.insert(1, "Publisher Company", display_df["QMP ID"].astype(str).map(publisher_map).fillna("Unknown"))
                    
                        # Show metrics first (compact)
                        metric_col1, metric_col2, metric_col3 = st.columns(3)
                        with metric_col1:
                            st.metric("Sources", f"{len(qmp_export)}")
                        with metric_col2:
                            st.metric("Avg Modifier", f"{qmp_export['Mobile modifier'].mean():.0f}%")
                        with metric_col3:
                            st.metric("Range", f"{qmp_export['Mobile modifier'].min():.0f}% - {qmp_export['Mobile modifier'].max():.0f}%")
                    
                        # Make table editable with AgGrid - FULL WIDTH
                        gb = GridOptionsBuilder.from_dataframe(display_df)
                        gb.configure_default_column(editable=False, resizable=True, wrapText=False, autoHeight=False)
                        gb.configure_column("Mobile modifier", editable=True, type=["numericColumn"], width=120)
                        gb.configure_column("Desktop modifier", editable=True, type=["numericColumn"], width=130)
                        gb.configure_column("Tablet modifier", editable=True, type=["numericColumn"], width=120)
                        # Continuous scrolling - no pagination
                        gb.configure_grid_options(domLayout='normal')
                        gridOptions = gb.build()
                    
                        grid_response = AgGrid(
                            display_df,
                            gridOptions=gridOptions,
                            update_mode=GridUpdateMode.VALUE_CHANGED,
                            allow_unsafe_jscode=True,
                            fit_columns_on_grid_load=True,
                            columns_auto_size_mode=ColumnsAutoSizeMode.FIT_CONTENTS,
                            height=min(450, len(display_df) * 35 + 100),
                            theme='streamlit'
                        )
                    
                        # Get edited data
                        edited_df = pd.DataFrame(grid_response['data'])
                    
                        # Update qmp_export with edited values (remove Publisher Company before export)
                        export_cols = ["QMP ID", "Mobile modifier", "Desktop modifier", "Tablet modifier"]
                        qmp_export = edited_df[export_cols].copy()
                    
                        if len(display_df) > 10:
                            st.caption(f"Showing all {len(display_df)} rows (paginated)")
    
                    
                        # Excel download
                        import io
                        buffer = io.BytesIO()
                        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                            qmp_export.to_excel(writer, sheet_name='source-modifiers', index=False)
                        buffer.seek(0)
                    
                        filename = f"QMP_Modifiers{product_label}_{date_suffix}.xlsx"
                    
                        st.download_button(
                            f"📥 Download {product_display} QMP Modifiers",
                            data=buffer,
                            file_name=filename,
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            help=f"Upload to QMP {product_display.replace(' Insurance', '')} campaign",
                            key=f"download_qmp_split_{product_display.replace(' ', '_').lower()}_file{file_idx}"
                        )
                        st.markdown("---")
                
                    st.success(f"✅ Created {len(products_found) - (1 if 'Unknown' in products_found else 0)} separate QMP export files")
            
                else:
                    # SINGLE PRODUCT MODE: Original behavior
                    # Determine product label for filename only (don't shadow product_suffix!)
                    product_file_label = ""
                    product_display_name = ""
                    if product_col and st.session_state.get("product_filter_mode") == "Analyze specific product (Recommended)":
                        analyzed_product = st.session_state.get("selected_product", "")
                        if "Auto" in analyzed_product:
                            product_file_label = "_AUTO"
                            product_display_name = "Auto Insurance"
                        elif "Home" in analyzed_product:
                            product_file_label = "_HOME"
                            product_display_name = "Home Insurance"
                
                    # Create QMP import format with device-aware recommendations
                    # Strategy: Update only device columns that have recommendations; preserve others from original file
                
                    # Get original modifiers from input file
                    modifier_matches = st.session_state.get("modifier_product_matches", {})
                    original_modifiers = {}  # {qmp_id: {'Mobile': X, 'Desktop': Y, 'Tablet': Z}}
                
                    if modifier_matches:
                        for product_name, modifier_df in modifier_matches.items():
                            if 'QMP ID' in modifier_df.columns:
                                for _, row in modifier_df.iterrows():
                                    try:
                                        qmp_id = int(row['QMP ID'])
                                        original_modifiers[qmp_id] = {
                                            'Mobile': int(row['Mobile modifier']) if not pd.isna(row.get('Mobile modifier')) else 100,
                                            'Desktop': int(row['Desktop modifier']) if not pd.isna(row.get('Desktop modifier')) else 100,
                                            'Tablet': int(row['Tablet modifier']) if not pd.isna(row.get('Tablet modifier')) else 100
                                        }
                                    except:
                                        pass
                
                    # Check if Device Type is in the export data
                    device_col_in_export = None
                    for col in export_df.columns:
                        if normalize(col) in {"device", "devicetype", "platform"}:
                            device_col_in_export = col
                            break
                
                    # Build recommendations by QMP ID and device
                    qmp_recommendations = {}  # {qmp_id: {'Mobile': X, 'Desktop': Y, 'Tablet': Z}}
                
                    for _, row in export_df.iterrows():
                        qmp_id = str(row[qmpid_export_col]).strip()  # QMPID can be hash string or number
                        recommended = int(row["Recommended Bid %"])
                    
                        if qmp_id not in qmp_recommendations:
                            # Start with original values (or 100 as default)
                            qmp_recommendations[qmp_id] = original_modifiers.get(qmp_id, {'Mobile': 100, 'Desktop': 100, 'Tablet': 100}).copy()
                    
                        # Update the specific device column if device type is present
                        if device_col_in_export and device_col_in_export in row.index:
                            device = str(row[device_col_in_export]).strip()
                            if 'mobile' in device.lower() or 'phone' in device.lower():
                                qmp_recommendations[qmp_id]['Mobile'] = recommended
                            elif 'desktop' in device.lower() or 'computer' in device.lower():
                                qmp_recommendations[qmp_id]['Desktop'] = recommended
                            elif 'tablet' in device.lower():
                                qmp_recommendations[qmp_id]['Tablet'] = recommended
                        else:
                            # No device column - update Mobile only (default behavior)
                            qmp_recommendations[qmp_id]['Mobile'] = recommended
                
                    # Create final export DataFrame
                    qmp_export_rows = []
                    for qmp_id, mods in qmp_recommendations.items():
                        qmp_export_rows.append({
                            "QMP ID": qmp_id,
                            "Mobile modifier": mods['Mobile'],
                            "Desktop modifier": mods['Desktop'],
                            "Tablet modifier": mods['Tablet']
                        })
                
                    qmp_export = pd.DataFrame(qmp_export_rows)
                
                    # Show preview with product context
                    header_text = f"**{len(qmp_export)} sources ready for QMP import**"
                    if product_display_name:
                        header_text += f" ({product_display_name})"
                    header_text += " (only 'Include' checked rows)"
                    st.markdown(header_text)
                
                    if product_display_name:
                        st.info(f"💡 **Important:** These {len(qmp_export)} sources are for **{product_display_name}** only.\n\n"
                               f"**QMP Workflow:** Export source modifiers from your {product_display_name.replace(' Insurance', '')} campaign → "
                               f"Analyze here → Import the updated file back to the same {product_display_name.replace(' Insurance', '')} campaign.\n\n"
                               f"**Note:** QMP exports from different campaigns may have the same filename (`SourceModifier.xlsx`), "
                               f"so this app adds the product name and date to prevent overwriting files.")
                
                    # Add Publisher Company for display (not export)
                    publisher_map = {}
                    publisher_col_name = None
                    for col in grouped.columns:
                        if "Publisher" in col or "Company" in col:
                            publisher_col_name = col
                            break
                
                    if publisher_col_name and qmpid_col in grouped.columns:
                        for _, row in grouped[[qmpid_col, publisher_col_name]].iterrows():
                            qmpid_str = str(row[qmpid_col]).strip()
                            publisher_map[qmpid_str] = row[publisher_col_name]
                
                    # Create display table with Publisher Company
                    display_df = qmp_export.copy()
                    if publisher_map:
                        display_df.insert(1, "Publisher Company", display_df["QMP ID"].astype(str).map(publisher_map).fillna("Unknown"))
                
                    # Show metrics first (compact)
                    metric_col1, metric_col2, metric_col3 = st.columns(3)
                    with metric_col1:
                        st.metric("Sources", f"{len(qmp_export)}")
                    with metric_col2:
                        st.metric("Avg Modifier", f"{qmp_export['Mobile modifier'].mean():.0f}%")
                    with metric_col3:
                        st.metric("Range", f"{qmp_export['Mobile modifier'].min():.0f}% - {qmp_export['Mobile modifier'].max():.0f}%")
                
                    # Make table editable with AgGrid - FULL WIDTH
                    gb = GridOptionsBuilder.from_dataframe(display_df)
                    gb.configure_default_column(editable=False, resizable=True, wrapText=False, autoHeight=False)
                    gb.configure_column("Mobile modifier", editable=True, type=["numericColumn"], width=120)
                    gb.configure_column("Desktop modifier", editable=True, type=["numericColumn"], width=130)
                    gb.configure_column("Tablet modifier", editable=True, type=["numericColumn"], width=120)
                    # Continuous scrolling - no pagination
                    gb.configure_grid_options(domLayout='normal')
                    gridOptions = gb.build()
                
                    grid_response = AgGrid(
                        display_df,
                        gridOptions=gridOptions,
                        update_mode=GridUpdateMode.VALUE_CHANGED,
                        allow_unsafe_jscode=True,
                        fit_columns_on_grid_load=True,
                        columns_auto_size_mode=ColumnsAutoSizeMode.FIT_CONTENTS,
                        height=min(450, len(display_df) * 35 + 100),
                        theme='streamlit'
                    )
                
                    # Get edited data
                    edited_df = pd.DataFrame(grid_response['data'])
                
                    # Update qmp_export with edited values (remove Publisher Company before export)
                    export_cols = ["QMP ID", "Mobile modifier", "Desktop modifier", "Tablet modifier"]
                    qmp_export = edited_df[export_cols].copy()
                
                    if len(display_df) > 10:
                        st.caption(f"Showing all {len(display_df)} rows (paginated)")
    
                
                    # Excel download (QMP import format)
                    import io
                    buffer = io.BytesIO()
                    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                        qmp_export.to_excel(writer, sheet_name='source-modifiers', index=False)
                    buffer.seek(0)
                
                    # Build filename with agent name if available
                    agent_suffix = ""
                    if st.session_state.get(f"selected_agent_name{product_suffix}") and st.session_state.get(f"selected_agent_name{product_suffix}") not in ["All Agents", "Unknown"]:
                        # Clean agent name for filename (remove special characters)
                        agent_name_clean = st.session_state.get(f"selected_agent_name{product_suffix}").replace(" - State Farm Agent", "").replace(" ", "_")
                        agent_suffix = f"_{agent_name_clean}"
                
                    # Filename: QMP_Modifiers_JohnDoe_AUTO_20260318.xlsx
                    filename = f"QMP_Modifiers{agent_suffix}{product_file_label}_{date_suffix}.xlsx"
                
                    st.download_button(
                        f"📥 Download {product_display_name + ' ' if product_display_name else ''}QMP Modifiers",
                        data=buffer,
                        file_name=filename,
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        help=f"Upload this file to QMP to update source modifiers{' for ' + product_display_name if product_display_name else ''}",
                        key=f"download_qmp_main{product_suffix}"
                    )
                    # Show agent info in caption if single agent
                    agent_caption = ""
                    if agent_suffix:
                        agent_caption = f" - Agent: {st.session_state.get(f'selected_agent_name{product_suffix}')}"
                    st.caption(f"💡 File: `{filename}`{agent_caption} - All device types (Mobile/Desktop/Tablet) will receive the same modifier.")
            else:
                st.warning("No sources selected for export. Check 'Include' boxes in the table above.")
        else:
            st.info("QMP export requires a 'QMP ID' column in your data. Upload the QMP Source Modifier export file to enable this feature.")
    
    # -----------------------------
    # 7) 🕐 Sub-Segmentation Analysis
    # -----------------------------
    if st.session_state.get("enable_subseg", False) and st.session_state.get("subseg_dimensions", []):
        with st.expander("7) 🕐 Sub-Segmentation Analysis", expanded=False):
            st.info("💡 Performance breakdown by time and geography. Green=better than target, Yellow=near target, Red=worse than target, Grey=insufficient data")
        
            subseg_dimensions = st.session_state.subseg_dimensions
            subseg_min_clicks_val = st.session_state.get("subseg_min_clicks", 5)
            subseg_geo_columns = st.session_state.get("subseg_geo_columns", {})
        
            # Map dimension names to column names
            dim_col_map = {
                "Day of Week": "subseg_dow_label",
                "Daypart": "subseg_daypart",
                "Hour of Day": "subseg_hour",
            }
        
            # Add geography columns dynamically
            for geo_type in subseg_geo_columns.keys():
                dim_col_map[f"Geography: {geo_type}"] = f"subseg_geo_{geo_type.lower().replace(' ', '_')}"
        
            # Prepare work data with subseg columns
            work_subseg = pd.DataFrame({g: click_work[g].astype(str).str.strip() for g in group_cols})
            if qmpid_col and qmpid_col in click_work.columns:
                work_subseg["QMPID"] = click_work[qmpid_col].astype(str).str.strip()
        
            # Add subseg dimension columns
            for dim in subseg_dimensions:
                col = dim_col_map.get(dim)
                if col and col in click_work.columns:
                    work_subseg[f"subseg_{dim}"] = click_work[col]
        
            # Add metrics
            if st.session_state.get(f"use_zero_A{product_suffix}", True):
                work_subseg["A"] = 0.0
            else:
                work_subseg["A"] = sum((to_num(click_work[c]) for c in st.session_state.get(f"a_cols{product_suffix}", [])))
        
            # Add stats
            if not stats_files or clicks_joined.empty:
                work_subseg["B"] = 0.0
            else:
                cd = click_work.copy()
                cd["__k"] = cd[click_key_col].astype(str).str.strip().str.lower()
                sr = clicks_joined.copy()
                sr["__k"] = sr["click_key"].astype(str).str.strip().str.lower()
                merged = cd[["__k"]].merge(sr[["__k","Quote Starts"]], on="__k", how="left")
                work_subseg["B"] = merged["Quote Starts"].fillna(0.0).values
        
            if "Clicks" in click_work.columns:
                work_subseg["Clicks"] = to_num(click_work["Clicks"])
            else:
                work_subseg["Clicks"] = 1  # Count each row as a click
        
            # For each dimension, compute sub-aggregation
            subseg_results = {}
        
            for dim in subseg_dimensions:
                dim_col_name = f"subseg_{dim}"
                if dim_col_name not in work_subseg.columns:
                    continue
            
                group_keys_sub = list(dict.fromkeys(group_cols + (["QMPID"] if "QMPID" in work_subseg.columns else []) + [dim_col_name]))
            
                agg_sub = work_subseg.groupby(group_keys_sub, as_index=False, dropna=False).agg(
                    A=("A","sum"),
                    B=("B","sum"),
                    Clicks=("Clicks","sum")
                )
            
                # Compute CPQS
                agg_sub["CPQS_raw"] = np.where(agg_sub["B"]>0, agg_sub["A"]/agg_sub["B"], np.nan)
                agg_sub["CPQS"] = agg_sub["CPQS_raw"]
            
                # Apply performance zones for color-coding
                # Use same thresholds as continuous bid scaling: 80% / 120% of target
                target_80 = target_cpqs * 0.8
                target_120 = target_cpqs * 1.2
            
                def bucket_subseg(cpqs, clicks):
                    if pd.isna(cpqs) or clicks < subseg_min_clicks_val:
                        return "Insufficient"
                    if cpqs < target_80:
                        return "Better than target"
                    elif cpqs > target_120:
                        return "Worse than target"
                    else:
                        return "Near target"
            
                agg_sub["Performance"] = agg_sub.apply(lambda r: bucket_subseg(r["CPQS"], r["Clicks"]), axis=1)
            
                subseg_results[dim] = agg_sub
        
            # Display results for each dimension
            for dim, df_sub in subseg_results.items():
                st.markdown(f"### {dim}")
            
                if df_sub.empty:
                    st.caption("No data for this dimension")
                    continue
            
                # Create source key
                dim_col_name = f"subseg_{dim}"
                source_cols = [c for c in group_cols if c in df_sub.columns]
                if "QMPID" in df_sub.columns:
                    source_cols.append("QMPID")
            
                if not source_cols:
                    st.warning("Cannot create source keys - no grouping columns found")
                    continue
            
                df_sub["source_key"] = df_sub[source_cols].astype(str).agg(" × ".join, axis=1)
                df_sub["source_key"] = df_sub["source_key"].str[:40]  # Truncate for readability
            
                # Create pivot table
                pivot_data = []
                for _, row in df_sub.iterrows():
                    pivot_data.append({
                        "source": row["source_key"],
                        "dimension_value": str(row[dim_col_name]),
                        "cpqs": row["CPQS"],
                        "clicks": row["Clicks"],
                        "performance": row["Performance"]
                    })
            
                if not pivot_data:
                    st.caption("No data to display")
                    continue
            
                pivot_df = pd.DataFrame(pivot_data)
                pivot_table = pivot_df.pivot_table(
                    index="source",
                    columns="dimension_value",
                    values="cpqs",
                    aggfunc="first"
                )
            
                # Create color mapping
                def color_cell(val):
                    if pd.isna(val):
                        return 'background-color: lightgrey'
                
                    # Find corresponding performance
                    matching = pivot_df[(pivot_df["cpqs"] == val)]
                    if matching.empty:
                        return ''
                
                    perf = matching.iloc[0]["performance"]
                    clicks = matching.iloc[0]["clicks"]
                
                    if perf == "Insufficient" or clicks < subseg_min_clicks_val:
                        return 'background-color: lightgrey; color: #999'
                    elif perf == "Better than target":
                        return 'background-color: #90EE90; color: black'
                    elif perf == "Near target":
                        return 'background-color: #FFD700; color: black'
                    elif perf == "Worse than target":
                        return 'background-color: #FFB6C1; color: black'
                    return ''
            
                # Apply styling and format
                styled = pivot_table.style.applymap(color_cell).format("${:.2f}", na_rep="–")
                st.write(styled.to_html(), unsafe_allow_html=True)
            
                # Find actionable insights
                st.markdown("**📊 Actionable Insights:**")
                insights = []
            
                for source in pivot_table.index:
                    row_data = pivot_df[pivot_df["source"] == source]
                    if len(row_data) < 2:
                        continue
                
                    perfs = row_data["performance"].unique()
                    if len(perfs) > 1 and "Insufficient" not in perfs:
                        cpqs_values = row_data[row_data["performance"] != "Insufficient"]["cpqs"].dropna()
                        if len(cpqs_values) > 1:
                            spread = cpqs_values.max() - cpqs_values.min()
                        
                            better_vals = row_data[row_data["performance"] == "Better than target"]
                            worse_vals = row_data[row_data["performance"] == "Worse than target"]
                        
                            if not better_vals.empty and not worse_vals.empty:
                                better_dims = ", ".join(better_vals["dimension_value"].astype(str).tolist())
                                worse_dims = ", ".join(worse_vals["dimension_value"].astype(str).tolist())
                                better_cpqs = better_vals["cpqs"].mean()
                                worse_cpqs = worse_vals["cpqs"].mean()
                            
                                insight = {
                                    "source": source,
                                    "spread": spread,
                                    "message": f"**{source}** performs worse than target on {worse_dims} (CPQS ${worse_cpqs:.2f}) but better than target on {better_dims} (CPQS ${better_cpqs:.2f}). Consider scheduling adjustments."
                                }
                                insights.append(insight)
            
                if insights:
                    # Sort by spread descending
                    insights.sort(key=lambda x: x["spread"], reverse=True)
                    for ins in insights[:5]:  # Show top 5
                        st.warning(f"⚠️ {ins['message']}")
                else:
                    st.caption("✓ No significant performance variations detected across this dimension")
            
                st.markdown("---")
    
    # End of this product's analysis
    # Loop continues to next product (if multiple products)

# ============================================================================
# END OF FILE LOOP - All products have been processed
# ============================================================================
# Visuals section shows combined data across all products
# TODO: Refactor visuals to collect data from all products and show combined charts

# TEMPORARILY DISABLED - Visuals need refactoring for multi-product support
# The current implementation references variables (grouped, group_keys) that are 
# defined inside the product loop and are not accessible here
if False:  # Disabled
    # -----------------------------
    # 11) Visuals
    # -----------------------------
    with st.expander("9) 📈 Visuals", expanded=False):
        pass  # Disabled - needs refactoring for multi-product support
        
# Rest of disabled code (keeping for reference)
if False:
    info_badge("Quick sanity charts. Hover for tooltips.")
    # Use grouped from the last product iteration (or single product if not multi-product mode)
    styled = grouped.copy()
    label_cols = [c for c in group_keys if c not in {"A","B","C","D"}]
    def make_label(row):
        return " • ".join(str(row[g]) for g in label_cols if g in row.index)
    styled["label"] = styled.apply(make_label, axis=1)

    t1,t2,t3,t4 = st.tabs([f"{eff_label} by Group","Quote Starts","Spend by Group","Recommended Bid %"])
    with t1:
        dt = styled.dropna(subset=[eff_label])
        if len(dt)==0: st.info("No rows with a valid efficiency value.")
        else:
            ch = alt.Chart(dt).mark_bar(color="#47B74F").encode(  # Cactus green)
                x=alt.X(f"{eff_label}:Q", title=f"{eff_label} (lower is better)"),
                y=alt.Y("label:N", sort="-x"),
                tooltip=[alt.Tooltip(eff_label, format=".2f"), "A","B"]
            ).properties(height=400, width="container")
            st.altair_chart(ch, use_container_width=True)
    with t2:
        ch = alt.Chart(styled).mark_bar(color="#CFBA97").encode(  # Mojave)
            x=alt.X("B:Q", title="Quote Starts (from stats)"),
            y=alt.Y("label:N", sort="-x"),
            tooltip=["A","B", alt.Tooltip(eff_label, format=".2f")]
        ).properties(height=400, width="container")
        st.altair_chart(ch, use_container_width=True)
    with t3:
        ch = alt.Chart(styled).mark_bar(color="#6C2126").encode(  # Cranberry)
            x=alt.X("A:Q", title=a_label),
            y=alt.Y("label:N", sort="-x"),
            tooltip=["A","B", alt.Tooltip(eff_label, format=".2f")]
        ).properties(height=400, width="container")
        st.altair_chart(ch, use_container_width=True)
    with t4:
        ch = alt.Chart(styled).mark_bar(color="#47B74F").encode(  # Cactus green)
            x=alt.X("Recommended Bid %:Q", title="Recommended Bid %"),
            y=alt.Y("label:N", sort="-x"),
            tooltip=["Recommended Bid %","B", alt.Tooltip(eff_label, format=".2f")]
        ).properties(height=400, width="container")
        st.altair_chart(ch, use_container_width=True)