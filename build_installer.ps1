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
    $candidates = @(
        "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        "C:\Program Files\Inno Setup 6\ISCC.exe",
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
    )
    $iscc = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $iscc) {
    Write-Warning "Inno Setup (ISCC.exe) not found. Install it from https://jrsoftware.org/isdl.php, then re-run this script to produce installer\output\PBIXLineageToolSetup.exe."
    exit 1
}

Write-Host "Building installer with Inno Setup..."
$versionLine = Select-String -Path "version.py" -Pattern '__version__\s*=\s*"([^"]+)"'
$appVersion = $versionLine.Matches[0].Groups[1].Value
& $iscc "/DMyAppVersion=$appVersion" "installer\setup.iss"

Write-Host "Done: installer\output\PBIXLineageToolSetup.exe"
