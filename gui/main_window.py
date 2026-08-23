"""Main application window - Fluent Design (Windows 11 style) UI wrapping
the existing PBIX -> Dataflow -> Physical Source lineage engine."""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFileDialog, QHeaderView, QTableWidgetItem,
    QScrollArea, QApplication,
)
from qfluentwidgets import (
    FluentWindow, FluentIcon as FIF, NavigationItemPosition,
    CardWidget, PushButton, PrimaryPushButton, LineEdit, TextEdit,
    IndeterminateProgressBar, TableWidget, InfoBar, InfoBarPosition,
    TitleLabel, SubtitleLabel, BodyLabel, StrongBodyLabel, CheckBox,
    SwitchButton, setTheme, Theme, MessageBox,
)

from gui import settings as app_settings
from gui.worker import PipelineWorker, UpdateWorker, DataflowExportWorker
from gui import updater
from version import __version__

STATUS_CARD_COLORS = {
    "Resolved": "#C6E0B4",
    "Needs Manual Override": "#FFF200",
    "Unresolved": "#D9D9D9",
    "Calculated": "#D9D9D9",
}


class StatusCard(CardWidget):
    def __init__(self, title, color, parent=None):
        super().__init__(parent)
        self.setFixedHeight(90)
        layout = QVBoxLayout(self)
        self.value_label = TitleLabel("0", self)
        self.title_label = BodyLabel(title, self)
        self.value_label.setStyleSheet("color: black;")
        self.title_label.setStyleSheet("color: black;")
        layout.addWidget(self.value_label, alignment=Qt.AlignCenter)
        layout.addWidget(self.title_label, alignment=Qt.AlignCenter)
        self.setStyleSheet(f"StatusCard {{ background-color: {color}; border-radius: 8px; }}")

    def set_value(self, value):
        self.value_label.setText(str(value))


