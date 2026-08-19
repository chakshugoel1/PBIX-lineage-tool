@echo off
setlocal enabledelayedexpansion

rem ============================================================================
rem  PBIX Lineage Tool - one-file bootstrap installer
rem ============================================================================
rem  Double-click this file on a brand-new machine. It downloads
rem  bootstrap-install.ps1 from GitHub and runs it, which installs Python/Git
rem  if needed, downloads the tool to a private per-user folder, sets up a
rem  virtual environment, and creates a Start Menu shortcut. This file can be
rem  copied/emailed/shared on its own - everything else it needs, it fetches
rem  itself.
rem
rem  Requires: PowerShell (built into Windows) and internet access to GitHub.
rem  If your network blocks raw.githubusercontent.com, ask for
rem  bootstrap-install.ps1 directly instead and run it with:
rem    powershell -NoProfile -ExecutionPolicy Bypass -File bootstrap-install.ps1
rem
rem  NOTE: unlike bootstrap-install.ps1 (re-fetched fresh from GitHub every
rem  run), THIS file does not auto-update itself - if you copied/emailed it
rem  before INSTALLER_VERSION below changed, get a fresh copy from the repo.
rem ============================================================================

set "INSTALLER_VERSION=2026-08-19.1"
set "SCRIPT_URL=https://raw.githubusercontent.com/chakshugoel1/PBIX-lineage-tool/main/bootstrap-install.ps1"
set "TEMP_SCRIPT=%TEMP%\pbix-lineage-bootstrap-install.ps1"

echo ===============================================================
echo   PBIX Lineage Tool - Installer ^(v%INSTALLER_VERSION%^)
echo ===============================================================
echo.
echo This will:
echo   - install Python and Git if missing (via winget)
echo   - download the tool to %%LOCALAPPDATA%%\PBIXLineageTool
echo   - set up a private virtual environment and install dependencies
echo   - create a Start Menu shortcut
echo.
echo You can close this window at any time; nothing runs until you continue.
echo.
pause

echo.
echo Downloading installer script...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; $ok=$false; for ($i=1; $i -le 4; $i++) { try { Invoke-WebRequest -UseBasicParsing -Uri '%SCRIPT_URL%' -OutFile '%TEMP_SCRIPT%' -TimeoutSec 30; $ok=$true; break } catch { Write-Host ('Attempt ' + $i + ' of 4 failed: ' + $_.Exception.Message); if ($i -lt 4) { Write-Host 'Retrying...'; Start-Sleep -Seconds (5*$i) } } }; if (-not $ok) { exit 1 }"
if errorlevel 1 goto DOWNLOAD_FAILED
if not exist "%TEMP_SCRIPT%" goto DOWNLOAD_FAILED

echo Download OK. Running installer...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%TEMP_SCRIPT%"
set "EXITCODE=%errorlevel%"

echo.
if "%EXITCODE%"=="0" (
  echo ===============================================================
  echo   Setup finished successfully.
  echo ===============================================================
) else (
  echo ===============================================================
  echo   Setup did not finish ^(exit code %EXITCODE%^).
  echo   Scroll up to see what failed. Fixing that and double-clicking
  echo   this file again will pick up from where it left off - steps
  echo   already completed are skipped automatically.
  echo ===============================================================
)
pause
exit /b %EXITCODE%

:DOWNLOAD_FAILED
echo.
echo ===============================================================
echo   Could not download the installer ^(after 4 attempts^).
echo ===============================================================
echo Check your internet connection, or whether this network blocks
echo raw.githubusercontent.com (some corporate proxies do).
echo A "504 Gateway Timeout" is usually a temporary GitHub/network
echo blip - just double-click this file again and try once more.
echo.
echo If it keeps failing every time, ask whoever sent you this file for
echo bootstrap-install.ps1 directly, put it next to this file, and run:
echo   powershell -NoProfile -ExecutionPolicy Bypass -File bootstrap-install.ps1
echo.
pause
exit /b 1
