"""
comparator.py — Pure Python comparison logic for AFS <-> Google Sheet verification.
No I/O, no LLM, no network calls. All inputs are plain Python dicts/strings.
"""
import re
from decimal import Decimal, InvalidOperation
from dataclasses import dataclass, field as dc_field
from typing import Optional

# ── Status codes ──────────────────────────────────────────────────────────────
MATCH = "MATCH"
MISMATCH = "MISMATCH"
INTERNAL_DISCREPANCY = "INTERNAL_DISCREPANCY"
NOT_FOUND_IN_AFS = "NOT_FOUND_IN_AFS"
NOT_FOUND_IN_SHEET = "NOT_FOUND_IN_SHEET"
SCHEMA_CAVEAT = "SCHEMA_CAVEAT"
INFO_ONLY = "INFO_ONLY"  # extracted & displayed, but never affects the verdict

PASS_STATUSES = {MATCH, SCHEMA_CAVEAT, INFO_ONLY}

REQUIRED_CONTRACT_FIELDS = {"unit_number", "agreement_value", "area_sqm", "area_sqft"}


@dataclass
class FieldResult:
    field_name: str
    status: str
    afs_occurrences: list        # raw occurrence dicts from extraction contract
    afs_distinct_values: list    # distinct_values from extraction contract
    sheet_raw: str               # raw value(s) from sheet (may show both if duplicate col)
    afs_normalized: Optional[str]
    sheet_normalized: Optional[str]
    detail: str = ""


@dataclass
class ComparisonResult:
    verdict: str          # "PASS" or "FAIL"
    fields: list          # list of FieldResult
    warnings: list        # sanity-check warnings (do not affect verdict)
    schema_caveats: list  # SCHEMA_CAVEAT detail strings


# ── Normalizers ───────────────────────────────────────────────────────────────

def normalize_header(header: str) -> str:
    """Lowercase, collapse whitespace/newlines, strip surrounding quotes."""
    h = str(header).strip()
    h = re.sub(r'[\n\r\t]+', ' ', h)
    h = re.sub(r'\s+', ' ', h)
    h = h.lower()
    h = h.strip('"\'')
    return h.strip()


def normalize_money(value: str) -> Optional[int]:
    """
    Strips Rs./Rs/currency symbols, commas, spaces, /-, bracketed words.
    Handles Indian grouping (99,77,517 -> 9977517). Returns int or None.
    """
    if not value or not str(value).strip():
        return None
    v = str(value).strip()
    # Remove bracketed/parenthesized words e.g. "(Rupees Ninety Nine Lakhs ...)"
    v = re.sub(r'\([^)]*\)', '', v)
    # Remove currency labels ("Rs.", "Rs", "Rs " — word-boundary-free to catch "Rs. 99...")
    v = re.sub(r'(?i)rs\.?\s*', '', v)
    v = v.replace('₹', '').replace('\\u20b9', '')
    # Remove trailing /- or standalone / or -
    v = re.sub(r'[/\-]+$', '', v.strip())
    # Remove commas and all whitespace
    v = re.sub(r'[,\s]+', '', v)
    v = v.strip()
    if not v:
        return None
    try:
        # Decimal handles "9977517.00" from sheet numeric cells
        return int(Decimal(v))
    except InvalidOperation:
        return None


def normalize_area(value: str) -> Optional[Decimal]:
    """
    Strips unit labels (sq.mt./sq.ft./etc.), commas, spaces.
    Returns Decimal for exact comparison (42.06 != 42.6). Returns None if empty/unparseable.
    """
    if not value or not str(value).strip():
        return None
    v = str(value).strip()
    # Remove unit labels (case-insensitive)
    v = re.sub(r'(?i)(sq\.?\s*m(?:t\.?)?|sq\.?\s*f(?:t\.?)?|m²|ft²|sqmt|sqft)', '', v)
    # Remove commas and extra whitespace
    v = re.sub(r'[,\s]+', '', v)
    v = v.strip()
    if not v:
        return None
    try:
        return Decimal(v)
    except InvalidOperation:
        return None


def normalize_unit_no(value: str) -> Optional[str]:
    """Strip whitespace. Returns None if empty."""
    if not value or not str(value).strip():
        return None
    return str(value).strip()


