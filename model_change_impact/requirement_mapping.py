"""Requirement-to-object mapping at the report grain users can point at.

The requirements workbook declares scope via page names, visual display
names, and visual IDs (the same names shown in the Baseline Estimation
Visual Inventory sheet) - never model internals. Resolution order:

1. Visuals named/ID'd in ``Impacted Visual IDs`` / ``Impacted Visuals`` ->
   mapped directly (``Visual ID`` / ``Visual Name``, High confidence).
2. Pages named in ``Impacted Pages`` -> every real visual on the page
   (``Page``, High confidence).
3. PBIX tags: ``[R-104]`` in a visual's title/display name (``Tag``, High).
4. Every mapped visual seeds the model objects it is bound to (the measures/
   columns in its field wells) - this is how the tool discovers the impacted
   model objects the user cannot reasonably know.
5. Seeded objects expand through the existing impact engine (DAX dependency
   graph + visual bindings); derived objects and visuals are recorded as
   ``Dependency`` / Medium confidence.

There is deliberately NO keyword/text inference: a requirement with no
matching scope entries gets zero mappings plus a warning, instead of a noisy
guess. ``build_requirement_mapping()`` is the only entry point most callers
need.

Dependency-expanded visuals are surfaced only when they are DIRECTLY bound to
an impacted object (``matched_via == "direct"``); transitive-chain visuals and
slicer-type visuals (filters, not content) are dropped from the requirement's
visual set so the count reflects reviewable impact, not report-wide filter
fan-out. Objects (measures/columns) are always expanded in full - the filter
only applies to visuals. Declared visuals (IDs/names/pages/tags) are never
filtered.
"""
import re

from model_change_impact import impact
from model_change_impact.baseline_estimation import build_selection_diff
from model_change_impact.history_store import object_key

_CONFIDENCE_RANK = {"Low": 1, "Medium": 2, "High": 3}
_SOURCE_CONFIDENCE = {"Tag": "High", "Requirement Number": "High", "Visual ID": "High", "Visual Name": "High",
                      "Page": "High", "Dependency": "Medium"}

_BRACKET_TAG_RE = re.compile(r"\[([^\[\]]{1,64})\]")

# Visual types that are filter controls, not content - excluded from
# dependency-surfaced visuals (they repeat on every page and drown the count).
_SLICER_TYPE_TOKENS = ("slicer", "textfilter")


def build_requirement_mapping(snapshot, report_layout, requirements, include_inactive=False):
    """Map every requirement to model objects and visuals.

    Returns ``{requirement_id: {"objects": {(kind, table, name): {"source",
    "confidence"}}, "visuals": {(page_id, visual_id): {"source", "confidence",
    ...display fields...}}, "warnings": [...]}}``. Inactive requirements
    (Status Done/Cancelled) get an empty entry so they still appear in the
    summary sheet.
    """
    known_ids = {req["id"].casefold(): req["id"] for req in requirements}
    visual_index = _visual_index(report_layout)
    visuals_by_name = {}
    for key, visual in visual_index.items():
        name = (visual.get("visual_display_name") or "").casefold()
        if name:
            visuals_by_name.setdefault(name, []).append(key)
    visuals_by_id = {str(visual.get("visual_id")).casefold(): key
                     for key, visual in visual_index.items() if visual.get("visual_id")}
    visuals_by_requirement_number = {
        f"REQ-{str(visual.get('visual_id'))}".casefold(): key
        for key, visual in visual_index.items() if visual.get("visual_id")
    }
    pages_by_name = {
        (page.get("display_name") or page.get("page_id") or "").casefold(): page
        for page in report_layout.get("pages", [])
    }

    mapping = {}
    for requirement in requirements:
        entry = {"objects": {}, "visuals": {}, "warnings": []}
        mapping[requirement["id"]] = entry
        if not requirement.get("active", True) and not include_inactive:
            continue
        req_id = requirement["id"]

        requirement_key = visuals_by_requirement_number.get(req_id.casefold())
        if requirement_key is not None:
            _map_visual(entry, visual_index, requirement_key, "Requirement Number")

        # PBIX tags: [R-ID] in the visual's display name.
        for visual_key, visual in visual_index.items():
            if req_id in _tagged_ids_in_brackets(visual.get("visual_display_name"), known_ids):
                _map_visual(entry, visual_index, visual_key, "Tag")

        # Explicit scope columns.
        for name in _split_scope(requirement.get("impacted_visual_ids")):
            key = visuals_by_id.get(name.casefold())
            if key is None:
                entry["warnings"].append(
                    f"{req_id}: impacted visual ID '{name}' not found in the report layout.")
            else:
                _map_visual(entry, visual_index, key, "Visual ID")
        for name in _split_scope(requirement.get("impacted_visuals")):
            keys = visuals_by_name.get(name.casefold(), [])
            if not keys:
                entry["warnings"].append(
                    f"{req_id}: impacted visual '{name}' not found in the report layout.")
            for key in keys:
                _map_visual(entry, visual_index, key, "Visual Name")
        for name in _split_scope(requirement.get("impacted_pages")):
            page = pages_by_name.get(name.casefold())
            if page is None:
                entry["warnings"].append(
                    f"{req_id}: impacted page '{name}' not found in the report layout.")
                continue
            for visual_key in _page_visual_keys(page, visual_index):
                _map_visual(entry, visual_index, visual_key, "Page")

        if not entry["visuals"]:
            entry["warnings"].append(
                f"{req_id}: no visuals or pages matched - requirement maps to nothing.")

        # Dependency expansion through the existing impact engine.
        _expand_requirement(entry, snapshot, report_layout)

    return mapping


