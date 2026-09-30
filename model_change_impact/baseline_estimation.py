"""Estimate pre-development impact for every object in one PBIX snapshot."""
import os
import re
from datetime import datetime

from openpyxl import Workbook
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from model_change_impact import excel_report, impact, visual_descriptions
from services import fileutils


SECTION_BY_KIND = {
    "table": "tables",
    "measure": "measures",
    "column": "columns",
    "relationship": "relationships",
}

BASELINE_HEADERS = [
    "Requirement Number", "Visual ID", "Page Name", "Visual Name", "Visual Type",
    "KPI Classification", "Measures", "Columns", "Tables", "Relationships",
    "Visual Description", "Page Filters", "Visual Filters", "Impact Basis", "Object Count",
    "Generated Date", "Generated Time",
]
REQUIREMENT_HEADERS = [
    "Requirement Number", "Requirement Type", "Raised Date", "Requested By",
    "Business Owner", "Business Area", "Title", "Status", "Description", "Priority",
    "Expected Change Date", "Impacted Pages", "Impacted Visuals", "Impacted Visual IDs", "Notes",
    "Generated Date", "Generated Time",
]
RTM_HEADERS = [
    "Requirement Number", "Requirement Status", "Visual ID", "Page Name", "Visual Name",
    "Visual Type", "KPI Classification", "Measures", "Columns", "Tables", "Relationships",
    "Visual Description", "Page Filters", "Visual Filters", "Impact Basis", "Object Count",
    "Generated Date", "Generated Time",
]
IMPACT_HEADERS = [
    "Requirement Number", "Requirement Status", "Requirement Title", "Priority", "Status", "Visual ID",
    "Page Name", "Visual Name", "Visual Type", "KPI Classification", "Measures",
    "Columns", "Tables", "Relationships", "Page Filters", "Visual Filters",
    "Visual Description", "Impact Basis", "Review Flag", "Generated Date", "Generated Time",
]


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

    The workbook always contains one consolidated ``Baseline Estimation``
    worksheet with every model object and its affected Visual ID. When
    ``requirements_path`` is given, requirement summary, traceability,
    history, unmapped-change, and pre-development ``Requirement Impact``
    sheets are added. When ``history_db_path`` is given, every run is recorded
    in the local change-history store (attributed to requirements when a
    mapping exists)."""
    if output_path is None:
        selections = list_selectable_objects(snapshot)
        output_path = selections_or_output_path
    else:
        selections = selections_or_output_path

    requirements_data = _prepare_requirements(snapshot, report_layout, requirements_path)
    if requirements_data is not None:
        selections = requirements_data["selections"]

    diff_result, impact_result = analyze_selected_objects(snapshot, report_layout, selections)
    baseline_selections = list_selectable_objects(snapshot)
    _baseline_diff, baseline_impact = analyze_selected_objects(
        snapshot, report_layout, baseline_selections)
    output_dir = os.path.dirname(os.path.abspath(output_path))
    description_cache_path = os.path.join(output_dir, "visual_descriptions.json")
    descriptions = visual_descriptions.load_or_generate(report_layout, description_cache_path)

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "RTM"
    baseline_rows = _build_baseline_estimation_rows(baseline_impact, report_layout, descriptions)
    generated_at = datetime.now()
    generated_date = generated_at.date().isoformat()
    generated_time = generated_at.strftime("%H:%M:%S")
    _apply_generated_at(baseline_rows, generated_date, generated_time)
    requirements = requirements_data["requirements"] if requirements_data else []
    mapping = requirements_data["mapping"] if requirements_data else {}
    previous_history = _load_previous_history(output_path)
    requirement_numbers = _requirement_numbers(
        requirements, mapping, baseline_rows, previous_history)
    rtm_rows = _build_rtm_rows(
        requirements, mapping, baseline_rows, requirement_numbers, previous_history)
    _write_rtm_sheet(sheet, rtm_rows)
    _write_rows(workbook.create_sheet("Requirement Copy"), REQUIREMENT_HEADERS,
                _build_requirement_rows(requirements, baseline_rows, generated_date, generated_time))
    impact_rows = _build_visual_requirement_rows(
        requirements, mapping, baseline_rows, requirement_numbers, previous_history,
        generated_date, generated_time)
    _write_rows(workbook.create_sheet("Visual Impact by Requirement"),
                IMPACT_HEADERS, impact_rows)

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
        "mapped_objects": len({key for entry in mapping.values() for key in entry["objects"]}),
        "unmapped_changes": 0,
        "impact_rows": len(impact_rows),
        "impacted_visuals": len({
            row["Visual ID"] for row in impact_rows if row.get("Visual ID")
        }),
        "affected_pages": len({
            row["Page Name"] for row in impact_rows if row.get("Page Name")
        }),
        "kpi_visuals": len({
            row["Visual ID"] for row in impact_rows
            if row.get("Visual ID") and row.get("KPI Classification") in ("certain", "heuristic")
        }),
        "rows": impact_rows,
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
        snapshot, report_layout, requirements_list, include_inactive=True)
    for entry in mapping.values():
        for warning in entry["warnings"]:
            print(f"Requirements warning: {warning}")

    for warning in requirement_mapping.validate_visual_requirement_assignments(mapping):
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


def _build_baseline_estimation_rows(impact_result, report_layout, descriptions=None):
    """Build exactly one aggregated row for every real Visual ID."""
    rows_by_visual = {}
    for page in report_layout.get("pages", []):
        for visual in page.get("visuals", []):
            if visual.get("kind") != "visual" or not visual.get("visual_id"):
                continue
            visual_id = visual["visual_id"]
            page_name = page.get("display_name") or page.get("page_id") or ""
            existing = rows_by_visual.get(visual_id)
            if existing is not None:
                if page_name and page_name not in existing["Page Name"].split("; "):
                    existing["Page Name"] += f"; {page_name}"
                continue
            rows_by_visual[visual_id] = {
                "Requirement Number": "", "Visual ID": visual_id,
                "Page Name": page_name,
                "Visual Name": visual.get("display_name") or _baseline_visual_name(visual),
                "Visual Type": visual.get("visual_type") or "",
                "KPI Classification": visual.get("kpi_classification") or "",
                "Visual Description": (descriptions or {}).get(
                    visual_id, _visual_description(visual)),
                "Measures": set(), "Columns": set(), "Tables": set(),
                "Relationships": set(), "Page Filters": _format_filters(page.get("filters")),
                "Visual Filters": _format_filters(visual.get("filters")), "Impact Basis": set(),
            }

    for section, object_type in (
        ("tables", "Table"), ("measures", "Measure"),
        ("columns", "Column"), ("relationships", "Relationship"),
    ):
        for record in impact_result.get(section, []):
            detail = record.get("detail", {})
            kind = section[:-1]
            object_name = excel_report._describe_changed_object(kind, detail)
            for visual in record.get("impacted_visuals", []):
                row = rows_by_visual.get(visual.get("visual_id"))
                if row is None:
                    continue
                row[_object_bucket(object_type)].add(object_name)
                if visual.get("matched_via"):
                    row["Impact Basis"].add(visual["matched_via"])

    rows = []
    for row in rows_by_visual.values():
        for key in ("Measures", "Columns", "Tables", "Relationships", "Impact Basis"):
            row[key] = "; ".join(sorted(row[key], key=str.casefold))
        row["Object Count"] = sum(
            len(value.split("; ")) for key, value in row.items()
            if key in ("Measures", "Columns", "Tables", "Relationships") and value
        )
        row["Requirement Number"] = _requirement_number(row["Visual ID"], row["Page Name"], 1)
        rows.append(row)
    return sorted(rows, key=lambda row: str(row["Visual ID"]).casefold())


def _format_filters(filters):
    values = []
    for item in filters or []:
        fields = item.get("fields") or []
        if fields:
            values.extend(
                f"{field.get('table')}[{field.get('field')}]"
                for field in fields if field.get("table") and field.get("field")
            )
        elif item.get("name"):
            values.append(str(item["name"]))
    return "; ".join(sorted(set(values), key=str.casefold))


def _visual_description(visual):
    visual_type = (visual.get("visual_type") or "visual").casefold()
    fields = visual.get("fields") or []
    named = [
        f"{field.get('table')}[{field.get('field')}]"
        for field in fields if field.get("table") and field.get("field")
    ]
    if "slicer" in visual_type or "textfilter" in visual_type:
        target = named[0] if named else "the selected field"
        return f"Filters the report by {target}."
    if "table" in visual_type or "matrix" in visual_type or "pivot" in visual_type:
        return f"Displays {', '.join(named[:4]) or 'the configured fields'} in a table."
    if "card" in visual_type or "kpi" in visual_type or "gauge" in visual_type:
        return f"Displays {named[0] if named else 'a key value'} as a KPI."
    if "chart" in visual_type or any(token in visual_type for token in ("column", "bar", "line", "area", "combo", "donut", "pie")):
        if len(named) >= 2:
            return f"Shows {named[0]} by {named[1]}."
        return f"Shows {named[0] if named else 'the configured values'} in a chart."
    if "button" in visual_type or "image" in visual_type:
        return "Provides report navigation or layout support."
    if "textbox" in visual_type:
        return "Displays configured report text."
    return f"Displays {', '.join(named[:4]) or 'configured report content'}."


def _apply_generated_at(rows, generated_date, generated_time):
    for row in rows:
        row["Generated Date"] = generated_date
        row["Generated Time"] = generated_time


def _requirement_number(visual_id, page_name, sequence):
    visual = re.sub(r"[^A-Za-z0-9]", "", str(visual_id or "UNKNOWN"))
    page = re.sub(r"[^A-Za-z0-9]", "", str(page_name or "UNKNOWN"))
    return f"RE1_{visual}_{page or 'UNKNOWN'}_{sequence:03d}"


def _object_bucket(object_type):
    return {
        "Measure": "Measures", "Column": "Columns", "Table": "Tables",
        "Relationship": "Relationships",
    }[object_type]


def _baseline_visual_name(visual):
    """Give unnamed visuals a stable human-readable label in the baseline."""
    visual_type = visual.get("visual_type") or "visual"
    matched = visual.get("matched_object") or {}
    if matched.get("table") and matched.get("name"):
        return f"(Untitled {visual_type}) {matched['table']}[{matched['name']}]"
    fields = visual.get("fields") or []
    if fields and fields[0].get("table") and fields[0].get("field"):
        return f"(Untitled {visual_type}) {fields[0]['table']}[{fields[0]['field']}]"
    return f"(Untitled {visual_type}) [ID: {visual.get('visual_id') or 'unknown'}]"


def _write_baseline_estimation_sheet(sheet, rows):
    excel_report._write_header(sheet, BASELINE_HEADERS)
    for row in rows:
        sheet.append([row.get(header, "") for header in BASELINE_HEADERS])
    sheet.auto_filter.ref = f"A1:Q{max(sheet.max_row, 1)}"
    sheet.freeze_panes = "A2"
    excel_report._apply_wrap(sheet)
    excel_report._autofit_columns(sheet)


def _sequence_number(requirement_id):
    match = re.search(r"_(\d+)$", requirement_id or "")
    return int(match.group(1)) if match else 0


def _requirement_status_by_visual(requirements, mapping):
    by_visual = {}
    for requirement in requirements:
        for page_id, visual_id in mapping.get(requirement["id"], {}).get("visuals", {}):
            by_visual.setdefault(visual_id, []).append(requirement)
    statuses = {}
    for visual_id, items in by_visual.items():
        ordered = sorted(items, key=lambda item: (
            item.get("raised_date") or "", _sequence_number(item["id"]), item.get("row_number", 0)))
        newest = ordered[-1]["id"]
        statuses[visual_id] = {item["id"]: ("Production" if item["id"] == newest else "Obsolete") for item in items}
    return statuses


def _load_previous_history(output_path):
    """Read the prior RTM and impact rows before the output is replaced."""
    if not os.path.exists(output_path):
        return {"rtm": [], "impact": []}
    try:
        workbook = load_workbook(output_path, read_only=True, data_only=True)
    except Exception:
        return {"rtm": [], "impact": []}

    history = {}
    for sheet_name, key in (("RTM", "rtm"), ("Visual Impact by Requirement", "impact")):
        if sheet_name not in workbook.sheetnames:
            history[key] = []
            continue
        sheet = workbook[sheet_name]
        rows = sheet.iter_rows(values_only=True)
        headers = [str(value).strip() if value is not None else "" for value in next(rows, ())]
        history[key] = [dict(zip(headers, row)) for row in rows]
    workbook.close()
    return history


def _requirement_numbers(requirements, mapping, baseline_rows, previous_history):
    """Reuse matching historical numbers and allocate the next visual sequence."""
    prior_requirement_numbers = {
        row.get("Requirement Number") for row in previous_history.get("impact", [])
        if row.get("Requirement Number")
    }
    old_rows = previous_history.get("impact", []) + [
        row for row in previous_history.get("rtm", [])
        if row.get("Requirement Number") in prior_requirement_numbers
    ]
    old_by_visual = {}
    for row in old_rows:
        visual_id = row.get("Visual ID")
        number = row.get("Requirement Number")
        if visual_id and number:
            old_by_visual.setdefault(str(visual_id), []).append(row)

    result = {}
    grouped = {}
    for requirement in requirements:
        for _page_id, visual_id in mapping.get(requirement["id"], {}).get("visuals", {}):
            grouped.setdefault(str(visual_id), []).append(requirement)
    for visual_id, visual_requirements in grouped.items():
        baseline = next((row for row in baseline_rows if row["Visual ID"] == visual_id), {})
        next_sequence = max([
            _sequence_number(str(row.get("Requirement Number")))
            for row in old_by_visual.get(visual_id, [])
        ] or [0]) + 1
        for requirement in sorted(visual_requirements, key=lambda item: (
                item.get("raised_date") or "", item.get("row_number", 0))):
            requirement_id = requirement["id"]
            candidates = [row for row in old_by_visual.get(visual_id, [])
                          if row.get("Requirement Title") == requirement.get("title")]
            if candidates:
                old_number = str(candidates[0]["Requirement Number"])
                old_sequence = _sequence_number(old_number) or next_sequence
                result[(requirement_id, visual_id)] = _requirement_number(
                    visual_id, str(baseline.get("Page Name", "")).split("; ")[0], old_sequence)
                continue
            result[(requirement_id, visual_id)] = _requirement_number(
                visual_id, str(baseline.get("Page Name", "")).split("; ")[0], next_sequence)
            next_sequence += 1
    return result


def _build_rtm_rows(requirements, mapping, baseline_rows, requirement_numbers, previous_history):
    status_by_visual = _requirement_status_by_visual(requirements, mapping)
    baseline_by_visual = {row["Visual ID"]: row for row in baseline_rows}
    mapped_ids = {visual_id for entry in mapping.values() for _, visual_id in entry.get("visuals", {})}
    requirements_by_visual = {}
    for requirement in requirements:
        for _page_id, visual_id in mapping.get(requirement["id"], {}).get("visuals", {}):
            requirements_by_visual.setdefault(visual_id, []).append(requirement)
    rows = []
    for visual_id, baseline in baseline_by_visual.items():
        history = sorted(requirements_by_visual.get(visual_id, []), key=lambda item: (
            item.get("raised_date") or "", _sequence_number(item["id"]), item.get("row_number", 0)))
        if not history:
            history = [None]
        for sequence, requirement in enumerate(history, start=1):
            requirement_id = requirement["id"] if requirement else ""
            row = dict(baseline)
            row["Requirement Number"] = requirement_numbers.get(
                (requirement_id, visual_id), baseline["Requirement Number"])
            row["Requirement Status"] = status_by_visual.get(visual_id, {}).get(
                requirement_id, "Production" if visual_id not in mapped_ids else "")
            rows.append(row)

        current_numbers = {
            row["Requirement Number"] for row in rows if row["Visual ID"] == visual_id
        }
        prior_requirement_numbers = {
            row.get("Requirement Number") for row in previous_history.get("impact", [])
            if row.get("Requirement Number")
        }
        for old_row in previous_history.get("rtm", []):
            if str(old_row.get("Visual ID")) != str(visual_id):
                continue
            if old_row.get("Requirement Number") not in prior_requirement_numbers:
                continue
            if old_row.get("Requirement Number") in current_numbers:
                continue
            historical = dict(baseline)
            historical.update({key: value for key, value in old_row.items() if key in RTM_HEADERS})
            historical["Requirement Number"] = _requirement_number(
                visual_id, str(baseline.get("Page Name", "")).split("; ")[0],
                _sequence_number(str(old_row.get("Requirement Number"))) or 1)
            historical["Requirement Status"] = "Obsolete"
            rows.append(historical)
    return sorted(rows, key=lambda row: (str(row["Visual ID"]).casefold(), row["Requirement Number"]))


def _write_rtm_sheet(sheet, rows):
    excel_report._write_header(sheet, RTM_HEADERS)
    for row in rows:
        sheet.append([row.get(header, "") for header in RTM_HEADERS])
    sheet.auto_filter.ref = f"A1:Q{max(sheet.max_row, 1)}"
    sheet.freeze_panes = "A2"
    excel_report._apply_wrap(sheet)
    excel_report._autofit_columns(sheet)


def _build_requirement_rows(requirements, baseline_rows, generated_date, generated_time):
    if requirements:
        return [{
            "Requirement Number": req["id"], "Requirement Type": req.get("requirement_type", "New Requirement"),
            "Raised Date": req.get("raised_date", ""), "Requested By": req.get("requested_by", ""),
            "Business Owner": req.get("business_owner", ""), "Business Area": req.get("business_area", ""),
            "Title": req["title"], "Status": req["status"],
            "Description": req.get("description", ""), "Priority": req.get("priority", ""),
            "Expected Change Date": req.get("expected_change_date", ""),
            "Impacted Pages": req.get("impacted_pages", ""),
            "Impacted Visuals": req.get("impacted_visuals", ""),
            "Impacted Visual IDs": req.get("impacted_visual_ids", ""), "Notes": req.get("notes", ""),
            "Generated Date": generated_date, "Generated Time": generated_time,
        } for req in requirements]
    return [{"Requirement Number": row["Requirement Number"],
             "Generated Date": generated_date, "Generated Time": generated_time}
            for row in baseline_rows]


def _build_visual_requirement_rows(requirements, mapping, baseline_rows, requirement_numbers=None,
                                   previous_history=None, generated_date="", generated_time=""):
    requirement_by_id = {req["id"]: req for req in requirements}
    baseline_by_visual = {row["Visual ID"]: row for row in baseline_rows}
    status_by_visual = _requirement_status_by_visual(requirements, mapping)
    rows = []
    for requirement_id, entry in mapping.items():
        req = requirement_by_id.get(requirement_id, {})
        for (_page_id, visual_id), info in entry.get("visuals", {}).items():
            baseline = baseline_by_visual.get(visual_id, {})
            history = sorted(
                [item for item in requirements if any(
                    visual_id == mapped_visual_id
                    for _, mapped_visual_id in mapping.get(item["id"], {}).get("visuals", {}))],
                key=lambda item: (item.get("raised_date") or "", _sequence_number(item["id"]), item.get("row_number", 0)),
            )
            sequence = next((index for index, item in enumerate(history, start=1)
                             if item["id"] == requirement_id), 1)
            rows.append({
                "Requirement Number": (requirement_numbers or {}).get(
                    (requirement_id, visual_id), _requirement_number(
                        visual_id, baseline.get("Page Name", "").split("; ")[0], sequence)),
                "Requirement Status": status_by_visual.get(visual_id, {}).get(requirement_id, ""),
                "Requirement Title": req.get("title", ""),
                "Priority": req.get("priority", ""), "Status": req.get("status", ""),
                "Visual ID": visual_id, "Page Name": baseline.get("Page Name", ""),
                "Visual Name": baseline.get("Visual Name", ""),
                "Visual Type": baseline.get("Visual Type", ""),
                "KPI Classification": baseline.get("KPI Classification", ""),
                "Visual Description": baseline.get("Visual Description", ""),
                "Measures": baseline.get("Measures", ""), "Columns": baseline.get("Columns", ""),
                "Tables": baseline.get("Tables", ""), "Relationships": baseline.get("Relationships", ""),
                "Page Filters": baseline.get("Page Filters", ""),
                "Visual Filters": baseline.get("Visual Filters", ""),
                "Impact Basis": baseline.get("Impact Basis", ""),
                "Review Flag": "Yes" if entry.get("warnings") or
                "transitive_dependency" in (baseline.get("Impact Basis", "")) else "",
                "Generated Date": generated_date, "Generated Time": generated_time,
            })
    current_numbers = {row["Requirement Number"] for row in rows}
    for old_row in (previous_history or {}).get("impact", []):
        if not old_row.get("Visual ID"):
            continue
        historical = dict(old_row)
        historical["Requirement Number"] = _requirement_number(
            old_row.get("Visual ID"), old_row.get("Page Name", ""),
            _sequence_number(str(old_row.get("Requirement Number"))) or 1)
        if historical["Requirement Number"] in current_numbers:
            continue
        historical["Requirement Status"] = "Obsolete"
        historical["Generated Date"] = generated_date
        historical["Generated Time"] = generated_time
        rows.append({header: historical.get(header, "") for header in IMPACT_HEADERS})
    return rows


def _write_rows(sheet, headers, rows):
    excel_report._write_header(sheet, headers)
    for row in rows:
        sheet.append([row.get(header, "") for header in headers])
    sheet.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{max(sheet.max_row, 1)}"
    excel_report._apply_wrap(sheet)
    excel_report._autofit_columns(sheet)


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