def normalize_text(value: str) -> Optional[str]:
    """
    Case-insensitive text key: lowercase + collapse internal whitespace + strip.
    Used for names, parking number/level/configuration, share certificate no., floor.
    Returns None if empty. (Raw, original-case value is shown separately in the report.)
    """
    if value is None:
        return None
    v = re.sub(r'\s+', ' ', str(value)).strip().lower()
    return v or None


def normalize_pan(value: str) -> Optional[str]:
    """Uppercase, strip all spaces and dashes. Returns None if empty."""
    if value is None:
        return None
    v = re.sub(r'[\s\-]+', '', str(value)).upper()
    return v or None


def normalize_email(value: str) -> Optional[str]:
    """Lowercase, strip all whitespace. Returns None if empty."""
    if value is None:
        return None
    v = re.sub(r'\s+', '', str(value)).lower()
    return v or None


def normalize_dimension(value: str) -> Optional[Decimal]:
    """
    Parking dimensions: drop everything except digits and the decimal point
    (strips 'm.', 'Up to', labels, spaces). Returns Decimal for exact compare.
    '2.5 m.' -> 2.5 ; 'Up to 2.5 m. Height' -> 2.5 ; returns None if unparseable.
    """
    if value is None:
        return None
    v = re.sub(r'[^0-9.]', '', str(value))
    if not v or v == '.':
        return None
    try:
        return Decimal(v)
    except InvalidOperation:
        return None


def normalize_int(value: str) -> Optional[int]:
    """Extract digits only and parse as int. Used for share numbers/counts."""
    if value is None:
        return None
    v = re.sub(r'[^0-9]', '', str(value))
    if not v:
        return None
    try:
        return int(v)
    except ValueError:
        return None


def _s(x) -> Optional[str]:
    """str(x) but preserve None (for the normalized columns in FieldResult)."""
    return str(x) if x is not None else None


def _first_nonempty(val) -> str:
    """
    When a sheet column header appears multiple times (e.g. merged cells),
    find_unit_row returns a list. This picks the first non-empty element.
    For plain strings, returns the string unchanged.
    """
    if isinstance(val, list):
        for v in val:
            if str(v).strip():
                return str(v).strip()
        return ""
    return str(val) if val is not None else ""


# ── Extraction contract validation ────────────────────────────────────────────

def validate_extraction(extraction: dict) -> tuple:
    """Returns (is_valid: bool, error_message: str). Empty string on success."""
    if not isinstance(extraction, dict):
        return False, "Extraction must be a dict"

    for fname in REQUIRED_CONTRACT_FIELDS:
        if fname not in extraction:
            return False, f"Missing field in extraction contract: '{fname}'"
        fd = extraction[fname]
        if not isinstance(fd, dict):
            return False, f"extraction['{fname}'] must be a dict"
        if not isinstance(fd.get("occurrences"), list):
            return False, f"extraction['{fname}'].occurrences must be a list"
        if not isinstance(fd.get("distinct_values"), list):
            return False, f"extraction['{fname}'].distinct_values must be a list"
        if fd.get("internal_status") not in ("INTERNAL_OK", "INTERNAL_DISCREPANCY"):
            return False, (
                f"extraction['{fname}'].internal_status must be "
                "'INTERNAL_OK' or 'INTERNAL_DISCREPANCY'"
            )

    if extraction.get("extraction_confidence") not in ("HIGH", "MEDIUM", "LOW"):
        return False, "extraction_confidence must be HIGH, MEDIUM, or LOW"

    if not isinstance(extraction.get("afs_meta"), dict):
        return False, "extraction.afs_meta must be a dict"

    return True, ""


# ── Per-field comparators ─────────────────────────────────────────────────────

