"""
test_model_change_impact_excel_report.py

End-to-end test for model_change_impact/excel_report.py: runs hand-built
snapshots through the real diff.diff_snapshots() and impact.analyze_impact()
(same as test_model_change_impact_impact.py), builds the Excel workbook into
a pytest tmp_path, and reads it back with openpyxl to verify sheet layout
and content. No real PBIX file needed, matching this repo's test convention.
"""
import copy
import os
import sys

import openpyxl
from openpyxl.cell.rich_text import CellRichText

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from model_change_impact import diff, impact, excel_report


def _column(name, expression=None, is_calculated=False, format_string=None):
    return {
        "name": name, "data_type": "Int64", "is_calculated": is_calculated,
        "expression": expression, "format_string": format_string, "is_hidden": False,
        "description": None, "display_folder": None, "lineage_tag": f"col-{name}",
    }


def _table(m_expression="M", columns=None):
    return {
        "is_calculated_table": False, "m_expression": m_expression, "is_hidden": False,
        "description": None, "lineage_tag": None, "columns": columns or [],
    }


def _measure(expression):
    return {
        "expression": expression, "display_folder": None, "description": "",
        "format_string": None, "is_hidden": False, "lineage_tag": None, "kpi_id": None,
    }


def _base_snapshot(source_file):
    return {
        "source_file": source_file,
        "tables": {
            "Sales": _table(columns=[_column("Amount"), _column("Region")]),
            "Calendar": _table(columns=[_column("Date")]),
        },
        "measures": {
            "_Measures": {
                "Total Sales": _measure("SUM(Sales[Amount])"),
                "Total Sales YTD": _measure("CALCULATE([Total Sales], DATESYTD(Calendar[Date]))"),
            }
        },
        "calculated_columns": [],
        "relationships": [
            {"from_table": "Sales", "from_column": "RegionID", "to_table": "Calendar", "to_column": "ID",
             "is_active": True, "cardinality": "M:1", "cross_filtering_behavior": "Single",
             "rely_on_referential_integrity": False},
        ],
    }


def _report_layout():
    return {
        "format": "pbir",
        "pages": [{
            "page_id": "p1", "display_name": "Overview",
            "visuals": [
                {"visual_id": "visKpi", "kind": "visual", "visual_type": "card", "kpi_classification": "certain",
                 "fields": [{"kind": "measure", "table": "_Measures", "field": "Total Sales YTD", "role": "Values"}]},
                {"visual_id": "visChart", "kind": "visual", "visual_type": "columnChart", "kpi_classification": None,
                 "fields": [{"kind": "column", "table": "Sales", "field": "Amount", "role": "Y"}]},
                {"visual_id": "visKpiCustom", "kind": "visual", "visual_type": "advanceCardXYZ123",
                 "kpi_classification": "heuristic",
                 "fields": [{"kind": "measure", "table": "_Measures", "field": "Total Sales", "role": "Values"}]},
            ],
        }],
    }


def _build_report(tmp_path):
    baseline = _base_snapshot("baseline.pbix")
    changed = copy.deepcopy(baseline)
    changed["source_file"] = "changed.pbix"
    changed["measures"]["_Measures"]["Total Sales"]["expression"] = "SUM(Sales[Amount]) * 1.1"
    changed["relationships"][0]["cardinality"] = "M:M"

    layout = _report_layout()
    diff_result = diff.diff_snapshots(baseline, changed)
    impact_result = impact.analyze_impact(baseline, changed, diff_result, layout)

    output_path = str(tmp_path / "model_change_impact.xlsx")
    excel_report.build_excel_report(baseline, changed, diff_result, impact_result, layout, output_path)
    return output_path


def test_builds_all_expected_sheets_in_order(tmp_path):
    wb = openpyxl.load_workbook(_build_report(tmp_path))
    assert wb.sheetnames == [
        "Summary", "Changed Tables", "Changed Measures", "Changed Columns",
        "Changed Relationships", "Visual Impact", "KPI Impact",
        "Playwright Input", "Manual Review", "Object Inventory",
    ]


def test_summary_sheet_has_file_names_and_counts(tmp_path):
    wb = openpyxl.load_workbook(_build_report(tmp_path))
    ws = wb["Summary"]
    assert ws["B2"].value == "baseline.pbix"
    assert ws["B3"].value == "changed.pbix"
    # header row for the counts table
    headers = [ws.cell(row=6, column=c).value for c in range(1, 8)]
    assert headers == ["Entity", "Added", "Removed", "Changed", "Rename Candidates", "Unchanged", "Detail Sheet"]
    # find the "measures" row and check its "Changed" count is 1
    measures_row = next(r for r in range(7, 11) if ws.cell(row=r, column=1).value == "Measures")
    assert ws.cell(row=measures_row, column=4).value == 1


def test_changed_measures_sheet_has_the_modified_measure(tmp_path):
    wb = openpyxl.load_workbook(_build_report(tmp_path))
    ws = wb["Changed Measures"]
    rows = list(ws.iter_rows(min_row=2, values_only=True))
    assert any(r[0] == "_Measures" and r[1] == "Total Sales" and r[2] == "Modified" for r in rows)


