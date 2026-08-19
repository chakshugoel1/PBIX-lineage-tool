"""Per-user settings persisted to %APPDATA%\\PBIXLineageTool\\settings.json,
so the app remembers last-used file paths even when installed to a
read-only location like Program Files."""
import json
import os

APP_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "PBIXLineageTool")
SETTINGS_PATH = os.path.join(APP_DIR, "settings.json")

DEFAULTS = {
    "pbix_path": "",
    "dataflow_folder": "",
    "output_folder": "",
    "archive_previous_runs": True,
    "theme": "dark",
}


def load():
    if not os.path.exists(SETTINGS_PATH):
        return dict(DEFAULTS)
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return dict(DEFAULTS)
    merged = dict(DEFAULTS)
    merged.update(data)
    return merged


def save(settings):
    os.makedirs(APP_DIR, exist_ok=True)
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)
