"""Local SQLite store tracking per-object change history across analysis runs.

Every Baseline Estimation run records the current state of every tracked
object (tables, columns, measures, relationships, pages, visuals) keyed by
the PBIX file's stem. On each run the current state is compared with the
stored state:

- object never seen before   -> ``Added`` event
- content hash changed       -> ``Modified`` event
- previously present, now gone -> ``Removed`` event
- (removed objects keep their state row, so first_seen/change_count survive)

Where a snapshot object carries Power BI's own ``modified_time`` metadata
(best-effort extraction in snapshot.py), a first-seen event uses that as the
change timestamp instead of the run time - giving some real history even on
the very first run.

`record_run()` is the main entry point; `default_db_path()` defines where the
database lives (`<output folder>/previous_runs/history/impact_history.db`).
"""
import datetime
import hashlib
import json
import os
import sqlite3

DEFAULT_DB_NAME = "impact_history.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    model_key TEXT NOT NULL,
    run_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS object_state (
    model_key TEXT NOT NULL,
    object_key TEXT NOT NULL,
    object_type TEXT NOT NULL,
    object_name TEXT NOT NULL,
    parent_object TEXT,
    content_hash TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_changed TEXT NOT NULL,
    change_count INTEGER NOT NULL DEFAULT 1,
    present INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (model_key, object_key)
);
CREATE TABLE IF NOT EXISTS change_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    model_key TEXT NOT NULL,
    run_id INTEGER NOT NULL,
    object_key TEXT NOT NULL,
    object_type TEXT NOT NULL,
    object_name TEXT NOT NULL,
    parent_object TEXT,
    change_type TEXT NOT NULL,
    changed_at TEXT NOT NULL,
    previous_change_at TEXT,
    attributed_requirement_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_change_events_key
    ON change_events (model_key, object_key);
