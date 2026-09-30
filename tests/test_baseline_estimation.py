"""Tests for one-PBIX, selection-based baseline impact estimation."""
from openpyxl import load_workbook

from model_change_impact import baseline_estimation


def _column(name):
    return {
        "name": name, "data_type": "Int64", "is_calculated": False,
        "expression": None, "format_string": None, "is_hidden": False,
        "description": None, "display_folder": None, "lineage_tag": None,
    }


def _measure(expression):
    return {
        "expression": expression, "display_folder": None, "description": None,
        "format_string": None, "is_hidden": False, "lineage_tag": None, "kpi_id": None,
    }


def _snapshot():
    return {
        "source_file": "baseline.pbix",
        "tables": {
            "Sales": {
                "is_calculated_table": False, "m_expression": "M", "is_hidden": False,
                "description": None, "lineage_tag": None,
                "columns": [_column("Amount"), _column("Region")],
            },
            "Calendar": {
                "is_calculated_table": False, "m_expression": "M", "is_hidden": False,
                "description": None, "lineage_tag": None, "columns": [_column("Date")],
            },
        },
        "measures": {
            "_Measures": {
                "Total Sales": _measure("SUM(Sales[Amount])"),
                "Total Sales YTD": _measure("CALCULATE([Total Sales], DATESYTD(Calendar[Date]))"),
                "Unused": _measure("1"),
            },
        },
        "relationships": [{
            "from_table": "Sales", "from_column": "Region",
            "to_table": "Calendar", "to_column": "Date",
            "is_active": True, "cardinality": "M:1",
            "cross_filtering_behavior": "Single", "rely_on_referential_integrity": False,
        }],
    }


def _layout():
    return {
        "format": "PBIR",
        "pages": [{
            "page_id": "p1", "display_name": "Overview",
            "visuals": [
                {
                    "visual_id": "kpi", "display_name": "Sales KPI", "kind": "visual",
                    "visual_type": "card", "kpi_classification": "certain",
                    "fields": [{"kind": "measure", "table": "_Measures",
                                "field": "Total Sales YTD", "role": "Values"}],
                },
                {
                    "visual_id": "chart", "display_name": "Sales Chart", "kind": "visual",
                    "visual_type": "columnChart", "kpi_classification": None,
                    "fields": [{"kind": "column", "table": "Sales",
                                "field": "Amount", "role": "Y"}],
                },
            ],
        }],
    }


def _selection(kind, table=None, name=None, **extra):
    return {"kind": kind, "table": table, "name": name, **extra}


def test_inventory_contains_all_supported_objects():
    objects = baseline_estimation.list_selectable_objects(_snapshot())

    assert [item["kind"] for item in objects].count("table") == 2
    assert [item["kind"] for item in objects].count("measure") == 3
    assert [item["kind"] for item in objects].count("column") == 3
    assert [item["kind"] for item in objects].count("relationship") == 1
    assert any(item["display"] == "_Measures[Total Sales]" for item in objects)


def test_selected_measure_uses_v2_transitive_visual_impact():
    selection = _selection("measure", "_Measures", "Total Sales")
    diff_result, impact_result = baseline_estimation.analyze_selected_objects(
        _snapshot(), _layout(), [selection],
    )

    assert len(diff_result["measures"]["changed"]) == 1
    record = impact_result["measures"][0]
    assert record["change_type"] == "modified"
    assert record["impacted_visuals"][0]["visual_id"] == "kpi"
    assert record["impacted_visuals"][0]["matched_via"] == "transitive_dependency"


def test_selected_column_returns_direct_and_dependency_chain_visuals():
    selection = _selection("column", "Sales", "Amount")
    _, impact_result = baseline_estimation.analyze_selected_objects(
        _snapshot(), _layout(), [selection],
    )

    record = impact_result["columns"][0]
    by_visual = {item["visual_id"]: item["matched_via"] for item in record["impacted_visuals"]}
    assert by_visual == {"chart": "direct", "kpi": "transitive_dependency"}


