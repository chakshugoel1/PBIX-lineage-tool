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

import build_lineage_report as blr
import build_dataflow_table_lineage_report as dtlr
import config
import dataflow_export
import fileutils
from gui import updater

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
                # "hard" unresolved: no source could be determined at all (unresolved/union).
                "hard_unresolved": sum(1 for r in rows if r.get("hard_unresolved")),
                "flagged_rows": [
                    {"table": r["table"], "issue": r.get("override_tag") or "", "remarks": r.get("remarks") or ""}
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