def _compare_unit_number(extraction: dict, sheet_row: dict) -> FieldResult:
    fd = extraction["unit_number"]
    occurrences = fd["occurrences"]
    distinct = fd["distinct_values"]

    if fd["internal_status"] == "INTERNAL_DISCREPANCY":
        return FieldResult(
            field_name="Unit Number", status=INTERNAL_DISCREPANCY,
            afs_occurrences=occurrences, afs_distinct_values=distinct,
            sheet_raw="", afs_normalized=None, sheet_normalized=None,
            detail=f"AFS contains conflicting unit numbers: {distinct}"
        )

    if not distinct:
        return FieldResult(
            field_name="Unit Number", status=NOT_FOUND_IN_AFS,
            afs_occurrences=occurrences, afs_distinct_values=distinct,
            sheet_raw="", afs_normalized=None, sheet_normalized=None,
            detail="Unit number not found in AFS"
        )

    afs_val = normalize_unit_no(distinct[0])
    sheet_raw = sheet_row.get("unit no.", "")

    # Handle two Unit No. columns (stored as list by sheets.find_unit_row)
    if isinstance(sheet_raw, list):
        sheet_raw_display = " | ".join(str(v) for v in sheet_raw)
        sheet_vals = [normalize_unit_no(v) for v in sheet_raw]
        non_null = [v for v in sheet_vals if v is not None]

        if not non_null:
            return FieldResult(
                field_name="Unit Number", status=NOT_FOUND_IN_SHEET,
                afs_occurrences=occurrences, afs_distinct_values=distinct,
                sheet_raw=sheet_raw_display, afs_normalized=afs_val,
                sheet_normalized=None,
                detail="Both 'Unit No.' columns in sheet are empty"
            )

        unique_vals = set(v for v in sheet_vals if v)
        if len(unique_vals) > 1:
            return FieldResult(
                field_name="Unit Number", status=MISMATCH,
                afs_occurrences=occurrences, afs_distinct_values=distinct,
                sheet_raw=sheet_raw_display, afs_normalized=afs_val,
                sheet_normalized=str(list(unique_vals)),
                detail=f"The two 'Unit No.' columns in the sheet disagree: {list(unique_vals)}"
            )

        sheet_normalized = non_null[0]
    else:
        sheet_raw_display = str(sheet_raw)
        sheet_normalized = normalize_unit_no(sheet_raw)
        if sheet_normalized is None:
            return FieldResult(
                field_name="Unit Number", status=NOT_FOUND_IN_SHEET,
                afs_occurrences=occurrences, afs_distinct_values=distinct,
                sheet_raw=sheet_raw_display, afs_normalized=afs_val,
                sheet_normalized=None,
                detail="'Unit No.' column in sheet is empty"
            )

    if afs_val is None:
        return FieldResult(
            field_name="Unit Number", status=NOT_FOUND_IN_AFS,
            afs_occurrences=occurrences, afs_distinct_values=distinct,
            sheet_raw=sheet_raw_display, afs_normalized=None,
            sheet_normalized=sheet_normalized,
            detail="AFS unit number value is empty"
        )

    status = MATCH if afs_val == sheet_normalized else MISMATCH
    detail = "" if status == MATCH else f"AFS: '{afs_val}'  vs  Sheet: '{sheet_normalized}'"
    return FieldResult(
        field_name="Unit Number", status=status,
        afs_occurrences=occurrences, afs_distinct_values=distinct,
        sheet_raw=sheet_raw_display, afs_normalized=afs_val,
        sheet_normalized=sheet_normalized, detail=detail
    )


