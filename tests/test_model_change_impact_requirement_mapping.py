"""Tests for model_change_impact.requirement_mapping - the hybrid
requirement -> object mapping (PBIX tags, Seed Objects, keyword inference,
dependency expansion)."""
from model_change_impact import requirement_mapping


def _column(name, description=None):
    return {
        "name": name, "data_type": "Int64", "is_calculated": False, "expression": None,
        "format_string": None, "is_hidden": False, "description": description,
        "display_folder": None, "lineage_tag": None,
    }


def _measure(expression, description=""):
    return {
        "expression": expression, "display_folder": "", "description": description,
        "format_string": None, "is_hidden": False, "lineage_tag": None, "kpi_id": None,
    }


def _snapshot():
    return {
        "source_file": "Demo.pbix",
        "tables": {
            "Sales": {
                "is_calculated_table": False, "m_expression": None, "is_hidden": False,
                "description": "Sales fact table. REQ:R-2", "lineage_tag": None,
                "columns": [_column("Amount")],
            },
        },
        "measures": {
            "Sales": {
                "Total Revenue": _measure("SUM(Sales[Amount])", description="Main KPI. REQ:R-1"),
                "Total Revenue YTD": _measure("TOTALYTD([Total Revenue])"),
            },
        },
        "calculated_columns": [],
        "relationships": [],
    }


def _layout():
    return {
        "format": "pbir",
        "pages": [{
            "page_id": "p1", "display_name": "Overview", "filters": [],
            "visuals": [
                {
                    "kind": "visual", "visual_id": "v1", "visual_type": "card",
                    "kpi_classification": "certain", "display_name": "[R-1] Revenue Card",
                    "fields": [{"kind": "measure", "table": "Sales",
                                "field": "Total Revenue", "role": "Values"}],
                    "filters": [],
                },
                {
                    "kind": "visual", "visual_id": "v2", "visual_type": "columnChart",
                    "kpi_classification": None, "display_name": "Sales Trend",
                    "fields": [{"kind": "column", "table": "Sales",
                                "field": "Amount", "role": "Values"}],
                    "filters": [],
                },
            ],
        }],
    }


def _req(req_id, title="Generic", description="", seed="", status="Approved"):
    return {
        "id": req_id, "title": title, "status": status,
        "active": status not in ("Done", "Cancelled"),
        "description": description, "priority": "High", "raised_date": "",
        "business_owner": "", "business_area": "", "expected_change_date": "",
        "seed_objects": seed, "notes": "", "row_number": 2,
    }


def test_pbix_tags_map_measure_and_visual():
    mapping = requirement_mapping.build_requirement_mapping(
        _snapshot(), _layout(), [_req("R-1", title="Zzz qqq")])
    entry = mapping["R-1"]
    assert entry["objects"][("measure", "Sales", "Total Revenue")] == {
        "source": "Tag", "confidence": "High"}
    visual = entry["visuals"][("p1", "v1")]
    assert visual["source"] == "Tag" and visual["confidence"] == "High"
    assert visual["page_display_name"] == "Overview"


def test_table_description_tag_and_dependency_expansion():
    mapping = requirement_mapping.build_requirement_mapping(
        _snapshot(), _layout(), [_req("R-2", title="Zzz qqq")])
    entry = mapping["R-2"]
    assert entry["objects"][("table", "Sales", "Sales")]["source"] == "Tag"
    # Dependency expansion: table change seeds its columns, whose dependents
    # are the measures referencing them.
    assert entry["objects"][("column", "Sales", "Amount")]["source"] == "Dependency"
    assert entry["objects"][("measure", "Sales", "Total Revenue")]["source"] == "Dependency"
    assert entry["objects"][("measure", "Sales", "Total Revenue YTD")]["source"] == "Dependency"
    assert entry["objects"][("column", "Sales", "Amount")]["confidence"] == "Medium"
    # Both visuals are impacted: v2 directly (bound to Amount), v1 transitively.
    assert ("p1", "v2") in entry["visuals"]
    assert ("p1", "v1") in entry["visuals"]


