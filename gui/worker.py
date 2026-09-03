"""Background thread that runs the existing build_lineage_report.py /
build_dataflow_table_lineage_report.py engine, so the UI never freezes.
Reuses the engine's own print()-based progress output by capturing stdout
and re-emitting each line as a Qt signal - no changes needed to the engine's
resolution logic."""
import io
import logging
import os
import sys
import threading
import traceback

from PySide6.QtCore import QThread, Signal

import config
from reporting import lineage_report as blr
from reporting import dataflow_table_report as dtlr
from services import dataflow_export, fileutils
from gui import updater
from model_change_impact import snapshot, report_layout, diff, impact, excel_report

logger = logging.getLogger(__name__)


class _StreamEmitter(io.TextIOBase):
    def __init__(self, emit_fn):
        super().__init__()
        self._emit = emit_fn
        self._buf = ""

    def write(self, s):
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line.strip():
                self._emit(line)
        return len(s)

    def flush(self):
        pass


class PipelineWorker(QThread):
    progress = Signal(str)
    finished_ok = Signal(dict)
    failed = Signal(str)

    def __init__(self, pbix_path, dataflow_folder, output_folder, archive_previous=True, parent=None):
        super().__init__(parent)
        self.pbix_path = pbix_path
        self.dataflow_folder = dataflow_folder
        self.output_folder = output_folder
        self.archive_previous = archive_previous
        self.cancel_event = threading.Event()

    def request_cancel(self):
        self.cancel_event.set()

    def run(self):
        old_stdout = sys.stdout
        sys.stdout = _StreamEmitter(self.progress.emit)
        summary = None
        error_message = None
        try:
            stem = config.pbix_stem(self.pbix_path)
            generated_path = os.path.join(self.output_folder, f"Generated_{stem}_Lineage.xlsx")
            dataflow_lineage_path = os.path.join(self.output_folder, f"Dataflow_Table_Lineage_Report_{stem}.xlsx")

            fileutils.archive_if_exists(generated_path, self.output_folder, self.archive_previous)
            fileutils.archive_if_exists(dataflow_lineage_path, self.output_folder, self.archive_previous)

            rows, ctx = blr.build_report(
                self.pbix_path, self.dataflow_folder,
                cancellation_event=self.cancel_event,
            )
            if self.cancel_event.is_set():
                raise RuntimeError("Pipeline cancelled.")
            blr.write_workbook(rows, ctx, generated_path)
            if self.cancel_event.is_set():
                raise RuntimeError("Pipeline cancelled.")
            dtlr.build_and_save(ctx, output_path=dataflow_lineage_path)

            summary = {
                "generated_path": generated_path,
                "dataflow_lineage_path": dataflow_lineage_path,
                "total": len(rows),
                "found": sum(1 for r in rows if r["status"] == "found"),
                "unresolved": sum(1 for r in rows if r["status"] == "unresolved"),
                "union": sum(1 for r in rows if r["status"] == "union"),
                "no_query": sum(1 for r in rows if r["status"] == "no_query"),
                # "soft" override: a source was found, just needs a quick confirm.
                "needs_override": sum(1 for r in rows if r.get("needs_override") and not r.get("hard_unresolved")),
                # Hard unresolved rows excluding those counted separately as access required.
                "hard_unresolved": sum(
                    1 for r in rows if r.get("hard_unresolved") and not r.get("access_request")
                ),
                "access_required": sum(1 for r in rows if r.get("access_request")),
                "flagged_rows": [
                    {
                        "table": r["table"], "issue": r.get("override_tag") or "",
                        "remarks": r.get("remarks") or "", "access_request": r.get("access_request"),
                    }
                    for r in rows if r.get("needs_override")
                ],
            }
        except PermissionError as e:
            error_message = (
                "A report file is open in another program (likely Excel) and could not be "
                f"overwritten. Please close it and try again.\n\n{e}"
            )
        except Exception as e:
            logger.exception("Pipeline worker failed")
            error_message = str(e)
        finally:
            sys.stdout = old_stdout

        if error_message is not None:
            self.failed.emit(error_message)
        else:
            self.finished_ok.emit(summary)


class UpdateWorker(QThread):
    """Runs `git pull` + a dependency re-install on a background thread so
    the UI stays responsive (see gui/updater.py for the actual work)."""
    progress = Signal(str)
    finished_ok = Signal(str)
    failed = Signal(str)

    def run(self):
        try:
            success, message = updater.run_update(progress_cb=self.progress.emit)
            if success:
                self.finished_ok.emit(message)
            else:
                self.failed.emit(message)
        except Exception as e:
            logger.exception("Update worker failed")
            self.failed.emit(f"Update failed: {e}")


