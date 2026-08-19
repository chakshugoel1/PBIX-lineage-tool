"""Small shared file-handling helpers used by more than one GUI worker."""
import datetime
import os
import re
import shutil

# Characters not allowed in Windows filenames, plus control characters.
_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def archive_if_exists(path, output_folder, archive_previous=True):
    """Move an existing file at `path` into `<output_folder>/previous_runs/<timestamp>/`
    before it gets overwritten, so re-running a report/export never silently
    destroys the previous output. No-op if the file doesn't exist or
    archiving is disabled."""
    if archive_previous and os.path.exists(path):
        stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        dest_dir = os.path.join(output_folder, "previous_runs", stamp)
        os.makedirs(dest_dir, exist_ok=True)
        shutil.move(path, os.path.join(dest_dir, os.path.basename(path)))


def sanitize_filename(name):
    """Replace characters that are invalid in Windows filenames with '_', so
    an arbitrary entity/table name can be used safely as a file name."""
    cleaned = _INVALID_FILENAME_CHARS.sub("_", name).strip().strip(".")
    return cleaned or "export"
