"""
End-to-end tests using the Unit 313 fixture + stubbed sheet rows.
No network, no LLM, no Google Sheets API calls.
Run with: pytest tests/test_e2e_sheet.py -v
"""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from comparator import run_comparison, MATCH, MISMATCH, SCHEMA_CAVEAT
from sheets import find_unit_row
from unittest.mock import MagicMock

FIXTURE_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "afs_313_extraction.json")


def _load_fixture():
    with open(FIXTURE_PATH) as f:
        return json.load(f)


def _matching_sheet_row():
    """Sheet row that exactly matches the fixture (Unit 313)."""
    return {
        "unit no.":                ["313", "313"],
        "agreement value":         "9977517",
        "unit area sq. mt.":       "42.06",
        "balcony sq. mt.":         "0",
        "total unit area sq. mt.": "42.06",
        "total unit area sq. ft.": "453",
    }


# ── Fixture shape tests ───────────────────────────────────────────────────────

class TestFixture:
    def test_fixture_loads_and_validates(self):
        from comparator import validate_extraction
        ext = _load_fixture()
        ok, err = validate_extraction(ext)
        assert ok is True, f"Fixture failed validation: {err}"

    def test_fixture_unit_313(self):
        ext = _load_fixture()
        assert ext["unit_number"]["distinct_values"] == ["313"]

    def test_fixture_agreement_value(self):
        ext = _load_fixture()
        assert ext["agreement_value"]["distinct_values"] == ["9977517"]

    def test_fixture_area_sqm(self):
        ext = _load_fixture()
        assert ext["area_sqm"]["distinct_values"] == ["42.06"]

    def test_fixture_area_sqft(self):
        ext = _load_fixture()
        assert ext["area_sqft"]["distinct_values"] == ["453"]

    def test_fixture_confidence_high(self):
        ext = _load_fixture()
        assert ext["extraction_confidence"] == "HIGH"


# ── End-to-end comparison tests ───────────────────────────────────────────────

class TestE2EComparison:
    def test_fixture_plus_matching_sheet_pass(self):
        result = run_comparison(_load_fixture(), _matching_sheet_row())
        assert result.verdict == "PASS"
        for f in result.fields:
            assert f.status in (MATCH, SCHEMA_CAVEAT), (
                f"{f.field_name} unexpected status: {f.status} — {f.detail}"
            )

    def test_flip_agreement_value_fails(self):
        sheet = _matching_sheet_row()
        sheet["agreement value"] = "9977518"   # one digit off
        result = run_comparison(_load_fixture(), sheet)
        assert result.verdict == "FAIL"
        agr = next(f for f in result.fields if f.field_name == "Agreement Value")
        assert agr.status == MISMATCH

    def test_flip_unit_no_fails(self):
        sheet = _matching_sheet_row()
        sheet["unit no."] = ["314", "314"]
        result = run_comparison(_load_fixture(), sheet)
        assert result.verdict == "FAIL"

    def test_flip_sqm_decimal_fails(self):
        sheet = _matching_sheet_row()
        sheet["unit area sq. mt."] = "42.60"   # different decimal
        result = run_comparison(_load_fixture(), sheet)
        assert result.verdict == "FAIL"

    def test_flip_sqft_fails(self):
        sheet = _matching_sheet_row()
        sheet["total unit area sq. ft."] = "452"
        result = run_comparison(_load_fixture(), sheet)
        assert result.verdict == "FAIL"

    def test_balcony_present_caveat_not_fail(self):
        sheet = _matching_sheet_row()
        sheet["balcony sq. mt."] = "3.5"
        result = run_comparison(_load_fixture(), sheet)
        sqft = next(f for f in result.fields if f.field_name == "Area (Sq.Ft)")
        assert sqft.status == SCHEMA_CAVEAT
        # All other fields still match -> PASS
        assert result.verdict == "PASS"

    def test_sheet_value_with_indian_comma(self):
        sheet = _matching_sheet_row()
        sheet["agreement value"] = "99,77,517"   # comma-grouped in sheet
        result = run_comparison(_load_fixture(), sheet)
        agr = next(f for f in result.fields if f.field_name == "Agreement Value")
        assert agr.status == MATCH

    def test_sheet_value_with_rs_prefix(self):
        sheet = _matching_sheet_row()
        sheet["agreement value"] = "Rs.9977517/-"
        result = run_comparison(_load_fixture(), sheet)
        agr = next(f for f in result.fields if f.field_name == "Agreement Value")
        assert agr.status == MATCH

    def test_agreement_value_list_match(self):
        """Sheet returns a list for agreement value (merged/duplicate header) — first non-empty wins."""
        sheet = _matching_sheet_row()
        sheet["agreement value"] = ["9977517", "", ""]
        result = run_comparison(_load_fixture(), sheet)
        agr = next(f for f in result.fields if f.field_name == "Agreement Value")
        assert agr.status == MATCH

    def test_agreement_value_list_mismatch(self):
        """When the list value differs from AFS, result must be MISMATCH not NOT_FOUND_IN_SHEET."""
        from comparator import MISMATCH
        sheet = _matching_sheet_row()
        sheet["agreement value"] = ["9979686", "", ""]   # wrong value
        result = run_comparison(_load_fixture(), sheet)
        agr = next(f for f in result.fields if f.field_name == "Agreement Value")
        assert agr.status == MISMATCH, f"Expected MISMATCH, got {agr.status}: {agr.detail}"

    def test_area_sqm_list_match(self):
        """Sheet returns a list for unit area sq.m."""
        sheet = _matching_sheet_row()
        sheet["unit area sq. mt."] = ["42.06", ""]
        result = run_comparison(_load_fixture(), sheet)
        sqm = next(f for f in result.fields if f.field_name == "Area (Sq.M)")
        assert sqm.status == MATCH