def test_seed_objects_column_resolution():
    mapping = requirement_mapping.build_requirement_mapping(
        _snapshot(), _layout(), [
            _req("R-3", title="Zzz qqq", seed="column:Sales[Amount]"),
            _req("R-4", title="Zzz qqq", seed="measure:Total Revenue"),
            _req("R-5", title="Zzz qqq", seed="visual:Sales Trend"),
            _req("R-6", title="Zzz qqq", seed="table:Sales"),
        ])
    assert mapping["R-3"]["objects"][("column", "Sales", "Amount")] == {
        "source": "Seed", "confidence": "High"}
    assert mapping["R-4"]["objects"][("measure", "Sales", "Total Revenue")] == {
        "source": "Seed", "confidence": "High"}
    assert mapping["R-5"]["visuals"][("p1", "v2")]["source"] == "Seed"
    # A seeded visual also maps the model objects it is bound to.
    assert mapping["R-5"]["objects"][("column", "Sales", "Amount")]["source"] == "Seed"
    assert mapping["R-6"]["objects"][("table", "Sales", "Sales")]["source"] == "Seed"


def test_page_seed_maps_all_page_visuals():
    mapping = requirement_mapping.build_requirement_mapping(
        _snapshot(), _layout(), [_req("R-7", title="Zzz qqq", seed="page:Overview")])
    entry = mapping["R-7"]
    assert entry["visuals"][("p1", "v1")]["source"] == "Seed"
    assert entry["visuals"][("p1", "v2")]["source"] == "Seed"


def test_keyword_inference_is_low_confidence():
    mapping = requirement_mapping.build_requirement_mapping(
        _snapshot(), _layout(), [_req("R-8", title="Amount tracking")])
    entry = mapping["R-8"]
    assert entry["objects"][("column", "Sales", "Amount")]["source"] == "Inferred"
    assert entry["objects"][("column", "Sales", "Amount")]["confidence"] == "Low"


def test_inactive_requirement_is_not_mapped():
    mapping = requirement_mapping.build_requirement_mapping(
        _snapshot(), _layout(), [_req("R-9", title="Amount tracking", status="Done")])
    entry = mapping["R-9"]
    assert entry["objects"] == {}
    assert entry["visuals"] == {}


def test_unresolvable_seed_produces_warning():
    mapping = requirement_mapping.build_requirement_mapping(
        _snapshot(), _layout(), [_req("R-10", title="Zzz qqq", seed="measure:No Such Measure")])
    entry = mapping["R-10"]
    assert any("No Such Measure" in warning for warning in entry["warnings"])
    assert entry["objects"] == {}


def test_explicit_mapping_wins_over_inference():
    # Title contains "Revenue" which would infer the measures; the REQ tag
    # must keep the stronger Tag/High classification.
    mapping = requirement_mapping.build_requirement_mapping(
        _snapshot(), _layout(), [_req("R-1", title="Revenue review")])
    entry = mapping["R-1"]
    assert entry["objects"][("measure", "Sales", "Total Revenue")]["source"] == "Tag"


def test_build_object_attribution_covers_objects_visuals_pages():
    mapping = requirement_mapping.build_requirement_mapping(
        _snapshot(), _layout(), [_req("R-1", title="Zzz qqq"), _req("R-2", title="Zzz qqq")])
    attribution = requirement_mapping.build_object_attribution(mapping)
    # Total Revenue: tagged by R-1, dependency-expanded by R-2.
    assert attribution["measure|Sales|Total Revenue"] == "R-1; R-2"
    assert attribution["visual|p1|v1"] == "R-1; R-2"
    assert attribution["page|p1"] == "R-1; R-2"
    assert "column|Sales|Amount" in attribution
