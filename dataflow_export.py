"""
dataflow_export.py

Exports the actual data rows of a Power BI (Gen1) Dataflow entity to a local
CSV file, by shelling out to powershell/Export-DataflowEntity.ps1 (auth,
metadata resolution, and data retrieval all happen there - see that script
for the full mechanism/caveats). This module only handles process invocation,
output-path naming/sanitization, and archiving any pre-existing file, using
the same conventions as the rest of the tool (fileutils.archive_if_exists,
subprocess-based process execution as in gui/updater.py).

Kept as an isolated, opt-in feature: nothing here is imported by the existing
lineage-report pipeline, so it cannot affect that code path.
"""
import json
import os
import subprocess

import fileutils

POWERSHELL_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "powershell", "Export-DataflowEntity.ps1")

RESULT_PREFIX = "##RESULT##"


def export_dataflow_entity(workspace_id, dataflow_id, entity_name, output_dir,
                            archive_previous=True, progress_cb=None):
    """Runs the PowerShell exporter and streams its progress lines.
    Returns (True, {"path": ..., "rows": ...}) on success, or
    (False, error_message) on failure."""

    def emit(line):
        if progress_cb and line.strip():
            progress_cb(line)

    for field_name, value in (("Workspace ID", workspace_id), ("Dataflow ID", dataflow_id),
                               ("Entity name", entity_name), ("Output folder", output_dir)):
        if not value or not str(value).strip():
            return False, f"{field_name} is required."

    filename = fileutils.sanitize_filename(entity_name) + ".csv"
    output_path = os.path.join(output_dir, filename)
    fileutils.archive_if_exists(output_path, output_dir, archive_previous)

    try:
        proc = subprocess.Popen(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", POWERSHELL_SCRIPT,
             "-WorkspaceId", workspace_id, "-DataflowId", dataflow_id,
             "-EntityName", entity_name, "-OutputPath", output_path],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
    except FileNotFoundError:
        return False, "PowerShell is not available on this machine."

    result = None
    for line in proc.stdout:
        line = line.rstrip("\n")
        if line.startswith(RESULT_PREFIX):
            try:
                result = json.loads(line[len(RESULT_PREFIX):])
            except json.JSONDecodeError:
                pass
        else:
            emit(line)
    proc.wait()

    if result is None:
        return False, f"Export script exited unexpectedly (code {proc.returncode}) without a result."
    if not result.get("success"):
        stage = result.get("stage", "unknown")
        return False, f"[{stage}] {result.get('message', 'Export failed.')}"

    return True, {"path": result.get("path", output_path), "rows": result.get("rows", 0)}
