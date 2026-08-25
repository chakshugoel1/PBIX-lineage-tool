"""
impact.py

Given a baseline snapshot, a changed snapshot, a diff (from `diff.py`), and
the CHANGED file's report layout (from `report_layout.py`), work out what
else is affected by each changed table/column/measure/relationship:

1. Build a DAX dependency graph (which measures/calculated columns
   reference which other measures/columns, purely from expression text -
   no hardcoded object names, this is a generic reference-token scanner).
2. Walk that graph "upward" (to dependents, not dependencies) from every
   changed object to find everything that could be affected, directly or
   transitively.
3. Cross-reference the impacted objects against the report's visual field
   bindings to find which visuals (and pages) are affected, including
   visuals that still reference a REMOVED object (a dangling binding -
   always worth a manual-review flag).

Dependency resolution is heuristic (regex-based token scanning, not a real
DAX parser) - good enough for impact triage, not a substitute for actually
opening the report. `analyze_impact()` is the only entry point most callers
need.
"""
import re

_REF_RE = re.compile(r"(?:'([^']+)'|([A-Za-z_][A-Za-z0-9_]*))?\[([^\[\]]+)\]")


def build_dependency_graph(snapshot):
    """Return a forward dependency graph: {(kind, table, name): {(kind,
    table, name), ...}} mapping each measure/calculated column to the
    other measures/columns its DAX expression references."""
    measure_tables_by_name = {}
    for table, measures in snapshot.get("measures", {}).items():
        for name in measures:
            measure_tables_by_name.setdefault(name, []).append(table)

    columns_by_table = {
        table: {c["name"] for c in t.get("columns", [])}
        for table, t in snapshot.get("tables", {}).items()
    }

    graph = {}
    for table, measures in snapshot.get("measures", {}).items():
        for name, measure in measures.items():
            key = ("measure", table, name)
            graph[key] = _extract_references(
                measure.get("expression") or "", table, measure_tables_by_name, columns_by_table,
            ) - {key}

    for table, t in snapshot.get("tables", {}).items():
        for col in t.get("columns", []):
            if not col.get("is_calculated"):
                continue
            key = ("column", table, col["name"])
            graph[key] = _extract_references(
                col.get("expression") or "", table, measure_tables_by_name, columns_by_table,
            ) - {key}

    return graph


def _extract_references(expression, current_table, measure_tables_by_name, columns_by_table):
    refs = set()
    for quoted, unquoted, name in _REF_RE.findall(expression):
        table_token = quoted or unquoted or None
        if table_token:
            if name in columns_by_table.get(table_token, ()):
                refs.add(("column", table_token, name))
            elif table_token in measure_tables_by_name.get(name, ()):
                refs.add(("measure", table_token, name))
            # else: unresolved reference (e.g. a function arg or unknown
            # table) - skip rather than guess
        else:
            if name in measure_tables_by_name:
                for table in measure_tables_by_name[name]:
                    refs.add(("measure", table, name))
            elif name in columns_by_table.get(current_table, ()):
                refs.add(("column", current_table, name))
    return refs


def _reverse_of(forward_graph):
    reverse = {}
    for src, targets in forward_graph.items():
        for target in targets:
            reverse.setdefault(target, set()).add(src)
    return reverse


def _merge_reverse_graphs(g1, g2):
    merged = {k: set(v) for k, v in g1.items()}
    for k, v in g2.items():
        merged.setdefault(k, set()).update(v)
    return merged


def _bfs_impacted(seed_keys, reverse_graph):
    visited = set(seed_keys)
    queue = list(seed_keys)
    while queue:
        current = queue.pop()
        for dependent in reverse_graph.get(current, ()):
            if dependent not in visited:
                visited.add(dependent)
                queue.append(dependent)
    return visited


def _table_column_keys(snapshot, table_name):
    if not snapshot:
        return set()
    table = snapshot.get("tables", {}).get(table_name)
    if not table:
        return set()
    return {("column", table_name, c["name"]) for c in table.get("columns", [])}


def _build_field_index(report_layout):
    index = {}
    for page in report_layout.get("pages", []):
        for visual in page.get("visuals", []):
            if visual.get("kind") != "visual":
                continue
            for field in visual.get("fields", []):
                kind = field.get("kind")
                if kind not in ("column", "measure"):
                    continue  # hierarchy_level refs aren't a single resolvable model object
                key = (kind, field.get("table"), field.get("field"))
                index.setdefault(key, []).append({
                    "page_id": page.get("page_id"),
                    "page_display_name": page.get("display_name"),
                    "visual_id": visual.get("visual_id"),
                    "visual_type": visual.get("visual_type"),
                    "kpi_classification": visual.get("kpi_classification"),
                })
    return index