class HomeInterface(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("HomeInterface")
        self.setAcceptDrops(True)
        self.worker = None

        cfg = app_settings.load()

        # Content lives in a scroll area so nothing is ever clipped/hidden
        # (e.g. behind the taskbar) on smaller screens or resolutions - the
        # window can shrink and the content becomes scrollable instead.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        outer.addWidget(scroll)

        content = QWidget(self)
        scroll.setWidget(content)
        root = QVBoxLayout(content)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        root.addWidget(TitleLabel("PBIX Lineage Tool", self))
        root.addWidget(BodyLabel(
            "Traces every table in a Power BI report back to its physical source. "
            "Drag & drop the .pbix file / dataflow folder below, or browse.", self))

        # --- Inputs card ---------------------------------------------------
        inputs_card = CardWidget(self)
        inputs_layout = QVBoxLayout(inputs_card)
        inputs_layout.setContentsMargins(16, 16, 16, 16)
        inputs_layout.addWidget(StrongBodyLabel("1. Select Files", self))

        self.pbix_edit = LineEdit(self)
        self.pbix_edit.setText(cfg["pbix_path"])
        self.pbix_edit.setPlaceholderText("Path to the .pbix file")
        inputs_layout.addLayout(self._row("PBIX file:", self.pbix_edit, self._browse_pbix))

        self.dataflow_edit = LineEdit(self)
        self.dataflow_edit.setText(cfg["dataflow_folder"])
        self.dataflow_edit.setPlaceholderText("Folder containing exported dataflow JSON files")
        inputs_layout.addLayout(self._row("Dataflow folder:", self.dataflow_edit, self._browse_dataflow_folder))

        self.output_edit = LineEdit(self)
        self.output_edit.setText(cfg["output_folder"])
        self.output_edit.setPlaceholderText("Defaults to the PBIX file's folder")
        inputs_layout.addLayout(self._row("Output folder:", self.output_edit, self._browse_output_folder))

        self.archive_check = CheckBox("Keep a timestamped copy of previous report runs", self)
        self.archive_check.setChecked(cfg["archive_previous_runs"])
        inputs_layout.addWidget(self.archive_check)

        root.addWidget(inputs_card)

        # --- Run controls ---------------------------------------------------
        run_row = QHBoxLayout()
        self.run_button = PrimaryPushButton(FIF.PLAY, "Run Pipeline", self)
        self.run_button.clicked.connect(self._on_run_clicked)
        run_row.addWidget(self.run_button)
        run_row.addStretch(1)
        self.toggle_log_button = PushButton("Show Log", self)
        self.toggle_log_button.clicked.connect(self._toggle_log)
        run_row.addWidget(self.toggle_log_button)
        root.addLayout(run_row)

        self.progress_bar = IndeterminateProgressBar(self)
        self.progress_bar.setVisible(False)
        root.addWidget(self.progress_bar)

        self.log_view = TextEdit(self)
        self.log_view.setReadOnly(True)
        self.log_view.setFixedHeight(160)
        self.log_view.setVisible(False)
        root.addWidget(self.log_view)

        # --- Results summary ---------------------------------------------------
        root.addWidget(StrongBodyLabel("2. Results", self))
        cards_row = QHBoxLayout()
        self.resolved_card = StatusCard("Resolved", STATUS_CARD_COLORS["Resolved"], self)
        self.override_card = StatusCard("Needs Manual Override", STATUS_CARD_COLORS["Needs Manual Override"], self)
        self.unresolved_card = StatusCard("Unresolved", STATUS_CARD_COLORS["Unresolved"], self)
        self.calculated_card = StatusCard("Calculated", STATUS_CARD_COLORS["Calculated"], self)
        cards_row.addWidget(self.resolved_card)
        cards_row.addWidget(self.override_card)
        cards_row.addWidget(self.unresolved_card)
        cards_row.addWidget(self.calculated_card)
        root.addLayout(cards_row)

        actions_row = QHBoxLayout()
        self.open_report_button = PushButton(FIF.DOCUMENT, "Open Main Report", self)
        self.open_report_button.clicked.connect(lambda: self._open_file(self._last_generated_path))
        self.open_report_button.setEnabled(False)
        self.open_companion_button = PushButton(FIF.DOCUMENT, "Open Dataflow Lineage Report", self)
        self.open_companion_button.clicked.connect(lambda: self._open_file(self._last_dataflow_lineage_path))
        self.open_companion_button.setEnabled(False)
        self.open_folder_button = PushButton(FIF.FOLDER, "Open Output Folder", self)
        self.open_folder_button.clicked.connect(self._open_output_folder)
        self.open_folder_button.setEnabled(False)
        actions_row.addWidget(self.open_report_button)
        actions_row.addWidget(self.open_companion_button)
        actions_row.addWidget(self.open_folder_button)
        actions_row.addStretch(1)
        root.addLayout(actions_row)

        root.addWidget(BodyLabel("Rows flagged NEEDS MANUAL OVERRIDE:", self))
        self.flagged_table = TableWidget(self)
        self.flagged_table.setColumnCount(3)
        self.flagged_table.setHorizontalHeaderLabels(["Table", "Issue Type", "Remarks"])
        self.flagged_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.flagged_table.setEditTriggers(TableWidget.NoEditTriggers)
        self.flagged_table.setMinimumHeight(200)
        root.addWidget(self.flagged_table, stretch=1)

        self._last_generated_path = None
        self._last_dataflow_lineage_path = None

    def _row(self, label_text, line_edit, browse_slot):
        row = QHBoxLayout()
        row.addWidget(BodyLabel(label_text, self), stretch=0)
        row.addWidget(line_edit, stretch=1)
        btn = PushButton(FIF.FOLDER, "Browse", self)
        btn.clicked.connect(browse_slot)
        row.addWidget(btn, stretch=0)
        return row

    def _browse_pbix(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select PBIX file", "", "Power BI Files (*.pbix)")
        if path:
            self.pbix_edit.setText(path)
            if not self.output_edit.text().strip():
                self.output_edit.setText(os.path.dirname(path))

    def _browse_dataflow_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Select dataflow JSON folder")
        if path:
            self.dataflow_edit.setText(path)

    def _browse_output_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Select output folder")
        if path:
            self.output_edit.setText(path)

    def _toggle_log(self):
        visible = not self.log_view.isVisible()
        self.log_view.setVisible(visible)
        self.toggle_log_button.setText("Hide Log" if visible else "Show Log")

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith(".pbix"):
                self.pbix_edit.setText(path)
                if not self.output_edit.text().strip():
                    self.output_edit.setText(os.path.dirname(path))
            elif os.path.isdir(path):
                self.dataflow_edit.setText(path)

    def _on_run_clicked(self):
        pbix_path = self.pbix_edit.text().strip()
        dataflow_folder = self.dataflow_edit.text().strip()
        output_folder = self.output_edit.text().strip() or (os.path.dirname(pbix_path) if pbix_path else "")

        if not pbix_path or not os.path.isfile(pbix_path):
            InfoBar.error("Missing PBIX file", "Please select a valid .pbix file.", parent=self,
                          position=InfoBarPosition.TOP)
            return
        if not dataflow_folder or not os.path.isdir(dataflow_folder):
            InfoBar.error("Missing dataflow folder", "Please select a valid dataflow JSON folder.", parent=self,
                          position=InfoBarPosition.TOP)
            return

        app_settings.save({
            "pbix_path": pbix_path,
            "dataflow_folder": dataflow_folder,
            "output_folder": output_folder,
            "archive_previous_runs": self.archive_check.isChecked(),
            "theme": app_settings.load().get("theme", "dark"),
        })

        self.run_button.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.log_view.clear()
        self.flagged_table.setRowCount(0)

        self.worker = PipelineWorker(pbix_path, dataflow_folder, output_folder,
                                      archive_previous=self.archive_check.isChecked())
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_finished)
        self.worker.failed.connect(self._on_failed)
        self.worker.start()

    def _on_progress(self, line):
        self.log_view.append(line)

    def _on_finished(self, summary):
        self.run_button.setEnabled(True)
        self.progress_bar.setVisible(False)
        self._last_generated_path = summary["generated_path"]
        self._last_dataflow_lineage_path = summary["dataflow_lineage_path"]
        self.open_report_button.setEnabled(True)
        self.open_companion_button.setEnabled(True)
        self.open_folder_button.setEnabled(True)

        self.resolved_card.set_value(summary["found"])
        self.override_card.set_value(summary["needs_override"])
        self.unresolved_card.set_value(summary["hard_unresolved"])
        self.calculated_card.set_value(summary["no_query"])

        self.flagged_table.setRowCount(len(summary["flagged_rows"]))
        for r, row in enumerate(summary["flagged_rows"]):
            self.flagged_table.setItem(r, 0, QTableWidgetItem(row["table"]))
            self.flagged_table.setItem(r, 1, QTableWidgetItem(row["issue"]))
            self.flagged_table.setItem(r, 2, QTableWidgetItem(row["remarks"]))

        InfoBar.success("Run complete",
                         f"{summary['found']} resolved, {summary['needs_override']} need manual override, "
                         f"{summary['hard_unresolved']} unresolved.",
                         parent=self, position=InfoBarPosition.TOP, duration=4000)

    def _on_failed(self, message):
        self.run_button.setEnabled(True)
        self.progress_bar.setVisible(False)
        InfoBar.error("Run failed", message, parent=self, position=InfoBarPosition.TOP, duration=8000)

    def _open_file(self, path):
        if path and os.path.exists(path):
            os.startfile(path)

    def _open_output_folder(self):
        output_folder = self.output_edit.text().strip()
        if output_folder and os.path.isdir(output_folder):
            os.startfile(output_folder)


class DataflowExportInterface(QWidget):
    """Batch-exports every dataflow in a Power BI workspace to a local .json
    file each (see dataflow_export.py / powershell/Export-AllDataflows.ps1).
    Kept as a separate tab/module so it cannot interfere with the existing
    PBIX -> Dataflow -> Source pipeline."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("DataflowExportInterface")
        self.worker = None

        cfg = app_settings.load()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        outer.addWidget(scroll)

        content = QWidget(self)
        scroll.setWidget(content)
        root = QVBoxLayout(content)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        root.addWidget(TitleLabel("Dataflow Export", self))
        root.addWidget(BodyLabel(
            "Downloads every dataflow in a Power BI workspace to a local .json "
            "file, in one go, via the Power BI REST API. Sign-in is interactive "
            "the first time; it's remembered afterwards.", self))

        inputs_card = CardWidget(self)
        inputs_layout = QVBoxLayout(inputs_card)
        inputs_layout.setContentsMargins(16, 16, 16, 16)
        inputs_layout.addWidget(StrongBodyLabel("1. Workspace", self))

        self.workspace_edit = LineEdit(self)
        self.workspace_edit.setText(cfg["dataflow_workspace_id"])
        self.workspace_edit.setPlaceholderText("Workspace (group) GUID")
        inputs_layout.addLayout(self._row("Workspace ID:", self.workspace_edit))

        self.output_edit = LineEdit(self)
        self.output_edit.setText(cfg["dataflow_output_dir"])
        self.output_edit.setPlaceholderText("Folder each dataflow's .json is saved to")
        inputs_layout.addLayout(self._row("Output folder:", self.output_edit, self._browse_output))

        root.addWidget(inputs_card)

        run_row = QHBoxLayout()
        self.export_button = PrimaryPushButton(FIF.CLOUD_DOWNLOAD, "Download All Dataflows", self)
        self.export_button.clicked.connect(self._on_export_clicked)
        run_row.addWidget(self.export_button)
        run_row.addStretch(1)
        self.toggle_log_button = PushButton("Show Log", self)
        self.toggle_log_button.clicked.connect(self._toggle_log)
        run_row.addWidget(self.toggle_log_button)
        root.addLayout(run_row)

        self.progress_bar = IndeterminateProgressBar(self)
        self.progress_bar.setVisible(False)
        root.addWidget(self.progress_bar)

        self.log_view = TextEdit(self)
        self.log_view.setReadOnly(True)
        self.log_view.setFixedHeight(220)
        self.log_view.setVisible(False)
        root.addWidget(self.log_view)
        root.addStretch(1)

    def _row(self, label_text, line_edit, browse_slot=None):
        row = QHBoxLayout()
        row.addWidget(BodyLabel(label_text, self), stretch=0)
        row.addWidget(line_edit, stretch=1)
        if browse_slot:
            btn = PushButton(FIF.FOLDER, "Browse", self)
            btn.clicked.connect(browse_slot)
            row.addWidget(btn, stretch=0)
        return row

    def _browse_output(self):
        path = QFileDialog.getExistingDirectory(self, "Select output folder")
        if path:
            self.output_edit.setText(path)

    def _toggle_log(self):
        visible = not self.log_view.isVisible()
        self.log_view.setVisible(visible)
        self.toggle_log_button.setText("Hide Log" if visible else "Show Log")

    def _on_export_clicked(self):
        workspace_id = self.workspace_edit.text().strip()
        output_dir = self.output_edit.text().strip()

        if not workspace_id or not output_dir:
            InfoBar.error("Missing details",
                          "Workspace ID and Output folder are both required.",
                          parent=self, position=InfoBarPosition.TOP)
            return

        app_settings.save({
            **app_settings.load(),
            "dataflow_workspace_id": workspace_id,
            "dataflow_output_dir": output_dir,
        })

        self.export_button.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.log_view.clear()

        self.worker = DataflowExportWorker(workspace_id, output_dir)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_finished)
        self.worker.failed.connect(self._on_failed)
        self.worker.start()

    def _on_progress(self, line):
        self.log_view.append(line)

    def _on_finished(self, result):
        self.export_button.setEnabled(True)
        self.progress_bar.setVisible(False)
        InfoBar.success("Export complete",
                         f"{result['message']} ({len(result['files'])} file(s))",
                         parent=self, position=InfoBarPosition.TOP, duration=6000)

    def _on_failed(self, message):
        self.export_button.setEnabled(True)
        self.progress_bar.setVisible(False)
        InfoBar.error("Export failed", message, parent=self, position=InfoBarPosition.TOP, duration=10000)


class AboutInterface(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("AboutInterface")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        layout.addWidget(TitleLabel("About", self))
        layout.addWidget(BodyLabel(f"PBIX Lineage Tool  -  version {__version__}", self))

        theme_row = QHBoxLayout()
        theme_row.addWidget(BodyLabel("Dark theme:", self))
        self.theme_switch = SwitchButton(self)
        cfg = app_settings.load()
        self.theme_switch.setChecked(cfg.get("theme", "dark") == "dark")
        self.theme_switch.checkedChanged.connect(self._on_theme_changed)
        theme_row.addWidget(self.theme_switch)
        theme_row.addStretch(1)
        layout.addLayout(theme_row)

        self.update_button = PushButton(FIF.SYNC, "Check for Updates", self)
        self.update_button.clicked.connect(self._on_check_update)
        layout.addWidget(self.update_button)
        self.update_status = BodyLabel("", self)
        layout.addWidget(self.update_status)

        layout.addWidget(BodyLabel(
            "If the app seems stuck, or keeps showing an error that should already be "
            "fixed, use Hard Reset: it stops any run in progress, kills any background "
            "process it started, and restarts the app fresh.", self))
        self.reset_button = PushButton(FIF.POWER_BUTTON, "Hard Reset App", self)
        self.reset_button.clicked.connect(self._on_hard_reset)
        layout.addWidget(self.reset_button)

        layout.addStretch(1)
        self.update_worker = None

    def _on_theme_changed(self, checked):
        setTheme(Theme.DARK if checked else Theme.LIGHT)
        cfg = app_settings.load()
        cfg["theme"] = "dark" if checked else "light"
        app_settings.save(cfg)

    def _on_check_update(self):
        self.update_button.setEnabled(False)
        self.update_status.setText("Checking for updates...")
        has_update, latest_tag, error = updater.check_for_update()
        if error:
            self.update_status.setText(f"Could not check for updates: {error}")
            self.update_button.setEnabled(True)
        elif not has_update:
            self.update_status.setText(f"You're up to date (latest: {latest_tag or __version__}).")
            self.update_button.setEnabled(True)
        else:
            self.update_status.setText(f"Update available: {latest_tag}. Updating...")
            self.update_worker = UpdateWorker(self)
            self.update_worker.progress.connect(self.update_status.setText)
            self.update_worker.finished_ok.connect(self._on_update_finished)
            self.update_worker.failed.connect(self._on_update_failed)
            self.update_worker.start()

    def _on_update_finished(self, message):
        self.update_status.setText(message)
        self.update_button.setEnabled(True)

    def _on_update_failed(self, message):
        self.update_status.setText(message)
        self.update_button.setEnabled(True)

    def _on_hard_reset(self):
        self.window().hard_reset()


class MainWindow(FluentWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"PBIX Lineage Tool  v{__version__}")
        self._size_to_screen()

        self.home_interface = HomeInterface(self)
        self.dataflow_export_interface = DataflowExportInterface(self)
        self.about_interface = AboutInterface(self)

        self.addSubInterface(self.home_interface, FIF.HOME, "Run")
        self.addSubInterface(self.dataflow_export_interface, FIF.CLOUD_DOWNLOAD, "Dataflow Export")
        self.addSubInterface(self.about_interface, FIF.INFO, "About", NavigationItemPosition.BOTTOM)

    def _size_to_screen(self):
        # Size/position from the *available* screen geometry (excludes the
        # taskbar) instead of a fixed 1000x800, so the window - and the About
        # page / results table within it - always fits on the current
        # display instead of being cut off on smaller resolutions.
        screen = self.screen() or QGuiApplication.primaryScreen()
        available = screen.availableGeometry()
        width = min(1000, available.width() - 40)
        height = min(800, available.height() - 40)
        self.setMinimumSize(480, 480)
        self.resize(max(width, 480), max(height, 480))
        self.move(
            available.x() + (available.width() - self.width()) // 2,
            available.y() + (available.height() - self.height()) // 2,
        )

    def hard_reset(self):
        """Stops any in-progress run/export, kills any background process it
        started, and relaunches the app as a brand-new process - so stale
        in-memory state (or code fixes the running process hasn't picked up
        yet) can never be the cause of a repeat error."""
        box = MessageBox(
            "Hard Reset",
            "This stops any pipeline run or dataflow export in progress, kills any "
            "background process it started, and restarts the app fresh. Continue?",
            self,
        )
        if not box.exec():
            return

        for worker in (self.home_interface.worker, self.dataflow_export_interface.worker):
            if worker is not None and worker.isRunning():
                if hasattr(worker, "request_cancel"):
                    worker.request_cancel()
                if hasattr(worker, "kill_child_process"):
                    worker.kill_child_process()
                worker.wait(2000)
                if worker.isRunning():
                    worker.terminate()
                    worker.wait(1000)

        script = os.path.abspath(sys.argv[0])
        if getattr(sys, "frozen", False):
            args = [sys.executable] + sys.argv[1:]
        else:
            args = [sys.executable, script] + sys.argv[1:]

        # Fully detach from this process's console/std handles - otherwise, on
        # Windows, the new process can get torn down along with this one (or
        # its parent terminal) before its own window even appears.
        popen_kwargs = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if os.name == "nt":
            popen_kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(args, cwd=os.path.dirname(script), **popen_kwargs)

        QApplication.instance().quit()


def main():
    cfg = app_settings.load()
    setTheme(Theme.DARK if cfg.get("theme", "dark") == "dark" else Theme.LIGHT)

    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
