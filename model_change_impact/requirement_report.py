"""Requirement traceability sheets for the Baseline Estimation workbook.

Adds five sheets driven by the report-grain requirement mapping
(requirement_mapping.py) and the local change-history store
(history_store.py):

- ``Requirement Summary``      - one row per requirement (counts + severity)
- ``Requirement Traceability`` - one row per requirement x impacted object
- ``Object Change History``    - one row per recorded object change event
- ``Unmapped Changes``         - changed objects with no requirement
                                 attribution, plus an empty
                                 ``Assign Requirement ID`` column users can
                                 copy into Seed Objects on the next run
- ``Requirement Impact``       - pre-development impact rows for each
                                 requirement and mapped Visual ID/object

``build_requirement_sheets()`` is the only entry point most callers need.
"""
import datetime

from openpyxl.utils import get_column_letter

from model_change_impact import excel_report
from model_change_impact.history_store import object_key

# Agreed severity model: total impacted objects x priority weight.
SEVERITY_WEIGHTS = {"Critical": 4, "High": 3, "Medium": 2, "Low": 1}

_OBJECT_TYPE_ORDER = {"table": 0, "column": 1, "measure": 2, "visual": 3, "page": 4}

_TRACEABILITY_HEADERS = [
    "Requirement ID", "Requirement Title", "Priority", "Status",
    "Object Type", "Object Name", "Parent Object", "Report Page",
    "Mapping Source", "Confidence", "Change Status",
    "Last Changed", "Previous Changed", "Days Since Last Change", "Review Flag",
]

_SUMMARY_HEADERS = [
    "Requirement ID", "Title", "Priority", "Status", "Business Area",
    "Impacted Tables", "Impacted Columns", "Impacted Measures",
    "Impacted Visuals", "Impacted Pages", "Total Impacted Objects",
    "Low Confidence Count", "Impact Severity", "Last Analyzed",
]

_HISTORY_HEADERS = [
    "Object Type", "Object Name", "Parent Object", "Change Type",
    "Changed At", "Previous Change At", "Days Between Changes",
    "Attributed Requirement ID", "First Seen", "Change Count",
]

_UNMAPPED_HEADERS = [
    "Object Type", "Object Name", "Change Type", "Changed At", "Assign Requirement ID",
]

_IMPACT_HEADERS = [
    "Requirement ID", "Requirement Title", "Priority", "Status",
    "Impact Item Type", "Visual ID", "Report Page", "Visual Name",
    "Object Type", "Object Name", "Mapping Source", "Confidence",
    "Impact Basis", "Review Flag",
]


def build_requirement_sheets(workbook, requirements, mapping, history, events,
                             changed_keys, run_at):
    """Append the requirement sheets to `workbook`. Returns summary
    stats: ``{"mapped_objects", "unmapped_changes", "low_confidence"}``."""
    traceability_rows = _build_traceability_rows(
        requirements, mapping, history, changed_keys, run_at)
    summary_rows = _build_summary_rows(requirements, mapping, run_at)
    unmapped_rows = _build_unmapped_rows(events)
    impact_rows = _build_requirement_impact_rows(requirements, mapping)

    _write_summary_sheet(workbook.create_sheet("Requirement Summary"), summary_rows)
    _write_traceability_sheet(workbook.create_sheet("Requirement Traceability"), traceability_rows)
    _write_history_sheet(workbook.create_sheet("Object Change History"), events)
    _write_unmapped_sheet(workbook.create_sheet("Unmapped Changes"), unmapped_rows)
    _write_impact_sheet(workbook.create_sheet("Requirement Impact"), impact_rows)

    return {
        "mapped_objects": len({
            key for entry in mapping.values() for key in entry["objects"]
        }),
        "unmapped_changes": len(unmapped_rows),
        "low_confidence": sum(1 for row in traceability_rows if row["Review Flag"] == "Yes"),
    }


# ---------------------------------------------------------------------------
# Row builders
# ---------------------------------------------------------------------------

