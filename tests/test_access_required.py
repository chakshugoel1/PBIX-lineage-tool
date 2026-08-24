import json

from core import lineage_lib as ll
from reporting import lineage_report as report


def test_guid_cache_supports_legacy_and_rich_entries(tmp_path):
    path = tmp_path / "guid_cache.json"
    path.write_text(json.dumps({
        "legacy/dataflow": "Legacy Dataflow",
        "rich/dataflow": {
            "workspace_name": "Finance Production",
            "dataflow_name": "Sales Dataflow",
        },
    }), encoding="utf-8")

    cache = ll.load_guid_cache(str(path))

    assert ll.guid_cache_dataflow_name(cache, "legacy/dataflow") == "Legacy Dataflow"
    assert ll.guid_cache_workspace_name(cache, "legacy/dataflow") is None
    assert ll.guid_cache_dataflow_name(cache, "rich/dataflow") == "Sales Dataflow"
    assert ll.guid_cache_workspace_name(cache, "rich/dataflow") == "Finance Production"


def test_missing_dataflow_reports_required_workspace_access(monkeypatch):
    level1 = {
        "workspace": "Finance Production",
        "workspace_id": "workspace-guid",
        "dataflow": "Sales Dataflow",
        "dataflow_id": "dataflow-guid",
        "entity": "FactSales",
        "method": "direct",
        "path": [],
    }
    context = {
        "entries": {"Sales": "let Source = 1 in Source"},
        "pbix_universe": object(),
        "direct": {},
        "entity_of": {},
        "dataflows": {},
        "entity_index": {},
        "name_index": {},
        "guid_cache": {},
    }
    monkeypatch.setattr(report.ll, "resolve_pbix_lineage", lambda *args: level1)
    monkeypatch.setattr(
        report.ll,
        "resolve_physical_source",
        lambda *args, **kwargs: {"unresolved": True, "reason": "Dataflow file for 'Sales Dataflow' not found among provided files.", "hops": []},
    )

    result = report.resolve_table_row("Sales", context)

    assert result["override_tag"] == "ACCESS REQUIRED - DATAFLOW NOT AVAILABLE"
    assert result["access_request"] == {
        "workspace": "Finance Production",
        "workspace_id": "workspace-guid",
        "dataflow": "Sales Dataflow",
        "dataflow_id": "dataflow-guid",
        "entity": "FactSales",
    }
    assert "Workspace: Finance Production" in result["remarks"]
    assert "Workspace ID: workspace-guid" in result["remarks"]
    assert "Dataflow: Sales Dataflow" in result["remarks"]
    assert "Request access to this Power BI workspace" in result["remarks"]


def test_guid_only_access_request_does_not_repeat_placeholder_names():
    request = report._access_request_from_level1({
        "workspace": "(workspaceId GUID) workspace-guid",
        "workspace_id": "workspace-guid",
        "dataflow": "(dataflowId GUID) dataflow-guid",
        "dataflow_id": "dataflow-guid",
    }, "FactSales")

    remarks = report._format_access_request(request)

    assert "Workspace: (workspaceId GUID)" not in remarks
    assert "Dataflow: (dataflowId GUID)" not in remarks
    assert "Workspace ID: workspace-guid" in remarks
    assert "Dataflow ID: dataflow-guid" in remarks
