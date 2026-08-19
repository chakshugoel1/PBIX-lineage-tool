"""Background thread that runs the existing build_lineage_report.py /
build_dataflow_table_lineage_report.py engine, so the UI never freezes.
Reuses the engine's own print()-based progress output by capturing stdout
and re-emitting each line as a Qt signal - no changes needed to the engine's
resolution logic."""
import datetime
import io
import os
import shutil
import sys

from PySide6.QtCore import QThread, Signal

import build_lineage_report as blr
import build_dataflow_table_lineage_report as dtlr
import config
from gui import updater


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

    def _archive_if_exists(self, path):
        if self.archive_previous and os.path.exists(path):
            stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
            dest_dir = os.path.join(self.output_folder, "previous_runs", stamp)
            os.makedirs(dest_dir, exist_ok=True)
            shutil.move(path, os.path.join(dest_dir, os.path.basename(path)))

    def run(self):
        old_stdout = sys.stdout
        sys.stdout = _StreamEmitter(self.progress.emit)
        summary = None
        error_message = None
        try:
            stem = config.pbix_stem(self.pbix_path)
            generated_path = os.path.join(self.output_folder, f"Generated_{stem}_Lineage.xlsx")
            dataflow_lineage_path = os.path.join(self.output_folder, f"Dataflow_Table_Lineage_Report_{stem}.xlsx")

            self._archive_if_exists(generated_path)
            self._archive_if_exists(dataflow_lineage_path)

            rows, ctx = blr.build_report(self.pbix_path, self.dataflow_folder)
            blr.write_workbook(rows, ctx, generated_path)
            dtlr.build_and_save(ctx, output_path=dataflow_lineage_path)

            summary = {
                "generated_path": generated_path,
                "dataflow_lineage_path": dataflow_lineage_path,
                "total": len(rows),
                "found": sum(1 for r in rows if r["status"] == "found"),
                "unresolved": sum(1 for r in rows if r["status"] == "unresolved"),
                "union": sum(1 for r in rows if r["status"] == "union"),
                "no_query": sum(1 for r in rows if r["status"] == "no_query"),
                "needs_override": sum(1 for r in rows if r.get("needs_override")),
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
        success, message = updater.run_update(progress_cb=self.progress.emit)
        if success:
            self.finished_ok.emit(message)
        else:
            self.failed.emit(message)
