# bootstrap-install.ps1
#
# Installs (or updates) the PBIX Lineage Tool for the current user only - no
# admin rights, no compiled/unsigned executable of any kind (so nothing here
# trips Defender Attack Surface Reduction rules that block new, unsigned
# native binaries). Everything lives under %LOCALAPPDATA%\PBIXLineageTool and
# runs as plain Python source through the standard, trusted python.exe.
#
# Safe to re-run: re-running this script later is also how you update - it
# pulls the latest code and re-installs dependencies in place.
$ErrorActionPreference = "Stop"

$RepoUrl  = "https://github.com/chakshugoel1/PBIX-lineage-tool.git"
$AppDir   = "$env:LOCALAPPDATA\PBIXLineageTool"

function Test-CommandExists($name) {
    return [bool](Get-Command $name -ErrorAction SilentlyContinue)
}

function Refresh-Path {
    $machine = [System.Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [System.Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machine;$user"
}

Write-Host "=== PBIX Lineage Tool installer ==="
Write-Host ""

# --- 1. Git -------------------------------------------------------------
if (-not (Test-CommandExists git)) {
    Write-Host "Git not found - installing via winget..."
    winget install --id Git.Git -e --accept-package-agreements --accept-source-agreements
    Refresh-Path
}

# --- 2. Python ------------------------------------------------------------
if (-not (Test-CommandExists python)) {
    Write-Host "Python not found - installing via winget..."
    winget install --id Python.Python.3.12 -e --accept-package-agreements --accept-source-agreements
    Refresh-Path
}

# --- 3. Get (or update) the code --------------------------------------------
if (Test-Path "$AppDir\.git") {
    Write-Host "Existing install found at $AppDir - updating..."
    Push-Location $AppDir
    git pull --ff-only
    Pop-Location
} else {
    Write-Host "Downloading PBIX Lineage Tool to $AppDir ..."
    git clone $RepoUrl $AppDir
}

# --- 4. Virtual environment + dependencies ----------------------------------
$venvPython = "$AppDir\.venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "Creating virtual environment..."
    python -m venv "$AppDir\.venv"
}
Write-Host "Installing dependencies (this can take a few minutes on first run)..."
& $venvPython -m pip install --upgrade pip --quiet
& $venvPython -m pip install -r "$AppDir\requirements.txt"

# --- 5. Start Menu shortcut --------------------------------------------------
$pythonw = "$AppDir\.venv\Scripts\pythonw.exe"
$startMenu = [Environment]::GetFolderPath("Programs")
$shortcutPath = "$startMenu\PBIX Lineage Tool.lnk"
$wsh = New-Object -ComObject WScript.Shell
$shortcut = $wsh.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $pythonw
$shortcut.Arguments = '"app.py"'
$shortcut.WorkingDirectory = $AppDir
$shortcut.Save()

Write-Host ""
Write-Host "=== Done ==="
Write-Host "Shortcut created: $shortcutPath"
Write-Host "Launch it any time from the Start Menu by searching 'PBIX Lineage Tool'."
Write-Host ""

$launch = Read-Host "Launch it now? (Y/n)"
if ($launch -ne "n" -and $launch -ne "N") {
    Start-Process -FilePath $pythonw -ArgumentList '"app.py"' -WorkingDirectory $AppDir
}
