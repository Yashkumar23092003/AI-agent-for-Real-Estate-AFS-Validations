"""
sheets.py — Google Sheets integration for AFS <-> Sheet verification.
Reads audited sheets and appends verification results to a log sheet,
using a service account. No LLM, no comparison logic.
"""
import os
import json
import datetime
import gspread
from google.oauth2 import service_account
from comparator import normalize_header

# Read-write on spreadsheets (needed to append log rows); read-only on Drive
# (needed only to open sheets by key).
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]


def _get_credentials() -> service_account.Credentials:
    """
    Loads service-account credentials from, in priority order:
      1. GOOGLE_SHEETS_CREDENTIALS_JSON — the raw JSON key as a string
         (use this on Streamlit Cloud, where there is no file on disk).
      2. GOOGLE_SHEETS_CREDENTIALS_PATH — path to the JSON key file (local dev).
    """
    creds_json = os.environ.get("GOOGLE_SHEETS_CREDENTIALS_JSON")
    if creds_json:
        info = json.loads(creds_json)
        return service_account.Credentials.from_service_account_info(
            info, scopes=SCOPES
        )

    creds_path = os.environ.get("GOOGLE_SHEETS_CREDENTIALS_PATH")
    if not creds_path:
        raise ValueError(
            "No Google credentials found. Set GOOGLE_SHEETS_CREDENTIALS_JSON "
            "(raw JSON, for Streamlit Cloud) or GOOGLE_SHEETS_CREDENTIALS_PATH "
            "(file path, for local dev)."
        )
    if not os.path.isfile(creds_path):
        raise FileNotFoundError(
            f"Service account file not found: {creds_path}"
        )
    return service_account.Credentials.from_service_account_file(
        creds_path, scopes=SCOPES
    )


def get_worksheet(sheet_id: str, tab: str) -> gspread.Worksheet:
    """Opens a worksheet by Google Sheet ID and tab name."""
    gc = gspread.authorize(_get_credentials())
    spreadsheet = gc.open_by_key(sheet_id)
    return spreadsheet.worksheet(tab)


def find_unit_row(ws: gspread.Worksheet, unit_no: str) -> dict:
    """
    Searches the worksheet for the row matching unit_no in the first 'Unit No.' column.

    Returns a dict of {normalized_header: raw_cell_value}.
    For duplicate headers (e.g. 'Unit No.' appears twice), the value is a list of
    raw values in column-order.

    Raises ValueError if:
      - No 'Unit No.' column exists
      - The unit is not found
      - The unit appears in more than one row
    """
    all_values = ws.get_all_values()
    if len(all_values) < 2:
        raise ValueError(
            "Sheet has fewer than 2 rows — expected at least a header row and one data row."
        )

    raw_headers = all_values[0]
    norm_headers = [normalize_header(h) for h in raw_headers]

    # Find indices of all 'unit no.' columns
    unit_no_indices = [i for i, h in enumerate(norm_headers) if h == "unit no."]
    if not unit_no_indices:
        raise ValueError(
            "No 'Unit No.' column found in sheet. "
            "Check that the header row contains 'Unit No.' exactly."
        )

    search_idx = unit_no_indices[0]
    unit_no_str = str(unit_no).strip()

    matching = []
    for row_num, row in enumerate(all_values[1:], start=2):
        cell = row[search_idx].strip() if search_idx < len(row) else ""
        if cell == unit_no_str:
            matching.append((row_num, row))

    if not matching:
        raise ValueError(
            f"Unit No. '{unit_no_str}' not found in sheet. "
            "Ensure the unit number matches exactly (case-sensitive, no leading zeros)."
        )
    if len(matching) > 1:
        row_nums = [r[0] for r in matching]
        raise ValueError(
            f"Unit No. '{unit_no_str}' found in multiple rows: {row_nums}. "
            "Sheet must contain unique unit numbers."
        )

    _, row = matching[0]

    # Build result dict; duplicate headers become lists
    result = {}
    for i, norm_h in enumerate(norm_headers):
        if not norm_h:       # skip blank-header columns
            continue
        raw_val = row[i] if i < len(row) else ""
        if norm_h in result:
            existing = result[norm_h]
            if isinstance(existing, list):
                existing.append(raw_val)
            else:
                result[norm_h] = [existing, raw_val]
        else:
            result[norm_h] = raw_val

    return result


