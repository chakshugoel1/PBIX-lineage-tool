"""Update checker/applier for the bootstrap-installed (git clone + private
venv) deployment. There is no compiled installer to download and launch -
"updating" means running `git pull` + re-installing dependencies in the
installed copy, using the same trusted git.exe/python.exe already on the
machine (nothing new/unsigned ever gets executed)."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import subprocess
import requests
from version import __version__

GITHUB_OWNER = "chakshugoel1"
GITHUB_REPO = "PBIX-lineage-tool"
UPDATE_COMMAND_TIMEOUT = 300


def _parse_version(tag):
    return tuple(int(p) for p in tag.lstrip("v").split("."))


def check_for_update(timeout=5):
    """Returns (has_update, latest_tag, error)."""
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
    try:
        resp = requests.get(url, timeout=timeout, headers={"Accept": "application/vnd.github+json"})
        resp.raise_for_status()
        data = resp.json()
        latest_tag = data.get("tag_name", "")
        if not latest_tag:
            return False, None, "No releases found."
        has_update = _parse_version(latest_tag) > _parse_version("v" + __version__)
        return has_update, latest_tag, None
    except Exception as e:
        return False, None, str(e)


def run_update(progress_cb=None):
    """Runs `git pull` + a dependency re-install in the installed copy.
    Returns (success, message)."""
    app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def emit(line):
        if progress_cb:
            progress_cb(line)

    try:
        clean = subprocess.run(
            ["git", "status", "--porcelain"], cwd=app_dir,
            capture_output=True, text=True, timeout=UPDATE_COMMAND_TIMEOUT,
        )
        if clean.returncode != 0:
            return False, f"Could not inspect the installed copy:\n{clean.stderr}"
        if clean.stdout.strip():
            return False, "Update stopped because the installed copy has local changes. Restore or save them, then retry."

        old_head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=app_dir,
            capture_output=True, text=True, check=True, timeout=UPDATE_COMMAND_TIMEOUT,
        ).stdout.strip()
        emit("Pulling latest changes...")
        result = subprocess.run(["git", "pull", "--ff-only"], cwd=app_dir,
                                 capture_output=True, text=True, timeout=UPDATE_COMMAND_TIMEOUT)
        emit(result.stdout.strip() or result.stderr.strip())
        if result.returncode != 0:
            return False, f"git pull failed:\n{result.stderr}"

        python_exe = os.path.join(app_dir, ".venv", "Scripts", "python.exe")
        if not os.path.isfile(python_exe):
            subprocess.run(["git", "reset", "--hard", old_head], cwd=app_dir,
                           capture_output=True, text=True, timeout=UPDATE_COMMAND_TIMEOUT)
            return False, "Update stopped because the installed virtual environment is missing. Re-run Install.cmd."
        emit("Installing/upgrading dependencies...")
        result = subprocess.run(
            [python_exe, "-m", "pip", "install", "--upgrade", "-r",
             os.path.join(app_dir, "requirements.txt")],
            cwd=app_dir, capture_output=True, text=True, timeout=UPDATE_COMMAND_TIMEOUT,
        )
        emit(result.stdout.strip() or result.stderr.strip())
        if result.returncode != 0:
            rollback = subprocess.run(
                ["git", "reset", "--hard", old_head], cwd=app_dir,
                capture_output=True, text=True, timeout=UPDATE_COMMAND_TIMEOUT,
            )
            suffix = "" if rollback.returncode == 0 else " Rollback also failed; reinstall from Install.cmd."
            return False, f"Dependency install failed; previous code was restored:\n{result.stderr}{suffix}"

        return True, "Update complete. Please restart the app for changes to take effect."
    except subprocess.TimeoutExpired as e:
        return False, f"Update timed out while running {e.cmd[0] if e.cmd else 'a command'}. Try again later."
    except FileNotFoundError:
        return False, "git is not available - this update mechanism requires the tool to have been installed via Install.cmd."
    except Exception as e:
        return False, f"Update failed: {e}"
