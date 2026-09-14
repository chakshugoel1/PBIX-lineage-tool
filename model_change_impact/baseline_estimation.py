"""Estimate pre-development impact for every object in one PBIX snapshot."""
import os

from openpyxl import Workbook

from model_change_impact import excel_report, impact
from services import fileutils


SECTION_BY_KIND = {
    "table": "tables",
    "measure": "measures",
    "column": "columns",
    "relationship": "relationships",
}


def list_selectable_objects(snapshot):
    """Return every table, measure, column and relationship in display order."""
    objects = []
    for table_name in sorted(snapshot.get("tables", {}), key=str.casefold):
        objects.append({"kind": "table", "table": table_name, "name": table_name,
                        "display": table_name})

    for table_name in sorted(snapshot.get("measures", {}), key=str.casefold):
        for measure_name in sorted(snapshot["measures"][table_name], key=str.casefold):
            objects.append({"kind": "measure", "table": table_name, "name": measure_name,
                            "display": f"{table_name}[{measure_name}]"})

    for table_name in sorted(snapshot.get("tables", {}), key=str.casefold):
        columns = snapshot["tables"][table_name].get("columns", [])
        for column in sorted(columns, key=lambda item: item["name"].casefold()):
            objects.append({"kind": "column", "table": table_name, "name": column["name"],
                            "display": f"{table_name}[{column['name']}]"})

    relationships = sorted(snapshot.get("relationships", []), key=_relationship_key)
    for relationship in relationships:
        objects.append({
            "kind": "relationship",
            "from_table": relationship.get("from_table"),
            "from_column": relationship.get("from_column"),
            "to_table": relationship.get("to_table"),
            "to_column": relationship.get("to_column"),
            "display": _relationship_display(relationship),
        })
    return objects


def selection_key(selection):
    """Stable key used to retain checkbox state while filtering the UI."""
    kind = selection["kind"]
    if kind == "relationship":
        return (kind,) + _relationship_key(selection)
    return kind, selection.get("table"), selection.get("name")


def build_selection_diff(snapshot, selections):
    """Represent selected existing objects as hypothetical modifications.

    The shape exactly matches ``diff.diff_snapshots`` so the established V2
    impact analyzer and Impact Summary row builder remain the source of truth.
    """
    result = {
        "baseline_file": snapshot.get("source_file"),
        "changed_file": "Baseline estimation (planned change)",
    }
    for section in SECTION_BY_KIND.values():
        result[section] = {"added": [], "removed": [], "changed": [], "unchanged_count": 0}

    seen = set()
    for selection in selections:
        key = selection_key(selection)
        if key in seen:
            continue
        seen.add(key)
        kind = selection["kind"]
        section = SECTION_BY_KIND.get(kind)
        if section is None:
            raise ValueError(f"Unsupported baseline-estimation object type: {kind}")
        result[section]["changed"].append(_selection_change_record(selection))
    return result


def analyze_selected_objects(snapshot, report_layout, selections):
    """Return a V2-compatible diff and impact result for selected objects."""
    diff_result = build_selection_diff(snapshot, selections)
    impact_result = impact.analyze_impact(snapshot, snapshot, diff_result, report_layout)
    return diff_result, impact_result


