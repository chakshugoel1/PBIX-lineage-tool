"""Tests for model_change_impact.requirements - the fixed requirements.xlsx
input contract (sheet name, mandatory/recommended columns, scope columns)."""
import datetime

import pytest
from openpyxl import Workbook

from model_change_impact import requirements as req_module

ALL_HEADERS = [
    "Requirement ID", "Title", "Status", "Description", "Priority", "Raised Date",
    "Business Owner", "Business Area", "Expected Change Date",
    "Impacted Pages", "Impacted Visuals", "Impacted Visual IDs", "Notes",
]


def _write_workbook(path, headers, rows, sheet_name="Requirements"):
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name
    ws.append(headers)
    for row in rows:
        ws.append(row)
    wb.save(path)


def test_load_full_row(tmp_path):
    path = tmp_path / "requirements.xlsx"
    _write_workbook(path, ALL_HEADERS, [[
        "R-1", "Headcount card rework", "Approved", "Update the headcount KPI cards",
        "Critical", datetime.date(2026, 9, 1), "Alice", "Finance",
        datetime.date(2026, 10, 1), "CXO, DU Head", "HeadcountPrevYearvalue",
        "6897a41505887576b092", "first note",
    ]])
    requirements, warnings = req_module.load_requirements(path)
    assert warnings == []
    assert len(requirements) == 1
    req = requirements[0]
    assert req["id"] == "R-1"
    assert req["title"] == "Headcount card rework"
    assert req["status"] == "Approved"
    assert req["active"] is True
    assert req["priority"] == "Critical"
    assert req["raised_date"] == "2026-09-01"
    assert req["expected_change_date"] == "2026-10-01"
    assert req["business_owner"] == "Alice"
    assert req["business_area"] == "Finance"
    assert req["impacted_pages"] == "CXO, DU Head"
    assert req["impacted_visuals"] == "HeadcountPrevYearvalue"
    assert req["impacted_visual_ids"] == "6897a41505887576b092"
    assert req["notes"] == "first note"


def test_scope_columns_are_optional(tmp_path):
    path = tmp_path / "requirements.xlsx"
    _write_workbook(path, ["Requirement ID", "Title", "Status"], [["R-1", "T", "Proposed"]])
    requirements, _warnings = req_module.load_requirements(path)
    assert requirements[0]["impacted_pages"] == ""
    assert requirements[0]["impacted_visuals"] == ""
    assert requirements[0]["impacted_visual_ids"] == ""


def test_missing_sheet_fails(tmp_path):
    path = tmp_path / "requirements.xlsx"
    _write_workbook(path, ALL_HEADERS, [], sheet_name="OtherSheet")
    with pytest.raises(req_module.RequirementsError, match="Requirements"):
        req_module.load_requirements(path)


def test_missing_mandatory_column_fails(tmp_path):
    path = tmp_path / "requirements.xlsx"
    headers = [h for h in ALL_HEADERS if h != "Title"]
    _write_workbook(path, headers, [])
    with pytest.raises(req_module.RequirementsError, match="Title"):
        req_module.load_requirements(path)


def test_duplicate_id_fails(tmp_path):
    path = tmp_path / "requirements.xlsx"
    _write_workbook(path, ALL_HEADERS, [
        ["R-1", "First", "Proposed"],
        ["r-1", "Second", "Proposed"],  # case-insensitive duplicate
    ])
    with pytest.raises(req_module.RequirementsError, match="Duplicate"):
        req_module.load_requirements(path)


def test_empty_mandatory_cell_fails_with_row_number(tmp_path):
    path = tmp_path / "requirements.xlsx"
    _write_workbook(path, ALL_HEADERS, [
        ["R-1", "Valid", "Proposed"],
        ["R-2", "", "Proposed"],
    ])
    with pytest.raises(req_module.RequirementsError, match="Row 3"):
        req_module.load_requirements(path)


def test_unknown_column_warns_and_is_ignored(tmp_path):
    path = tmp_path / "requirements.xlsx"
    _write_workbook(path, ALL_HEADERS + ["Jira Link"], [
        ["R-1", "Title", "Proposed", "", "", "", "", "", "", "", "", "", "ABC-123"],
    ])
    requirements, warnings = req_module.load_requirements(path)
    assert any("Jira Link" in warning for warning in warnings)
    assert len(requirements) == 1


def test_missing_recommended_columns_warn_and_default(tmp_path):
    path = tmp_path / "requirements.xlsx"
    _write_workbook(path, ["Requirement ID", "Title", "Status"], [["R-1", "T", "Proposed"]])
    requirements, warnings = req_module.load_requirements(path)
    assert any("Description" in warning for warning in warnings)
    assert any("Priority" in warning for warning in warnings)
    assert any("Raised Date" in warning for warning in warnings)
    assert requirements[0]["priority"] == "Medium"


def test_invalid_priority_falls_back_to_medium(tmp_path):
    path = tmp_path / "requirements.xlsx"
    _write_workbook(path, ALL_HEADERS, [["R-1", "T", "Proposed", "", "Urgent"]])
    requirements, warnings = req_module.load_requirements(path)
    assert any("Priority" in warning for warning in warnings)
    assert requirements[0]["priority"] == "Medium"


def test_inactive_statuses_and_case_normalization(tmp_path):
    path = tmp_path / "requirements.xlsx"
    _write_workbook(path, ALL_HEADERS, [
        ["R-1", "Done item", "done"],
        ["R-2", "Cancelled item", "CANCELLED"],
        ["R-3", "Active item", "in progress"],
    ])
    requirements, _warnings = req_module.load_requirements(path)
    assert requirements[0]["status"] == "Done" and requirements[0]["active"] is False
    assert requirements[1]["status"] == "Cancelled" and requirements[1]["active"] is False
    assert requirements[2]["status"] == "In Progress" and requirements[2]["active"] is True


def test_invalid_date_warns_and_blanks(tmp_path):
    path = tmp_path / "requirements.xlsx"
    _write_workbook(path, ALL_HEADERS, [["R-1", "T", "Proposed", "", "", "not-a-date"]])
    requirements, warnings = req_module.load_requirements(path)
    assert any("Raised Date" in warning for warning in warnings)
    assert requirements[0]["raised_date"] == ""
