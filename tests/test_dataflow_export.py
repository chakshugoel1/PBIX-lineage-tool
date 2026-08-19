"""
Tests for dataflow_export.py and the shared fileutils helpers it relies on.

No live Power BI/PowerShell process is required: subprocess.Popen is mocked
with a fake process object whose stdout yields the same plain-text progress
lines + trailing ##RESULT##{...} JSON line that the real
powershell/Export-DataflowEntity.ps1 script produces.
"""
import json
import os
from unittest.mock import MagicMock, patch

import pytest

import dataflow_export
import fileutils


class FakeProc:
    """Stands in for the object returned by subprocess.Popen."""

    def __init__(self, lines, returncode=0):
        self.stdout = iter(lines)
        self.returncode = returncode

    def wait(self):
        pass


def result_line(payload):
    return dataflow_export.RESULT_PREFIX + json.dumps(payload)


def make_popen(lines, returncode=0):
    return lambda *args, **kwargs: FakeProc(lines, returncode)


# --- successful export -------------------------------------------------------

def test_successful_export(tmp_path):
    output_dir = str(tmp_path)
    lines = [
        "Starting Power BI Dataflow export",
        "Authenticating...",
        "Rows retrieved: 42",
        "Writing CSV...",
        "Export completed successfully",
        result_line({"success": True, "path": os.path.join(output_dir, "FACT_EMP.csv"), "rows": 42}),
    ]
    progress_lines = []
    with patch("subprocess.Popen", side_effect=make_popen(lines)):
        ok, result = dataflow_export.export_dataflow_entity(
            "ws-1", "df-1", "FACT_EMP", output_dir, progress_cb=progress_lines.append)

    assert ok is True
    assert result["rows"] == 42
    assert "Authenticating..." in progress_lines
    assert not any(line.startswith("##RESULT##") for line in progress_lines)


# --- failure stages -----------------------------------------------------------

@pytest.mark.parametrize("stage,message", [
    ("auth", "Could not authenticate to Power BI: interactive sign-in was cancelled."),
    ("dataflow", "Could not resolve dataflow 'bad-id' in workspace 'ws-1': 404."),
    ("entity", "Entity 'DOES_NOT_EXIST' not found in dataflow. Available entities: A, B."),
    ("empty", "Entity 'EMPTY_TABLE' returned no rows."),
    ("workspace", "Could not resolve workspace 'ws-1': 403 Forbidden."),
])
def test_failure_stages(tmp_path, stage, message):
    lines = [
        "Starting Power BI Dataflow export",
        result_line({"success": False, "stage": stage, "message": message}),
    ]
    with patch("subprocess.Popen", side_effect=make_popen(lines)):
        ok, result = dataflow_export.export_dataflow_entity("ws-1", "df-1", "ENTITY", str(tmp_path))

    assert ok is False
    assert stage in result
    assert message in result


def test_missing_result_line_is_reported_as_failure(tmp_path):
    lines = ["Starting Power BI Dataflow export", "something crashed with no JSON result"]
    with patch("subprocess.Popen", side_effect=make_popen(lines, returncode=1)):
        ok, result = dataflow_export.export_dataflow_entity("ws-1", "df-1", "ENTITY", str(tmp_path))

    assert ok is False
    assert "unexpectedly" in result


def test_powershell_not_available(tmp_path):
    with patch("subprocess.Popen", side_effect=FileNotFoundError()):
        ok, result = dataflow_export.export_dataflow_entity("ws-1", "df-1", "ENTITY", str(tmp_path))

    assert ok is False
    assert "PowerShell" in result


# --- input validation ----------------------------------------------------------

@pytest.mark.parametrize("workspace_id,dataflow_id,entity_name,output_dir", [
    ("", "df-1", "ENTITY", "C:\\out"),
    ("ws-1", "", "ENTITY", "C:\\out"),
    ("ws-1", "df-1", "", "C:\\out"),
    ("ws-1", "df-1", "ENTITY", ""),
])
def test_missing_required_fields(workspace_id, dataflow_id, entity_name, output_dir):
    ok, result = dataflow_export.export_dataflow_entity(workspace_id, dataflow_id, entity_name, output_dir)
    assert ok is False
    assert "required" in result


# --- output directory does not exist yet ----------------------------------------

def test_output_directory_does_not_exist_yet(tmp_path):
    output_dir = str(tmp_path / "does" / "not" / "exist")
    lines = [result_line({"success": True, "path": os.path.join(output_dir, "ENTITY.csv"), "rows": 1})]
    with patch("subprocess.Popen", side_effect=make_popen(lines)):
        ok, result = dataflow_export.export_dataflow_entity("ws-1", "df-1", "ENTITY", output_dir)

    assert ok is True  # directory creation is delegated to the PowerShell script


# --- existing output file gets archived, not destroyed --------------------------

def test_existing_output_file_is_archived(tmp_path):
    output_dir = tmp_path
    existing = output_dir / "FACT_EMP.csv"
    existing.write_text("old data")

    lines = [result_line({"success": True, "path": str(existing), "rows": 5})]
    with patch("subprocess.Popen", side_effect=make_popen(lines)):
        ok, _ = dataflow_export.export_dataflow_entity("ws-1", "df-1", "FACT_EMP", str(output_dir))

    assert ok is True
    assert not existing.exists() or existing.read_text() == "old data"  # moved, not deleted in place
    previous_runs = output_dir / "previous_runs"
    assert previous_runs.exists()
    archived = list(previous_runs.rglob("FACT_EMP.csv"))
    assert len(archived) == 1
    assert archived[0].read_text() == "old data"


# --- special characters in entity name ------------------------------------------

@pytest.mark.parametrize("entity_name,expected_stem", [
    ("FACT_EMP_DETAILS", "FACT_EMP_DETAILS"),
    ("Sales/Region:2024", "Sales_Region_2024"),
    ('Weird<>"|Name', "Weird____Name"),
    ("...", "export"),
])
def test_sanitize_filename(entity_name, expected_stem):
    assert fileutils.sanitize_filename(entity_name) == expected_stem


def test_output_path_uses_sanitized_entity_name(tmp_path):
    captured_args = {}

    def fake_popen(args, **kwargs):
        captured_args["args"] = args
        return FakeProc([result_line({"success": True, "path": "x", "rows": 1})])

    with patch("subprocess.Popen", side_effect=fake_popen):
        dataflow_export.export_dataflow_entity("ws-1", "df-1", "Sales/Region:2024", str(tmp_path))

    output_path_arg = captured_args["args"][captured_args["args"].index("-OutputPath") + 1]
    assert output_path_arg.endswith("Sales_Region_2024.csv")
