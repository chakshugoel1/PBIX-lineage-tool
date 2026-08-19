# Requires: Python 3.12 available on PATH as "python" (or edit $pythonExe below).
# Usage:
#   powershell -ExecutionPolicy Bypass -File setup.ps1

$ErrorActionPreference = "Stop"
$pythonExe = "python"

Write-Host "Creating virtual environment (.venv) with $pythonExe ..."
& $pythonExe -m venv .venv

Write-Host "Installing pinned dependencies from requirements.txt ..."
& ".venv\Scripts\python.exe" -m pip install --upgrade pip
& ".venv\Scripts\python.exe" -m pip install -r requirements.txt

Write-Host ""
Write-Host "Setup complete. To use this environment in a new terminal, run:"
Write-Host '  .venv\Scripts\Activate.ps1'
Write-Host ""
Write-Host "Then edit config.py to point at your PBIX file / dataflow folder / target workbook,"
Write-Host "and run:"
Write-Host "  python build_lineage_report.py"