def _build_traceability_rows(requirements, mapping, history, changed_keys, run_at):
    rows = []
    for requirement in requirements:
        entry = mapping.get(requirement["id"], {"objects": {}, "visuals": {}})
        base = {
            "Requirement ID": requirement["id"],
            "Requirement Title": requirement["title"],
            "Priority": requirement["priority"],
            "Status": requirement["status"],
        }
        object_rows = []
        for (kind, table, name), info in sorted(
                entry["objects"].items(),
                key=lambda item: (_OBJECT_TYPE_ORDER.get(item[0][0], 9), item[0][1], item[0][2])):
            key = object_key(kind, table) if kind == "table" else object_key(kind, table, name)
            object_rows.append(_traceability_row(
                base, key, kind.title(),
                name if kind == "table" else f"{table}[{name}]",
                "" if kind == "table" else table, "", info,
                history, changed_keys, run_at))
        for (page_id, visual_id), info in sorted(
                entry["visuals"].items(), key=lambda item: (str(item[0][0]), str(item[0][1]))):
            page_name = info.get("page_display_name") or page_id or ""
            visual_name = info.get("visual_display_name") or (
                f"(Untitled {info.get('visual_type') or 'visual'}) [ID: {visual_id}]")
            object_rows.append(_traceability_row(
                base, object_key("visual", page_id, visual_id), "Visual",
                visual_name, page_name, page_name, info, history, changed_keys, run_at))
        for page_id, page_name in _requirement_pages(entry).items():
            source, confidence = _page_mapping_source(entry, page_id)
            object_rows.append(_traceability_row(
                base, object_key("page", page_id), "Page", page_name, "", page_name,
                {"source": source, "confidence": confidence}, history, changed_keys, run_at))

        if object_rows:
            rows.extend(object_rows)
        else:
            rows.append({**base, "Object Type": "(no objects mapped)", "Object Name": "",
                         "Parent Object": "", "Report Page": "", "Mapping Source": "",
                         "Confidence": "", "Change Status": "", "Last Changed": "",
                         "Previous Changed": "", "Days Since Last Change": "", "Review Flag": ""})
    return rows


def _traceability_row(base, key, object_type, name, parent, page, info, history, changed_keys, run_at):
    status, last_changed, previous_changed, days = _change_status(key, history, changed_keys, run_at)
    return {
        **base,
        "Object Type": object_type,
        "Object Name": name,
        "Parent Object": parent,
        "Report Page": page,
        "Mapping Source": info.get("source", ""),
        "Confidence": info.get("confidence", ""),
        "Change Status": status,
        "Last Changed": last_changed,
        "Previous Changed": previous_changed,
        "Days Since Last Change": days,
        "Review Flag": "Yes" if info.get("confidence") == "Low" else "",
    }


def _requirement_pages(entry):
    pages = {}
    for (page_id, _visual_id), info in entry["visuals"].items():
        pages[page_id] = info.get("page_display_name") or page_id or ""
    return pages


def _page_mapping_source(entry, page_id):
    """A page inherits the strongest mapping source of its mapped visuals."""
    for (vid_page, _visual_id), info in entry["visuals"].items():
        if vid_page == page_id and info.get("confidence") == "High":
            return info["source"], "High"
    return "Dependency", "Medium"


def _change_status(key, history, changed_keys, run_at):
    info = history.get(key)
    if info is None:
        return "", "", "", ""
    status = info["last_change_type"] if key in changed_keys else "Unchanged"
    if not status:
        status = "Unchanged"
    days = _days_between(info.get("last_changed"), run_at)
    return status, info.get("last_changed", ""), info.get("previous_change_at", ""), days


def _build_summary_rows(requirements, mapping, run_at):
    rows = []
    for requirement in requirements:
        entry = mapping.get(requirement["id"], {"objects": {}, "visuals": {}})
        objects = entry["objects"]
        pages = _requirement_pages(entry)
        counts = {
            "tables": sum(1 for kind, _, _ in objects if kind == "table"),
            "columns": sum(1 for kind, _, _ in objects if kind == "column"),
            "measures": sum(1 for kind, _, _ in objects if kind == "measure"),
            "visuals": len(entry["visuals"]),
            "pages": len(pages),
        }
        total = counts["tables"] + counts["columns"] + counts["measures"] + counts["visuals"] + counts["pages"]
        low_confidence = (
            sum(1 for info in objects.values() if info.get("confidence") == "Low")
            + sum(1 for info in entry["visuals"].values() if info.get("confidence") == "Low")
        )
        weight = SEVERITY_WEIGHTS.get(requirement.get("priority"), SEVERITY_WEIGHTS["Medium"])
        rows.append({
            "Requirement ID": requirement["id"],
            "Title": requirement["title"],
            "Priority": requirement.get("priority", "Medium"),
            "Status": requirement["status"],
            "Business Area": requirement.get("business_area", ""),
            "Impacted Tables": counts["tables"],
            "Impacted Columns": counts["columns"],
            "Impacted Measures": counts["measures"],
            "Impacted Visuals": counts["visuals"],
            "Impacted Pages": counts["pages"],
            "Total Impacted Objects": total,
            "Low Confidence Count": low_confidence,
            "Impact Severity": total * weight,
            "Last Analyzed": run_at,
        })
    return rows


def _build_unmapped_rows(events):
    """Changed objects with no requirement attribution. Events from the very
    first recorded run for a model are the baseline registration (everything
    'Added'), not reviewable changes, so they are excluded."""
    if not events:
        return []
    baseline_run_id = min(event.get("run_id", 0) for event in events)
    rows = []
    for event in events:
        if event.get("run_id") == baseline_run_id:
            continue
        if event.get("attributed_requirement_id") in (None, "", "Unmapped"):
            rows.append({
                "Object Type": event.get("object_type", ""),
                "Object Name": event.get("object_name", ""),
                "Change Type": event.get("change_type", ""),
                "Changed At": event.get("changed_at", ""),
                "Assign Requirement ID": "",
            })
    return rows


