"""
Unit tests for comparator.py.
No network, no LLM, no file I/O (except loading the fixture for helpers).
Run with: pytest tests/test_comparator.py -v
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from decimal import Decimal
import pytest
from comparator import (
    normalize_money, normalize_area, normalize_header, normalize_unit_no,
    validate_extraction, run_comparison,
    MATCH, MISMATCH, SCHEMA_CAVEAT, INTERNAL_DISCREPANCY,
    NOT_FOUND_IN_AFS, NOT_FOUND_IN_SHEET,
)


# ── Normalizer tests ──────────────────────────────────────────────────────────

class TestNormalizeMoney:
    def test_plain_int(self):
        assert normalize_money("9977517") == 9977517

    def test_indian_comma_grouping(self):
        assert normalize_money("99,77,517") == 9977517

    def test_rs_prefix_with_slash(self):
        assert normalize_money("Rs. 99,77,517/-") == 9977517

    def test_rs_no_space(self):
        assert normalize_money("Rs.9977517") == 9977517

    def test_rupee_symbol(self):
        assert normalize_money("₹ 99,77,517") == 9977517

    def test_bracketed_words_removed(self):
        assert normalize_money("9977517 (Rupees Ninety Nine Lakhs Only)") == 9977517

    def test_decimal_sheet_cell(self):
        assert normalize_money("9977517.00") == 9977517

    def test_empty_returns_none(self):
        assert normalize_money("") is None
        assert normalize_money("   ") is None

    def test_non_numeric_returns_none(self):
        assert normalize_money("N/A") is None

    def test_off_by_one(self):
        assert normalize_money("9977516") == 9977516
        assert normalize_money("9977516") != 9977517


class TestNormalizeArea:
    def test_plain_decimal(self):
        assert normalize_area("42.06") == Decimal("42.06")

    def test_sq_mt_suffix(self):
        assert normalize_area("42.06 Sq. Mt.") == Decimal("42.06")

    def test_sqmt_no_space(self):
        assert normalize_area("453sqft") == Decimal("453")

    def test_precision_preserved(self):
        # 42.06 must NOT equal 42.6
        assert normalize_area("42.06") != normalize_area("42.6")

    def test_trailing_zeros_equal(self):
        # 453.00 should equal 453 numerically
        assert normalize_area("453.00") == normalize_area("453")

    def test_integer_area(self):
        assert normalize_area("453") == Decimal("453")

    def test_empty_returns_none(self):
        assert normalize_area("") is None
        assert normalize_area("  ") is None


class TestNormalizeHeader:
    def test_lowercase(self):
        assert normalize_header("Unit No.") == "unit no."

    def test_newlines_collapsed(self):
        assert normalize_header('"Unit Area \n Sq. Mt."') == "unit area sq. mt."

    def test_quotes_stripped(self):
        assert normalize_header('"Balcony\n Sq. Mt."') == "balcony sq. mt."

    def test_total_sqft(self):
        assert normalize_header('"Total Unit Area\n Sq. Ft."') == "total unit area sq. ft."

    def test_agreement_value(self):
        assert normalize_header("Agreement Value") == "agreement value"


# ── Extraction validation tests ───────────────────────────────────────────────

def _make_extraction(overrides=None):
    base = {
        "unit_number":     {"occurrences": [], "distinct_values": ["313"], "internal_status": "INTERNAL_OK"},
        "agreement_value": {"occurrences": [], "distinct_values": ["9977517"], "internal_status": "INTERNAL_OK", "figure_vs_words": "OK"},
        "area_sqm":        {"occurrences": [], "distinct_values": ["42.06"], "internal_status": "INTERNAL_OK"},
        "area_sqft":       {"occurrences": [], "distinct_values": ["453"],   "internal_status": "INTERNAL_OK"},
        "extraction_confidence": "HIGH",
        "afs_meta": {"buyer_name": "Test Buyer", "project_name": "Project A", "afs_date": "15/04/2026"},
    }
    if overrides:
        base.update(overrides)
    return base

def _make_sheet_row(overrides=None):
    base = {
        "unit no.":                ["313", "313"],
        "agreement value":         "9977517",
        "unit area sq. mt.":       "42.06",
        "balcony sq. mt.":         "0",
        "total unit area sq. mt.": "42.06",
        "total unit area sq. ft.": "453",
    }
    if overrides:
        base.update(overrides)
    return base

class TestValidateExtraction:
    def test_valid_passes(self):
        ok, err = validate_extraction(_make_extraction())
        assert ok is True
        assert err == ""

    def test_missing_field_fails(self):
        ext = _make_extraction()
        del ext["area_sqft"]
        ok, err = validate_extraction(ext)
        assert ok is False
        assert "area_sqft" in err

    def test_bad_internal_status_fails(self):
        ext = _make_extraction()
        ext["unit_number"]["internal_status"] = "UNKNOWN"
        ok, err = validate_extraction(ext)
        assert ok is False

    def test_bad_confidence_fails(self):
        ext = _make_extraction()
        ext["extraction_confidence"] = "VERY_HIGH"
        ok, err = validate_extraction(ext)
        assert ok is False


# ── run_comparison tests ──────────────────────────────────────────────────────

class TestRunComparison:
    def test_exact_match_pass(self):
        result = run_comparison(_make_extraction(), _make_sheet_row())
        assert result.verdict == "PASS"
        for f in result.fields:
            assert f.status in (MATCH, SCHEMA_CAVEAT)

    def test_money_off_by_one_mismatch(self):
        sheet = _make_sheet_row({"agreement value": "9977516"})
        result = run_comparison(_make_extraction(), sheet)
        assert result.verdict == "FAIL"
        agr = next(f for f in result.fields if f.field_name == "Agreement Value")
        assert agr.status == MISMATCH
        assert "9977517" in agr.detail
        assert "9977516" in agr.detail

    def test_area_sqm_decimal_precision(self):
        # 42.06 in AFS vs 42.6 in sheet — must be MISMATCH
        sheet = _make_sheet_row({"unit area sq. mt.": "42.6"})
        result = run_comparison(_make_extraction(), sheet)
        assert result.verdict == "FAIL"
        sqm = next(f for f in result.fields if f.field_name == "Area (Sq.M)")
        assert sqm.status == MISMATCH

    def test_balcony_positive_schema_caveat(self):
        sheet = _make_sheet_row({"balcony sq. mt.": "5.5"})
        result = run_comparison(_make_extraction(), sheet)
        sqft = next(f for f in result.fields if f.field_name == "Area (Sq.Ft)")
        assert sqft.status == SCHEMA_CAVEAT
        # SCHEMA_CAVEAT alone should not make verdict FAIL
        # (all other fields match, so verdict should be PASS)
        assert result.verdict == "PASS"

    def test_internal_discrepancy_fails(self):
        ext = _make_extraction()
        ext["agreement_value"]["internal_status"] = "INTERNAL_DISCREPANCY"
        ext["agreement_value"]["distinct_values"] = ["9977517", "9977000"]
        result = run_comparison(ext, _make_sheet_row())
        assert result.verdict == "FAIL"
        agr = next(f for f in result.fields if f.field_name == "Agreement Value")
        assert agr.status == INTERNAL_DISCREPANCY

    def test_unit_not_found_in_sheet(self):
        sheet = _make_sheet_row()
        del sheet["unit no."]
        result = run_comparison(_make_extraction(), sheet)
        assert result.verdict == "FAIL"
        unit = next(f for f in result.fields if f.field_name == "Unit Number")
        assert unit.status == NOT_FOUND_IN_SHEET

    def test_unit_not_found_in_afs(self):
        ext = _make_extraction()
        ext["unit_number"]["distinct_values"] = []
        result = run_comparison(ext, _make_sheet_row())
        assert result.verdict == "FAIL"
        unit = next(f for f in result.fields if f.field_name == "Unit Number")
        assert unit.status == NOT_FOUND_IN_AFS

    def test_two_sheet_unit_cols_agree_match(self):
        sheet = _make_sheet_row({"unit no.": ["313", "313"]})
        result = run_comparison(_make_extraction(), sheet)
        unit = next(f for f in result.fields if f.field_name == "Unit Number")
        assert unit.status == MATCH

    def test_two_sheet_unit_cols_disagree_mismatch(self):
        sheet = _make_sheet_row({"unit no.": ["313", "314"]})
        result = run_comparison(_make_extraction(), sheet)
        assert result.verdict == "FAIL"
        unit = next(f for f in result.fields if f.field_name == "Unit Number")
        assert unit.status == MISMATCH
        assert "disagree" in unit.detail.lower()

    def test_invalid_contract_returns_fail(self):
        result = run_comparison({"bad": "data"}, {})
        assert result.verdict == "FAIL"

    def test_money_with_rs_commas_in_afs(self):
        ext = _make_extraction()
        ext["agreement_value"]["distinct_values"] = ["Rs. 99,77,517/-"]
        result = run_comparison(ext, _make_sheet_row())
        agr = next(f for f in result.fields if f.field_name == "Agreement Value")
        assert agr.status == MATCH

    def test_area_sqft_empty_sheet_cell(self):
        sheet = _make_sheet_row({"total unit area sq. ft.": ""})
        result = run_comparison(_make_extraction(), sheet)
        sqft = next(f for f in result.fields if f.field_name == "Area (Sq.Ft)")
        assert sqft.status == NOT_FOUND_IN_SHEET

    def test_sanity_check_warning(self):
        # unit(42.06) + balcony(0) = 42.06 but total says 43.00 -> warning
        sheet = _make_sheet_row({
            "unit area sq. mt.": "42.06",
            "balcony sq. mt.":   "0",
            "total unit area sq. mt.": "43.00",
        })
        result = run_comparison(_make_extraction(), sheet)
        assert len(result.warnings) > 0
        assert "sanity" in result.warnings[0].lower()
