import openpyxl
import pandas as pd

from reporting import lineage_report as report


def _row(**overrides):
    row = {
        "status": "found",
        "is_used": True,
        "hard_unresolved": False,
        "needs_override": False,
        "entities_used": "Entity Name: Sales",
        "level1": "Level 1",
        "level2": "Level 2",
        "final_source": "Final Source",
        "folder": "Folder",
        "file": "File",
        "remarks": "Remark",
        "dataflow_stems": set(),
    }
    row.update(overrides)
    return row


def test_main_report_uses_requested_column_order(monkeypatch, tmp_path):
    monkeypatch.setattr(report.btr, "add_transformations_sheet", lambda workbook, rows, ctx: None)
    output_path = tmp_path / "main.xlsx"
    report.write_workbook([_row()], {"pbix_path": "CashPlus.pbix", "dataflows": {}}, str(output_path))

    worksheet = openpyxl.load_workbook(output_path).active
    headers = [worksheet.cell(row=1, column=column).value for column in range(1, 10)]
    assert headers == [
        "Report Name", "Status", "Remarks", "Entities Used across report",
        "Source - Level 1", "Source - Level 2", "Final Source", "Folder PATH", "File Name",
    ]
    assert worksheet.cell(row=2, column=1).value == "Report: CashPlus\nDownloaded: WKS DTF FINANCE"
    assert worksheet.cell(row=2, column=2).value == "Found Source [Automatically]"
    assert worksheet.cell(row=2, column=3).value == "Remark"
    assert worksheet.cell(row=2, column=4).value == "Entity Name: Sales"
    assert worksheet.cell(row=2, column=5).value == "Level 1"


def test_unrecognized_connector_is_described_as_m_query_review(monkeypatch):
    context = {
        "entries": {"Sales": "let Source = PowerPlatform.Dataflows(null) in Source"},
        "pbix_universe": object(),
        "direct": {},
        "entity_of": {},
        "unrecognized_dataflow_patterns": [{"query": "Sales", "snippet": "..."}],
    }
    monkeypatch.setattr(report.ll, "resolve_pbix_lineage", lambda *args: None)
    monkeypatch.setattr(report.ll, "chain_hits_unrecognized", lambda *args: True)

    result = report.resolve_table_row("Sales", context)

    assert result["override_tag"] == "M QUERY REFERENCE NOT EXPLICIT"
    assert "dynamically constructed, indirect, or not explicitly stated" in result["remarks"]
    assert "tool" not in result["remarks"].lower()


def test_report_name_is_repeated_for_each_data_row(monkeypatch, tmp_path):
    monkeypatch.setattr(report.btr, "add_transformations_sheet", lambda workbook, rows, ctx: None)
    output_path = tmp_path / "main.xlsx"
    report.write_workbook([_row(), _row(entities_used="Entity Name: Finance")],
                          {"pbix_path": "CashPlus.pbix", "dataflows": {}}, str(output_path))

    worksheet = openpyxl.load_workbook(output_path).active
    assert worksheet.cell(row=2, column=1).value == worksheet.cell(row=3, column=1).value


def test_slicer_only_table_is_used_and_flagged():
    class Model:
        tables = ["Slicer Table", "Unused Table"]
        relationships = pd.DataFrame()
        dax_measures = pd.DataFrame()
        dax_columns = pd.DataFrame()
        schema = pd.DataFrame()

    layout = {
        "pages": [{
            "filters": [],
            "visuals": [{
                "visual_type": "slicer",
                "fields": [{"table": "Slicer Table", "field": "Category", "kind": "column"}],
                "filters": [],
            }],
        }]
    }

    usage = report._build_table_usage(Model(), layout)

    assert report._usage_reason_from_flags(usage["Slicer Table"]) == (
        "Present in semantic model; used only in slicer"
    )
    assert report._is_slicer_only_usage(usage["Slicer Table"])
    assert report._usage_reason_from_flags(usage["Unused Table"]) == (
        "Present in semantic model; no relationship, DAX, visual, slicer, or filter usage detected"
    )


def test_report_visual_and_filter_usage_reasons_are_included():
    class Model:
        tables = ["Visual Table", "Page Filter Table", "Report Filter Table", "Visual Filter Table"]
        relationships = pd.DataFrame()
        dax_measures = pd.DataFrame()
        dax_columns = pd.DataFrame()
        schema = pd.DataFrame()

    layout = {
        "filters": [{"fields": [{"table": "Report Filter Table"}]}],
        "pages": [{
            "filters": [{"fields": [{"table": "Page Filter Table"}]}],
            "visuals": [{
                "visual_type": "tableEx",
                "fields": [{"table": "Visual Table", "field": "Amount", "kind": "column"}],
                "filters": [{"fields": [{"table": "Visual Filter Table"}]}],
            }],
        }]
    }

    usage = report._build_table_usage(Model(), layout)

    assert report._usage_reason_from_flags(usage["Visual Table"]) == (
        "Present in semantic model; Used in chart/table visual"
    )
    assert report._usage_reason_from_flags(usage["Page Filter Table"]) == (
        "Present in semantic model; Used in page filter"
    )
    assert report._usage_reason_from_flags(usage["Report Filter Table"]) == (
        "Present in semantic model; Used in report filter"
    )
    assert report._usage_reason_from_flags(usage["Visual Filter Table"]) == (
        "Present in semantic model; Used in visual filter"
    )


