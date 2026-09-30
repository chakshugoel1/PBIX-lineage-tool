"""Tests for model_change_impact.requirement_mapping - page/visual/ID-grain
requirement mapping with bound-field seeding and dependency expansion."""
from model_change_impact import requirement_mapping


def _column(name):
    return {
        "name": name, "data_type": "Int64", "is_calculated": False, "expression": None,
        "format_string": None, "is_hidden": False, "description": None,
        "display_folder": None, "lineage_tag": None,
    }


def _measure(expression):
    return {
        "expression": expression, "display_folder": "", "description": "",
        "format_string": None, "is_hidden": False, "lineage_tag": None, "kpi_id": None,
    }


def _snapshot():
    return {
        "source_file": "Demo.pbix",
        "tables": {
            "Sales": {
                "is_calculated_table": False, "m_expression": None, "is_hidden": False,
                "description": None, "lineage_tag": None,
                "columns": [_column("Amount"), _column("Region")],
            },
        },
        "measures": {
            "Sales": {
                "Total Revenue": _measure("SUM(Sales[Amount])"),
                "Total Revenue YTD": _measure("TOTALYTD([Total Revenue])"),
            },
        },
        "calculated_columns": [],
        "relationships": [],
    }


def _layout():
    return {
        "format": "pbir",
        "pages": [
            {
                "page_id": "p1", "display_name": "Overview", "filters": [],
                "visuals": [
                    {
                        "kind": "visual", "visual_id": "v1", "visual_type": "card",
                        "kpi_classification": "certain", "display_name": "[R-9] Revenue Card",
                        "fields": [{"kind": "measure", "table": "Sales",
                                    "field": "Total Revenue", "role": "Values"}],
                        "filters": [],
                    },
                    {
                        "kind": "visual", "visual_id": "v2", "visual_type": "columnChart",
                        "kpi_classification": None, "display_name": "Sales Trend",
                        "fields": [{"kind": "column", "table": "Sales",
                                    "field": "Amount", "role": "Y"}],
                        "filters": [],
                    },
                ],
            },
            {
                "page_id": "p2", "display_name": "Details", "filters": [],
                "visuals": [
                    {
                        "kind": "visual", "visual_id": "v3", "visual_type": "tableEx",
                        "kpi_classification": None, "display_name": "Sales Trend",
                        "fields": [{"kind": "column", "table": "Sales",
                                    "field": "Region", "role": "Rows"}],
                        "filters": [],
                    },
                    {
                        "kind": "visualGroup", "visual_id": "g1",
                        "display_name": "Layout group", "group_mode": "single",
                    },
                ],
            },
        ],
    }


def _req(req_id, pages="", visuals="", visual_ids="", status="Approved",
         description="Any prose here must never be scanned"):
    return {
        "id": req_id, "title": f"Title for {req_id}", "status": status,
        "active": status not in ("Done", "Cancelled"),
        "description": description, "priority": "High", "raised_date": "",
        "business_owner": "", "business_area": "", "expected_change_date": "",
        "impacted_pages": pages, "impacted_visuals": visuals,
        "impacted_visual_ids": visual_ids, "notes": "", "row_number": 2,
    }


def test_visual_id_maps_visual_and_bound_fields():
    mapping = requirement_mapping.build_requirement_mapping(
        _snapshot(), _layout(), [_req("R-1", visual_ids="v1")])
    entry = mapping["R-1"]
    visual = entry["visuals"][("p1", "v1")]
    assert visual["source"] == "Visual ID" and visual["confidence"] == "High"
    # The visual's bound measure is discovered automatically.
    assert entry["objects"][("measure", "Sales", "Total Revenue")] == {
        "source": "Visual ID", "confidence": "High"}
    # Dependency expansion: the YTD measure references Total Revenue.
    assert entry["objects"][("measure", "Sales", "Total Revenue YTD")]["source"] == "Dependency"
    # And every visual bound anywhere in that chain is impacted.
    assert ("p1", "v1") in entry["visuals"]


def test_visual_name_matches_all_visuals_with_that_name():
    mapping = requirement_mapping.build_requirement_mapping(
        _snapshot(), _layout(), [_req("R-2", visuals="Sales Trend")])
    entry = mapping["R-2"]
    # Same visual name on two pages -> both map.
    assert entry["visuals"][("p1", "v2")]["source"] == "Visual Name"
    assert entry["visuals"][("p2", "v3")]["source"] == "Visual Name"
    # Their bound columns are discovered.
    assert ("column", "Sales", "Amount") in entry["objects"]
    assert ("column", "Sales", "Region") in entry["objects"]


def test_page_scope_maps_every_real_visual_on_the_page():
    mapping = requirement_mapping.build_requirement_mapping(
        _snapshot(), _layout(), [_req("R-3", pages="Overview")])
    entry = mapping["R-3"]
    assert entry["visuals"][("p1", "v1")]["source"] == "Page"
    assert entry["visuals"][("p1", "v2")]["source"] == "Page"
    assert ("p2", "v3") not in entry["visuals"]
    # Bound objects of all page visuals are seeded.
    assert ("measure", "Sales", "Total Revenue") in entry["objects"]
    assert ("column", "Sales", "Amount") in entry["objects"]