def _compare_agreement_value(extraction: dict, sheet_row: dict) -> FieldResult:
    fd = extraction["agreement_value"]
    occurrences = fd["occurrences"]
    distinct = fd["distinct_values"]

    if fd["internal_status"] == "INTERNAL_DISCREPANCY":
        return FieldResult(
            field_name="Agreement Value", status=INTERNAL_DISCREPANCY,
            afs_occurrences=occurrences, afs_distinct_values=distinct,
            sheet_raw="", afs_normalized=None, sheet_normalized=None,
            detail=f"AFS contains conflicting agreement values: {distinct}"
        )

    if not distinct:
        return FieldResult(
            field_name="Agreement Value", status=NOT_FOUND_IN_AFS,
            afs_occurrences=occurrences, afs_distinct_values=distinct,
            sheet_raw="", afs_normalized=None, sheet_normalized=None,
            detail="Agreement value not found in AFS"
        )

    afs_int = normalize_money(distinct[0])
    sheet_raw_val = sheet_row.get("agreement value", "")
    sheet_raw = _first_nonempty(sheet_raw_val)
    sheet_int = normalize_money(sheet_raw)

    sheet_raw_display = str(sheet_raw_val)

    if afs_int is None:
        return FieldResult(
            field_name="Agreement Value", status=NOT_FOUND_IN_AFS,
            afs_occurrences=occurrences, afs_distinct_values=distinct,
            sheet_raw=sheet_raw_display, afs_normalized=None,
            sheet_normalized=str(sheet_int) if sheet_int is not None else None,
            detail=f"AFS value '{distinct[0]}' could not be parsed as a number"
        )

    if sheet_int is None:
        detail = (
            "'Agreement Value' column not found in sheet"
            if "agreement value" not in sheet_row
            else f"'Agreement Value' cell is empty (raw: '{sheet_raw}')"
        )
        return FieldResult(
            field_name="Agreement Value", status=NOT_FOUND_IN_SHEET,
            afs_occurrences=occurrences, afs_distinct_values=distinct,
            sheet_raw=sheet_raw_display, afs_normalized=str(afs_int),
            sheet_normalized=None, detail=detail
        )

    status = MATCH if afs_int == sheet_int else MISMATCH
    detail = "" if status == MATCH else f"AFS: {afs_int}  vs  Sheet: {sheet_int}"
    return FieldResult(
        field_name="Agreement Value", status=status,
        afs_occurrences=occurrences, afs_distinct_values=distinct,
        sheet_raw=sheet_raw_display, afs_normalized=str(afs_int),
        sheet_normalized=str(sheet_int), detail=detail
    )


def _compare_area_sqm(extraction: dict, sheet_row: dict) -> FieldResult:
    fd = extraction["area_sqm"]
    occurrences = fd["occurrences"]
    distinct = fd["distinct_values"]

    if fd["internal_status"] == "INTERNAL_DISCREPANCY":
        return FieldResult(
            field_name="Area (Sq.M)", status=INTERNAL_DISCREPANCY,
            afs_occurrences=occurrences, afs_distinct_values=distinct,
            sheet_raw="", afs_normalized=None, sheet_normalized=None,
            detail=f"AFS contains conflicting Sq.M values: {distinct}"
        )

    if not distinct:
        return FieldResult(
            field_name="Area (Sq.M)", status=NOT_FOUND_IN_AFS,
            afs_occurrences=occurrences, afs_distinct_values=distinct,
            sheet_raw="", afs_normalized=None, sheet_normalized=None,
            detail="Area Sq.M not found in AFS"
        )

    afs_dec = normalize_area(distinct[0])
    sheet_raw_val = sheet_row.get("unit area sq. mt.", "")
    sheet_raw = _first_nonempty(sheet_raw_val)
    sheet_dec = normalize_area(sheet_raw)
    sheet_raw_display = str(sheet_raw_val)

    if afs_dec is None:
        return FieldResult(
            field_name="Area (Sq.M)", status=NOT_FOUND_IN_AFS,
            afs_occurrences=occurrences, afs_distinct_values=distinct,
            sheet_raw=sheet_raw_display, afs_normalized=None,
            sheet_normalized=str(sheet_dec) if sheet_dec is not None else None,
            detail=f"AFS value '{distinct[0]}' could not be parsed as a decimal"
        )

    if sheet_dec is None:
        detail = (
            "'Unit Area Sq. Mt.' column not found in sheet"
            if "unit area sq. mt." not in sheet_row
            else f"'Unit Area Sq. Mt.' cell is empty (raw: '{sheet_raw}')"
        )
        return FieldResult(
            field_name="Area (Sq.M)", status=NOT_FOUND_IN_SHEET,
            afs_occurrences=occurrences, afs_distinct_values=distinct,
            sheet_raw=sheet_raw_display, afs_normalized=str(afs_dec),
            sheet_normalized=None, detail=detail
        )

    status = MATCH if afs_dec == sheet_dec else MISMATCH
    detail = "" if status == MATCH else f"AFS: {afs_dec}  vs  Sheet: {sheet_dec}"
    return FieldResult(
        field_name="Area (Sq.M)", status=status,
        afs_occurrences=occurrences, afs_distinct_values=distinct,
        sheet_raw=sheet_raw_display, afs_normalized=str(afs_dec),
        sheet_normalized=str(sheet_dec), detail=detail
    )