def build_report(snapshot, report_layout, selections_or_output_path, output_path=None,
                 requirements_path=None, history_db_path=None):
    """Write the complete one-PBIX baseline-estimation workbook.

    Existing callers may pass ``(selections, output_path)``. Passing only an
    output path analyzes the complete model inventory.

    When ``requirements_path`` is given, the requirements workbook drives the
    object selection (hybrid mapping: PBIX tags -> Seed Objects -> keyword
    inference, expanded through the impact engine) and four extra sheets are
    added: Requirement Summary, Requirement Traceability, Object Change
    History, Unmapped Changes. When ``history_db_path`` is given, every run is
    recorded in the local change-history store (attributed to requirements
    when a mapping exists)."""
    if output_path is None:
        selections = list_selectable_objects(snapshot)
        output_path = selections_or_output_path
    else:
        selections = selections_or_output_path

    requirements_data = _prepare_requirements(snapshot, report_layout, requirements_path)
    if requirements_data is not None:
        selections = requirements_data["selections"]

    history_data = _record_history(
        history_db_path, snapshot, report_layout,
        requirements_data["mapping"] if requirements_data else None)

    diff_result, impact_result = analyze_selected_objects(snapshot, report_layout, selections)
    visual_rows = excel_report._flatten_visual_impacts(impact_result)
    rows = excel_report._build_impact_summary_rows(diff_result, impact_result, visual_rows)

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Object Summary"
    object_rows = _build_object_summary_rows(impact_result)
    _write_object_summary_sheet(sheet, object_rows)
    excel_report._write_impact_summary_sheet(workbook.create_sheet("Impact Summary"), rows)
    _write_model_inventory_sheet(workbook.create_sheet("Model Inventory"), snapshot)
    visual_inventory_rows = _build_visual_inventory_rows(report_layout)
    _write_visual_inventory_sheet(workbook.create_sheet("Visual Inventory"), visual_inventory_rows)

    requirement_stats = {"mapped_objects": 0, "unmapped_changes": 0, "low_confidence": 0}
    if requirements_data is not None and history_data is not None:
        from model_change_impact import requirement_report
        requirement_stats = requirement_report.build_requirement_sheets(
            workbook,
            requirements_data["requirements"],
            requirements_data["mapping"],
            history_data["history"],
            history_data["events"],
            history_data["run_info"]["changed_keys"],
            history_data["run_info"]["run_at"],
        )

    output_dir = os.path.dirname(os.path.abspath(output_path))
    if output_dir and not os.path.isdir(output_dir):
        os.makedirs(output_dir, exist_ok=True)
    fileutils.atomic_replace_workbook(workbook, output_path)

    active_requirements = (
        sum(1 for req in requirements_data["requirements"] if req.get("active", True))
        if requirements_data else 0
    )
    return {
        "output_path": output_path,
        "selected_objects": len({selection_key(item) for item in selections}),
        "model_objects": len({selection_key(item) for item in selections}),
        "requirements": active_requirements,
        "mapped_objects": requirement_stats["mapped_objects"],
        "unmapped_changes": requirement_stats["unmapped_changes"],
        "impact_rows": len(rows),
        "impacted_visuals": len({
            (visual["page_id"], visual["visual_id"])
            for records in impact_result.values()
            for record in records
            for visual in record["impacted_visuals"]
        }),
        "affected_pages": len({
            visual["page_id"]
            for records in impact_result.values()
            for record in records
            for visual in record["impacted_visuals"]
        }),
        "kpi_visuals": len({
            (visual["page_id"], visual["visual_id"])
            for records in impact_result.values()
            for record in records
            for visual in record["impacted_visuals"]
            if visual.get("kpi_classification") in ("certain", "heuristic")
        }),
        "rows": rows,
    }


def _prepare_requirements(snapshot, report_layout, requirements_path):
    """Load the requirements workbook and build the hybrid mapping. Returns
    None when no requirements file was provided. Lazy imports avoid a module
    cycle (requirement_mapping reuses this module's build_selection_diff)."""
    if not requirements_path:
        return None
    from model_change_impact import requirement_mapping
    from model_change_impact import requirements as requirements_module

    requirements_list, warnings = requirements_module.load_requirements(requirements_path)
    for warning in warnings:
        print(f"Requirements warning: {warning}")
    mapping = requirement_mapping.build_requirement_mapping(
        snapshot, report_layout, requirements_list)
    for entry in mapping.values():
        for warning in entry["warnings"]:
            print(f"Requirements warning: {warning}")

    selections = []
    seen = set()
    for entry in mapping.values():
        for kind, table, name in sorted(entry["objects"]):
            key = (kind, table, name)
            if key not in seen:
                seen.add(key)
                selections.append({"kind": kind, "table": table, "name": name})
    return {"requirements": requirements_list, "mapping": mapping, "selections": selections}


