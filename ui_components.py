# ui_components.py
"""Reusable UI components for MM Click Analyzer."""

import streamlit as st
from config import BRAND


def apply_custom_css():
    """Apply custom CSS styling to the Streamlit app."""
    st.markdown(f"""
    <style>
    :root {{
      --panel-bg: {BRAND['panel']};
      --app-bg: {BRAND['neutral']};
      --text: {BRAND['text']};
    }}
    [data-testid="stAppViewContainer"]{{background:var(--app-bg);}}
    section[data-testid="stSidebar"]{{background:var(--panel-bg);}}
    [data-testid="stDataFrame"]{{
        border-radius:12px;
        overflow:hidden;
        box-shadow:0 1px 3px rgba(0,0,0,.06);
    }}
    h1,h2,h3,h4{{color:var(--text);}}
    
    /* Sticky expander headers */
    [data-testid="stExpander"] > div > div:first-child {{
      position: sticky;
      top: 0;
      z-index: 2;
      background: var(--panel-bg);
      border-bottom: 1px solid rgba(0,0,0,.06);
    }}
    
    .badge-info {{
      display:inline-block;
      margin-left:.35rem;
      background:#EEF2FF;
      color:#3730A3;
      padding:.08rem .35rem;
      border-radius:.5rem;
      font-size:.80rem;
      cursor:help;
      border:1px solid #C7D2FE;
    }}
    
    /* Tighter file uploader button */
    [data-testid="stFileUploader"] button{{padding:0.45rem 0.75rem}}
    
    /* Dark mode support */
    @media (prefers-color-scheme: dark) {{
      :root {{
        --panel-bg: #0e1117;
        --app-bg: #0e1117;
        --text: #E5E7EB;
      }}
      [data-testid="stDataFrame"]{{box-shadow: none;}}
      [data-testid="stExpander"] > div > div:first-child {{
        background: var(--panel-bg);
        border-bottom: 1px solid rgba(255,255,255,.12);
      }}
    }}
    </style>
    """, unsafe_allow_html=True)


def info_badge(text: str, label: str = "ℹ️ Info"):
    """Display an info badge with tooltip.
    
    Args:
        text: Tooltip text
        label: Badge label
    """
    st.markdown(
        f'<span class="badge-info" title="{text}">{label}</span>',
        unsafe_allow_html=True
    )


def session_state_default(key: str, default_value):
    """Get or set default value in session state.
    
    Args:
        key: Session state key
        default_value: Default value if key doesn't exist
        
    Returns:
        Current value from session state
    """
    if key not in st.session_state:
        st.session_state[key] = default_value
    return st.session_state[key]


def expander_with_info(title: str, info_text: str, expanded: bool = False):
    """Create expander with info badge.
    
    Args:
        title: Expander title
        info_text: Info badge tooltip
        expanded: Whether expander is initially expanded
        
    Returns:
        Streamlit expander context manager
    """
    expander = st.expander(title, expanded=expanded)
    with expander:
        info_badge(info_text)
    return expander