def test_duplicate_selections_produce_one_change_record():
    selection = _selection("measure", "_Measures", "Total Sales")
    diff_result = baseline_estimation.build_selection_diff(_snapshot(), [selection, selection])

    assert len(diff_result["measures"]["changed"]) == 1


def test_selected_relationship_uses_broad_v2_impact():
    relationship = baseline_estimation.list_selectable_objects(_snapshot())[-1]
    _, impact_result = baseline_estimation.analyze_selected_objects(
        _snapshot(), _layout(), [relationship],
    )

    visual_ids = {item["visual_id"] for item in impact_result["relationships"][0]["impacted_visuals"]}
    assert visual_ids == {"chart", "kpi"}


def test_report_has_consolidated_baseline_grain_and_headers(tmp_path):
    selections = [
        _selection("measure", "_Measures", "Total Sales"),
        _selection("measure", "_Measures", "Unused"),
    ]
    output_path = tmp_path / "baseline.xlsx"

    summary = baseline_estimation.build_report(_snapshot(), _layout(), selections, str(output_path))
    workbook = load_workbook(output_path)
    sheet = workbook["RTM"]

    assert workbook.sheetnames == [
        "RTM", "Requirement Copy", "Visual Impact by Requirement",
    ]
    assert [cell.value for cell in sheet[1]] == [
        "Requirement Number", "Requirement Status", "Visual ID", "Page Name", "Visual Name",
        "Visual Type", "KPI Classification", "Measures", "Columns", "Tables", "Relationships",
        "Visual Description", "Page Filters", "Visual Filters", "Impact Basis", "Object Count",
        "Generated Date", "Generated Time",
    ]
    assert sheet.auto_filter.ref.startswith("A1:Q")
    assert summary["selected_objects"] == 2
    assert summary["impact_rows"] == 0
    assert summary["impacted_visuals"] == 0
    rows = list(sheet.iter_rows(min_row=2, values_only=True))
    assert len(rows) == 2
    by_visual = {row[2]: row for row in rows}
    assert by_visual["kpi"][0] == "RE1_kpi_Overview_001"
    assert by_visual["kpi"][3] == "Overview"
    assert by_visual["kpi"][11] == "Displays _Measures[Total Sales YTD] as a KPI."
    assert by_visual["kpi"][15] and by_visual["kpi"][16]


def test_all_object_report_includes_every_object_and_summary_counts(tmp_path):
    output_path = tmp_path / "all-objects.xlsx"

    summary = baseline_estimation.build_report(_snapshot(), _layout(), str(output_path))
    workbook = load_workbook(output_path)
    baseline = workbook["RTM"]

    assert summary["model_objects"] == 9
    assert baseline.max_row == 3
    rows = list(baseline.iter_rows(min_row=2, values_only=True))
    assert len({row[2] for row in rows}) == 2
    assert any(row[7] or row[8] or row[9] or row[10] for row in rows)


def test_visual_inventory_has_one_row_per_binding_and_name_source(tmp_path):
    layout = _layout()
    layout["pages"][0]["visuals"][0]["display_name"] = None
    layout["pages"][0]["visuals"][0]["display_name_source"] = "Not configured"
    output_path = tmp_path / "visual-inventory.xlsx"

    baseline_estimation.build_report(_snapshot(), layout, str(output_path))
    sheet = load_workbook(output_path)["RTM"]
    rows = list(sheet.iter_rows(min_row=2, values_only=True))

    assert sheet.auto_filter.ref.startswith("A1:Q")
    kpi_row = next(row for row in rows if row[2] == "kpi")
    assert kpi_row[4] == "(Untitled card) _Measures[Total Sales YTD]"
    assert "Displays" in kpi_row[11]


def test_untitled_visual_name_is_explicit_in_impact_summary(tmp_path):
    layout = _layout()
    layout["pages"][0]["visuals"][0]["display_name"] = None
    output_path = tmp_path / "untitled.xlsx"
    selection = _selection("measure", "_Measures", "Total Sales")

    baseline_estimation.build_report(_snapshot(), layout, [selection], str(output_path))
    row = next(
        row for row in load_workbook(output_path)["RTM"].iter_rows(min_row=2, values_only=True)
        if row[2] == "kpi" and row[4])

    assert row[4] == "(Untitled card) _Measures[Total Sales YTD]"


