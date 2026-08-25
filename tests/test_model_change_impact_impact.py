"""
test_model_change_impact_impact.py

Tests for model_change_impact/impact.py. Uses hand-built snapshot dicts fed
through the real diff.diff_snapshots() (so the whole snapshot -> diff ->
impact pipeline is exercised) plus a hand-built report_layout-shaped dict
(no real PBIX file needed, matching this repo's existing test convention).
"""
import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from model_change_impact import diff, impact


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


def _base_snapshot():
    return {
        "source_file": "x.pbix",
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
        "relationships": [],
    }


def _report_layout():
    return {
        "pages": [
            {
                "page_id": "p1",
                "display_name": "Overview",
                "visuals": [
                    {
                        "visual_id": "visKpi", "kind": "visual", "visual_type": "card",
                        "kpi_classification": "certain",
                        "fields": [{"kind": "measure", "table": "_Measures", "field": "Total Sales YTD", "role": "Values"}],
                    },
                    {
                        "visual_id": "visChart", "kind": "visual", "visual_type": "columnChart",
                        "kpi_classification": None,
                        "fields": [{"kind": "column", "table": "Sales", "field": "Amount", "role": "Y"}],
                    },
                    {"visual_id": "visGroup", "kind": "visualGroup", "fields": []},
                ],
            }
        ]
    }


def _find(records, table, name=None):
    for r in records:
        detail = r["detail"]
        t = detail.get("table") or detail.get("identity_after", {}).get("table")
        n = detail.get("name") or detail.get("identity_after", {}).get("name")
        if t == table and (name is None or n == name):
            return r
    raise AssertionError(f"no record found for {table}/{name}")


def _visual_ids(record):
    return {v["visual_id"] for v in record["impacted_visuals"]}


class TestDependencyGraph:
    def test_resolves_table_scoped_and_bare_references(self):
        snapshot = _base_snapshot()
        graph = impact.build_dependency_graph(snapshot)
        assert graph[("measure", "_Measures", "Total Sales")] == {("column", "Sales", "Amount")}
        assert graph[("measure", "_Measures", "Total Sales YTD")] == {
            ("measure", "_Measures", "Total Sales"),
            ("column", "Calendar", "Date"),
        }

    def test_calculated_column_bare_reference_resolves_to_own_table(self):
        snapshot = _base_snapshot()
        snapshot["tables"]["Sales"]["columns"].append(
            _column("Margin", expression="[Amount] * 0.1", is_calculated=True)
        )
        graph = impact.build_dependency_graph(snapshot)
        assert graph[("column", "Sales", "Margin")] == {("column", "Sales", "Amount")}


class TestMeasureImpact:
    def test_modified_measure_impacts_transitive_dependent_and_its_visual_only(self):
        baseline = _base_snapshot()
        changed = copy.deepcopy(baseline)
        changed["measures"]["_Measures"]["Total Sales"]["expression"] = "SUM(Sales[Amount]) * 1.1"

        diff_result = diff.diff_snapshots(baseline, changed)
        result = impact.analyze_impact(baseline, changed, diff_result, _report_layout())

        record = _find(result["measures"], "_Measures", "Total Sales")
        assert record["change_type"] == "modified"
        assert {"kind": "measure", "table": "_Measures", "name": "Total Sales YTD"} in record["dependent_objects"]
        assert _visual_ids(record) == {"visKpi"}
        via = {v["visual_id"]: v["matched_via"] for v in record["impacted_visuals"]}
        assert via["visKpi"] == "transitive_dependency"


class TestColumnImpact:
    def test_modified_column_impacts_two_hop_chain_and_both_visuals(self):
        baseline = _base_snapshot()
        changed = copy.deepcopy(baseline)
        changed["tables"]["Sales"]["columns"][0]["format_string"] = "$#,##0"

        diff_result = diff.diff_snapshots(baseline, changed)
        result = impact.analyze_impact(baseline, changed, diff_result, _report_layout())

        record = _find(result["columns"], "Sales", "Amount")
        dep_names = {(d["kind"], d["table"], d["name"]) for d in record["dependent_objects"]}
        assert ("measure", "_Measures", "Total Sales") in dep_names
        assert ("measure", "_Measures", "Total Sales YTD") in dep_names
        assert _visual_ids(record) == {"visChart", "visKpi"}
        via = {v["visual_id"]: v["matched_via"] for v in record["impacted_visuals"]}
        assert via["visChart"] == "direct"
        assert via["visKpi"] == "transitive_dependency"


class TestTableImpact:
    def test_table_level_change_seeds_all_its_columns(self):
        baseline = _base_snapshot()
        changed = copy.deepcopy(baseline)
        changed["tables"]["Sales"]["m_expression"] = "let X = 1 in X"

        diff_result = diff.diff_snapshots(baseline, changed)
        result = impact.analyze_impact(baseline, changed, diff_result, _report_layout())

        record = _find(result["tables"], "Sales")
        assert record["change_type"] == "modified"
        assert _visual_ids(record) == {"visChart", "visKpi"}


class TestRelationshipImpact:
    def test_added_relationship_is_coarse_and_broad(self):
        baseline = _base_snapshot()
        changed = copy.deepcopy(baseline)
        changed["relationships"].append({
            "from_table": "Sales", "from_column": "Region",
            "to_table": "Calendar", "to_column": "Date",
            "is_active": True, "cardinality": "M:1",
            "cross_filtering_behavior": "Single", "rely_on_referential_integrity": False,
        })

        diff_result = diff.diff_snapshots(baseline, changed)
        result = impact.analyze_impact(baseline, changed, diff_result, _report_layout())

        assert len(result["relationships"]) == 1
        record = result["relationships"][0]
        assert record["change_type"] == "added"
        assert _visual_ids(record) == {"visChart", "visKpi"}


class TestRemovedMeasureBrokenBinding:
    def test_removed_measure_still_flags_dangling_visual_binding(self):
        baseline = _base_snapshot()
        baseline["measures"]["_Measures"]["Old KPI"] = _measure("1")
        changed = copy.deepcopy(baseline)
        del changed["measures"]["_Measures"]["Old KPI"]

        layout = _report_layout()
        layout["pages"][0]["visuals"].append({
            "visual_id": "visBroken", "kind": "visual", "visual_type": "card",
            "kpi_classification": "certain",
            "fields": [{"kind": "measure", "table": "_Measures", "field": "Old KPI", "role": "Values"}],
        })

        diff_result = diff.diff_snapshots(baseline, changed)
        result = impact.analyze_impact(baseline, changed, diff_result, layout)

        record = _find(result["measures"], "_Measures", "Old KPI")
        assert record["change_type"] == "removed"
        via = {v["visual_id"]: v["matched_via"] for v in record["impacted_visuals"]}
        assert via["visBroken"] == "direct"