def _compare_area_sqft(extraction: dict, sheet_row: dict) -> FieldResult:
    fd = extraction["area_sqft"]
    occurrences = fd["occurrences"]
    distinct = fd["distinct_values"]

    # Check balcony first — affects whether a hard compare is valid
    balcony_raw = sheet_row.get("balcony sq. mt.", "")
    balcony_dec = normalize_area(str(balcony_raw))
    has_balcony = balcony_dec is not None and balcony_dec > Decimal("0")

    if fd["internal_status"] == "INTERNAL_DISCREPANCY":
        return FieldResult(
            field_name="Area (Sq.Ft)", status=INTERNAL_DISCREPANCY,
            afs_occurrences=occurrences, afs_distinct_values=distinct,
            sheet_raw="", afs_normalized=None, sheet_normalized=None,
            detail=f"AFS contains conflicting Sq.Ft values: {distinct}"
        )

    if not distinct:
        return FieldResult(
            field_name="Area (Sq.Ft)", status=NOT_FOUND_IN_AFS,
            afs_occurrences=occurrences, afs_distinct_values=distinct,
            sheet_raw="", afs_normalized=None, sheet_normalized=None,
            detail="Area Sq.Ft not found in AFS"
        )

    sheet_raw_val = sheet_row.get("total unit area sq. ft.", "")
    sheet_raw = _first_nonempty(sheet_raw_val)
    sheet_raw_display = str(sheet_raw_val)

    if has_balcony:
        return FieldResult(
            field_name="Area (Sq.Ft)", status=SCHEMA_CAVEAT,
            afs_occurrences=occurrences, afs_distinct_values=distinct,
            sheet_raw=sheet_raw_display, afs_normalized=distinct[0],
            sheet_normalized=None,
            detail=(
                f"Sheet 'Total Unit Area Sq.Ft.' includes balcony ({balcony_raw} Sq.Mt.). "
                "No carpet-only Sq.Ft. column exists — hard comparison skipped."
            )
        )

    afs_dec = normalize_area(distinct[0])
    sheet_dec = normalize_area(sheet_raw)

    if afs_dec is None:
        return FieldResult(
            field_name="Area (Sq.Ft)", status=NOT_FOUND_IN_AFS,
            afs_occurrences=occurrences, afs_distinct_values=distinct,
            sheet_raw=sheet_raw_display, afs_normalized=None,
            sheet_normalized=str(sheet_dec) if sheet_dec is not None else None,
            detail=f"AFS value '{distinct[0]}' could not be parsed as a decimal"
        )

    if sheet_dec is None:
        detail = (
            "'Total Unit Area Sq. Ft.' column not found in sheet"
            if "total unit area sq. ft." not in sheet_row
            else f"'Total Unit Area Sq. Ft.' cell is empty (raw: '{sheet_raw}')"
        )
        return FieldResult(
            field_name="Area (Sq.Ft)", status=NOT_FOUND_IN_SHEET,
            afs_occurrences=occurrences, afs_distinct_values=distinct,
            sheet_raw=sheet_raw_display, afs_normalized=str(afs_dec),
            sheet_normalized=None, detail=detail
        )

    status = MATCH if afs_dec == sheet_dec else MISMATCH
    detail = "" if status == MATCH else f"AFS: {afs_dec}  vs  Sheet: {sheet_dec}"
    return FieldResult(
        field_name="Area (Sq.Ft)", status=status,
        afs_occurrences=occurrences, afs_distinct_values=distinct,
        sheet_raw=sheet_raw_display, afs_normalized=str(afs_dec),
        sheet_normalized=str(sheet_dec), detail=detail
    )


# ── Generic comparator for the remaining (non-core) sheet fields ─────────────