def _build_requirement_impact_rows(requirements, mapping):
    """Build the user-facing pre-development impact table.

    Requirements select report-grain visuals. Their bound model objects are
    listed alongside the Visual IDs so a reviewer can see both the declared
    scope and the derived model impact without editing the baseline inventory.
    """
    rows = []
    for requirement in requirements:
        entry = mapping.get(requirement["id"], {"objects": {}, "visuals": {}, "warnings": []})
        review = "Yes" if entry.get("warnings") else ""
        base = {
            "Requirement ID": requirement["id"],
            "Requirement Title": requirement["title"],
            "Priority": requirement.get("priority", "Medium"),
            "Status": requirement["status"],
        }
        for (page_id, visual_id), info in sorted(
                entry.get("visuals", {}).items(), key=lambda item: (str(item[0][0]), str(item[0][1]))):
            visual_name = info.get("visual_display_name") or (
                f"(Untitled {info.get('visual_type') or 'visual'}) [ID: {visual_id}]")
            rows.append({
                **base, "Impact Item Type": "Visual", "Visual ID": visual_id,
                "Report Page": info.get("page_display_name") or page_id or "",
                "Visual Name": visual_name, "Object Type": "", "Object Name": "",
                "Mapping Source": info.get("source", ""),
                "Confidence": info.get("confidence", ""),
                "Impact Basis": "Declared scope" if info.get("source") != "Dependency" else "Dependency",
                "Review Flag": review,
            })
        for (kind, table, name), info in sorted(
                entry.get("objects", {}).items(), key=lambda item: (item[0][0], item[0][1], item[0][2])):
            object_name = name if kind == "table" else f"{table}[{name}]"
            rows.append({
                **base, "Impact Item Type": "Model Object", "Visual ID": "",
                "Report Page": "", "Visual Name": "", "Object Type": kind.title(),
                "Object Name": object_name, "Mapping Source": info.get("source", ""),
                "Confidence": info.get("confidence", ""),
                "Impact Basis": "Direct binding" if info.get("source") != "Dependency" else "Dependency chain",
                "Review Flag": "Yes" if review or info.get("confidence") == "Low" else "",
            })
        if not entry.get("visuals") and not entry.get("objects"):
            rows.append({
                **base, "Impact Item Type": "No mapping", "Visual ID": "", "Report Page": "",
                "Visual Name": "", "Object Type": "", "Object Name": "",
                "Mapping Source": "", "Confidence": "", "Impact Basis": "No current impact detected",
                "Review Flag": "Yes",
            })
    return rows


def _days_between(start, end):
    start_dt, end_dt = _parse_ts(start), _parse_ts(end)
    if start_dt is None or end_dt is None:
        return ""
    return (end_dt - start_dt).days


def _parse_ts(value):
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(str(value))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Sheet writers
# ---------------------------------------------------------------------------

def _write_rows(ws, headers, rows, highlight_column=None, highlight_value=None):
    excel_report._write_header(ws, headers)
    for row in rows:
        ws.append([row.get(header, "") for header in headers])
        if highlight_column in headers and row.get(highlight_column) == highlight_value:
            for cell in ws[ws.max_row]:
                cell.fill = excel_report.YELLOW_FILL
    last_column = get_column_letter(len(headers))
    ws.auto_filter.ref = f"A1:{last_column}{max(ws.max_row, 1)}"
    excel_report._apply_wrap(ws)
    excel_report._autofit_columns(ws)


def _write_summary_sheet(ws, rows):
    _write_rows(ws, _SUMMARY_HEADERS, rows)


def _write_traceability_sheet(ws, rows):
    _write_rows(ws, _TRACEABILITY_HEADERS, rows,
                highlight_column="Review Flag", highlight_value="Yes")


def _write_history_sheet(ws, events):
    rows = []
    for event in events:
        rows.append({
            "Object Type": event.get("object_type", ""),
            "Object Name": event.get("object_name", ""),
            "Parent Object": event.get("parent_object", ""),
            "Change Type": event.get("change_type", ""),
            "Changed At": event.get("changed_at", ""),
            "Previous Change At": event.get("previous_change_at", ""),
            "Days Between Changes": _days_between(
                event.get("previous_change_at"), event.get("changed_at")),
            "Attributed Requirement ID": event.get("attributed_requirement_id", ""),
            "First Seen": event.get("first_seen", ""),
            "Change Count": event.get("change_count", ""),
        })
    _write_rows(ws, _HISTORY_HEADERS, rows)


def _write_unmapped_sheet(ws, rows):
    _write_rows(ws, _UNMAPPED_HEADERS, rows)


def _write_impact_sheet(ws, rows):
    _write_rows(ws, _IMPACT_HEADERS, rows,
                highlight_column="Review Flag", highlight_value="Yes")
