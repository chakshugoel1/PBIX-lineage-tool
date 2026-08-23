"""Small shared file-handling helpers used by more than one GUI worker."""
import datetime
import os
import re
import shutil
import tempfile

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


def atomic_replace_workbook(workbook, output_path):
    """Save a workbook beside the destination, then replace it atomically."""
    output_dir = os.path.dirname(os.path.abspath(output_path))
    fd, temp_path = tempfile.mkstemp(prefix=".lineage-", suffix=".xlsx", dir=output_dir)
    os.close(fd)
    try:
        workbook.save(temp_path)
        os.replace(temp_path, output_path)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def sanitize_filename(name, max_length=255):
    """Replace characters that are invalid in Windows filenames with '_' and
    truncate to Windows filename limit (255 chars) if necessary."""
    cleaned = _INVALID_FILENAME_CHARS.sub("_", name).strip().strip(".")
    cleaned = cleaned or "export"

    if len(cleaned) > max_length:
        # Truncate to fit within limit, preserving the constraint
        original = cleaned
        cleaned = cleaned[:max_length]
        import logging
        logging.getLogger(__name__).warning(
            f"Filename truncated from {len(original)} to {len(cleaned)} chars: "
            f"'{original[:50]}...' -> '{cleaned[:50]}...'"
        )

    return cleaned
