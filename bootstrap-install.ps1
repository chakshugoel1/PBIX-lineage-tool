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

# pbixray's pinned dependencies only ship prebuilt wheels for Python 3.12; a
# newer "python" already on PATH (e.g. 3.14) is not enough - pip will try to
# build packages like xpress9 from source and fail. Always look for/install
# 3.12 specifically and use that to create the venv.
function Get-Python312Launcher {
    if (Test-CommandExists py) {
        $list = (& py -0) 2>$null | Out-String
        if ($list -match "3\.12") {
            return @{ Exe = "py"; Args = @("-3.12") }
        }
    }
    if (Test-CommandExists python) {
        $ver = ((& python --version) 2>&1 | Out-String)
        if ($ver -match "3\.12") {
            return @{ Exe = "python"; Args = @() }
        }
    }
    return $null
}

Write-Host "=== PBIX Lineage Tool installer ==="
Write-Host ""

# --- 1. Git -------------------------------------------------------------
if (-not (Test-CommandExists git)) {
    Write-Host "Git not found - installing via winget..."
    winget install --id Git.Git -e --accept-package-agreements --accept-source-agreements
    Refresh-Path
}

# --- 2. Python 3.12 ---------------------------------------------------------
$py312 = Get-Python312Launcher
if (-not $py312) {
    Write-Host "Python 3.12 not found - installing via winget..."
    winget install --id Python.Python.3.12 -e --accept-package-agreements --accept-source-agreements
    Refresh-Path
    $py312 = Get-Python312Launcher
}
if (-not $py312) {
    Write-Error "Could not find or install Python 3.12. Install it from https://www.python.org/downloads/release/python-3120/ and re-run this script."
    exit 1
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
$venvOk = $false
if (Test-Path $venvPython) {
    $existingVer = ((& $venvPython --version) 2>&1 | Out-String)
    if ($existingVer -match "3\.12") {
        $venvOk = $true
    } else {
        Write-Host "Existing virtual environment is not Python 3.12 - recreating..."
        Remove-Item "$AppDir\.venv" -Recurse -Force
    }
}
if (-not $venvOk) {
    Write-Host "Creating virtual environment (Python 3.12)..."
    & $py312.Exe @($py312.Args) -m venv "$AppDir\.venv"
}
Write-Host "Installing dependencies (this can take a few minutes on first run)..."
& $venvPython -m pip install --upgrade pip --quiet
& $venvPython -m pip install -r "$AppDir\requirements.txt"
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to install dependencies (pip exited with code $LASTEXITCODE). Setup did not complete - fix the error above and re-run this script."
    exit 1
}

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
