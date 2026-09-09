"""Fix 3: ask the Power BI service for the friendly name of a dataflow that
report M code only refers to by GUID, by shelling out to
powershell/Resolve-DataflowNames.ps1. Requires an interactive sign-in with
access to the workspace, so it is always opt-in - the lineage pipeline runs
fully offline unless the caller explicitly enables it.

Whatever is resolved is written straight into the shared GUID cache, so each
unknown GUID only ever costs one lookup."""
import json
import os
import subprocess
import tempfile

import config
from core import lineage_lib as ll
from services import dataflow_export

RESULT_PREFIX = dataflow_export.RESULT_PREFIX
_SCRIPT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "powershell", "Resolve-DataflowNames.ps1")


def resolve_dataflow_names(pairs, progress_cb=None, timeout_seconds=None):
    """`pairs` is an iterable of {"workspace_id": ..., "dataflow_id": ...}.
    Returns (True, {"mappings": {...}, "failures": [...], "message": ...}) or
    (False, error_message)."""
    emit = progress_cb or (lambda line: None)
    pairs = [p for p in (pairs or []) if p.get("workspace_id") and p.get("dataflow_id")]
    if not pairs:
        return True, {"mappings": {}, "failures": [], "message": "No unknown dataflow GUIDs to look up."}

    timeout_seconds = timeout_seconds or config.POWERSHELL_EXPORT_TIMEOUT
    fd, pairs_file = tempfile.mkstemp(prefix="pbix_guid_lookup_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump([{"workspace_id": p["workspace_id"], "dataflow_id": p["dataflow_id"]} for p in pairs], fh)

        try:
            proc = subprocess.run(
                ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-File", _SCRIPT_PATH, "-PairsFile", pairs_file],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                timeout=timeout_seconds,
            )
        except FileNotFoundError:
            return False, "PowerShell is not available on this machine."
        except subprocess.TimeoutExpired:
            return False, f"Dataflow name lookup timed out after {timeout_seconds} seconds."
    finally:
        try:
            os.remove(pairs_file)
        except OSError:
            pass

    result = None
    for line in (proc.stdout or "").splitlines():
        line = line.rstrip()
        if not line:
            continue
        if line.startswith(RESULT_PREFIX):
            try:
                result = json.loads(line[len(RESULT_PREFIX):])
            except json.JSONDecodeError:
                pass
        else:
            emit(line)

    if result is None:
        return False, "The dataflow name lookup script did not report a result (it may have crashed)."
    if not result.get("success"):
        return False, result.get("message") or "Dataflow name lookup failed."

    saved = dataflow_export.persist_guid_mappings(result.get("mappings"), source="powerbi-lookup")
    failures = result.get("failures") or []
    if isinstance(failures, str):
        failures = [failures]
    return True, {"mappings": saved, "failures": failures, "message": result.get("message", "")}


def resolve_and_reload_cache(pairs, progress_cb=None, timeout_seconds=None):
    """Convenience wrapper: run the lookup, then return the refreshed cache."""
    success, result = resolve_dataflow_names(pairs, progress_cb, timeout_seconds)
    if not success:
        return False, result, ll.load_guid_cache()
    return True, result, ll.load_guid_cache()