def _compare_simple_field(extraction: dict, sheet_row: dict, contract_key: str,
                          display_name: str, sheet_header: str, normalizer) -> FieldResult:
    """
    Single-occurrence, single-column comparison used for every field beyond the
    four core ones. Reads the AFS value from extraction[contract_key], the sheet
    value from sheet_row[sheet_header], normalizes both with `normalizer`, and
    compares for exact equality. Mirrors the status logic of the core comparators.
    """
    fd = extraction.get(contract_key) or {}
    occurrences = fd.get("occurrences", [])
    distinct = fd.get("distinct_values", [])

    if fd.get("internal_status") == "INTERNAL_DISCREPANCY":
        return FieldResult(
            field_name=display_name, status=INTERNAL_DISCREPANCY,
            afs_occurrences=occurrences, afs_distinct_values=distinct,
            sheet_raw="", afs_normalized=None, sheet_normalized=None,
            detail=f"AFS contains conflicting values: {distinct}"
        )

    if not distinct:
        return FieldResult(
            field_name=display_name, status=NOT_FOUND_IN_AFS,
            afs_occurrences=occurrences, afs_distinct_values=distinct,
            sheet_raw="", afs_normalized=None, sheet_normalized=None,
            detail=f"{display_name} not found in AFS"
        )

    afs_norm = normalizer(distinct[0])
    sheet_raw_val = sheet_row.get(sheet_header, "")
    sheet_raw = _first_nonempty(sheet_raw_val)
    sheet_norm = normalizer(sheet_raw)
    sheet_raw_display = str(sheet_raw_val)

    if afs_norm is None:
        return FieldResult(
            field_name=display_name, status=NOT_FOUND_IN_AFS,
            afs_occurrences=occurrences, afs_distinct_values=distinct,
            sheet_raw=sheet_raw_display, afs_normalized=None,
            sheet_normalized=_s(sheet_norm),
            detail=f"AFS value '{distinct[0]}' could not be parsed"
        )

    if sheet_norm is None:
        detail = (
            f"'{sheet_header}' column not found in sheet"
            if sheet_header not in sheet_row
            else f"'{sheet_header}' cell is empty (raw: '{sheet_raw}')"
        )
        return FieldResult(
            field_name=display_name, status=NOT_FOUND_IN_SHEET,
            afs_occurrences=occurrences, afs_distinct_values=distinct,
            sheet_raw=sheet_raw_display, afs_normalized=_s(afs_norm),
            sheet_normalized=None, detail=detail
        )

    status = MATCH if afs_norm == sheet_norm else MISMATCH
    detail = "" if status == MATCH else f"AFS: '{afs_norm}'  vs  Sheet: '{sheet_norm}'"
    return FieldResult(
        field_name=display_name, status=status,
        afs_occurrences=occurrences, afs_distinct_values=distinct,
        sheet_raw=sheet_raw_display, afs_normalized=_s(afs_norm),
        sheet_normalized=_s(sheet_norm), detail=detail
    )


# Non-core fields, in report display order. Each: (extraction contract key,
# display name, EXACT normalized sheet header, normalizer, info_only). Sheet
# headers come from the live "Inventory Sheet" tab (note quirks:
# 'legal charges (15k)', 'parking conf. (stack /tendem)'). A field is compared
# only when the extraction actually contains it, so 4-field extractions are
# unaffected. info_only=True fields are shown but never affect the verdict.
#   - parking_level: sheet stores a code like 'BS-LVL-03', so digits are
#     extracted from both sides (normalize_int) to match the AFS '3'.
#   - legal_charges: sheet stores a payment status ('Paid'), not a figure,
#     so it is info-only (cannot be a money match).
_OPTIONAL_FIELD_SPECS = [
    ("floor",              "Floor",                    "floor",                          normalize_text,      False),
    ("applicant_name",     "Applicant Name",           "applicants name",                normalize_text,      False),
    ("applicant_pan",      "Applicant PAN",            "applicant's pan no.",            normalize_pan,       False),
    ("applicant_email",    "Applicant Email",          "email id",                       normalize_email,     False),
    ("parking_no",         "Parking No.",              "parking no.",                    normalize_text,      False),
    ("parking_level",      "Parking Level (Basement)", "basement level",                 normalize_int,       False),
    ("parking_conf",       "Parking Configuration",    "parking conf. (stack /tendem)",  normalize_text,      False),
    ("parking_length",     "Parking Length (M)",       "parking length (m)",             normalize_dimension, False),
    ("parking_width",      "Parking Width (M)",        "parking width (m)",              normalize_dimension, False),
    ("parking_height",     "Parking Height (M)",       "parking height (m)",             normalize_dimension, False),
    ("parking_total_area", "Parking Total Area (M)",   "parking total (m)",              normalize_dimension, False),
    ("share_cert_no",      "Share Certificate No.",    "share certificate no.",          normalize_text,      False),
    ("share_from",         "Share Alloted From",       "share alloted from",             normalize_int,       False),
    ("share_to",           "Share Alloted",            "share alloted",                  normalize_int,       False),
    ("total_shares",       "Total No. of Shares",      "total no. of shares",            normalize_int,       False),
    ("legal_charges",      "Legal Charges",            "legal charges (15k)",            normalize_money,     True),
]