class UpdateCheckWorker(QThread):
    """Checks the installed main branch without blocking the desktop UI."""
    checked = Signal(bool, str, str)

    def run(self):
        try:
            has_update, revision, error = updater.check_for_update()
            self.checked.emit(has_update, revision or "", error or "")
        except Exception as e:
            logger.exception("Update check worker failed")
            self.checked.emit(False, "", f"Could not check for updates: {e}")


class DataflowExportWorker(QThread):
    """Runs dataflow_export.py (which shells out to
    powershell/Export-AllDataflows.ps1) on a background thread so the UI
    stays responsive; see dataflow_export.py for the actual export logic."""
    progress = Signal(str)
    finished_ok = Signal(dict)
    failed = Signal(str)

    def __init__(self, workspace_id, output_dir, archive_previous=True, parent=None):
        super().__init__(parent)
        self.workspace_id = workspace_id
        self.output_dir = output_dir
        self.archive_previous = archive_previous
        self.proc_holder = []

    def run(self):
        try:
            success, result = dataflow_export.export_all_dataflows(
                self.workspace_id, self.output_dir,
                archive_previous=self.archive_previous, progress_cb=self.progress.emit,
                proc_holder=self.proc_holder,
            )
            if success:
                self.finished_ok.emit(result)
            else:
                self.failed.emit(result)
        except Exception as e:
            logger.exception("Dataflow export worker failed")
            self.failed.emit(f"Export failed: {e}")

    def kill_child_process(self):
        """Force-kills the PowerShell exporter subprocess, if one is running (used by hard reset)."""
        for proc in self.proc_holder:
            if proc.poll() is None:
                proc.kill()


class ModelChangeImpactWorker(QThread):
    """Runs the V2 model_change_impact pipeline (snapshot -> report_layout ->
    diff -> impact -> excel_report) on a background thread so the UI stays
    responsive. Entirely separate from the V1 PipelineWorker above - shares
    no state or code path with the existing lineage engine."""
    progress = Signal(str)
    finished_ok = Signal(dict)
    failed = Signal(str)

    def __init__(self, baseline_path, changed_path, output_path, parent=None):
        super().__init__(parent)
        self.baseline_path = baseline_path
        self.changed_path = changed_path
        self.output_path = output_path
        self.cancel_event = threading.Event()

    def request_cancel(self):
        self.cancel_event.set()

    def run(self):
        try:
            self.progress.emit("Reading baseline model...")
            baseline_snapshot = snapshot.build_snapshot(self.baseline_path)
            if self.cancel_event.is_set():
                raise RuntimeError("Analysis cancelled.")

            self.progress.emit("Reading changed model...")
            changed_snapshot = snapshot.build_snapshot(self.changed_path)
            if self.cancel_event.is_set():
                raise RuntimeError("Analysis cancelled.")

            self.progress.emit("Reading baseline report's visuals/pages...")
            baseline_layout = report_layout.build_report_layout(self.baseline_path)
            if self.cancel_event.is_set():
                raise RuntimeError("Analysis cancelled.")

            self.progress.emit("Reading changed report's visuals/pages...")
            changed_layout = report_layout.build_report_layout(self.changed_path)
            if self.cancel_event.is_set():
                raise RuntimeError("Analysis cancelled.")

            self.progress.emit("Comparing models...")
            diff_result = diff.diff_snapshots(baseline_snapshot, changed_snapshot)
            if self.cancel_event.is_set():
                raise RuntimeError("Analysis cancelled.")

            self.progress.emit("Analyzing downstream impact on visuals...")
            impact_result = impact.analyze_impact(baseline_snapshot, changed_snapshot, diff_result, changed_layout)
            if self.cancel_event.is_set():
                raise RuntimeError("Analysis cancelled.")

            self.progress.emit("Writing Excel report...")
            excel_report.build_excel_report(
                baseline_snapshot,
                changed_snapshot,
                diff_result,
                impact_result,
                changed_layout,
                self.output_path,
                baseline_report_layout=baseline_layout,
            )

            def _count(section):
                return {k: (v if k == "unchanged_count" else len(v)) for k, v in section.items()}

            summary = {
                "output_path": self.output_path,
                "tables": _count(diff_result["tables"]),
                "columns": _count(diff_result["columns"]),
                "measures": _count(diff_result["measures"]),
                "relationships": _count(diff_result["relationships"]),
                "impacted_visuals": len({
                    (v["page_id"], v["visual_id"])
                    for section in impact_result.values()
                    for row in section
                    for v in row["impacted_visuals"]
                }),
            }
        except PermissionError as e:
            self.failed.emit(
                "The report file is open in another program (likely Excel) and could not be "
                f"overwritten. Please close it and try again.\n\n{e}"
            )
            return
        except Exception as e:
            logger.exception("Model Change Impact worker failed")
            self.failed.emit(str(e))
            return

        self.finished_ok.emit(summary)
