# build_installer.ps1
# Builds the standalone app and the Windows installer in one step.
# Requires: pip install -r requirements-dev.txt, and Inno Setup installed
# (https://jrsoftware.org/isdl.php) with ISCC.exe on PATH or at the default
# install location.
$ErrorActionPreference = "Stop"

Write-Host "Building standalone executable with PyInstaller..."
& ".venv\Scripts\python.exe" -m PyInstaller PBIXLineageTool.spec --noconfirm

$iscc = Get-Command iscc.exe -ErrorAction SilentlyContinue
if (-not $iscc) {
    $default = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
    if (Test-Path $default) { $iscc = $default } else { $iscc = $null }
}
if (-not $iscc) {
    Write-Warning "Inno Setup (ISCC.exe) not found. Install it from https://jrsoftware.org/isdl.php, then re-run this script to produce installer\output\PBIXLineageToolSetup.exe."
    exit 1
}

Write-Host "Building installer with Inno Setup..."
& $iscc "installer\setup.iss"

Write-Host "Done: installer\output\PBIXLineageToolSetup.exe"
