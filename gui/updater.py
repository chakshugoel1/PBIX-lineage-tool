"""GitHub-releases-based update checker. Configure GITHUB_OWNER/GITHUB_REPO
once the repo is created (left unset for now - check_for_update() reports
that cleanly instead of failing)."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import requests
from version import __version__

GITHUB_OWNER = None
GITHUB_REPO = None


def _parse_version(tag):
    return tuple(int(p) for p in tag.lstrip("v").split("."))


def check_for_update(timeout=5):
    """Returns (has_update, latest_tag, download_url, error)."""
    if not GITHUB_OWNER or not GITHUB_REPO:
        return False, None, None, "Updater not configured yet (no GitHub repo set)."
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
    try:
        resp = requests.get(url, timeout=timeout, headers={"Accept": "application/vnd.github+json"})
        resp.raise_for_status()
        data = resp.json()
        latest_tag = data.get("tag_name", "")
        exe_asset = next((a for a in data.get("assets", []) if a["name"].lower().endswith("setup.exe")), None)
        if not latest_tag or not exe_asset:
            return False, latest_tag or None, None, "No installer asset found on the latest release."
        if _parse_version(latest_tag) > _parse_version("v" + __version__):
            return True, latest_tag, exe_asset["browser_download_url"], None
        return False, latest_tag, None, None
    except Exception as e:
        return False, None, None, str(e)


def download_and_launch_installer(url, progress_cb=None):
    """Downloads the installer to a temp file and launches it silently
    (Inno Setup /VERYSILENT), then the caller should exit the app so the
    installer can replace the running executable."""
    import tempfile
    import subprocess

    dest = os.path.join(tempfile.gettempdir(), "PBIXLineageToolUpdate.exe")
    with requests.get(url, stream=True, timeout=30) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", 0))
        done = 0
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1 << 20):
                f.write(chunk)
                done += len(chunk)
                if progress_cb and total:
                    progress_cb(done / total)
    subprocess.Popen([dest, "/VERYSILENT", "/NORESTART", "/SUPPRESSMSGBOXES"])
    return dest
