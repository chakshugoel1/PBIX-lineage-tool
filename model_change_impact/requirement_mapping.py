"""Hybrid requirement-to-object mapping.

Three mapping legs, by decreasing trust:

1. Explicit tags inside the PBIX: ``[R-104]`` in a visual's title/display
   name, ``REQ:R-104`` (comma-separated for several) in a measure/table/
   column description. -> ``Tag`` / High confidence.
2. The requirements file's ``Seed Objects`` column (``measure:Name``,
   ``table:Name``, ``column:Table[Col]``, ``visual:Name``, ``page:Name``).
   -> ``Seed`` / High confidence.
3. Keyword inference: significant tokens from the requirement Title +
   Description matched against object names and visual display names.
   -> ``Inferred`` / Low confidence (the human-review queue).

Whatever a requirement touches is then expanded through the existing impact
engine (DAX dependency graph + visual bindings); derived objects and visuals
are recorded as ``Dependency`` / Medium confidence.

``build_requirement_mapping()`` is the only entry point most callers need.
"""
import re

from model_change_impact import impact
from model_change_impact.baseline_estimation import build_selection_diff
from model_change_impact.history_store import object_key

_CONFIDENCE_RANK = {"Low": 1, "Medium": 2, "High": 3}
_SOURCE_CONFIDENCE = {"Tag": "High", "Seed": "High", "Dependency": "Medium", "Inferred": "Low"}

_SEED_KINDS = ("measure", "table", "column", "visual", "page")

_COLUMN_REF_RE = re.compile(r"^\s*(?P<table>[^\[\]]+?)\s*\[\s*(?P<name>[^\[\]]+?)\s*\]\s*$")
_DESC_TAG_RE = re.compile(r"REQ\s*:\s*([^\n]+)", re.IGNORECASE)
_BRACKET_TAG_RE = re.compile(r"\[([^\[\]]{1,64})\]")
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")

# Generic words that would otherwise match half the model.
_STOPWORDS = {
    "this", "that", "with", "from", "should", "must", "have", "will", "would",
    "into", "when", "then", "than", "they", "them", "there", "where", "which",
    "report", "page", "pages", "visual", "visuals", "measure", "measures",
    "table", "tables", "column", "columns", "data", "show", "shows", "display",
    "displays", "need", "needs", "user", "users", "able", "change", "changes",
    "changed", "update", "updates", "updated", "add", "added", "remove",
    "removed", "create", "created", "existing", "current", "currently",
    "each", "every", "only", "also", "more", "most", "some", "such", "like",
    "want", "make", "made", "using", "used", "based", "given", "per",
}


def build_requirement_mapping(snapshot, report_layout, requirements):
    """Map every requirement to model objects and visuals.

    Returns ``{requirement_id: {"objects": {(kind, table, name): {"source",
    "confidence"}}, "visuals": {(page_id, visual_id): {"source", "confidence",
    ...display fields...}}, "warnings": [...]}}``. Inactive requirements
    (Status Done/Cancelled) get an empty entry so they still appear in the
    summary sheet.
    """
    known_ids = {req["id"].casefold(): req["id"] for req in requirements}
    model_objects = _iter_model_objects(snapshot)
    visual_index = _visual_index(report_layout)
    pages_by_name = {
        (page.get("display_name") or page.get("page_id") or "").casefold(): page
        for page in report_layout.get("pages", [])
    }

    mapping = {}
    for requirement in requirements:
        entry = {"objects": {}, "visuals": {}, "warnings": []}
        mapping[requirement["id"]] = entry
        if not requirement.get("active", True):
            continue
        req_id = requirement["id"]

        # Leg 1: explicit tags inside the PBIX.
        for kind, table, name, details in model_objects:
            if req_id in _tagged_ids_in_description(details.get("description"), known_ids):
                _add(entry, "objects", (kind, table, name), "Tag")
        for visual_key, visual in visual_index.items():
            if req_id in _tagged_ids_in_brackets(visual.get("visual_display_name"), known_ids):
                _add(entry, "visuals", visual_key, "Tag", extra=_visual_extra(visual))
                _seed_visual_fields(entry, visual, "Tag")

        # Leg 2: Seed Objects column in the requirements file.
        for kind, value in _parse_seed_objects(requirement.get("seed_objects")):
            _resolve_seed(entry, kind, value, req_id, snapshot, visual_index, pages_by_name)

        # Leg 3: keyword inference from Title + Description.
        tokens = _keyword_tokens(requirement.get("title", "") + " " + requirement.get("description", ""))
        if tokens:
            for kind, table, name, _details in model_objects:
                if _matches_any(name, tokens):
                    _add(entry, "objects", (kind, table, name), "Inferred")
            for visual_key, visual in visual_index.items():
                if _matches_any(visual.get("visual_display_name") or "", tokens):
                    _add(entry, "visuals", visual_key, "Inferred", extra=_visual_extra(visual))

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


# ---------------------------------------------------------------------------
# Mapping primitives
# ---------------------------------------------------------------------------

def _add(entry, collection, key, source, extra=None):
    confidence = _SOURCE_CONFIDENCE[source]
    existing = entry[collection].get(key)
    if existing is None or _CONFIDENCE_RANK[confidence] > _CONFIDENCE_RANK[existing["confidence"]]:
        entry[collection][key] = {"source": source, "confidence": confidence, **(extra or {})}