# ── Log sheet (append-only audit trail) ───────────────────────────────────────

# Canonical AFS field order — must match _OPTIONAL_FIELD_SPECS + the 4 core
# fields in comparator.py, so AFS_Log columns line up run to run.
AFS_FIELD_ORDER = [
    "Unit Number", "Agreement Value", "Area (Sq.M)", "Area (Sq.Ft)",
    "Floor", "Applicant Name", "Applicant PAN", "Applicant Email",
    "Parking No.", "Parking Level (Basement)", "Parking Configuration",
    "Parking Length (M)", "Parking Width (M)", "Parking Height (M)",
    "Parking Total Area (M)", "Share Certificate No.", "Share Alloted From",
    "Share Alloted", "Total No. of Shares", "Legal Charges",
]

KYC_LOG_HEADERS = [
    "Timestamp", "Buyer Name", "Project Name", "Unit Number", "Status", "Report",
]

AFS_LOG_HEADERS = [
    "Timestamp", "Unit No", "Buyer Name", "Project Name",
    "Sheet ID", "Tab", "AFS Filename", "Verdict",
] + [
    f"{name} {suffix}" for name in AFS_FIELD_ORDER for suffix in ("Status", "AFS", "Sheet")
]

KYC_LOG_TAB = "KYC_Log"
AFS_LOG_TAB = "AFS_Log"


def _log_sheet_id() -> str:
    sheet_id = os.environ.get("LOG_SHEET_ID")
    if not sheet_id:
        raise ValueError(
            "LOG_SHEET_ID is not set. Create the log sheet, share it with the "
            "service account as Editor, and put its ID in the environment."
        )
    return sheet_id


def _get_or_create_tab(spreadsheet, tab: str, headers: list) -> gspread.Worksheet:
    """Returns the tab, creating it with a header row if it doesn't exist."""
    try:
        return spreadsheet.worksheet(tab)
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=tab, rows=1000, cols=len(headers))
        ws.append_row(headers, value_input_option="USER_ENTERED")
        return ws


def _now() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def append_kyc_log(buyer_name, project_name, unit_number, status, report_text):
    """Appends one row to the KYC_Log tab of the log sheet."""
    gc = gspread.authorize(_get_credentials())
    ss = gc.open_by_key(_log_sheet_id())
    ws = _get_or_create_tab(ss, KYC_LOG_TAB, KYC_LOG_HEADERS)
    row = [_now(), buyer_name, project_name, unit_number, status,
           (report_text or "")[:45000]]  # cell hard limit is 50k chars
    ws.append_row(row, value_input_option="USER_ENTERED")


def append_afs_log(unit_no, buyer_name, project_name, sheet_id, tab_name,
                   verdict, fields, afs_filename):
    """
    Appends one row to the AFS_Log tab. `fields` is the list of FieldResult
    objects; each field gets a Status/AFS/Sheet column triplet.
    """
    gc = gspread.authorize(_get_credentials())
    ss = gc.open_by_key(_log_sheet_id())
    ws = _get_or_create_tab(ss, AFS_LOG_TAB, AFS_LOG_HEADERS)

    result_by_field = {f.field_name: f for f in fields}
    field_cells = []
    for name in AFS_FIELD_ORDER:
        f = result_by_field.get(name)
        if f is None:
            field_cells += ["", "", ""]
        else:
            field_cells += [f.status, f.afs_normalized or "", f.sheet_normalized or ""]
    row = [_now(), unit_no, buyer_name, project_name, sheet_id, tab_name,
           afs_filename, verdict] + field_cells
    ws.append_row(row, value_input_option="USER_ENTERED")