def test_page_scope_never_maps_visual_groups():
    mapping = requirement_mapping.build_requirement_mapping(
        _snapshot(), _layout(), [_req("R-4", pages="Details")])
    entry = mapping["R-4"]
    assert set(entry["visuals"]) == {("p2", "v3")}  # g1 visualGroup excluded


def test_pbix_tag_still_works():
    mapping = requirement_mapping.build_requirement_mapping(
        _snapshot(), _layout(), [_req("R-9")])
    entry = mapping["R-9"]
    assert entry["visuals"][("p1", "v1")]["source"] == "Tag"
    assert entry["objects"][("measure", "Sales", "Total Revenue")]["source"] == "Tag"


def test_no_scope_means_no_mapping_plus_warning():
    mapping = requirement_mapping.build_requirement_mapping(
        _snapshot(), _layout(), [_req("R-5")])
    entry = mapping["R-5"]
    # The Description text is never scanned: no keyword inference exists.
    assert entry["objects"] == {}
    assert entry["visuals"] == {}
    assert any("maps to nothing" in warning for warning in entry["warnings"])


def test_unresolvable_scope_entries_warn():
    mapping = requirement_mapping.build_requirement_mapping(
        _snapshot(), _layout(), [
            _req("R-6", pages="No Such Page", visuals="No Such Visual", visual_ids="nope"),
        ])
    entry = mapping["R-6"]
    assert any("No Such Page" in warning for warning in entry["warnings"])
    assert any("No Such Visual" in warning for warning in entry["warnings"])
    assert any("'nope'" in warning for warning in entry["warnings"])
    assert entry["objects"] == {}
    assert entry["visuals"] == {}


def test_inactive_requirement_is_not_mapped():
    mapping = requirement_mapping.build_requirement_mapping(
        _snapshot(), _layout(), [_req("R-7", pages="Overview", status="Done")])
    entry = mapping["R-7"]
    assert entry["objects"] == {}
    assert entry["visuals"] == {}


def test_case_insensitive_scope_matching():
    mapping = requirement_mapping.build_requirement_mapping(
        _snapshot(), _layout(), [
            _req("R-8", pages="overview", visuals="sales trend", visual_ids="V1"),
        ])
    entry = mapping["R-8"]
    assert ("p1", "v1") in entry["visuals"]
    assert ("p1", "v2") in entry["visuals"]
    assert ("p2", "v3") in entry["visuals"]


def test_build_object_attribution_covers_objects_visuals_pages():
    mapping = requirement_mapping.build_requirement_mapping(
        _snapshot(), _layout(), [_req("R-1", visual_ids="v1"), _req("R-2", visuals="Sales Trend")])
    attribution = requirement_mapping.build_object_attribution(mapping)
    # Total Revenue: seeded by R-1's visual, and also reached by R-2 through
    # the dependency chain (R-2 seeds Sales[Amount], which Total Revenue sums).
    assert attribution["measure|Sales|Total Revenue"] == "R-1; R-2"
    assert attribution["column|Sales|Amount"] == "R-2"
    assert attribution["visual|p1|v2"] == "R-2"
    assert "page|p1" in attribution
    assert "page|p2" in attribution


def test_dependency_visuals_keep_only_direct_non_slicer():
    """A declared visual's bound column seeds other visuals. Dependency
    expansion must surface only visuals DIRECTLY bound to an impacted object
    and never slicer-type filter controls."""
    layout = _layout()
    # A slicer bound to Sales[Amount] (direct) - must be dropped.
    layout["pages"][0]["visuals"].append({
        "kind": "visual", "visual_id": "slicer1", "visual_type": "slicer",
        "kpi_classification": None, "display_name": "Amount filter",
        "fields": [{"kind": "column", "table": "Sales", "field": "Amount", "role": "Values"}],
        "filters": [],
    })
    # A chart on another page bound to Sales[Amount] (direct) - must be kept.
    layout["pages"][1]["visuals"].append({
        "kind": "visual", "visual_id": "v4", "visual_type": "columnChart",
        "kpi_classification": None, "display_name": "Amount Chart",
        "fields": [{"kind": "column", "table": "Sales", "field": "Amount", "role": "Y"}],
        "filters": [],
    })
    # Declare the "Sales Trend" chart on p1, which is bound to Sales[Amount].
    mapping = requirement_mapping.build_requirement_mapping(
        _snapshot(), layout, [_req("R-10", visuals="Sales Trend")])
    entry = mapping["R-10"]
    # Sales[Amount] is seeded (bound to the declared chart).
    assert ("column", "Sales", "Amount") in entry["objects"]
    # The directly-bound non-slicer chart on p2 is surfaced via Dependency.
    assert entry["visuals"][("p2", "v4")]["source"] == "Dependency"
    assert entry["visuals"][("p2", "v4")]["matched_via"] == "direct"
    # The slicer is never surfaced.
    assert ("p1", "slicer1") not in entry["visuals"]
