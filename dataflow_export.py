"""Isolated feature: batch-exports every dataflow in a Power BI workspace to
a local .json file each (the "Export .json" action, done for every dataflow
at once), by shelling out to powershell/Export-AllDataflows.ps1. Kept
separate from the lineage pipeline so it cannot affect it."""
import json
import os
import queue
import subprocess
import threading
import time

import config
import fileutils

RESULT_PREFIX = "##RESULT##"
_SCRIPT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "powershell", "Export-AllDataflows.ps1")


def export_all_dataflows(workspace_id, output_dir, archive_previous=True, progress_cb=None, proc_holder=None, timeout_seconds=None):
    """Runs the PowerShell exporter and streams its output through
    `progress_cb`. Returns (True, {"files": [...], "message": ...}) on
    success or (False, error_message) on failure.

    If `proc_holder` (a list) is passed, the running subprocess.Popen is
    appended to it as soon as it starts, so a caller on another thread can
    kill it (e.g. on a hard reset) even while this call is still blocked
    reading its output. Default timeout is config.POWERSHELL_EXPORT_TIMEOUT."""
    if not workspace_id:
        return False, "Workspace ID is required."
    if not output_dir:
        return False, "Output folder is required."

    timeout_seconds = timeout_seconds or config.POWERSHELL_EXPORT_TIMEOUT
    emit = progress_cb or (lambda line: None)

    if archive_previous and os.path.isdir(output_dir):
        for name in os.listdir(output_dir):
            if name.lower().endswith(".json"):
                fileutils.archive_if_exists(os.path.join(output_dir, name), output_dir, archive_previous)

    try:
        proc = subprocess.Popen(
            [
                "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", _SCRIPT_PATH,
                "-WorkspaceId", workspace_id,
                "-OutputDir", output_dir,
            ],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
    except FileNotFoundError:
        return False, "PowerShell is not available on this machine."
    if proc_holder is not None:
        proc_holder.append(proc)

    result = None
    start_time = time.monotonic()
    lines = queue.Queue()

    def read_output():
        try:
            for output_line in proc.stdout:
                lines.put(("line", output_line))
        finally:
            lines.put(("eof", None))

    reader = threading.Thread(target=read_output, name="dataflow-export-reader", daemon=True)
    reader.start()
    try:
        while True:
            elapsed = time.monotonic() - start_time
            if elapsed >= timeout_seconds:
                proc.kill()
                proc.wait()
                return False, f"Export timed out after {timeout_seconds} seconds. Process killed."
            try:
                kind, line = lines.get(timeout=min(0.25, timeout_seconds - elapsed))
            except queue.Empty:
                continue
            if kind == "eof":
                break
            line = line.rstrip("\r\n")
            if not line:
                continue
            if line.startswith(RESULT_PREFIX):
                try:
                    result = json.loads(line[len(RESULT_PREFIX):])
                except json.JSONDecodeError:
                    pass
            else:
                emit(line)
        proc.wait()
    except Exception as e:
        try:
            proc.kill()
        except Exception:
            pass
        return False, f"Export process error: {e}"

    if result is None:
        return False, "The export script did not report a result (it may have crashed)."
    if not result.get("success"):
        return False, result.get("message") or "Export failed."
    return True, {"files": result.get("files", []), "message": result.get("message", "")}