"""


def default_db_path(output_folder):
    return os.path.join(output_folder, "previous_runs", "history", DEFAULT_DB_NAME)


def object_key(kind, *parts):
    """Stable identity key, e.g. ``column|Sales|Amount``, ``visual|p1|v1``."""
    return "|".join([kind] + [str(part) for part in parts if part is not None])


def iter_tracked_objects(snapshot, report_layout=None):
    """Yield every tracked object of a snapshot (+ report layout) as dicts:
    object_key, object_type, object_name, parent_object, content, modified_time.
    """
    objects = []
    for table_name, table in snapshot.get("tables", {}).items():
        objects.append({
            "object_key": object_key("table", table_name),
            "object_type": "Table",
            "object_name": table_name,
            "parent_object": "",
            "modified_time": table.get("modified_time"),
            "content": {
                "m_expression": table.get("m_expression"),
                "is_hidden": table.get("is_hidden"),
                "description": table.get("description"),
                "is_calculated_table": table.get("is_calculated_table"),
            },
        })
        for column in table.get("columns", []):
            objects.append({
                "object_key": object_key("column", table_name, column["name"]),
                "object_type": "Column",
                "object_name": f"{table_name}[{column['name']}]",
                "parent_object": table_name,
                "modified_time": column.get("modified_time"),
                "content": {
                    "data_type": column.get("data_type"),
                    "is_calculated": column.get("is_calculated"),
                    "expression": column.get("expression"),
                    "format_string": column.get("format_string"),
                    "is_hidden": column.get("is_hidden"),
                    "description": column.get("description"),
                },
            })
    for table_name, measures in snapshot.get("measures", {}).items():
        for measure_name, measure in measures.items():
            objects.append({
                "object_key": object_key("measure", table_name, measure_name),
                "object_type": "Measure",
                "object_name": f"{table_name}[{measure_name}]",
                "parent_object": table_name,
                "modified_time": measure.get("modified_time"),
                "content": {
                    "expression": measure.get("expression"),
                    "format_string": measure.get("format_string"),
                    "description": measure.get("description"),
                    "display_folder": measure.get("display_folder"),
                    "is_hidden": measure.get("is_hidden"),
                },
            })
    for relationship in snapshot.get("relationships", []):
        key = object_key(
            "relationship",
            relationship.get("from_table"), relationship.get("from_column"),
            relationship.get("to_table"), relationship.get("to_column"),
        )
        display = (
            f"{relationship.get('from_table')}[{relationship.get('from_column')}] -> "
            f"{relationship.get('to_table')}[{relationship.get('to_column')}]"
        )
        objects.append({
            "object_key": key,
            "object_type": "Relationship",
            "object_name": display,
            "parent_object": "",
            "modified_time": None,
            "content": {
                "is_active": relationship.get("is_active"),
                "cardinality": relationship.get("cardinality"),
                "cross_filtering_behavior": relationship.get("cross_filtering_behavior"),
                "rely_on_referential_integrity": relationship.get("rely_on_referential_integrity"),
            },
        })

    for page in (report_layout or {}).get("pages", []):
        page_id = page.get("page_id")
        page_visuals = [v for v in page.get("visuals", []) if v.get("kind") == "visual"]
        objects.append({
            "object_key": object_key("page", page_id),
            "object_type": "Page",
            "object_name": page.get("display_name") or page_id or "",
            "parent_object": "",
            "modified_time": None,
            "content": {
                "display_name": page.get("display_name"),
                "visual_ids": sorted(v.get("visual_id") for v in page_visuals),
            },
        })
        for visual in page_visuals:
            objects.append({
                "object_key": object_key("visual", page_id, visual.get("visual_id")),
                "object_type": "Visual",
                "object_name": visual.get("display_name")
                    or f"(Untitled {visual.get('visual_type') or 'visual'}) [ID: {visual.get('visual_id')}]",
                "parent_object": page.get("display_name") or page_id or "",
                "modified_time": None,
                "content": {
                    "visual_type": visual.get("visual_type"),
                    "display_name": visual.get("display_name"),
                    "fields": sorted(
                        (f.get("kind"), f.get("table"), f.get("field"), f.get("role"))
                        for f in visual.get("fields", [])
                    ),
                },
            })
    return objects


def _content_hash(content):
    payload = json.dumps(content, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _connect(db_path):
    directory = os.path.dirname(os.path.abspath(db_path))
    os.makedirs(directory, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.executescript(_SCHEMA)
    return connection


def record_run(db_path, model_key, snapshot, report_layout=None, attribution=None, run_at=None):
    """Compare the current snapshot/layout against the stored state, append
    change events for anything that changed, and return a summary dict:
    ``{run_id, run_at, first_run, events, changed_keys}``.

    `attribution` maps object_key -> requirement id(s) (string); objects
    without an entry are recorded as ``Unmapped``.
    """
    attribution = attribution or {}
    run_at = run_at or datetime.datetime.now().isoformat(timespec="seconds")
    current = {obj["object_key"]: obj for obj in iter_tracked_objects(snapshot, report_layout)}

    connection = _connect(db_path)
    try:
        cursor = connection.cursor()
        first_run = cursor.execute(
            "SELECT COUNT(*) FROM runs WHERE model_key = ?", (model_key,),
        ).fetchone()[0] == 0
        cursor.execute("INSERT INTO runs (model_key, run_at) VALUES (?, ?)", (model_key, run_at))
        run_id = cursor.lastrowid

        stored = {
            row[0]: row
            for row in cursor.execute(
                "SELECT object_key, object_type, object_name, parent_object, content_hash, "
                "first_seen, last_changed, change_count, present "
                "FROM object_state WHERE model_key = ?",
                (model_key,),
            )
        }

        events = []
        for key in sorted(current):
            obj = current[key]
            content_hash = _content_hash(obj["content"])
            row = stored.get(key)
            if row is None:
                changed_at = _valid_modified_time(obj.get("modified_time")) or run_at
                cursor.execute(
                    "INSERT INTO object_state (model_key, object_key, object_type, object_name, "
                    "parent_object, content_hash, first_seen, last_changed, change_count, present) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 1)",
                    (model_key, key, obj["object_type"], obj["object_name"], obj["parent_object"],
                     content_hash, changed_at, changed_at),
                )
                events.append(_insert_event(
                    cursor, run_id, model_key, obj, "Added", changed_at, None, attribution,
                ))
            else:
                (_, _, _, _, old_hash, first_seen, last_changed, change_count, present) = row
                if not present:
                    events.append(_insert_event(
                        cursor, run_id, model_key, obj, "Added", run_at, last_changed, attribution,
                    ))
                    cursor.execute(
                        "UPDATE object_state SET object_type = ?, object_name = ?, "
                        "parent_object = ?, content_hash = ?, last_changed = ?, "
                        "change_count = ?, present = 1 "
                        "WHERE model_key = ? AND object_key = ?",
                        (obj["object_type"], obj["object_name"], obj["parent_object"],
                         content_hash, run_at, change_count + 1, model_key, key),
                    )
                elif old_hash != content_hash:
                    events.append(_insert_event(
                        cursor, run_id, model_key, obj, "Modified", run_at, last_changed, attribution,
                    ))
                    cursor.execute(
                        "UPDATE object_state SET object_type = ?, object_name = ?, "
                        "parent_object = ?, content_hash = ?, last_changed = ?, "
                        "change_count = ?, present = 1 "
                        "WHERE model_key = ? AND object_key = ?",
                        (obj["object_type"], obj["object_name"], obj["parent_object"],
                         content_hash, run_at, change_count + 1, model_key, key),
                    )

        for key, row in stored.items():
            (_, object_type, object_name, parent_object, _, _, last_changed, change_count, present) = row
            if present and key not in current:
                obj = {"object_key": key, "object_type": object_type,
                       "object_name": object_name, "parent_object": parent_object}
                events.append(_insert_event(
                    cursor, run_id, model_key, obj, "Removed", run_at, last_changed, attribution,
                ))
                cursor.execute(
                    "UPDATE object_state SET last_changed = ?, change_count = ?, present = 0 "
                    "WHERE model_key = ? AND object_key = ?",
                    (run_at, change_count + 1, model_key, key),
                )

        connection.commit()
    finally:
        connection.close()

    return {
        "run_id": run_id,
        "run_at": run_at,
        "first_run": first_run,
        "events": events,
        "changed_keys": {event["object_key"] for event in events},
    }


def _insert_event(cursor, run_id, model_key, obj, change_type, changed_at, previous_change_at, attribution):
    attributed = attribution.get(obj["object_key"]) or "Unmapped"
    cursor.execute(
        "INSERT INTO change_events (model_key, run_id, object_key, object_type, object_name, "
        "parent_object, change_type, changed_at, previous_change_at, attributed_requirement_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (model_key, run_id, obj["object_key"], obj["object_type"], obj["object_name"],
         obj["parent_object"], change_type, changed_at, previous_change_at, attributed),
    )
    return {
        "object_key": obj["object_key"],
        "object_type": obj["object_type"],
        "object_name": obj["object_name"],
        "parent_object": obj["parent_object"],
        "change_type": change_type,
        "changed_at": changed_at,
        "previous_change_at": previous_change_at,
        "attributed_requirement_id": attributed,
    }


def _valid_modified_time(value):
    """Return `value` if it looks like an ISO-ish timestamp, else None."""
    if not value:
        return None
    text = str(value)
    try:
        datetime.datetime.fromisoformat(text)
        return text
    except ValueError:
        return None


def get_object_history(db_path, model_key):
    """Return ``{object_key: {object_type, object_name, parent_object,
    first_seen, last_changed, previous_change_at, change_count,
    last_change_type}}`` for every object ever recorded for `model_key`.
    Empty dict when no database exists yet."""
    if not os.path.exists(db_path):
        return {}
    connection = _connect(db_path)
    try:
        states = connection.execute(
            "SELECT object_key, object_type, object_name, parent_object, first_seen, "
            "last_changed, change_count FROM object_state WHERE model_key = ?",
            (model_key,),
        ).fetchall()
        latest_events = connection.execute(
            "SELECT e.object_key, e.change_type, e.changed_at, e.previous_change_at "
            "FROM change_events e WHERE e.model_key = ? AND e.event_id = ("
            "    SELECT MAX(event_id) FROM change_events "
            "    WHERE model_key = e.model_key AND object_key = e.object_key)",
            (model_key,),
        ).fetchall()
    finally:
        connection.close()

    latest = {row[0]: row[1:] for row in latest_events}
    history = {}
    for key, object_type, object_name, parent_object, first_seen, last_changed, change_count in states:
        event = latest.get(key, (None, None, None))
        history[key] = {
            "object_type": object_type,
            "object_name": object_name,
            "parent_object": parent_object or "",
            "first_seen": first_seen,
            "last_changed": last_changed,
            "previous_change_at": event[2] or "",
            "change_count": change_count,
            "last_change_type": event[0] or "",
        }
    return history


def list_change_events(db_path, model_key):
    """Return all change events for `model_key`, newest first, each a dict
    with object/change fields plus first_seen and change_count."""
    if not os.path.exists(db_path):
        return []
    connection = _connect(db_path)
    try:
        rows = connection.execute(
            "SELECT e.object_key, e.object_type, e.object_name, e.parent_object, e.change_type, "
            "e.changed_at, e.previous_change_at, e.attributed_requirement_id, "
            "s.first_seen, s.change_count, e.run_id "
            "FROM change_events e "
            "LEFT JOIN object_state s ON s.model_key = e.model_key AND s.object_key = e.object_key "
            "WHERE e.model_key = ? ORDER BY e.event_id DESC",
            (model_key,),
        ).fetchall()
    finally:
        connection.close()
    return [
        {
            "object_key": row[0],
            "object_type": row[1],
            "object_name": row[2],
            "parent_object": row[3] or "",
            "change_type": row[4],
            "changed_at": row[5],
            "previous_change_at": row[6] or "",
            "attributed_requirement_id": row[7] or "Unmapped",
            "first_seen": row[8] or "",
            "change_count": row[9] or 0,
            "run_id": row[10],
        }
        for row in rows
    ]
