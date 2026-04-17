# file_operations.py
"""File upload, export, and preset management."""

import io
import json
from datetime import datetime
from typing import Dict, Optional, Tuple

import pandas as pd
import streamlit as st


def read_csv_file(file) -> pd.DataFrame:
    """Read CSV file with fallback encoding.
    
    Args:
        file: Uploaded file object
        
    Returns:
        DataFrame
    """
    try:
        return pd.read_csv(file)
    except Exception:
        file.seek(0)
        return pd.read_csv(file, encoding="utf-8", engine="python")


def read_excel_file(file, sheet_name=None) -> Dict[str, pd.DataFrame]:
    """Read Excel file with fallback.
    
    Args:
        file: Uploaded file object
        sheet_name: Specific sheet name or None for all sheets
        
    Returns:
        Dict of sheet_name -> DataFrame
    """
    try:
        return pd.read_excel(file, sheet_name=sheet_name)
    except Exception:
        file.seek(0)
        return pd.read_excel(file, sheet_name=sheet_name)


def save_preset(session_state) -> str:
    """Save session state as JSON preset.
    
    Args:
        session_state: Streamlit session state
        
    Returns:
        JSON string of preset
    """
    # Filter out private/system keys
    preset_data = {
        k: v for k, v in session_state.items()
        if not k.startswith("_")
    }
    return json.dumps(preset_data, indent=2)


def load_preset(file, session_state) -> bool:
    """Load preset from JSON file.
    
    Args:
        file: Uploaded JSON file
        session_state: Streamlit session state to update
        
    Returns:
        True if successful, False otherwise
    """
    try:
        data = json.load(file)
        for key, value in data.items():
            if isinstance(key, str) and not key.startswith("_"):
                session_state[key] = value
        return True
    except Exception as e:
        st.error(f"Failed to load preset: {e}")
        return False


def create_device_modifiers_excel(
    modifiers: Dict[Tuple[str, str], int],
    qmp_ids: list,
    filename_suffix: str = ""
) -> bytes:
    """Create Excel file for device modifiers upload.
    
    Args:
        modifiers: Dict of (qmp_id, device) -> bid percentage
        qmp_ids: List of QMP IDs to include
        filename_suffix: Optional suffix for filename
        
    Returns:
        Excel file bytes
    """
    try:
        import openpyxl
    except ImportError:
        raise ImportError("openpyxl is required. Install with: pip install openpyxl")
    
    # Create workbook
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "source-modifiers"
    
    # Headers
    ws.cell(row=1, column=1, value="QMP ID")
    ws.cell(row=1, column=2, value="Mobile modifier")
    ws.cell(row=1, column=3, value="Desktop modifier")
    ws.cell(row=1, column=4, value="Tablet modifier")
    
    # Build rows
    row_num = 2
    for qmp_id in qmp_ids:
        ws.cell(row=row_num, column=1, value=qmp_id)
        
        # Get device modifiers (None = blank = 100%)
        mobile = modifiers.get((qmp_id, "mobile"))
        desktop = modifiers.get((qmp_id, "desktop"))
        tablet = modifiers.get((qmp_id, "tablet"))
        
        ws.cell(row=row_num, column=2, value=None if mobile is None else int(mobile))
        ws.cell(row=row_num, column=3, value=None if desktop is None else int(desktop))
        ws.cell(row=row_num, column=4, value=None if tablet is None else int(tablet))
        
        row_num += 1
    
    # Save to bytes
    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output.getvalue()


def parse_existing_modifiers(
    file,
    required_columns: set = None
) -> Optional[Dict[Tuple[str, str], int]]:
    """Parse existing device modifiers from Excel file.
    
    Args:
        file: Uploaded Excel file
        required_columns: Set of required column names
        
    Returns:
        Dict of (canonical_qmp_id, device) -> modifier percentage, or None if invalid
    """
    if required_columns is None:
        required_columns = {
            "QMP ID",
            "Mobile modifier",
            "Desktop modifier",
            "Tablet modifier"
        }
    
    try:
        from data_utils import canonical_id
        
        # Read all sheets
        sheets = read_excel_file(file, sheet_name=None)
        
        # Find sheet with required columns
        target_df = None
        for sheet_df in sheets.values():
            if required_columns.issubset(set(sheet_df.columns)):
                target_df = sheet_df.copy()
                break
        
        if target_df is None:
            st.error(
                f"Excel file must contain sheet with columns: {', '.join(required_columns)}"
            )
            return None
        
        # Extract and parse
        df = target_df[list(required_columns)].copy()
        df["QMP_canonical"] = df["QMP ID"].map(canonical_id)
        
        # Coerce percentages (blank = 100)
        def coerce_percentage(col: pd.Series) -> pd.Series:
            numeric = pd.to_numeric(col, errors="coerce")
            numeric = numeric.fillna(100)
            return numeric.round().astype(int)
        
        df["Mobile modifier"] = coerce_percentage(df["Mobile modifier"])
        df["Desktop modifier"] = coerce_percentage(df["Desktop modifier"])
        df["Tablet modifier"] = coerce_percentage(df["Tablet modifier"])
        
        # Build mapping
        modifier_map = {}
        for _, row in df.iterrows():
            qmp_can = row["QMP_canonical"]
            if not qmp_can:
                continue
            
            modifier_map[(qmp_can, "mobile")] = int(row["Mobile modifier"])
            modifier_map[(qmp_can, "desktop")] = int(row["Desktop modifier"])
            modifier_map[(qmp_can, "tablet")] = int(row["Tablet modifier"])
        
        return modifier_map
        
    except Exception as e:
        st.error(f"Error parsing existing modifiers: {e}")
        return None
