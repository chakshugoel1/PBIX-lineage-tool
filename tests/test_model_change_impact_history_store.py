"""Tests for model_change_impact.history_store - local SQLite change history."""
from model_change_impact import history_store


def _snapshot(expression="SUM(Sales[Amount])", with_column=True, modified_time=None):
    column = {
        "name": "Amount", "data_type": "Int64", "is_calculated": False,
        "expression": None, "format_string": None, "is_hidden": False,
        "description": None, "display_folder": None, "lineage_tag": None,
    }
    measure = {
        "expression": expression, "display_folder": "", "description": "",
        "format_string": None, "is_hidden": False, "lineage_tag": None, "kpi_id": None,
    }
    if modified_time:
        measure["modified_time"] = modified_time
    return {
        "source_file": "Demo.pbix",
        "tables": {
            "Sales": {
                "is_calculated_table": False, "m_expression": None, "is_hidden": False,
                "description": None, "lineage_tag": None,
                "columns": [column] if with_column else [],
            },
        },
        "measures": {"Sales": {"Total Revenue": measure}},
        "calculated_columns": [],
        "relationships": [],
    }


def _layout(visual_type="card"):
    return {
        "format": "pbir",
        "pages": [{
            "page_id": "p1", "display_name": "Overview", "filters": [],
            "visuals": [{
                "kind": "visual", "visual_id": "v1", "visual_type": visual_type,
                "kpi_classification": "certain", "display_name": "Revenue Card",
                "fields": [{"kind": "measure", "table": "Sales",
                            "field": "Total Revenue", "role": "Values"}],
                "filters": [],
            }],
        }],
    }


def test_first_run_records_added_events(tmp_path):
    db = str(tmp_path / "history.db")
    result = history_store.record_run(
        db, "Demo", _snapshot(), _layout(), run_at="2026-09-14T10:00:00")
    assert result["first_run"] is True
    changed = {event["object_key"]: event for event in result["events"]}
    assert set(changed) == {
        "table|Sales", "column|Sales|Amount", "measure|Sales|Total Revenue",
        "page|p1", "visual|p1|v1",
    }
    assert all(event["change_type"] == "Added" for event in changed.values())
    assert all(event["attributed_requirement_id"] == "Unmapped" for event in changed.values())


def test_first_run_uses_pbix_modified_time_when_available(tmp_path):
    db = str(tmp_path / "history.db")
    result = history_store.record_run(
        db, "Demo", _snapshot(modified_time="2026-01-15T08:30:00"), None,
        run_at="2026-09-14T10:00:00")
    measure_event = next(
        e for e in result["events"] if e["object_key"] == "measure|Sales|Total Revenue")
    assert measure_event["changed_at"] == "2026-01-15T08:30:00"


def test_unchanged_run_records_no_events(tmp_path):
    db = str(tmp_path / "history.db")
    history_store.record_run(db, "Demo", _snapshot(), _layout(), run_at="2026-09-13T10:00:00")
    result = history_store.record_run(
        db, "Demo", _snapshot(), _layout(), run_at="2026-09-14T10:00:00")
    assert result["first_run"] is False
    assert result["events"] == []
    assert result["changed_keys"] == set()


def test_modified_removed_and_readded_events(tmp_path):
    db = str(tmp_path / "history.db")
    history_store.record_run(db, "Demo", _snapshot(), _layout(), run_at="2026-09-12T10:00:00")
    result = history_store.record_run(
        db, "Demo", _snapshot(expression="SUMX(Sales, Sales[Amount])", with_column=False),
        None, run_at="2026-09-13T10:00:00")
    by_key = {event["object_key"]: event for event in result["events"]}

    modified = by_key["measure|Sales|Total Revenue"]
    assert modified["change_type"] == "Modified"
    assert modified["previous_change_at"] == "2026-09-12T10:00:00"

    assert by_key["column|Sales|Amount"]["change_type"] == "Removed"
    assert by_key["page|p1"]["change_type"] == "Removed"
    assert by_key["visual|p1|v1"]["change_type"] == "Removed"

    # Re-adding the column produces a fresh Added event chained to the removal.
    result = history_store.record_run(
        db, "Demo", _snapshot(expression="SUMX(Sales, Sales[Amount])"), _layout(),
        run_at="2026-09-14T10:00:00")
    readded = next(e for e in result["events"] if e["object_key"] == "column|Sales|Amount")
    assert readded["change_type"] == "Added"
    assert readded["previous_change_at"] == "2026-09-13T10:00:00"

    history = history_store.get_object_history(db, "Demo")
    column_history = history["column|Sales|Amount"]
    assert column_history["first_seen"] == "2026-09-12T10:00:00"
    assert column_history["last_changed"] == "2026-09-14T10:00:00"
    assert column_history["change_count"] == 3
    assert column_history["last_change_type"] == "Added"
    measure_history = history["measure|Sales|Total Revenue"]
    assert measure_history["change_count"] == 2
    assert measure_history["previous_change_at"] == "2026-09-12T10:00:00"


def test_attribution_is_stored_on_events(tmp_path):
    db = str(tmp_path / "history.db")
    result = history_store.record_run(
        db, "Demo", _snapshot(), None,
        attribution={"measure|Sales|Total Revenue": "R-1; R-2"},
        run_at="2026-09-14T10:00:00")
    by_key = {event["object_key"]: event for event in result["events"]}
    assert by_key["measure|Sales|Total Revenue"]["attributed_requirement_id"] == "R-1; R-2"
    assert by_key["table|Sales"]["attributed_requirement_id"] == "Unmapped"


def test_list_change_events_newest_first(tmp_path):
    db = str(tmp_path / "history.db")
    history_store.record_run(db, "Demo", _snapshot(), None, run_at="2026-09-13T10:00:00")
    history_store.record_run(
        db, "Demo", _snapshot(expression="SUMX(Sales, Sales[Amount])"), None,
        run_at="2026-09-14T10:00:00")
    events = history_store.list_change_events(db, "Demo")
    assert events[0]["change_type"] == "Modified"
    assert events[0]["object_key"] == "measure|Sales|Total Revenue"
    assert events[0]["first_seen"] == "2026-09-13T10:00:00"
    assert events[0]["change_count"] == 2
    assert len(events) == 4  # 3 first-run Added + 1 Modified


def test_missing_database_returns_empty(tmp_path):
    db = str(tmp_path / "does_not_exist" / "history.db")
    assert history_store.get_object_history(db, "Demo") == {}
    assert history_store.list_change_events(db, "Demo") == []


def test_default_db_path_layout():
    path = history_store.default_db_path(r"C:\out")
    assert path.replace("/", "\\").endswith(r"previous_runs\history\impact_history.db")