def test_table_used_in_slicer_and_visual_is_not_slicer_only():
    class Model:
        tables = ["Shared Table"]
        relationships = pd.DataFrame()
        dax_measures = pd.DataFrame()
        dax_columns = pd.DataFrame()
        schema = pd.DataFrame()

    layout = {
        "pages": [{
            "filters": [],
            "visuals": [
                {"visual_type": "slicer", "fields": [{"table": "Shared Table"}], "filters": []},
                {"visual_type": "tableEx", "fields": [{"table": "Shared Table"}], "filters": []},
            ],
        }]
    }

    usage = report._build_table_usage(Model(), layout)

    assert not report._is_slicer_only_usage(usage["Shared Table"])
    assert report._unused_sheet_category({
        "is_used": True,
        "usage_flags": usage["Shared Table"],
    }) is None


def test_table_referenced_by_another_table_dax_formula_is_used():
    class Model:
        tables = ["Referenced Table", "Owner Table"]
        relationships = pd.DataFrame()
        dax_measures = pd.DataFrame([
            {"TableName": "Owner Table", "Expression": "SUM('Referenced Table'[Amount])"},
        ])
        dax_columns = pd.DataFrame()
        schema = pd.DataFrame([
            {"TableName": "Referenced Table", "ColumnName": "Amount"},
        ])

    usage = report._build_table_usage(Model())

    assert report._usage_reason_from_flags(usage["Referenced Table"]) == (
        "Present in semantic model; Referenced by another measure/calculated column's DAX formula"
    )


def test_self_referencing_calculated_column_does_not_count_as_used():
    class Model:
        tables = ["Self Table"]
        relationships = pd.DataFrame()
        dax_measures = pd.DataFrame()
        dax_columns = pd.DataFrame([
            {"TableName": "Self Table", "Expression": "'Self Table'[Year] * 100"},
        ])
        schema = pd.DataFrame([
            {"TableName": "Self Table", "ColumnName": "Year"},
        ])

    usage = report._build_table_usage(Model())

    assert report._usage_reason_from_flags(usage["Self Table"]) == (
        "Present in semantic model; no relationship, DAX, visual, slicer, or filter usage detected"
    )


def test_unused_tables_sheet_includes_reason_and_excludes_slicer_only(monkeypatch, tmp_path):
    monkeypatch.setattr(report.btr, "add_transformations_sheet", lambda workbook, rows, ctx: None)
    output_path = tmp_path / "main.xlsx"
    rows = [
        _row(table="Slicer Table", is_used=True, is_slicer_only=True, usage_reason="Used only in slicer"),
        _row(
            table="Semantic Table",
            is_used=True,
            usage_flags={key: key == "semantic_model" for key in report.USAGE_KEYS},
            usage_reason="Present in semantic model; no relationship, DAX, visual, slicer, or filter usage detected",
        ),
    ]

    report.write_workbook(rows, {
        "pbix_path": "CashPlus.pbix",
        "dataflows": {},
        "report_layout": {
            "filters": [],
            "pages": [{
                "filters": [],
                "visuals": [{
                    "visual_type": "slicer",
                    "fields": [{"table": "Orphan Slicer Table"}],
                    "filters": [],
                }],
            }],
        },
    }, str(output_path))

    workbook = openpyxl.load_workbook(output_path)
    main_sheet = workbook.active
    unused_sheet = workbook["Unused tables"]
    assert main_sheet.cell(row=2, column=2).value == "Used only in slicer"
    assert [unused_sheet.cell(row=1, column=column).value for column in range(1, 4)] == [
        "Category", "Table Name", "Reason"
    ]
    assert [unused_sheet.cell(row=2, column=column).value for column in range(1, 4)] == [
        "Used only in slicer", "Slicer Table", "Used only in slicer"
    ]
    assert [unused_sheet.cell(row=3, column=column).value for column in range(1, 4)] == [
        "Semantic model only", "Semantic Table",
        "Present in semantic model; no relationship, DAX, visual, slicer, or filter usage detected",
    ]
    assert [unused_sheet.cell(row=4, column=column).value for column in range(1, 4)] == [
        "Used only in slicer (not in semantic model)", "Orphan Slicer Table",
        "Used only in slicer; table not found in semantic model.",
    ]
    assert unused_sheet.cell(row=5, column=1).value is None
