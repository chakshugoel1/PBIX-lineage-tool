"""Entry point for the PBIX Lineage Tool desktop app."""
import os
import logging
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gui.main_window import main
from gui import updater


def _configure_windows_app_identity():
    if os.name != "nt":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(updater.APP_USER_MODEL_ID)
    except Exception:
        pass


def _configure_runtime_diagnostics():
    log_dir = os.path.join(os.environ.get("LOCALAPPDATA", os.path.dirname(__file__)), "PBIXLineageTool", "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "runtime.log")
    logging.basicConfig(
        filename=log_path,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        encoding="utf-8",
    )

    def handle_exception(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        logging.critical("Unhandled application exception", exc_info=(exc_type, exc_value, exc_traceback))

    sys.excepthook = handle_exception

if __name__ == "__main__":
    _configure_windows_app_identity()
    _configure_runtime_diagnostics()
    updater.refresh_shortcut_icon(os.path.dirname(os.path.abspath(__file__)))
    main()