# ── sheets.find_unit_row unit tests (using mocked gspread worksheet) ──────────

def _make_ws(header_row, data_rows):
    """Creates a mock gspread Worksheet that returns header + data via get_all_values()."""
    ws = MagicMock()
    ws.get_all_values.return_value = [header_row] + data_rows
    return ws


HEADERS = [
    "Sr.No.", "Unit Type", "Unit No.", "Floor",
    '"Unit Area \n Sq. Mt."', '"Balcony\n Sq. Mt."',
    '"Total Unit Area\n Sq. Mt."', '"Total Unit Area\n Sq. Ft."',
    '"Sold / \nUnsold"', "Unit No.", "Applicants Name",
    "Agreement Value",
]


class TestFindUnitRow:
    def _data_row(self, unit="313", agr="9977517", sqm="42.06", balcony="0",
                  total_sqm="42.06", sqft="453", unit2="313"):
        return ["1", "1BHK", unit, "3", sqm, balcony, total_sqm, sqft, "Sold", unit2,
                "Test Buyer", agr]

    def test_unit_found_returns_dict(self):
        ws = _make_ws(HEADERS, [self._data_row()])
        row = find_unit_row(ws, "313")
        assert isinstance(row, dict)
        assert row.get("agreement value") == "9977517"

    def test_unit_no_duplicate_header_becomes_list(self):
        ws = _make_ws(HEADERS, [self._data_row()])
        row = find_unit_row(ws, "313")
        assert isinstance(row.get("unit no."), list)
        assert len(row["unit no."]) == 2

    def test_unit_not_found_raises(self):
        ws = _make_ws(HEADERS, [self._data_row(unit="313", unit2="313")])
        with pytest.raises(ValueError, match="not found"):
            find_unit_row(ws, "999")

    def test_duplicate_unit_raises(self):
        ws = _make_ws(HEADERS, [self._data_row(), self._data_row()])
        with pytest.raises(ValueError, match="multiple rows"):
            find_unit_row(ws, "313")

    def test_empty_sheet_raises(self):
        ws = MagicMock()
        ws.get_all_values.return_value = []
        with pytest.raises(ValueError, match="fewer than 2 rows"):
            find_unit_row(ws, "313")

    def test_no_unit_no_column_raises(self):
        ws = _make_ws(["Sr.No.", "Unit Type", "Floor"], [["1", "1BHK", "3"]])
        with pytest.raises(ValueError, match="No 'Unit No.'"):
            find_unit_row(ws, "313")

    def test_normalized_headers_resolve_sqm(self):
        ws = _make_ws(HEADERS, [self._data_row()])
        row = find_unit_row(ws, "313")
        assert "unit area sq. mt." in row
        assert "balcony sq. mt." in row
        assert "total unit area sq. ft." in row
