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

PASS_STATUSES = {MATCH, SCHEMA_CAVEAT}

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

    warnings = _sanity_check_area(sheet_row)
    schema_caveats = [f.detail for f in fields if f.status == SCHEMA_CAVEAT]

    fail_fields = [f for f in fields if f.status not in PASS_STATUSES]
    verdict = "PASS" if not fail_fields else "FAIL"

    return ComparisonResult(verdict=verdict, fields=fields,
                            warnings=warnings, schema_caveats=schema_caveats)