def _write_requirements(path):
    from openpyxl import Workbook
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Requirements"
    sheet.append(["Requirement ID", "Title", "Status", "Priority", "Impacted Visuals"])
    sheet.append(["R-1", "Sales KPI rework", "Approved", "High", "Sales KPI"])
    workbook.save(path)


def test_requirements_driven_report_and_history_round_trip(tmp_path):
    requirements_path = tmp_path / "requirements.xlsx"
    _write_requirements(requirements_path)
    history_db = str(tmp_path / "history" / "impact_history.db")
    output_path = tmp_path / "baseline.xlsx"

    summary = baseline_estimation.build_report(
        _snapshot(), _layout(), str(output_path),
        requirements_path=str(requirements_path), history_db_path=history_db)

    assert summary["requirements"] == 1
    assert summary["mapped_objects"] >= 1
    workbook = load_workbook(output_path)
    assert workbook.sheetnames == [
        "RTM", "Requirement Copy", "Visual Impact by Requirement",
    ]
    requirement_rows = list(workbook["Requirement Copy"].iter_rows(min_row=2, values_only=True))
    assert requirement_rows[0][0] == "R-1"
    impact_rows = list(workbook["Visual Impact by Requirement"].iter_rows(min_row=2, values_only=True))
    assert impact_rows[0][0] == "RE1_kpi_Overview_001"
    assert impact_rows[0][5] == "kpi"
    assert impact_rows[0][7] == "Sales KPI"
    impact_headers = [cell.value for cell in workbook["Visual Impact by Requirement"][1]]
    impact_record = dict(zip(impact_headers, impact_rows[0]))
    assert impact_record["Visual Description"]
    assert impact_record["Generated Date"] and impact_record["Generated Time"]


def test_rtm_marks_latest_requirement_production_and_older_obsolete(tmp_path):
    requirements_path = tmp_path / "requirements.xlsx"
    from openpyxl import Workbook
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Requirements"
    sheet.append([
        "Requirement Number", "Requirement Type", "Raised Date", "Requested By",
        "Business Owner", "Business Area", "Title", "Status", "Impacted Visual IDs",
    ])
    sheet.append([
        "REQ_kpi_Overview_001", "New Requirement", "2026-01-01", "Alice",
        "Finance Owner", "Finance", "Original KPI", "Done", "kpi",
    ])
    sheet.append([
        "REQ_kpi_Overview_002", "Enhancement", "2026-02-01", "Bob",
        "Finance Owner", "Finance", "Updated KPI", "Approved", "kpi",
    ])
    workbook.save(requirements_path)

    output_path = tmp_path / "rtm.xlsx"
    baseline_estimation.build_report(
        _snapshot(), _layout(), str(output_path), requirements_path=str(requirements_path))
    workbook = load_workbook(output_path)
    rows = [dict(zip([cell.value for cell in workbook["RTM"][1]], row))
            for row in workbook["RTM"].iter_rows(min_row=2, values_only=True)]
    kpi_rows = [row for row in rows if row["Visual ID"] == "kpi"]
    statuses = {row["Requirement Number"]: row["Requirement Status"] for row in kpi_rows}
    assert statuses == {
        "RE1_kpi_Overview_001": "Obsolete",
        "RE1_kpi_Overview_002": "Production",
    }


def test_requirement_number_removes_all_page_whitespace(tmp_path):
    output_path = tmp_path / "rtm.xlsx"
    layout = _layout()
    layout["pages"][0]["display_name"] = "Overview / Sales   Detail"
    baseline_estimation.build_report(_snapshot(), layout, str(output_path))
    workbook = load_workbook(output_path)
    rows = list(workbook["RTM"].iter_rows(min_row=2, values_only=True))
    assert rows[0][0] == "RE1_chart_OverviewSalesDetail_001"