def _record_history(history_db_path, snapshot, report_layout, mapping):
    """Record this run in the local change-history store. Returns None when
    no history database path was provided."""
    if not history_db_path:
        return None
    from model_change_impact import history_store, requirement_mapping

    model_key = os.path.splitext(os.path.basename(snapshot.get("source_file") or "model"))[0]
    attribution = requirement_mapping.build_object_attribution(mapping) if mapping else None
    run_info = history_store.record_run(
        history_db_path, model_key, snapshot, report_layout, attribution=attribution)
    return {
        "run_info": run_info,
        "history": history_store.get_object_history(history_db_path, model_key),
        "events": history_store.list_change_events(history_db_path, model_key),
    }


def _build_object_summary_rows(impact_result):
    rows = []
    for section, object_type in (
        ("tables", "Table"), ("measures", "Measure"),
        ("columns", "Column"), ("relationships", "Relationship"),
    ):
        kind = section[:-1]
        for record in impact_result.get(section, []):
            visuals = record.get("impacted_visuals", [])
            direct = {(v["page_id"], v["visual_id"]) for v in visuals if v["matched_via"] == "direct"}
            dependency = {(v["page_id"], v["visual_id"]) for v in visuals
                          if v["matched_via"] == "transitive_dependency"}
            unique = direct | dependency
            kpis = {(v["page_id"], v["visual_id"]) for v in visuals
                    if v.get("kpi_classification") in ("certain", "heuristic")}
            pages = {v["page_id"] for v in visuals}
            if kind in ("table", "relationship"):
                impact_type = "Broad model impact" if unique else "No visual binding found"
                confidence = "Broad heuristic"
            elif direct and dependency:
                impact_type = "Direct and dependency chain"
                confidence = "Direct binding + DAX dependency scan"
            elif direct:
                impact_type = "Direct"
                confidence = "Direct visual binding"
            elif dependency:
                impact_type = "Dependency chain"
                confidence = "DAX dependency scan"
            else:
                impact_type = "No visual binding found"
                confidence = "No matching binding"
            rows.append({
                "Object Type": object_type,
                "Object": excel_report._describe_changed_object(kind, record["detail"]),
                "Direct Visuals": len(direct),
                "Dependency Visuals": len(dependency),
                "Total Unique Visuals": len(unique),
                "KPI Visuals": len(kpis),
                "Affected Pages": len(pages),
                "Dependent Objects": len(record.get("dependent_objects", [])),
                "Impact Type": impact_type,
                "Analysis Confidence": confidence,
            })
    return sorted(rows, key=lambda row: (row["Object Type"], row["Object"].casefold()))


def _write_object_summary_sheet(sheet, rows):
    headers = [
        "Object Type", "Object", "Direct Visuals", "Dependency Visuals",
        "Total Unique Visuals", "KPI Visuals", "Affected Pages",
        "Dependent Objects", "Impact Type", "Analysis Confidence",
    ]
    excel_report._write_header(sheet, headers)
    for row in rows:
        sheet.append([row.get(header, "") for header in headers])
    sheet.auto_filter.ref = f"A1:J{max(sheet.max_row, 1)}"
    excel_report._apply_wrap(sheet)
    excel_report._autofit_columns(sheet)


def _write_model_inventory_sheet(sheet, snapshot):
    headers = [
        "Object Type", "Object", "Table", "Name", "Expression / Definition",
        "Data Type", "Format String", "Display Folder", "Is Hidden",
        "Is Calculated", "Is Active", "Cardinality", "Cross Filtering",
    ]
    excel_report._write_header(sheet, headers)
    for item in list_selectable_objects(snapshot):
        kind = item["kind"]
        if kind == "table":
            details = snapshot["tables"][item["table"]]
            row = ["Table", item["display"], item["table"], "",
                   details.get("m_expression"), "", "", "", details.get("is_hidden"),
                   details.get("is_calculated_table"), "", "", ""]
        elif kind == "measure":
            details = snapshot["measures"][item["table"]][item["name"]]
            row = ["Measure", item["display"], item["table"], item["name"],
                   details.get("expression"), "", details.get("format_string"),
                   details.get("display_folder"), details.get("is_hidden"), True, "", "", ""]
        elif kind == "column":
            details = next(column for column in snapshot["tables"][item["table"]]["columns"]
                           if column["name"] == item["name"])
            row = ["Column", item["display"], item["table"], item["name"],
                   details.get("expression"), details.get("data_type"), details.get("format_string"),
                   details.get("display_folder"), details.get("is_hidden"),
                   details.get("is_calculated"), "", "", ""]
        else:
            details = next(rel for rel in snapshot.get("relationships", [])
                           if _relationship_key(rel) == _relationship_key(item))
            row = ["Relationship", item["display"], details.get("from_table"), "",
                   item["display"], "", "", "", "", "", details.get("is_active"),
                   details.get("cardinality"), details.get("cross_filtering_behavior")]
        sheet.append(row)
    sheet.auto_filter.ref = f"A1:M{max(sheet.max_row, 1)}"
    excel_report._apply_wrap(sheet)
    excel_report._autofit_columns(sheet)


