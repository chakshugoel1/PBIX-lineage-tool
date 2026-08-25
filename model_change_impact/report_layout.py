"""
report_layout.py

Generic, read-only parser for a PBIX's report definition (pages, visuals,
field/measure bindings, filters). Nothing here assumes a specific report's
page names, visual IDs, or field names - it works off whatever structure
is present in the given PBIX, so the same code supports any file.

Supports both report-layout formats a PBIX can use:
- "pbir": the modern multi-file format (Power BI enhanced Report format),
  where each page/visual is its own small JSON part inside the pbix zip.
  This is the well-verified path (checked against a real, large PBIX).
- "legacy_layout": the older single-blob `Report/Layout` part. Parsing here
  is best-effort (no real legacy-format sample was available while writing
  this), so callers should treat `unsupported_reason` / lower-confidence
  results as a signal to flag affected visuals for manual review.

`build_report_layout()` is the only entry point most callers need.
"""
import datetime
import json
import os
import zipfile

PBIR_PAGES_INDEX = "Report/definition/pages/pages.json"
LEGACY_LAYOUT_PART = "Report/Layout"

_KPI_LIKE_TYPES = {"card", "multirowcard", "cardvisual", "gauge"}
_KPI_LIKE_SUBSTRINGS = ("card", "kpi", "gauge")


def build_report_layout(pbix_path):
    """Read `pbix_path` (a zip container) and return a JSON-serializable
    dict describing its report pages/visuals/field bindings/filters."""
    with zipfile.ZipFile(pbix_path) as zf:
        names = set(zf.namelist())
        if PBIR_PAGES_INDEX in names:
            parsed = _parse_pbir(zf, names)
        elif LEGACY_LAYOUT_PART in names:
            parsed = _parse_legacy(zf)
        else:
            parsed = {
                "format": "none",
                "pages": [],
                "unsupported_reason": "No report layout part (PBIR or legacy) found in this PBIX.",
            }
    return {
        "source_file": _safe_basename(pbix_path),
        "extracted_at": datetime.datetime.now().isoformat(timespec="seconds"),
        **parsed,
    }


def _safe_basename(pbix_path):
    """`pbix_path` is normally a filesystem path, but tests pass an
    in-memory file-like object instead - fall back to "" rather than
    raising in that case."""
    try:
        return os.path.basename(pbix_path)
    except TypeError:
        return ""


# ---------------------------------------------------------------------------
# Shared field-reference extraction (used by both PBIR and legacy parsing,
# since both formats express Column/Measure references the same way).
# ---------------------------------------------------------------------------

def _entity_and_property(column_or_measure):
    expression = column_or_measure.get("Expression", {}) if isinstance(column_or_measure, dict) else {}
    source_ref = expression.get("SourceRef", {}) if isinstance(expression, dict) else {}
    entity = source_ref.get("Entity") if isinstance(source_ref, dict) else None
    prop = column_or_measure.get("Property") if isinstance(column_or_measure, dict) else None
    return entity, prop


def _extract_field_refs(node):
    """Recursively find Column/Measure/HierarchyLevel references inside a
    query-definition fragment. Recursing (rather than hardcoding every
    wrapper shape like Aggregation/Percentile) keeps this generic across
    visual types and future query-shape variations."""
    refs = []
    if isinstance(node, dict):
        if isinstance(node.get("Column"), dict):
            entity, prop = _entity_and_property(node["Column"])
            if entity or prop:
                refs.append({"kind": "column", "table": entity, "field": prop})
        elif isinstance(node.get("Measure"), dict):
            entity, prop = _entity_and_property(node["Measure"])
            if entity or prop:
                refs.append({"kind": "measure", "table": entity, "field": prop})
        elif isinstance(node.get("HierarchyLevel"), dict):
            hl = node["HierarchyLevel"]
            hierarchy_expr = hl.get("Expression", {}).get("Hierarchy", {})
            entity = hierarchy_expr.get("Expression", {}).get("SourceRef", {}).get("Entity")
            hierarchy_name = hierarchy_expr.get("Hierarchy")
            level = hl.get("Level")
            if entity or hierarchy_name or level:
                refs.append({
                    "kind": "hierarchy_level",
                    "table": entity,
                    "field": f"{hierarchy_name}.{level}" if hierarchy_name and level else level,
                })
        else:
            for value in node.values():
                refs.extend(_extract_field_refs(value))
    elif isinstance(node, list):
        for item in node:
            refs.extend(_extract_field_refs(item))
    return refs


def _classify_kpi(visual_type):
    """Heuristic KPI/card/gauge classification. Native types are a certain
    match; custom-visual type strings just containing "card"/"kpi"/"gauge"
    are flagged too, but only as a heuristic - arbitrary custom visual GUIDs
    can't be fully classified by name alone."""
    if not visual_type:
        return None
    normalized = visual_type.lower()
    if normalized in _KPI_LIKE_TYPES:
        return "certain"
    if any(sub in normalized for sub in _KPI_LIKE_SUBSTRINGS):
        return "heuristic"
    return None


def _extract_filters(filters_list):
    out = []
    for f in filters_list or []:
        out.append({
            "name": f.get("name"),
            "type": f.get("type"),
            "fields": _extract_field_refs(f),
        })
    return out


# ---------------------------------------------------------------------------
# PBIR (modern multi-file) format
# ---------------------------------------------------------------------------

def _read_json(zf, name):
    return json.loads(zf.read(name).decode("utf-8-sig"))


