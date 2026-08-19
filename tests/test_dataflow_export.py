"""Tests for dataflow_export.py - mocks subprocess.Popen so these never
need a real PowerShell/Power BI connection."""
import json
import os

import pytest

import dataflow_export


class FakeProc:
    """Stand-in for subprocess.Popen: stdout is an iterable of lines,
    wait() is a no-op."""

    def __init__(self, lines):
        self.stdout = iter(lines)

    def wait(self):
        pass


def _result_line(success, stage, message, files=None):
    payload = {"success": success, "stage": stage, "message": message, "files": files or []}
    return "##RESULT##" + json.dumps(payload)


def test_successful_export(monkeypatch, tmp_path):
    output_dir = str(tmp_path)
    files = [os.path.join(output_dir, "A.json"), os.path.join(output_dir, "B.json")]
    lines = [
        "Exporting 'A' (id-1)...",
        "Exporting 'B' (id-2)...",
        _result_line(True, "done", "2 of 2 dataflow(s) exported.", files),
    ]
    captured_cmd = {}

    def fake_popen(cmd, **kwargs):
        captured_cmd["cmd"] = cmd
        return FakeProc(lines)

    monkeypatch.setattr(dataflow_export.subprocess, "Popen", fake_popen)

    progress_lines = []
    ok, result = dataflow_export.export_all_dataflows(
        "ws-1", output_dir, progress_cb=progress_lines.append)

    assert ok is True
    assert result["files"] == files
    assert result["message"] == "2 of 2 dataflow(s) exported."
    assert progress_lines == ["Exporting 'A' (id-1)...", "Exporting 'B' (id-2)..."]
    assert "-WorkspaceId" in captured_cmd["cmd"]
    assert "ws-1" in captured_cmd["cmd"]


@pytest.mark.parametrize("stage,message", [
    ("module", "Could not install/import MicrosoftPowerBIMgmt: boom"),
    ("auth", "Sign-in failed or was cancelled: boom"),
    ("workspace", "Workspace 'ws-1' was not found, or you don't have access to it."),
    ("list", "Could not list dataflows in this workspace: boom"),
    ("export", "All 3 dataflow(s) failed to export."),
])
def test_failure_stages(monkeypatch, tmp_path, stage, message):
    lines = [_result_line(False, stage, message)]
    monkeypatch.setattr(dataflow_export.subprocess, "Popen", lambda cmd, **kw: FakeProc(lines))

    ok, result = dataflow_export.export_all_dataflows("ws-1", str(tmp_path))

    assert ok is False
    assert result == message


def test_empty_workspace(monkeypatch, tmp_path):
    lines = [_result_line(True, "empty", "No dataflows found in this workspace.", [])]
    monkeypatch.setattr(dataflow_export.subprocess, "Popen", lambda cmd, **kw: FakeProc(lines))

    ok, result = dataflow_export.export_all_dataflows("ws-1", str(tmp_path))

    assert ok is True
    assert result["files"] == []


def test_missing_result_line_is_reported_as_failure(monkeypatch, tmp_path):
    lines = ["some stray output", "no result line here"]
    monkeypatch.setattr(dataflow_export.subprocess, "Popen", lambda cmd, **kw: FakeProc(lines))

    ok, result = dataflow_export.export_all_dataflows("ws-1", str(tmp_path))

    assert ok is False
    assert "did not report a result" in result


def test_powershell_not_available(monkeypatch, tmp_path):
    def fake_popen(cmd, **kwargs):
        raise FileNotFoundError()

    monkeypatch.setattr(dataflow_export.subprocess, "Popen", fake_popen)

    ok, result = dataflow_export.export_all_dataflows("ws-1", str(tmp_path))

    assert ok is False
    assert "PowerShell is not available" in result


@pytest.mark.parametrize("workspace_id,output_dir,missing", [
    ("", "C:/out", "Workspace ID"),
    ("ws-1", "", "Output folder"),
])
def test_missing_required_fields(workspace_id, output_dir, missing):
    ok, result = dataflow_export.export_all_dataflows(workspace_id, output_dir)

    assert ok is False
    assert missing in result


def test_existing_json_files_are_archived(monkeypatch, tmp_path):
    output_dir = str(tmp_path)
    existing = os.path.join(output_dir, "Old.json")
    with open(existing, "w", encoding="utf-8") as f:
        f.write("old")

    lines = [_result_line(True, "done", "1 of 1 dataflow(s) exported.", [os.path.join(output_dir, "New.json")])]
    monkeypatch.setattr(dataflow_export.subprocess, "Popen", lambda cmd, **kw: FakeProc(lines))

    ok, _ = dataflow_export.export_all_dataflows("ws-1", output_dir, archive_previous=True)

    assert ok is True
    assert not os.path.exists(existing)
    assert os.path.isdir(os.path.join(output_dir, "previous_runs"))