def _iter_model_objects(snapshot):
    objects = []
    for table_name, table in snapshot.get("tables", {}).items():
        objects.append(("table", table_name, table_name, table))
        for column in table.get("columns", []):
            objects.append(("column", table_name, column["name"], column))
    for table_name, measures in snapshot.get("measures", {}).items():
        for measure_name, measure in measures.items():
            objects.append(("measure", table_name, measure_name, measure))
    return objects


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


def _visual_extra(visual, matched_via=None):
    return {
        "page_display_name": visual.get("page_display_name") or "",
        "visual_display_name": visual.get("visual_display_name") or "",
        "visual_type": visual.get("visual_type") or "",
        "kpi_classification": visual.get("kpi_classification"),
        "matched_via": matched_via,
    }


def _tagged_ids_in_brackets(text, known_ids):
    found = set()
    for content in _BRACKET_TAG_RE.findall(text or ""):
        actual = known_ids.get(content.strip().casefold())
        if actual:
            found.add(actual)
    return found


def _tagged_ids_in_description(text, known_ids):
    found = set()
    for chunk in _DESC_TAG_RE.findall(text or ""):
        for token in re.split(r"[,;]", chunk):
            actual = known_ids.get(token.strip().casefold())
            if actual:
                found.add(actual)
    return found


def _keyword_tokens(text):
    tokens = set()
    for token in _TOKEN_RE.findall(text or ""):
        folded = token.casefold()
        if len(folded) >= 4 and folded not in _STOPWORDS:
            tokens.add(folded)
    return sorted(tokens)


def _matches_any(name, tokens):
    folded = name.casefold()
    return any(token in folded for token in tokens)


# ---------------------------------------------------------------------------
# Seed Objects resolution
# ---------------------------------------------------------------------------

def _parse_seed_objects(text):
    """Parse the Seed Objects cell: comma-separated ``kind:value`` entries.
    Returns a list of (kind, value); kind is None for unparseable chunks."""
    seeds = []
    for chunk in (text or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" not in chunk:
            seeds.append((None, chunk))
            continue
        kind, value = chunk.split(":", 1)
        seeds.append((kind.strip().casefold(), value.strip()))
    return seeds


def _resolve_seed(entry, kind, value, req_id, snapshot, visual_index, pages_by_name):
    if kind not in _SEED_KINDS or not value:
        entry["warnings"].append(
            f"{req_id}: unrecognized Seed Objects entry "
            f"'{kind + ':' if kind else ''}{value}' (expected measure:/table:/column:/visual:/page:)."
        )
        return

    if kind == "measure":
        hits = [(table, name) for table, measures in snapshot.get("measures", {}).items()
                for name in measures if name.casefold() == value.casefold()]
        for table, name in hits:
            _add(entry, "objects", ("measure", table, name), "Seed")
        if not hits:
            entry["warnings"].append(f"{req_id}: seed measure '{value}' not found in the model.")
    elif kind == "table":
        match = next((t for t in snapshot.get("tables", {}) if t.casefold() == value.casefold()), None)
        if match is None:
            entry["warnings"].append(f"{req_id}: seed table '{value}' not found in the model.")
        else:
            _add(entry, "objects", ("table", match, match), "Seed")
    elif kind == "column":
        ref = _COLUMN_REF_RE.match(value)
        hits = []
        for table, table_details in snapshot.get("tables", {}).items():
            if ref and table.casefold() != ref.group("table").strip().casefold():
                continue
            wanted = (ref.group("name") if ref else value).strip().casefold()
            for column in table_details.get("columns", []):
                if column["name"].casefold() == wanted:
                    hits.append((table, column["name"]))
        for table, name in hits:
            _add(entry, "objects", ("column", table, name), "Seed")
        if not hits:
            entry["warnings"].append(f"{req_id}: seed column '{value}' not found in the model.")
    elif kind == "visual":
        hits = _find_visuals(visual_index, value)
        for visual_key in hits:
            _add(entry, "visuals", visual_key, "Seed", extra=_visual_extra(visual_index[visual_key]))
            _seed_visual_fields(entry, visual_index[visual_key], "Seed")
        if not hits:
            entry["warnings"].append(f"{req_id}: seed visual '{value}' not found in the report layout.")
    elif kind == "page":
        page = pages_by_name.get(value.casefold())
        if page is None:
            entry["warnings"].append(f"{req_id}: seed page '{value}' not found in the report layout.")
            return
        for visual in page.get("visuals", []):
            if visual.get("kind") != "visual":
                continue
            visual_key = (page.get("page_id"), visual.get("visual_id"))
            if visual_key in visual_index:
                _add(entry, "visuals", visual_key, "Seed", extra=_visual_extra(visual_index[visual_key]))
                _seed_visual_fields(entry, visual_index[visual_key], "Seed")


def _find_visuals(visual_index, name):
    folded = name.casefold()
    exact = [key for key, visual in visual_index.items()
             if (visual.get("visual_display_name") or "").casefold() == folded]
    if exact:
        return exact
    return [key for key, visual in visual_index.items()
            if folded and folded in (visual.get("visual_display_name") or "").casefold()]


def _seed_visual_fields(entry, visual, source):
    """A tagged/seeded visual also maps the model objects it is bound to."""
    for field in visual.get("fields", []):
        if field.get("kind") in ("column", "measure") and field.get("table") and field.get("field"):
            _add(entry, "objects", (field["kind"], field["table"], field["field"]), source)


# ---------------------------------------------------------------------------
# Dependency expansion
# ---------------------------------------------------------------------------

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
                visual_key = (visual.get("page_id"), visual.get("visual_id"))
                _add(entry, "visuals", visual_key, "Dependency",
                     extra=_visual_extra(visual, matched_via=visual.get("matched_via")))
