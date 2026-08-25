"""
diff.py

Compare two snapshots produced by `snapshot.build_snapshot()` (a baseline
and a changed PBIX) and classify every table/column/measure/relationship as
added, removed, changed, or unchanged. Nothing here is specific to any one
model - it only knows the snapshot schema, not any particular table/column/
measure name, so the same code works for any baseline/changed PBIX pair.

Matching strategy: entities are matched primarily by `lineage_tag` (a
stable GUID Power BI Desktop keeps across renames), falling back to a
name-based key when either side lacks a tag. A lineage_tag match where the
name (and/or table) differs is flagged as a rename candidate rather than
being reported as a separate add+remove.

`diff_snapshots()` is the only entry point most callers need.
"""

_TABLE_IDENTITY_FIELDS = ("table",)
_TABLE_COMPARE_FIELDS = ("is_calculated_table", "m_expression", "is_hidden", "description")

_COLUMN_IDENTITY_FIELDS = ("table", "name")
_COLUMN_COMPARE_FIELDS = (
    "data_type", "is_calculated", "expression", "format_string",
    "is_hidden", "description", "display_folder",
)

_MEASURE_IDENTITY_FIELDS = ("table", "name")
_MEASURE_COMPARE_FIELDS = (
    "expression", "display_folder", "description", "format_string",
    "is_hidden", "kpi_id",
)

_RELATIONSHIP_IDENTITY_FIELDS = ("from_table", "from_column", "to_table", "to_column")
_RELATIONSHIP_COMPARE_FIELDS = (
    "is_active", "cardinality", "cross_filtering_behavior", "rely_on_referential_integrity",
)


def diff_snapshots(baseline, changed):
    """Compare a baseline and changed snapshot (both from
    `snapshot.build_snapshot()`) and return a JSON-serializable diff dict
    with one section per entity type, each shaped as
    `{added: [...], removed: [...], changed: [...], unchanged_count: N}`."""
    return {
        "baseline_file": baseline.get("source_file"),
        "changed_file": changed.get("source_file"),
        "tables": _diff_entities(
            _flatten_tables(baseline), _flatten_tables(changed),
            _TABLE_IDENTITY_FIELDS, _TABLE_COMPARE_FIELDS,
        ),
        "columns": _diff_entities(
            _flatten_columns(baseline), _flatten_columns(changed),
            _COLUMN_IDENTITY_FIELDS, _COLUMN_COMPARE_FIELDS,
        ),
        "measures": _diff_entities(
            _flatten_measures(baseline), _flatten_measures(changed),
            _MEASURE_IDENTITY_FIELDS, _MEASURE_COMPARE_FIELDS,
        ),
        "relationships": _diff_entities(
            list(baseline.get("relationships", [])), list(changed.get("relationships", [])),
            _RELATIONSHIP_IDENTITY_FIELDS, _RELATIONSHIP_COMPARE_FIELDS,
            tag_field=None,  # relationships have no stable lineage_tag in the snapshot
        ),
    }


def _flatten_tables(snapshot):
    out = []
    for table_name, table in snapshot.get("tables", {}).items():
        item = dict(table)
        item.pop("columns", None)  # columns are diffed separately, at column granularity
        item["table"] = table_name
        out.append(item)
    return out


def _flatten_columns(snapshot):
    out = []
    for table_name, table in snapshot.get("tables", {}).items():
        for col in table.get("columns", []):
            item = dict(col)
            item["table"] = table_name
            out.append(item)
    return out


def _flatten_measures(snapshot):
    out = []
    for table_name, measures in snapshot.get("measures", {}).items():
        for measure_name, measure in measures.items():
            item = dict(measure)
            item["table"] = table_name
            item["name"] = measure_name
            out.append(item)
    return out


def _key_of(item, fields):
    return tuple(item.get(f) for f in fields)


def _diff_entities(baseline_items, changed_items, identity_fields, compare_fields, tag_field="lineage_tag"):
    """Match `baseline_items`/`changed_items` (by lineage_tag first, then by
    `identity_fields`) and classify each matched pair as changed/unchanged,
    plus whatever's left over as added/removed."""
    matched_baseline_ids = set()
    matched_changed_ids = set()
    matched_pairs = []

    if tag_field:
        baseline_by_tag = {i[tag_field]: i for i in baseline_items if i.get(tag_field)}
        changed_by_tag = {i[tag_field]: i for i in changed_items if i.get(tag_field)}
        for tag, b_item in baseline_by_tag.items():
            c_item = changed_by_tag.get(tag)
            if c_item is not None:
                matched_pairs.append((b_item, c_item, "lineage_tag"))
                matched_baseline_ids.add(id(b_item))
                matched_changed_ids.add(id(c_item))

    baseline_by_key = {}
    for b_item in baseline_items:
        if id(b_item) not in matched_baseline_ids:
            baseline_by_key.setdefault(_key_of(b_item, identity_fields), b_item)
    changed_by_key = {}
    for c_item in changed_items:
        if id(c_item) not in matched_changed_ids:
            changed_by_key.setdefault(_key_of(c_item, identity_fields), c_item)

    for key, b_item in baseline_by_key.items():
        c_item = changed_by_key.get(key)
        if c_item is not None:
            matched_pairs.append((b_item, c_item, "key"))
            matched_baseline_ids.add(id(b_item))
            matched_changed_ids.add(id(c_item))

    added = [c for c in changed_items if id(c) not in matched_changed_ids]
    removed = [b for b in baseline_items if id(b) not in matched_baseline_ids]

    changed = []
    unchanged_count = 0
    for b_item, c_item, matched_by in matched_pairs:
        field_changes = {}
        for field in compare_fields:
            before, after = b_item.get(field), c_item.get(field)
            if before != after:
                field_changes[field] = {"before": before, "after": after}

        identity_before = {f: b_item.get(f) for f in identity_fields}
        identity_after = {f: c_item.get(f) for f in identity_fields}
        is_rename_candidate = matched_by == "lineage_tag" and identity_before != identity_after

        if field_changes or is_rename_candidate:
            record = {
                "matched_by": matched_by,
                "is_rename_candidate": is_rename_candidate,
                "identity_before": identity_before,
                "identity_after": identity_after,
                "field_changes": field_changes,
            }
            if tag_field:
                record["lineage_tag_before"] = b_item.get(tag_field)
                record["lineage_tag_after"] = c_item.get(tag_field)
            changed.append(record)
        else:
            unchanged_count += 1

    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "unchanged_count": unchanged_count,
    }