# ── Sanity checks (warnings only, do not affect verdict) ─────────────────────

def _sanity_check_area(sheet_row: dict) -> list:
    """Warns if unit_area_sqm + balcony_sqm != total_unit_area_sqm."""
    unit_dec = normalize_area(_first_nonempty(sheet_row.get("unit area sq. mt.", "")))
    balcony_dec = normalize_area(_first_nonempty(sheet_row.get("balcony sq. mt.", ""))) or Decimal("0")
    total_dec = normalize_area(_first_nonempty(sheet_row.get("total unit area sq. mt.", "")))

    if unit_dec is not None and total_dec is not None:
        expected = unit_dec + balcony_dec
        if expected != total_dec:
            return [
                f"Sheet sanity check failed: 'Unit Area Sq.Mt.' ({unit_dec}) + "
                f"'Balcony Sq.Mt.' ({balcony_dec}) = {expected}, but "
                f"'Total Unit Area Sq.Mt.' = {total_dec}. Verify sheet data."
            ]
    return []


# ── Main entry point ──────────────────────────────────────────────────────────

def run_comparison(extraction: dict, sheet_row: dict) -> ComparisonResult:
    """
    Validates the extraction contract, runs all four field comparisons,
    applies sanity checks, and returns a ComparisonResult.
    Verdict = PASS only if all fields are MATCH or SCHEMA_CAVEAT.
    """
    valid, err = validate_extraction(extraction)
    if not valid:
        return ComparisonResult(
            verdict="FAIL",
            fields=[FieldResult(
                field_name="SCHEMA_ERROR", status=MISMATCH,
                afs_occurrences=[], afs_distinct_values=[],
                sheet_raw="", afs_normalized=None, sheet_normalized=None,
                detail=f"Invalid extraction contract: {err}"
            )],
            warnings=[], schema_caveats=[]
        )

    fields = [
        _compare_unit_number(extraction, sheet_row),
        _compare_agreement_value(extraction, sheet_row),
        _compare_area_sqm(extraction, sheet_row),
        _compare_area_sqft(extraction, sheet_row),
    ]

    # Append the remaining sheet-comparable fields, but only those the extraction
    # actually contains — so a core-only (4-field) extraction is unchanged.
    for contract_key, display_name, sheet_header, normalizer, info_only in _OPTIONAL_FIELD_SPECS:
        if contract_key not in extraction:
            continue
        fr = _compare_simple_field(
            extraction, sheet_row, contract_key,
            display_name, sheet_header, normalizer,
        )
        if info_only:
            # Show the AFS value and sheet cell, but mark INFO_ONLY so the
            # verdict is never affected (e.g. sheet stores a status, not a figure).
            note = "Info only — does not affect verdict."
            if fr.detail:
                note += f" ({fr.detail})"
            fr = FieldResult(
                field_name=display_name, status=INFO_ONLY,
                afs_occurrences=fr.afs_occurrences, afs_distinct_values=fr.afs_distinct_values,
                sheet_raw=fr.sheet_raw, afs_normalized=fr.afs_normalized,
                sheet_normalized=fr.sheet_normalized, detail=note,
            )
        fields.append(fr)

    warnings = _sanity_check_area(sheet_row)
    schema_caveats = [f.detail for f in fields if f.status == SCHEMA_CAVEAT]

    fail_fields = [f for f in fields if f.status not in PASS_STATUSES]
    verdict = "PASS" if not fail_fields else "FAIL"

    return ComparisonResult(verdict=verdict, fields=fields,
                            warnings=warnings, schema_caveats=schema_caveats)
