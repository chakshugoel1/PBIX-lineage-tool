"""
test_model_change_impact_diff.py

Tests for model_change_impact/diff.py using hand-built baseline/changed
snapshot dicts (same shape as snapshot.build_snapshot() output) - no real
PBIX file needed, matching this repo's existing test convention.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from model_change_impact import diff


def _table(m_expression="M", is_hidden=False, description=None, lineage_tag=None, is_calculated_table=False, columns=None):
    return {
        "is_calculated_table": is_calculated_table,
        "m_expression": m_expression,
        "is_hidden": is_hidden,
        "description": description,
        "lineage_tag": lineage_tag,
        "columns": columns or [],
    }


def _column(name, lineage_tag=None, format_string=None, data_type="Int64", is_calculated=False, expression=None):
    return {
        "name": name,
        "data_type": data_type,
        "is_calculated": is_calculated,
        "expression": expression,
        "format_string": format_string,
        "is_hidden": False,
        "description": None,
        "display_folder": None,
        "lineage_tag": lineage_tag,
    }


def _measure(expression="SUM(1)", display_folder=None, description="", format_string=None, is_hidden=False, lineage_tag=None, kpi_id=None):
    return {
        "expression": expression,
        "display_folder": display_folder,
        "description": description,
        "format_string": format_string,
        "is_hidden": is_hidden,
        "lineage_tag": lineage_tag,
        "kpi_id": kpi_id,
    }


def _relationship(from_table, from_column, to_table, to_column, is_active=True, cardinality="M:1"):
    return {
        "from_table": from_table,
        "from_column": from_column,
        "to_table": to_table,
        "to_column": to_column,
        "is_active": is_active,
        "cardinality": cardinality,
        "cross_filtering_behavior": "Single",
        "rely_on_referential_integrity": False,
    }


class TestTableDiff:
    def test_added_removed_modified_and_renamed(self):
        baseline = {
            "tables": {
                "Unchanged": _table(lineage_tag="tag-u"),
                "Customers": _table(m_expression="let A", lineage_tag="tag-c"),
                "OldTable": _table(lineage_tag="tag-old"),
                "Products": _table(lineage_tag="tag-p"),
            },
            "measures": {}, "relationships": [],
        }
        changed = {
            "tables": {
                "Unchanged": _table(lineage_tag="tag-u"),
                "Customers": _table(m_expression="let B", lineage_tag="tag-c"),
                "NewTable": _table(lineage_tag="tag-new"),
                "ProductsRenamed": _table(lineage_tag="tag-p"),
            },
            "measures": {}, "relationships": [],
        }
        result = diff.diff_snapshots(baseline, changed)["tables"]

        assert [t["table"] for t in result["added"]] == ["NewTable"]
        assert [t["table"] for t in result["removed"]] == ["OldTable"]
        assert result["unchanged_count"] == 1

        by_before_name = {c["identity_before"]["table"]: c for c in result["changed"]}
        modified = by_before_name["Customers"]
        assert modified["matched_by"] == "lineage_tag"
        assert modified["is_rename_candidate"] is False
        assert modified["field_changes"]["m_expression"] == {"before": "let A", "after": "let B"}

        renamed = by_before_name["Products"]
        assert renamed["is_rename_candidate"] is True
        assert renamed["identity_after"]["table"] == "ProductsRenamed"
        assert renamed["field_changes"] == {}


class TestColumnDiff:
    def test_added_removed_modified_renamed_and_moved(self):
        baseline = {
            "tables": {
                "Orders": _table(lineage_tag="t-orders", columns=[
                    _column("Region", lineage_tag="col-region"),
                    _column("OldCol", lineage_tag="col-old"),
                    _column("Amount", lineage_tag="col-amount", format_string=None),
                    _column("Name", lineage_tag="col-name"),
                ]),
            },
            "measures": {}, "relationships": [],
        }
        changed = {
            "tables": {
                "Orders": _table(lineage_tag="t-orders", columns=[
                    _column("Region", lineage_tag="col-region"),
                    _column("NewCol", lineage_tag="col-new"),
                    _column("Amount", lineage_tag="col-amount", format_string="$#,##0"),
                    _column("FullName", lineage_tag="col-name"),
                ]),
                "Contacts": _table(lineage_tag="t-contacts", columns=[]),
            },
            "measures": {}, "relationships": [],
        }
        result = diff.diff_snapshots(baseline, changed)["columns"]

        assert [c["name"] for c in result["added"]] == ["NewCol"]
        assert [c["name"] for c in result["removed"]] == ["OldCol"]
        assert result["unchanged_count"] == 1  # Region

        by_tag = {c["lineage_tag_before"]: c for c in result["changed"]}
        amount = by_tag["col-amount"]
        assert amount["is_rename_candidate"] is False
        assert amount["field_changes"]["format_string"] == {"before": None, "after": "$#,##0"}

        renamed = by_tag["col-name"]
        assert renamed["is_rename_candidate"] is True
        assert renamed["identity_before"] == {"table": "Orders", "name": "Name"}
        assert renamed["identity_after"] == {"table": "Orders", "name": "FullName"}

    def test_column_moved_to_different_table_is_rename_candidate(self):
        baseline = {"tables": {"A": _table(lineage_tag="t-a", columns=[_column("X", lineage_tag="col-x")])},
                     "measures": {}, "relationships": []}
        changed = {"tables": {"A": _table(lineage_tag="t-a", columns=[]),
                               "B": _table(lineage_tag="t-b", columns=[_column("X", lineage_tag="col-x")])},
                    "measures": {}, "relationships": []}
        result = diff.diff_snapshots(baseline, changed)["columns"]
        assert len(result["changed"]) == 1
        moved = result["changed"][0]
        assert moved["is_rename_candidate"] is True
        assert moved["identity_before"]["table"] == "A"
        assert moved["identity_after"]["table"] == "B"


class TestMeasureDiff:
    def test_added_removed_modified_renamed(self):
        baseline = {
            "tables": {}, "relationships": [],
            "measures": {
                "Sales": {
                    "Total Sales": _measure(expression="SUM(A)", lineage_tag="m-total"),
                    "Old Measure": _measure(lineage_tag="m-old"),
                    "Stable": _measure(lineage_tag="m-stable"),
                }
            },
        }
        changed = {
            "tables": {}, "relationships": [],
            "measures": {
                "Sales": {
                    "Total Sales": _measure(expression="SUM(B)", lineage_tag="m-total"),
                    "New Measure": _measure(lineage_tag="m-new"),
                    "Stable": _measure(lineage_tag="m-stable"),
                    "Total Sales Renamed": _measure(lineage_tag="m-total-2", expression="SUM(A)"),
                }
            },
        }
        # add a distinct rename case with its own tag to avoid clashing with the modified one
        baseline["measures"]["Sales"]["ToRename"] = _measure(lineage_tag="m-total-2", expression="SUM(A)")

        result = diff.diff_snapshots(baseline, changed)["measures"]
        added_names = {m["name"] for m in result["added"]}
        removed_names = {m["name"] for m in result["removed"]}
        assert added_names == {"New Measure"}
        assert removed_names == {"Old Measure"}
        assert result["unchanged_count"] == 1  # Stable

        by_tag = {c["lineage_tag_before"]: c for c in result["changed"]}
        modified = by_tag["m-total"]
        assert modified["field_changes"]["expression"] == {"before": "SUM(A)", "after": "SUM(B)"}
        assert modified["is_rename_candidate"] is False

        renamed = by_tag["m-total-2"]
        assert renamed["is_rename_candidate"] is True
        assert renamed["identity_before"] == {"table": "Sales", "name": "ToRename"}
        assert renamed["identity_after"] == {"table": "Sales", "name": "Total Sales Renamed"}


class TestRelationshipDiff:
    def test_added_removed_modified_no_rename_concept(self):
        baseline = {
            "tables": {}, "measures": {},
            "relationships": [
                _relationship("Orders", "CustomerID", "Customers", "ID"),
                _relationship("Orders", "ProductID", "Products", "ID", cardinality="M:1"),
                _relationship("Orders", "RegionID", "Regions", "ID"),
            ],
        }
        changed = {
            "tables": {}, "measures": {},
            "relationships": [
                _relationship("Orders", "CustomerID", "Customers", "ID"),
                _relationship("Orders", "ProductID", "Products", "ID", cardinality="M:M"),
                _relationship("Orders", "DateID", "Dates", "ID"),
            ],
        }
        result = diff.diff_snapshots(baseline, changed)["relationships"]
        assert len(result["added"]) == 1
        assert result["added"][0]["from_column"] == "DateID"
        assert len(result["removed"]) == 1
        assert result["removed"][0]["from_column"] == "RegionID"
        assert result["unchanged_count"] == 1

        assert len(result["changed"]) == 1
        modified = result["changed"][0]
        assert modified["matched_by"] == "key"
        assert modified["is_rename_candidate"] is False
        assert "lineage_tag_before" not in modified
        assert modified["field_changes"]["cardinality"] == {"before": "M:1", "after": "M:M"}