def build_object_attribution(mapping):
    """Flatten the mapping to ``{history object_key: "R-1; R-2"}`` covering
    model objects, visuals and pages - used to attribute history events."""
    attribution = {}
    for req_id, entry in mapping.items():
        for kind, table, name in entry["objects"]:
            key = object_key(kind, table) if kind == "table" else object_key(kind, table, name)
            attribution.setdefault(key, []).append(req_id)
        for page_id, visual_id in entry["visuals"]:
            attribution.setdefault(object_key("visual", page_id, visual_id), []).append(req_id)
            attribution.setdefault(object_key("page", page_id), []).append(req_id)
    return {key: "; ".join(sorted(set(ids))) for key, ids in attribution.items()}


def validate_visual_requirement_assignments(mapping):
    """Return warnings when a Visual ID is assigned to multiple requirements."""
    assignments = {}
    for requirement_id, entry in mapping.items():
        for visual_key in entry.get("visuals", {}):
            assignments.setdefault(visual_key, []).append(requirement_id)
    warnings = []
    for (page_id, visual_id), requirement_ids in sorted(assignments.items(), key=str):
        unique_ids = sorted(set(requirement_ids), key=str.casefold)
        if len(unique_ids) > 1:
            warnings.append(
                f"Visual ID '{visual_id}' on page '{page_id}' is assigned to multiple "
                f"requirements: {', '.join(unique_ids)}. One Visual ID must map to one requirement.")
    return warnings


# ---------------------------------------------------------------------------
# Resolution primitives
# ---------------------------------------------------------------------------

def _split_scope(text):
    """Split a comma-separated scope cell into trimmed, non-empty entries."""
    return [part.strip() for part in (text or "").split(",") if part.strip()]


def _visual_index(report_layout):
    index = {}
    for page in report_layout.get("pages", []):
        for visual in page.get("visuals", []):
            if visual.get("kind") != "visual":
                continue
            index[(page.get("page_id"), visual.get("visual_id"))] = {
                "page_id": page.get("page_id"),
                "page_display_name": page.get("display_name"),
                "visual_id": visual.get("visual_id"),
                "visual_display_name": visual.get("display_name"),
                "visual_type": visual.get("visual_type"),
                "kpi_classification": visual.get("kpi_classification"),
                "fields": visual.get("fields", []),
            }
    return index


def _page_visual_keys(page, visual_index):
    return [key for key in visual_index if key[0] == page.get("page_id")]


def _visual_extra(visual, matched_via=None):
    return {
        "page_display_name": visual.get("page_display_name") or "",
        "visual_display_name": visual.get("visual_display_name") or "",
        "visual_type": visual.get("visual_type") or "",
        "kpi_classification": visual.get("kpi_classification"),
        "matched_via": matched_via,
    }


def _add(entry, collection, key, source, extra=None):
    confidence = _SOURCE_CONFIDENCE[source]
    existing = entry[collection].get(key)
    if existing is None or _CONFIDENCE_RANK[confidence] > _CONFIDENCE_RANK[existing["confidence"]]:
        entry[collection][key] = {"source": source, "confidence": confidence, **(extra or {})}


def _map_visual(entry, visual_index, visual_key, source):
    """Map a visual and the model objects it is bound to (field wells)."""
    visual = visual_index[visual_key]
    _add(entry, "visuals", visual_key, source, extra=_visual_extra(visual))
    for field in visual.get("fields", []):
        if field.get("kind") in ("column", "measure") and field.get("table") and field.get("field"):
            _add(entry, "objects", (field["kind"], field["table"], field["field"]), source)


def _tagged_ids_in_brackets(text, known_ids):
    found = set()
    for content in _BRACKET_TAG_RE.findall(text or ""):
        actual = known_ids.get(content.strip().casefold())
        if actual:
            found.add(actual)
    return found


# ---------------------------------------------------------------------------
# Dependency expansion
# ---------------------------------------------------------------------------

def _is_slicer_type(visual_type):
    folded = (visual_type or "").casefold()
    return any(token in folded for token in _SLICER_TYPE_TOKENS)


def _expand_requirement(entry, snapshot, report_layout):
    selections = [
        {"kind": kind, "table": table, "name": name}
        for kind, table, name in entry["objects"]
    ]
    if not selections:
        return
    diff_result = build_selection_diff(snapshot, selections)
    impact_result = impact.analyze_impact(snapshot, snapshot, diff_result, report_layout)
    for section, records in impact_result.items():
        for record in records:
            if section == "tables":
                # A table-level seed implicitly covers all of its columns.
                table_name = record["detail"]["identity_after"]["table"]
                for column in snapshot.get("tables", {}).get(table_name, {}).get("columns", []):
                    _add(entry, "objects", ("column", table_name, column["name"]), "Dependency")
            for dependent in record.get("dependent_objects", []):
                _add(entry, "objects",
                     (dependent["kind"], dependent["table"], dependent["name"]), "Dependency")
            for visual in record.get("impacted_visuals", []):
                # Only surface visuals directly bound to an impacted object,
                # and skip slicer-type filter controls - transitive-chain and
                # slicer matches are report-wide fan-out, not reviewable impact.
                if visual.get("matched_via") != "direct":
                    continue
                if _is_slicer_type(visual.get("visual_type")):
                    continue
                visual_key = (visual.get("page_id"), visual.get("visual_id"))
                _add(entry, "visuals", visual_key, "Dependency",
                     extra=_visual_extra(visual, matched_via=visual.get("matched_via")))