# ── ID document update (write extracted Aadhaar/PAN/Passport into a Unit row) ──
# Additive only — does not touch any function above. Maps onto the *actual*
# column headers found in the live "Inventory Sheet" / "4QT Details" tabs.
# Columns that don't exist yet (e.g. Passport, co-applicant PAN/Aadhaar) are
# auto-created at the end of the header row on first write.

PRIMARY_APPLICANT_FIELD_MAP = {
    "name": "Applicants Name",
    "pan": "Applicant's Pan No.",
    "aadhaar": "Applicant's Aadhar No.",
    "email": "Email Id",
    "address": "Address",
    "contact": "Contact No.",
    "passport": "Passport No.",
}

CO_APPLICANT_FIELD_MAP = {
    "name": "Co- Applicant's Name",
    "pan": "Co-Applicant's Pan No.",
    "aadhaar": "Co-Applicant's Aadhar No.",
    "email": "Co-Applicant's Email Id",
    "passport": "Co-Applicant's Passport No.",
}


def find_unit_row_index(ws: gspread.Worksheet, unit_no: str) -> tuple:
    """
    Like find_unit_row(), but returns (row_num, raw_headers) instead of a
    value dict — needed so callers can write back to the same row.

    Reuses the identical 'Unit No.' column-detection and uniqueness rules as
    find_unit_row() so write-path matching never diverges from the read-path.
    Raises ValueError on the same conditions find_unit_row() does.
    """
    all_values = ws.get_all_values()
    if len(all_values) < 2:
        raise ValueError(
            "Sheet has fewer than 2 rows — expected at least a header row and one data row."
        )

    raw_headers = all_values[0]
    norm_headers = [normalize_header(h) for h in raw_headers]

    unit_no_indices = [i for i, h in enumerate(norm_headers) if h == "unit no."]
    if not unit_no_indices:
        raise ValueError(
            "No 'Unit No.' column found in sheet. "
            "Check that the header row contains 'Unit No.' exactly."
        )

    search_idx = unit_no_indices[0]
    unit_no_str = str(unit_no).strip()

    matching_rows = []
    for row_num, row in enumerate(all_values[1:], start=2):
        cell = row[search_idx].strip() if search_idx < len(row) else ""
        if cell == unit_no_str:
            matching_rows.append(row_num)

    if not matching_rows:
        raise ValueError(
            f"Unit No. '{unit_no_str}' not found in sheet. "
            "Ensure the unit number matches exactly (case-sensitive, no leading zeros)."
        )
    if len(matching_rows) > 1:
        raise ValueError(
            f"Unit No. '{unit_no_str}' found in multiple rows: {matching_rows}. "
            "Sheet must contain unique unit numbers."
        )

    return matching_rows[0], raw_headers


def update_unit_row(sheet_id: str, tab: str, unit_no: str, field_values: dict) -> dict:
    """
    Writes extracted ID-document fields into the row matching `unit_no`.

    `field_values` is a flat {column_header: value} dict (use
    PRIMARY_APPLICANT_FIELD_MAP / CO_APPLICANT_FIELD_MAP to build it from
    extracted name/pan/aadhaar/... keys). Empty/None values are skipped —
    fields the caller didn't extract are left untouched in the sheet.

    If a column header doesn't exist yet, it is appended to the end of the
    header row (row 1) before writing the value. Existing columns and rows
    are never reordered or removed.

    Returns {"row": <row_num>, "updated_columns": [...]}.
    """
    ws = get_worksheet(sheet_id, tab)
    row_num, raw_headers = find_unit_row_index(ws, unit_no)

    updated_columns = []
    for header, value in field_values.items():
        if value is None or str(value).strip() == "":
            continue

        col_idx = None
        for i, h in enumerate(raw_headers):
            if h == header:
                col_idx = i
                break

        if col_idx is None:
            col_idx = len(raw_headers)
            raw_headers.append(header)
            if col_idx + 1 > ws.col_count:
                ws.add_cols(col_idx + 1 - ws.col_count)
            ws.update_cell(1, col_idx + 1, header)

        ws.update_cell(row_num, col_idx + 1, value)
        updated_columns.append(header)

    return {"row": row_num, "updated_columns": updated_columns}
