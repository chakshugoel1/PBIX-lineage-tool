"""
test_model_change_impact_report_layout.py

Tests for model_change_impact/report_layout.py using synthetic in-memory
zip archives (no real proprietary PBIX file needed/committed - mirrors this
repo's existing test convention). Covers the PBIR (modern multi-file)
format, the legacy single-blob Report/Layout format, and the "no report
layout part present" case.
"""
import io
import json
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from model_change_impact import report_layout


def _write_zip(parts):
    """parts: {zip_member_name: str_or_bytes}"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in parts.items():
            if isinstance(content, str):
                content = content.encode("utf-8")
            zf.writestr(name, content)
    buf.seek(0)
    return buf


class TestPbirFormat:
    def _build_pbix(self):
        report_json = json.dumps({
            "filterConfig": {
                "filters": [
                    {
                        "name": "ReportRegionFilter",
                        "type": "Categorical",
                        "field": {"Column": {
                            "Expression": {"SourceRef": {"Entity": "ReportFilterTable"}},
                            "Property": "Region",
                        }},
                    }
                ]
            },
        })
        pages_json = json.dumps({"pageOrder": ["page1"]})
        page_json = json.dumps({
            "displayName": "Sales Overview",
            "filterConfig": {
                "filters": [
                    {
                        "name": "RegionFilter",
                        "type": "Categorical",
                        "field": {"Column": {
                            "Expression": {"SourceRef": {"Entity": "Orders"}},
                            "Property": "Region",
                        }},
                    }
                ]
            },
        })
        chart_visual = json.dumps({
            "visual": {
                "visualType": "columnChart",
                "visualContainerObjects": {
                    "title": [{
                        "properties": {
                            "text": {"expr": {"Literal": {"Value": "'Sales by region'"}}}
                        }
                    }]
                },
                "query": {
                    "queryState": {
                        "Category": {"projections": [{"field": {"Column": {
                            "Expression": {"SourceRef": {"Entity": "Orders"}},
                            "Property": "Region",
                        }}}]},
                        "Y": {"projections": [{"field": {"Measure": {
                            "Expression": {"SourceRef": {"Entity": "Orders"}},
                            "Property": "Total Sales",
                        }}}]},
                    }
                },
            }
        })
        card_visual = json.dumps({
            "visual": {"visualType": "card", "query": {"queryState": {}}},
            "parentGroupName": "grpA",
        })
        custom_kpi_visual = json.dumps({
            "visual": {"visualType": "advanceCardE0376012ABCD", "query": {"queryState": {}}},
        })
        group_visual = json.dumps({
            "visualGroup": {"displayName": "KPI Row", "groupMode": "Fixed"},
        })
        return _write_zip({
            "Report/definition/report.json": report_json,
            "Report/definition/pages/pages.json": pages_json,
            "Report/definition/pages/page1/page.json": page_json,
            "Report/definition/pages/page1/visuals/visChart/visual.json": chart_visual,
            "Report/definition/pages/page1/visuals/visCard/visual.json": card_visual,
            "Report/definition/pages/page1/visuals/visKpi/visual.json": custom_kpi_visual,
            "Report/definition/pages/page1/visuals/grpA/visual.json": group_visual,
        })

    def test_detects_pbir_format(self):
        result = report_layout.build_report_layout(self._build_pbix())
        assert result["format"] == "pbir"
        assert result["unsupported_reason"] is None
        assert result["source_file"] == ""

    def test_page_metadata_and_filters(self):
        result = report_layout.build_report_layout(self._build_pbix())
        assert result["filters"] == [{
            "name": "ReportRegionFilter",
            "type": "Categorical",
            "fields": [{"kind": "column", "table": "ReportFilterTable", "field": "Region"}],
        }]
        page = result["pages"][0]
        assert page["page_id"] == "page1"
        assert page["display_name"] == "Sales Overview"
        assert page["filters"] == [{
            "name": "RegionFilter",
            "type": "Categorical",
            "fields": [{"kind": "column", "table": "Orders", "field": "Region"}],
        }]

    def test_visual_field_bindings(self):
        result = report_layout.build_report_layout(self._build_pbix())
        visuals = {v["visual_id"]: v for v in result["pages"][0]["visuals"]}
        chart = visuals["visChart"]
        assert chart["kind"] == "visual"
        assert chart["visual_type"] == "columnChart"
        assert chart["display_name"] == "Sales by region"
        assert {"kind": "column", "table": "Orders", "field": "Region", "role": "Category"} in chart["fields"]
        assert {"kind": "measure", "table": "Orders", "field": "Total Sales", "role": "Y"} in chart["fields"]

    def test_kpi_classification(self):
        result = report_layout.build_report_layout(self._build_pbix())
        visuals = {v["visual_id"]: v for v in result["pages"][0]["visuals"]}
        assert visuals["visCard"]["kpi_classification"] == "certain"
        assert visuals["visKpi"]["kpi_classification"] == "heuristic"
        assert visuals["visChart"]["kpi_classification"] is None

    def test_visual_group_excluded_from_visual_semantics(self):
        result = report_layout.build_report_layout(self._build_pbix())
        visuals = {v["visual_id"]: v for v in result["pages"][0]["visuals"]}
        group = visuals["grpA"]
        assert group["kind"] == "visualGroup"
        assert group["display_name"] == "KPI Row"
        assert group["fields"] == []

    def test_parent_group_id_captured(self):
        result = report_layout.build_report_layout(self._build_pbix())
        visuals = {v["visual_id"]: v for v in result["pages"][0]["visuals"]}
        assert visuals["visCard"]["parent_group_id"] == "grpA"

    def test_visual_name_source_distinguishes_configured_and_untitled(self):
        result = report_layout.build_report_layout(self._build_pbix())
        visuals = {v["visual_id"]: v for v in result["pages"][0]["visuals"]}

        assert visuals["visChart"]["display_name_source"] == "Configured title"
        assert visuals["visCard"]["display_name"] == "KPI Row"
        assert visuals["visCard"]["display_name_source"] == "Parent group display name"


class TestLegacyFormat:
    def _build_pbix(self):
        page_filters = json.dumps([{
            "name": "PeriodFilter",
            "type": "Categorical",
            "expression": {"Column": {
                "Expression": {"SourceRef": {"Entity": "001_PERIOD"}},
                "Property": "PERIOD",
            }},
        }])
        visual_filters = json.dumps([{
            "name": "RefreshDateFilter",
            "type": "Categorical",
            "expression": {"Column": {
                "Expression": {"SourceRef": {"Source": "d"}},
                "Property": "DTS REFRESH DATE",
            }},
        }])
        chart_config = json.dumps({
            "singleVisual": {
                "visualType": "clusteredColumnChart",
                "vcObjects": {
                    "title": {
                        "properties": {
                            "text": {"expr": {"Literal": {"Value": "'Regional sales'"}}}
                        }
                    }
                },
                "prototypeQuery": {
                    "From": [{"Name": "o", "Entity": "Orders", "Type": 0}],
                    "Select": [
                        {
                            "Column": {
                                "Expression": {"SourceRef": {"Source": "o"}},
                                "Property": "Region",
                            },
                            "Name": "Orders.Region",
                        },
                        {
                            "Measure": {
                                "Expression": {"SourceRef": {"Source": "o"}},
                                "Property": "Total Sales",
                            },
                            "Name": "Orders.Total Sales",
                        },
                    ]
                },
                "projections": {
                    "Category": [{"queryRef": "Orders.Region"}],
                    "Y": [{"queryRef": "Orders.Total Sales"}],
                },
            }
        })
        card_config = json.dumps({
            "singleVisual": {
                "visualType": "card",
                "prototypeQuery": {
                    "From": [{"Name": "d", "Entity": "DTS REFRESH DATE", "Type": 0}],
                    "Select": [{
                        "Aggregation": {
                            "Expression": {"Column": {
                                "Expression": {"SourceRef": {"Source": "d"}},
                                "Property": "DTS REFRESH DATE",
                            }},
                            "Function": 4,
                        },
                        "Name": "Min(DTS REFRESH DATE.DTS REFRESH DATE)",
                    }],
                },
                "projections": {
                    "Values": [{"queryRef": "Min(DTS REFRESH DATE.DTS REFRESH DATE)"}],
                },
            }
        })
        slicer_config = json.dumps({
            "singleVisual": {
                "visualType": "slicer",
                "prototypeQuery": {
                    "From": [{"Name": "p", "Entity": "001_PERIOD", "Type": 0}],
                    "Select": [{
                        "Column": {
                            "Expression": {"SourceRef": {"Source": "p"}},
                            "Property": "PERIOD",
                        },
                        "Name": "001_PERIOD.PERIOD",
                    }],
                },
                "projections": {
                    "Values": [{"queryRef": "001_PERIOD.PERIOD"}],
                },
            }
        })
        group_config = json.dumps({"name": "Legacy Group"})
        layout = {
            "sections": [
                {
                    "name": "Section1",
                    "displayName": "Overview",
                    "filters": page_filters,
                    "visualContainers": [
                        {"name": "visChart", "config": chart_config},
                        {"name": "visCard", "config": card_config, "filters": visual_filters},
                        {"name": "visSlicer", "config": slicer_config},
                        {"name": "visGroup", "config": group_config},
                    ],
                }
            ]
        }
        layout_bytes = json.dumps(layout).encode("utf-16")
        return _write_zip({"Report/Layout": layout_bytes})

    def test_detects_legacy_format(self):
        result = report_layout.build_report_layout(self._build_pbix())
        assert result["format"] == "legacy_layout"
        assert result["unsupported_reason"] is not None

    def test_legacy_page_and_visuals(self):
        result = report_layout.build_report_layout(self._build_pbix())
        page = result["pages"][0]
        assert page["page_id"] == "Section1"
        assert page["display_name"] == "Overview"
        visuals = {v["visual_id"]: v for v in page["visuals"]}
        chart = visuals["visChart"]
        assert chart["kind"] == "visual"
        assert chart["visual_type"] == "clusteredColumnChart"
        assert chart["display_name"] == "Regional sales"
        assert {"kind": "column", "table": "Orders", "field": "Region", "role": "Category"} in chart["fields"]
        assert {"kind": "measure", "table": "Orders", "field": "Total Sales", "role": "Y"} in chart["fields"]
        card = visuals["visCard"]
        assert {"kind": "column", "table": "DTS REFRESH DATE", "field": "DTS REFRESH DATE", "role": "Values"} in card["fields"]
        assert card["filters"] == [{
            "name": "RefreshDateFilter",
            "type": "Categorical",
            "fields": [{"kind": "column", "table": "DTS REFRESH DATE", "field": "DTS REFRESH DATE"}],
        }]
        slicer = visuals["visSlicer"]
        assert slicer["visual_type"] == "slicer"
        assert {"kind": "column", "table": "001_PERIOD", "field": "PERIOD", "role": "Values"} in slicer["fields"]
        assert page["filters"] == [{
            "name": "PeriodFilter",
            "type": "Categorical",
            "fields": [{"kind": "column", "table": "001_PERIOD", "field": "PERIOD"}],
        }]
        assert visuals["visGroup"]["kind"] == "visualGroup"

    def test_legacy_visual_without_name_gets_stable_fallback_id(self):
        layout = {
            "sections": [{
                "name": "Section1",
                "visualContainers": [{
                    "config": json.dumps({"singleVisual": {"visualType": "card"}}),
                }],
            }],
        }
        pbix = _write_zip({"Report/Layout": json.dumps(layout).encode("utf-16")})

        result = report_layout.build_report_layout(pbix)

        assert result["pages"][0]["visuals"][0]["visual_id"] == "legacy:Section1:0"


class TestNoLayoutPresent:
    def test_no_report_part(self):
        buf = _write_zip({"SomeOtherPart.xml": "<root/>"})
        result = report_layout.build_report_layout(buf)
        assert result["format"] == "none"
        assert result["pages"] == []
        assert result["unsupported_reason"]