def _parse_pbir(zf, names):
    pages_index = _read_json(zf, PBIR_PAGES_INDEX)
    pages = []
    for order, page_id in enumerate(pages_index.get("pageOrder", [])):
        page_part = f"Report/definition/pages/{page_id}/page.json"
        if page_part not in names:
            continue
        page_json = _read_json(zf, page_part)
        pages.append({
            "page_id": page_id,
            "display_name": page_json.get("displayName", page_id),
            "order": order,
            "filters": _extract_filters(page_json.get("filterConfig", {}).get("filters", [])),
            "visuals": _parse_pbir_visuals(zf, names, page_id),
        })
    return {"format": "pbir", "pages": pages, "unsupported_reason": None}


def _parse_pbir_visuals(zf, names, page_id):
    prefix = f"Report/definition/pages/{page_id}/visuals/"
    suffix = "/visual.json"
    visuals = []
    for name in sorted(names):
        if not (name.startswith(prefix) and name.endswith(suffix)):
            continue
        visual_id = name[len(prefix):-len(suffix)]
        if "/" in visual_id:
            continue  # unexpected nested structure - skip rather than misparse
        visuals.append(_build_pbir_visual(visual_id, _read_json(zf, name)))
    return visuals


def _build_pbir_visual(visual_id, visual_json):
    parent_group_id = visual_json.get("parentGroupName")
    if "visual" in visual_json:
        v = visual_json["visual"]
        visual_type = v.get("visualType")
        query_state = v.get("query", {}).get("queryState", {})
        fields = []
        for role, role_obj in (query_state or {}).items():
            for proj in role_obj.get("projections", []):
                for ref in _extract_field_refs(proj.get("field", {})):
                    fields.append({**ref, "role": role})
        return {
            "visual_id": visual_id,
            "kind": "visual",
            "visual_type": visual_type,
            "parent_group_id": parent_group_id,
            "kpi_classification": _classify_kpi(visual_type),
            "fields": fields,
            "filters": _extract_filters(visual_json.get("filterConfig", {}).get("filters", [])),
        }
    group = visual_json.get("visualGroup", {})
    return {
        "visual_id": visual_id,
        "kind": "visualGroup",
        "display_name": group.get("displayName"),
        "group_mode": group.get("groupMode"),
        "parent_group_id": parent_group_id,
        "fields": [],
        "filters": [],
    }


# ---------------------------------------------------------------------------
# Legacy (single-blob Report/Layout) format - best-effort
# ---------------------------------------------------------------------------

def _read_json_legacy(zf, name):
    raw = zf.read(name)
    for encoding in ("utf-16", "utf-8-sig", "utf-8"):
        try:
            return json.loads(raw.decode(encoding))
        except (UnicodeError, json.JSONDecodeError):
            continue
    raise ValueError("could not decode Report/Layout as utf-16, utf-8-sig, or utf-8")


def _parse_legacy(zf):
    try:
        layout = _read_json_legacy(zf, LEGACY_LAYOUT_PART)
    except Exception as exc:
        return {
            "format": "legacy_layout",
            "pages": [],
            "unsupported_reason": f"Legacy Report/Layout part could not be parsed: {exc}",
        }
    pages = []
    for order, section in enumerate(layout.get("sections", [])):
        pages.append({
            "page_id": section.get("name", str(order)),
            "display_name": section.get("displayName", section.get("name", str(order))),
            "order": order,
            # Legacy page-level filters live in a JSON-string `section["filters"]`
            # field; not parsed yet - treat as manual-review via unsupported_reason.
            "filters": [],
            "visuals": [_build_legacy_visual(vc) for vc in section.get("visualContainers", [])],
        })
    return {
        "format": "legacy_layout",
        "pages": pages,
        "unsupported_reason": (
            "Legacy single-blob Report/Layout format detected. Parsing is "
            "best-effort (field bindings/KPI classification are less battle-"
            "tested than the PBIR path) - flag results for manual review."
        ),
    }


def _build_legacy_visual(vc):
    visual_id = vc.get("name", "")
    parent_group_id = vc.get("parentGroupName")
    config = vc.get("config")
    if isinstance(config, str):
        try:
            config = json.loads(config)
        except (TypeError, ValueError):
            config = {}
    if not isinstance(config, dict):
        config = {}

    single_visual = config.get("singleVisual")
    if not single_visual:
        return {
            "visual_id": visual_id,
            "kind": "visualGroup",
            "display_name": config.get("name"),
            "group_mode": None,
            "parent_group_id": parent_group_id,
            "fields": [],
            "filters": [],
        }

    visual_type = single_visual.get("visualType")
    select = single_visual.get("prototypeQuery", {}).get("Select", [])
    select_by_name = {}
    for entry in select:
        name = entry.get("Name")
        if not name:
            continue
        refs = _extract_field_refs(entry)
        if refs:
            select_by_name[name] = refs[0]

    fields = []
    for role, proj_list in (single_visual.get("projections") or {}).items():
        for proj in proj_list:
            ref = select_by_name.get(proj.get("queryRef"))
            if ref:
                fields.append({**ref, "role": role})

    return {
        "visual_id": visual_id,
        "kind": "visual",
        "visual_type": visual_type,
        "parent_group_id": parent_group_id,
        "kpi_classification": _classify_kpi(visual_type),
        "fields": fields,
        # Legacy visual-level filters live in a JSON-string `vc["filters"]`
        # field; not parsed yet - manual review via unsupported_reason.
        "filters": [],
    }