def _find_visuals_for_keys(keys, field_index, seed_keys):
    matches = {}
    for key in keys:
        for visual_ref in field_index.get(key, ()):
            via = "direct" if key in seed_keys else "transitive_dependency"
            dedup_key = (visual_ref["page_id"], visual_ref["visual_id"])
            existing = matches.get(dedup_key)
            if existing is None or (existing["matched_via"] != "direct" and via == "direct"):
                matches[dedup_key] = {
                    **visual_ref,
                    "matched_via": via,
                    "matched_object": {"kind": key[0], "table": key[1], "name": key[2]},
                }
    return sorted(matches.values(), key=lambda v: (v["page_id"] or "", v["visual_id"] or ""))


def _build_impact_record(change_type, detail, seed_keys, reverse_graph, field_index):
    impacted_set = _bfs_impacted(seed_keys, reverse_graph)
    dependent_objects = sorted(
        ({"kind": k[0], "table": k[1], "name": k[2]} for k in impacted_set - seed_keys),
        key=lambda o: (o["kind"], o["table"], o["name"]),
    )
    return {
        "change_type": change_type,
        "detail": detail,
        "dependent_objects": dependent_objects,
        "impacted_visuals": _find_visuals_for_keys(impacted_set, field_index, seed_keys),
    }


def _entity_diff_records(section_diff, kind, reverse_graph, field_index):
    records = []
    for item in section_diff["added"]:
        seed = {(kind, item["table"], item["name"])}
        records.append(_build_impact_record("added", item, seed, reverse_graph, field_index))
    for item in section_diff["removed"]:
        seed = {(kind, item["table"], item["name"])}
        records.append(_build_impact_record("removed", item, seed, reverse_graph, field_index))
    for item in section_diff["changed"]:
        change_type = "renamed" if item["is_rename_candidate"] else "modified"
        seed = {
            (kind, item["identity_before"]["table"], item["identity_before"]["name"]),
            (kind, item["identity_after"]["table"], item["identity_after"]["name"]),
        }
        records.append(_build_impact_record(change_type, item, seed, reverse_graph, field_index))
    return records


def _table_diff_records(table_diff, baseline_snapshot, changed_snapshot, reverse_graph, field_index):
    records = []
    for item in table_diff["added"]:
        seed = _table_column_keys(changed_snapshot, item["table"])
        records.append(_build_impact_record("added", item, seed, reverse_graph, field_index))
    for item in table_diff["removed"]:
        seed = _table_column_keys(baseline_snapshot, item["table"])
        records.append(_build_impact_record("removed", item, seed, reverse_graph, field_index))
    for item in table_diff["changed"]:
        change_type = "renamed" if item["is_rename_candidate"] else "modified"
        seed = (
            _table_column_keys(baseline_snapshot, item["identity_before"]["table"])
            | _table_column_keys(changed_snapshot, item["identity_after"]["table"])
        )
        records.append(_build_impact_record(change_type, item, seed, reverse_graph, field_index))
    return records


def _relationship_diff_records(rel_diff, baseline_snapshot, changed_snapshot, reverse_graph, field_index):
    """Coarse, heuristic impact: a relationship change can alter filter
    propagation across its two tables, so every column of both tables is
    treated as a (broad) seed - this is intentionally over-inclusive and
    should be treated as a manual-review signal, not a precise result."""
    records = []
    for item in rel_diff["added"]:
        seed = _table_column_keys(changed_snapshot, item["from_table"]) | _table_column_keys(changed_snapshot, item["to_table"])
        records.append(_build_impact_record("added", item, seed, reverse_graph, field_index))
    for item in rel_diff["removed"]:
        seed = _table_column_keys(baseline_snapshot, item["from_table"]) | _table_column_keys(baseline_snapshot, item["to_table"])
        records.append(_build_impact_record("removed", item, seed, reverse_graph, field_index))
    for item in rel_diff["changed"]:
        from_table = item["identity_after"]["from_table"]
        to_table = item["identity_after"]["to_table"]
        seed = _table_column_keys(changed_snapshot, from_table) | _table_column_keys(changed_snapshot, to_table)
        records.append(_build_impact_record("modified", item, seed, reverse_graph, field_index))
    return records


def analyze_impact(baseline_snapshot, changed_snapshot, diff_result, report_layout):
    """Return `{measures, columns, tables, relationships}`, each a list of
    impact records: `{change_type, detail, dependent_objects,
    impacted_visuals}`. `detail` is the corresponding entry from
    `diff_result` (added/removed item, or a `changed` record)."""
    reverse_graph = _merge_reverse_graphs(
        _reverse_of(build_dependency_graph(baseline_snapshot)),
        _reverse_of(build_dependency_graph(changed_snapshot)),
    )
    field_index = _build_field_index(report_layout)

    return {
        "measures": _entity_diff_records(diff_result["measures"], "measure", reverse_graph, field_index),
        "columns": _entity_diff_records(diff_result["columns"], "column", reverse_graph, field_index),
        "tables": _table_diff_records(diff_result["tables"], baseline_snapshot, changed_snapshot, reverse_graph, field_index),
        "relationships": _relationship_diff_records(
            diff_result["relationships"], baseline_snapshot, changed_snapshot, reverse_graph, field_index,
        ),
    }