def _build_visual_inventory_rows(report_layout):
    rows = []
    for page in report_layout.get("pages", []):
        for visual in page.get("visuals", []):
            if visual.get("kind") != "visual":
                continue
            fields = visual.get("fields") or [None]
            for field in fields:
                name, source = _visual_inventory_name(visual, field)
                rows.append({
                    "Page Name": page.get("display_name") or page.get("page_id") or "",
                    "Visual Name": name,
                    "Visual Name Source": source,
                    "Visual ID": visual.get("visual_id") or "",
                    "Visual Type": visual.get("visual_type") or "",
                    "Is KPI": "Yes" if visual.get("kpi_classification") in ("certain", "heuristic") else "No",
                    "KPI Confidence": visual.get("kpi_classification") or "",
                    "Bound Object Type": (field or {}).get("kind", ""),
                    "Bound Table": (field or {}).get("table", ""),
                    "Bound Object": (field or {}).get("field", ""),
                    "Visual Role": (field or {}).get("role", ""),
                })
    return sorted(rows, key=lambda row: (row["Page Name"], row["Visual ID"], row["Bound Object"]))


def _visual_inventory_name(visual, field=None):
    if visual.get("display_name"):
        return visual["display_name"], visual.get("display_name_source") or "Configured metadata"
    visual_type = visual.get("visual_type") or "visual"
    if field and field.get("table") and field.get("field"):
        return (f"(Untitled {visual_type}) {field['table']}[{field['field']}]",
                "Generated from visual type and binding")
    return (f"(Untitled {visual_type}) [ID: {visual.get('visual_id') or 'unknown'}]",
            "Generated from visual type and ID")


def _write_visual_inventory_sheet(sheet, rows):
    headers = [
        "Page Name", "Visual Name", "Visual Name Source", "Visual ID", "Visual Type",
        "Is KPI", "KPI Confidence", "Bound Object Type", "Bound Table",
        "Bound Object", "Visual Role",
    ]
    excel_report._write_header(sheet, headers)
    for row in rows:
        sheet.append([row.get(header, "") for header in headers])
    sheet.auto_filter.ref = f"A1:K{max(sheet.max_row, 1)}"
    excel_report._apply_wrap(sheet)
    excel_report._autofit_columns(sheet)


def _selection_change_record(selection):
    kind = selection["kind"]
    if kind == "relationship":
        identity = {
            "from_table": selection.get("from_table"),
            "from_column": selection.get("from_column"),
            "to_table": selection.get("to_table"),
            "to_column": selection.get("to_column"),
        }
        return {
            "matched_by": "selection",
            "is_rename_candidate": False,
            "identity_before": dict(identity),
            "identity_after": dict(identity),
            "field_changes": {},
            "detection_method": "MANUAL",
        }

    identity = {"table": selection.get("table")}
    if kind != "table":
        identity["name"] = selection.get("name")
    return {
        "matched_by": "selection",
        "is_rename_candidate": False,
        "identity_before": dict(identity),
        "identity_after": dict(identity),
        "field_changes": {},
    }


def _relationship_key(relationship):
    return (
        relationship.get("from_table") or "",
        relationship.get("from_column") or "",
        relationship.get("to_table") or "",
        relationship.get("to_column") or "",
    )


def _relationship_display(relationship):
    return (
        f"{relationship.get('from_table')}[{relationship.get('from_column')}] -> "
        f"{relationship.get('to_table')}[{relationship.get('to_column')}]"
    )