def test_playwright_input_severity_reflects_kpi_classification(tmp_path):
    wb = openpyxl.load_workbook(_build_report(tmp_path))
    ws = wb["Playwright Input"]
    rows = list(ws.iter_rows(min_row=2, values_only=True))
    assert len(rows) == 3  # one deduped row per visual, not one per changed object
    by_visual = {r[1]: r for r in rows}
    # visKpi has kpi_classification "certain" -> always "High".
    assert by_visual["visKpi"][3] == "High"
    # visChart has no KPI classification and is only reachable via the relationship's
    # broad, table-wide seeding (not a direct binding) -> demoted to "Low", not "Medium"/"High".
    # This is the concrete scenario the tool must get right: an unrelated change
    # (e.g. a relationship edit) must not make an unrelated visual look as urgent
    # as a genuinely, directly affected one.
    assert by_visual["visChart"][3] == "Low"
    assert by_visual["visChart"][4] == "Broad (table/relationship-level)"
    # visKpiCustom is directly bound to the modified measure and only heuristically
    # classified as a KPI (custom visual type name) -> "Medium" (direct basis, not certain KPI).
    assert by_visual["visKpiCustom"][3] == "Medium"


def test_manual_review_flags_relationship_change_and_heuristic_kpi(tmp_path):
    wb = openpyxl.load_workbook(_build_report(tmp_path))
    ws = wb["Manual Review"]
    categories = [ws.cell(row=r, column=1).value for r in range(2, ws.max_row + 1)]
    assert "Relationship" in categories
    assert "KPI classification" in categories


def test_object_inventory_lists_current_model_objects(tmp_path):
    wb = openpyxl.load_workbook(_build_report(tmp_path))
    ws = wb["Object Inventory"]
    rows = list(ws.iter_rows(min_row=2, values_only=True))
    assert any(r[0] == "Table" and r[1] == "Sales" for r in rows)
    assert any(r[0] == "Measure" and r[2] == "Total Sales YTD" for r in rows)
    assert any(r[0] == "Relationship" for r in rows)


def _cell_text(value):
    if isinstance(value, CellRichText):
        return "".join(str(part) for part in value)
    return value


def test_changed_measure_shows_full_before_after_with_highlighting(tmp_path):
    wb = openpyxl.load_workbook(_build_report(tmp_path), rich_text=True)
    ws = wb["Changed Measures"]
    rows = list(ws.iter_rows(min_row=2, values_only=True))
    row = next(r for r in rows if r[0] == "_Measures" and r[1] == "Total Sales" and r[4] == "expression")
    before, after = row[5], row[6]
    # After changed (new tokens added) -> highlighted rich text; before is unchanged -> plain text.
    assert _cell_text(before) == "SUM(Sales[Amount])"
    assert isinstance(after, CellRichText)
    assert _cell_text(after) == "SUM(Sales[Amount]) * 1.1"


def test_long_dax_expression_is_not_truncated(tmp_path):
    baseline = _base_snapshot("baseline.pbix")
    changed = copy.deepcopy(baseline)
    changed["source_file"] = "changed.pbix"
    long_expr = "SWITCH(TRUE(), " + ", ".join(f"Sales[Region] = \"R{i}\", {i}" for i in range(60)) + ", 0)"
    assert len(long_expr) > 500
    changed["measures"]["_Measures"]["Total Sales"]["expression"] = long_expr

    layout = _report_layout()
    diff_result = diff.diff_snapshots(baseline, changed)
    impact_result = impact.analyze_impact(baseline, changed, diff_result, layout)
    output_path = str(tmp_path / "long_expr.xlsx")
    excel_report.build_excel_report(baseline, changed, diff_result, impact_result, layout, output_path)

    wb = openpyxl.load_workbook(output_path, rich_text=True)
    ws = wb["Changed Measures"]
    rows = list(ws.iter_rows(min_row=2, values_only=True))
    row = next(r for r in rows if r[1] == "Total Sales" and r[4] == "expression")
    after_text = _cell_text(row[6])
    assert after_text == long_expr
    assert "..." not in after_text


def test_visual_impact_sheet_is_grouped_by_page_and_deduped_per_visual(tmp_path):
    wb = openpyxl.load_workbook(_build_report(tmp_path))
    ws = wb["Visual Impact"]
    assert ws.cell(row=2, column=1).value == "Page: Overview"

    data_rows = list(ws.iter_rows(min_row=3, values_only=True))
    visual_ids = [r[1] for r in data_rows]
    assert visual_ids == sorted(visual_ids)
    # each visual appears exactly once, even though several are touched by more
    # than one root-cause change (the measure change and the relationship change).
    assert len(visual_ids) == len(set(visual_ids)) == 3


def test_visual_impact_basis_distinguishes_direct_from_broad(tmp_path):
    wb = openpyxl.load_workbook(_build_report(tmp_path))
    ws = wb["Visual Impact"]
    by_visual = {r[1]: r for r in ws.iter_rows(min_row=3, values_only=True)}
    # visKpiCustom is bound directly to the modified measure -> "Direct".
    assert by_visual["visKpiCustom"][4] == "Direct"
    # visChart is only reachable through the relationship's broad, whole-table seeding.
    assert by_visual["visChart"][4] == "Broad (table/relationship-level)"


def test_kpi_impact_sheet_only_lists_kpi_visuals_grouped_by_page(tmp_path):
    wb = openpyxl.load_workbook(_build_report(tmp_path))
    ws = wb["KPI Impact"]
    assert ws.cell(row=2, column=1).value == "Page: Overview"
    visual_ids = [r[1] for r in ws.iter_rows(min_row=3, values_only=True)]
    assert set(visual_ids) == {"visKpi", "visKpiCustom"}


def test_manual_review_notes_cardinality_only_relationship_change(tmp_path):
    wb = openpyxl.load_workbook(_build_report(tmp_path))
    ws = wb["Manual Review"]
    rows = list(ws.iter_rows(min_row=2, values_only=True))
    relationship_row = next(r for r in rows if r[0] == "Relationship")
    assert "data refresh" in relationship_row[2].lower()
