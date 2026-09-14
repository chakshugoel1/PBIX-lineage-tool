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


def test_report_has_exact_v2_impact_summary_grain_and_headers(tmp_path):
    selections = [
        _selection("measure", "_Measures", "Total Sales"),
        _selection("measure", "_Measures", "Unused"),
    ]
    output_path = tmp_path / "baseline.xlsx"

    summary = baseline_estimation.build_report(_snapshot(), _layout(), selections, str(output_path))
    workbook = load_workbook(output_path)
    sheet = workbook["Impact Summary"]

    assert workbook.sheetnames == ["Object Summary", "Impact Summary", "Model Inventory", "Visual Inventory"]
    assert [cell.value for cell in sheet[1]] == [
        "Changed Object Type", "Changed Object", "Change Type", "Affected Visual ID",
        "Affected Visual Name", "Visual Type", "Page Name", "Is KPI", "KPI Confidence",
        "Impact Basis", "Actual Report Change",
    ]
    assert sheet.auto_filter.ref == "A1:K3"
    assert summary["selected_objects"] == 2
    assert summary["impact_rows"] == 2
    assert summary["impacted_visuals"] == 1
    rows = list(sheet.iter_rows(min_row=2, values_only=True))
    by_object = {row[1]: row for row in rows}
    assert by_object["_Measures[Total Sales]"][4] == "Sales KPI"
    assert by_object["_Measures[Total Sales]"][9] == "Dependency chain"
    assert by_object["_Measures[Unused]"][9] == "No visual binding found"


def test_all_object_report_includes_every_object_and_summary_counts(tmp_path):
    output_path = tmp_path / "all-objects.xlsx"

    summary = baseline_estimation.build_report(_snapshot(), _layout(), str(output_path))
    workbook = load_workbook(output_path)
    object_summary = workbook["Object Summary"]
    model_inventory = workbook["Model Inventory"]

    assert summary["model_objects"] == 9
    assert object_summary.max_row == 10
    assert object_summary.auto_filter.ref == "A1:J10"
    assert model_inventory.max_row == 10
    assert model_inventory.auto_filter.ref == "A1:M10"
    rows = list(object_summary.iter_rows(min_row=2, values_only=True))
    total_sales = next(row for row in rows if row[1] == "_Measures[Total Sales]")
    assert total_sales[2:7] == (0, 1, 1, 1, 1)


def test_visual_inventory_has_one_row_per_binding_and_name_source(tmp_path):
    layout = _layout()
    layout["pages"][0]["visuals"][0]["display_name"] = None
    layout["pages"][0]["visuals"][0]["display_name_source"] = "Not configured"
    output_path = tmp_path / "visual-inventory.xlsx"

    baseline_estimation.build_report(_snapshot(), layout, str(output_path))
    sheet = load_workbook(output_path)["Visual Inventory"]
    rows = list(sheet.iter_rows(min_row=2, values_only=True))

    assert sheet.auto_filter.ref == "A1:K3"
    kpi_row = next(row for row in rows if row[3] == "kpi")
    assert kpi_row[1] == "(Untitled card) _Measures[Total Sales YTD]"
    assert kpi_row[2] == "Generated from visual type and binding"


def test_untitled_visual_name_is_explicit_in_impact_summary(tmp_path):
    layout = _layout()
    layout["pages"][0]["visuals"][0]["display_name"] = None
    output_path = tmp_path / "untitled.xlsx"
    selection = _selection("measure", "_Measures", "Total Sales")

    baseline_estimation.build_report(_snapshot(), layout, [selection], str(output_path))
    row = next(load_workbook(output_path)["Impact Summary"].iter_rows(min_row=2, values_only=True))

    assert row[4] == "(Untitled card) _Measures[Total Sales YTD]"


def _write_requirements(path):
    from openpyxl import Workbook
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Requirements"
    sheet.append(["Requirement ID", "Title", "Status", "Priority", "Seed Objects"])
    sheet.append(["R-1", "Sales KPI rework", "Approved", "High", "measure:Total Sales"])
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
        "Object Summary", "Impact Summary", "Model Inventory", "Visual Inventory",
        "Requirement Summary", "Requirement Traceability",
        "Object Change History", "Unmapped Changes",
    ]
    requirement_summary = list(
        workbook["Requirement Summary"].iter_rows(min_row=2, values_only=True))
    assert requirement_summary[0][0] == "R-1"
    assert requirement_summary[0][7] == 2  # Impacted Measures: seed + YTD dependency

    # Second run with a changed measure expression -> attributed Modified event.
    changed = _snapshot()
    changed["measures"]["_Measures"]["Total Sales"] = _measure("SUMX(Sales, Sales[Amount])")
    summary = baseline_estimation.build_report(
        changed, _layout(), str(output_path),
        requirements_path=str(requirements_path), history_db_path=history_db)

    history_rows = list(load_workbook(output_path)["Object Change History"].iter_rows(
        min_row=2, values_only=True))
    modified = next(row for row in history_rows if row[3] == "Modified")
    assert modified[1] == "_Measures[Total Sales]"
    assert modified[7] == "R-1"
    assert summary["unmapped_changes"] == 0